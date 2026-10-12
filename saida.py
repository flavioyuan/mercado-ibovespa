"""Escrita dos arquivos de saída, de forma atômica.

São três arquivos por execução, todos com a janela no nome (o que permite manter
vários horizontes no mesmo diretório):

- ``mercado_<inicio>_<fim>.csv`` — a **matriz larga**: uma linha por data do
  calendário comum e uma coluna por série. É o arquivo que vai para o modelo
  econométrico, e todas as colunas têm exatamente o mesmo número de linhas.
- ``mercado_<inicio>_<fim>_imputacoes.csv`` — as células que **não** vieram de
  publicação, no formato longo ``data,serie,situacao``. É a lista para filtrar
  quando o preenchimento incomodar.
- ``mercado_<inicio>_<fim>.json`` — a proveniência: janela, calendário, como a
  composição foi obtida, resumo por série e avisos.

A gravação é atômica: escreve-se num arquivo temporário no mesmo diretório e só
então ele substitui o destino com :func:`os.replace`. Assim uma falha no meio da
escrita não deixa um CSV truncado no lugar de um bom.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TextIO

from mercado_ibovespa.alinhamento import ConjuntoAlinhado, Situacao

# Preço, ponto de índice, % ao ano e R$/US$ cabem com folga em 10 casas; o valor
# é configurável na CLI para quem quiser um arquivo mais enxuto.
CASAS_DECIMAIS = 10
CODIFICACAO = "utf-8"
FIM_DE_LINHA = "\n"
PREFIXO_TEMPORARIO = "."
SUFIXO_TEMPORARIO = ".tmp"


class EscritaFalhou(Exception):
    """Não foi possível gravar um arquivo de saída."""


@dataclass(frozen=True, slots=True)
class CaminhosSaida:
    """Os três arquivos de uma execução."""

    matriz: Path
    imputacoes: Path
    metadados: Path


def caminhos_saida(diretorio: Path, inicio: date, fim: date) -> CaminhosSaida:
    """Deriva os nomes dos arquivos de saída a partir da janela.

    Args:
        diretorio: Diretório de saída.
        inicio: Primeira data da janela.
        fim: Última data da janela.

    Returns:
        Os caminhos da matriz, da lista de imputações e dos metadados.
    """
    base = f"mercado_{inicio.isoformat()}_{fim.isoformat()}"
    return CaminhosSaida(
        matriz=diretorio / f"{base}.csv",
        imputacoes=diretorio / f"{base}_imputacoes.csv",
        metadados=diretorio / f"{base}.json",
    )


def escrever_matriz(
    caminho: Path,
    conjunto: ConjuntoAlinhado,
    *,
    casas_decimais: int = CASAS_DECIMAIS,
) -> None:
    """Grava a matriz larga: ``data`` e uma coluna por série.

    Args:
        caminho: Arquivo de destino.
        conjunto: Conjunto alinhado a gravar.
        casas_decimais: Casas decimais de todos os valores.

    Raises:
        EscritaFalhou: Se não for possível gravar.
    """
    nomes = conjunto.nomes

    def escrever(arquivo: TextIO) -> None:
        escritor = csv.writer(arquivo, lineterminator=FIM_DE_LINHA)
        escritor.writerow(["data", *nomes])
        for indice, dia in enumerate(conjunto.grade):
            escritor.writerow(
                [
                    dia.isoformat(),
                    *(f"{serie.valores[indice]:.{casas_decimais}f}" for serie in conjunto.series),
                ]
            )

    _escrever_atomico(caminho, escrever)


def escrever_imputacoes(caminho: Path, conjunto: ConjuntoAlinhado) -> None:
    """Grava, no formato longo, as células preenchidas por repetição ou retroação.

    Só as células que **não** vieram de publicação entram no arquivo; uma série
    inteiramente observada não gera nenhuma linha.

    Args:
        caminho: Arquivo de destino.
        conjunto: Conjunto alinhado a gravar.

    Raises:
        EscritaFalhou: Se não for possível gravar.
    """

    def escrever(arquivo: TextIO) -> None:
        escritor = csv.writer(arquivo, lineterminator=FIM_DE_LINHA)
        escritor.writerow(["data", "serie", "situacao"])
        for serie in conjunto.series:
            for dia, situacao in zip(serie.grade, serie.situacoes, strict=True):
                if situacao is not Situacao.OBSERVADO:
                    escritor.writerow([dia.isoformat(), serie.nome, situacao.value])

    _escrever_atomico(caminho, escrever)


def escrever_json(caminho: Path, conteudo: Mapping[str, object]) -> None:
    """Grava um objeto JSON com recuo, em UTF-8.

    Args:
        caminho: Arquivo de destino.
        conteudo: Estrutura serializável em JSON.

    Raises:
        EscritaFalhou: Se não for possível gravar.
    """

    def escrever(arquivo: TextIO) -> None:
        json.dump(conteudo, arquivo, ensure_ascii=False, indent=2, sort_keys=False)
        arquivo.write(FIM_DE_LINHA)

    _escrever_atomico(caminho, escrever)


def _escrever_atomico(caminho: Path, escrever: Callable[[TextIO], None]) -> None:
    """Executa ``escrever`` num temporário e só então substitui o destino.

    Args:
        caminho: Arquivo de destino.
        escrever: Função que escreve o conteúdo no arquivo já aberto.

    Raises:
        EscritaFalhou: Se o diretório não puder ser criado, se a escrita falhar ou
            se a substituição atômica falhar. O temporário é sempre removido.
    """
    temporario: Path | None = None
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        descritor, nome = tempfile.mkstemp(
            dir=caminho.parent,
            prefix=f"{PREFIXO_TEMPORARIO}{caminho.name}{PREFIXO_TEMPORARIO}",
            suffix=SUFIXO_TEMPORARIO,
        )
        temporario = Path(nome)
        with os.fdopen(descritor, "w", encoding=CODIFICACAO, newline="") as arquivo:
            escrever(arquivo)
            arquivo.flush()
            # Sincronizar antes de substituir garante que o destino nunca aponte
            # para um arquivo cujo conteúdo ainda está só no cache do sistema.
            os.fsync(arquivo.fileno())
        # mkstemp cria o arquivo com 0600; um open() comum seguiria o umask do
        # processo, e o dado aqui é público — não há motivo para restringir.
        temporario.chmod(_modo_padrao())
        temporario.replace(caminho)
        temporario = None
    except OSError as erro:
        raise EscritaFalhou(f"não foi possível gravar {caminho}: {erro}") from erro
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)


def _modo_padrao() -> int:
    """Modo de arquivo que um ``open()`` comum daria: 0o666 menos o umask atual."""
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask
