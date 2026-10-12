"""Lógica pura do painel: leitura da matriz alinhada e cálculos para os gráficos.

Este módulo não importa ``pandas``, ``altair`` nem ``streamlit`` — só a biblioteca
padrão. A consequência prática é que toda a conta que pode dar errado (ler o CSV,
normalizar, calcular retorno, volatilidade e correlação) fica testável offline, e o
app do painel (:mod:`mercado_ibovespa.painel`) fica responsável apenas por desenhar.

Convenções:

- uma **matriz** é o CSV largo gravado por :mod:`mercado_ibovespa.saida`: a coluna
  ``data`` mais uma coluna por série, todas com o mesmo número de linhas;
- **retorno** é sempre log-retorno diário, ``ln(p_t / p_{t-1})``, que é a grandeza
  que se assume aditiva no tempo;
- **volatilidade anualizada** é o desvio-padrão amostral dos log-retornos vezes
  ``sqrt(252)``, a convenção usual de dias úteis por ano.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from pathlib import Path

from mercado_ibovespa import sgs, yahoo

# Nomes das séries que não são ação, usados pelo painel para separar os grupos.
NOME_INDICE = yahoo.NOME_INDICE
NOMES_MACRO: tuple[str, ...] = tuple(chave.upper() for chave in sgs.CATALOGO)

PADRAO_ARQUIVO = "mercado_*.csv"
SUFIXO_IMPUTACOES = "_imputacoes.csv"
SUFIXO_METADADOS = ".json"
DIAS_UTEIS_POR_ANO = 252
COLUNA_DATA = "data"
SITUACOES = ("observado", "repetido", "retrocidido")


class MatrizInvalida(ValueError):
    """O arquivo não tem o formato da matriz alinhada ou tem conteúdo inconsistente."""


@dataclass(frozen=True, slots=True)
class Matriz:
    """Uma matriz alinhada: datas na linha, séries na coluna."""

    caminho: Path
    datas: tuple[date, ...]
    series: Mapping[str, tuple[float, ...]]

    def __post_init__(self) -> None:
        for nome, valores in self.series.items():
            if len(valores) != len(self.datas):
                raise MatrizInvalida(
                    f"a série {nome!r} tem {len(valores)} valores para {len(self.datas)} datas"
                )

    @property
    def nomes(self) -> tuple[str, ...]:
        """Nomes das séries, na ordem das colunas do arquivo."""
        return tuple(self.series)

    @property
    def observacoes(self) -> int:
        """Número de datas — o mesmo para todas as séries."""
        return len(self.datas)

    @property
    def periodo(self) -> tuple[date, date]:
        """Primeira e última data da matriz.

        Raises:
            MatrizInvalida: Se não houver nenhuma data.
        """
        if not self.datas:
            raise MatrizInvalida("a matriz não tem nenhuma data")
        return self.datas[0], self.datas[-1]

    def valores(self, nome: str) -> tuple[float, ...]:
        """Valores de uma série.

        Raises:
            KeyError: Se a série não existir.
        """
        return self.series[nome]

    def acoes(self) -> tuple[str, ...]:
        """Séries que são ação (nem índice, nem macro)."""
        return tuple(nome for nome in self.nomes if nome != NOME_INDICE and nome not in NOMES_MACRO)

    def recortar(self, inicio: date, fim: date) -> Matriz:
        """Devolve a matriz restrita a um intervalo de datas, inclusive nas pontas.

        Args:
            inicio: Primeira data a manter.
            fim: Última data a manter.

        Returns:
            Uma nova matriz com as datas dentro do intervalo.

        Raises:
            MatrizInvalida: Se ``inicio`` for posterior a ``fim`` ou se o recorte
                ficar vazio.
        """
        if inicio > fim:
            raise MatrizInvalida(f"data inicial {inicio} é posterior à final {fim}")
        indices = [indice for indice, dia in enumerate(self.datas) if inicio <= dia <= fim]
        if not indices:
            raise MatrizInvalida(f"não há observações entre {inicio} e {fim}")
        return Matriz(
            caminho=self.caminho,
            datas=tuple(self.datas[indice] for indice in indices),
            series={
                nome: tuple(valores[indice] for indice in indices)
                for nome, valores in self.series.items()
            },
        )

    def selecionar(self, nomes: Sequence[str]) -> Matriz:
        """Devolve a matriz restrita a algumas séries.

        Raises:
            MatrizInvalida: Se a seleção ficar vazia ou citar série inexistente.
        """
        if not nomes:
            raise MatrizInvalida("a seleção de séries está vazia")
        desconhecidas = [nome for nome in nomes if nome not in self.series]
        if desconhecidas:
            raise MatrizInvalida(
                "séries não encontradas na matriz: " + ", ".join(sorted(desconhecidas))
            )
        return Matriz(
            caminho=self.caminho,
            datas=self.datas,
            series={nome: self.series[nome] for nome in nomes},
        )


@dataclass(frozen=True, slots=True)
class Estatisticas:
    """Resumo de uma série num período."""

    nome: str
    observacoes: int
    primeiro: float
    ultimo: float
    minimo: float
    maximo: float
    retorno_total: float
    volatilidade_anual: float

    def para_dicionario(self) -> dict[str, object]:
        """Converte em estrutura serializável, para a tabela do painel."""
        return {
            "série": self.nome,
            "primeiro": self.primeiro,
            "último": self.ultimo,
            "mínimo": self.minimo,
            "máximo": self.maximo,
            "retorno no período": self.retorno_total,
            "volatilidade anual": self.volatilidade_anual,
        }


@dataclass(frozen=True, slots=True)
class Imputacao:
    """Uma célula da matriz que não veio de publicação da fonte."""

    data: date
    serie: str
    situacao: str


@dataclass(frozen=True, slots=True)
class Proveniencia:
    """O que os metadados da execução dizem sobre a origem da matriz."""

    gerado_em: str
    estrategia: str
    composicao_fonte: str
    acoes: int
    observacoes: int
    avisos: tuple[str, ...]


def listar_matrizes(diretorio: Path) -> tuple[Path, ...]:
    """Lista as matrizes alinhadas de um diretório, da janela mais recente para a mais antiga.

    Args:
        diretorio: Diretório onde o downloader grava a saída.

    Returns:
        Os caminhos dos arquivos ``mercado_*.csv``, excluindo as listas de
        imputações. Vazio se o diretório não existir.
    """
    if not diretorio.is_dir():
        return ()
    return tuple(
        sorted(
            (
                arquivo
                for arquivo in diretorio.glob(PADRAO_ARQUIVO)
                if not arquivo.name.endswith(SUFIXO_IMPUTACOES)
            ),
            key=lambda arquivo: arquivo.name,
            reverse=True,
        )
    )


def caminho_imputacoes(caminho_matriz: Path) -> Path:
    """Deriva o caminho da lista de imputações a partir do caminho da matriz."""
    return caminho_matriz.with_name(caminho_matriz.stem + SUFIXO_IMPUTACOES)


def caminho_metadados(caminho_matriz: Path) -> Path:
    """Deriva o caminho dos metadados a partir do caminho da matriz."""
    return caminho_matriz.with_suffix(SUFIXO_METADADOS)


def carregar_matriz(caminho: Path) -> Matriz:
    """Lê uma matriz alinhada de um CSV.

    O arquivo é tratado como entrada não confiável: cabeçalho, datas, valores e
    consistência entre colunas são checados antes de qualquer gráfico.

    Args:
        caminho: Arquivo CSV gravado por :mod:`mercado_ibovespa.saida`.

    Returns:
        A matriz carregada.

    Raises:
        MatrizInvalida: Se o arquivo não existir, não for um CSV com a coluna
            ``data``, não tiver nenhuma série, tiver data ou valor ilegível, ou
            trouxer datas repetidas ou fora de ordem.
    """
    resolvido = caminho.expanduser()
    if not resolvido.is_file():
        raise MatrizInvalida(f"arquivo não encontrado: {resolvido}")

    try:
        with resolvido.open(encoding="utf-8", newline="") as arquivo:
            linhas = list(csv.reader(arquivo))
    except (OSError, UnicodeDecodeError) as erro:
        raise MatrizInvalida(f"não foi possível ler {resolvido}: {erro}") from erro

    if not linhas:
        raise MatrizInvalida(f"o arquivo está vazio: {resolvido}")
    cabecalho = linhas[0]
    if len(cabecalho) < 2 or cabecalho[0].strip() != COLUNA_DATA:
        raise MatrizInvalida(f"o cabeçalho deveria começar com {COLUNA_DATA!r}: {resolvido}")
    nomes = [nome.strip() for nome in cabecalho[1:]]
    if any(not nome for nome in nomes):
        raise MatrizInvalida(f"o cabeçalho tem coluna sem nome: {resolvido}")
    if len(set(nomes)) != len(nomes):
        raise MatrizInvalida(f"o cabeçalho tem colunas repetidas: {resolvido}")

    datas: list[date] = []
    colunas: dict[str, list[float]] = {nome: [] for nome in nomes}
    for numero, linha in enumerate(linhas[1:], start=2):
        if not linha:
            continue
        if len(linha) != len(cabecalho):
            raise MatrizInvalida(
                f"linha {numero}: {len(linha)} campos para {len(cabecalho)} colunas"
            )
        datas.append(_converter_data(linha[0], numero))
        for nome, texto in zip(nomes, linha[1:], strict=True):
            colunas[nome].append(_converter_valor(texto, numero, nome))

    if not datas:
        raise MatrizInvalida(f"o arquivo não tem nenhuma linha de dados: {resolvido}")
    if len(set(datas)) != len(datas):
        raise MatrizInvalida(f"o arquivo tem datas repetidas: {resolvido}")
    if datas != sorted(datas):
        raise MatrizInvalida(f"as datas não estão em ordem cronológica: {resolvido}")

    return Matriz(
        caminho=resolvido,
        datas=tuple(datas),
        series={nome: tuple(valores) for nome, valores in colunas.items()},
    )


def carregar_imputacoes(caminho: Path) -> tuple[Imputacao, ...]:
    """Lê a lista de imputações de um CSV.

    Args:
        caminho: Arquivo ``*_imputacoes.csv``.

    Returns:
        As imputações, na ordem do arquivo. Vazio se o arquivo não existir.
    """
    if not caminho.is_file():
        return ()
    try:
        with caminho.open(encoding="utf-8", newline="") as arquivo:
            linhas = list(csv.DictReader(arquivo))
    except (OSError, UnicodeDecodeError):
        return ()

    imputacoes: list[Imputacao] = []
    for linha in linhas:
        try:
            imputacoes.append(
                Imputacao(
                    data=date.fromisoformat((linha.get("data") or "").strip()),
                    serie=(linha.get("serie") or "").strip(),
                    situacao=(linha.get("situacao") or "").strip(),
                )
            )
        except ValueError:
            # Linha corrompida não derruba o painel: é relatório de qualidade,
            # não dado de análise.
            continue
    return tuple(imputacoes)


def carregar_proveniencia(caminho: Path) -> Proveniencia | None:
    """Lê os metadados da execução, tolerando arquivo ausente ou malformado.

    Args:
        caminho: Arquivo ``.json`` ao lado da matriz.

    Returns:
        A proveniência, ou ``None`` se o arquivo não existir ou não tiver o
        formato esperado.
    """
    if not caminho.is_file():
        return None
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(bruto, dict):
        return None

    calendario = bruto.get("calendario")
    composicao = bruto.get("composicao")
    avisos = bruto.get("avisos")
    return Proveniencia(
        gerado_em=_texto(bruto.get("gerado_em")),
        estrategia=_texto(calendario.get("estrategia")) if isinstance(calendario, dict) else "",
        composicao_fonte=(_texto(composicao.get("fonte")) if isinstance(composicao, dict) else ""),
        acoes=_inteiro(composicao.get("acoes")) if isinstance(composicao, dict) else 0,
        observacoes=(
            _inteiro(calendario.get("observacoes")) if isinstance(calendario, dict) else 0
        ),
        avisos=tuple(str(aviso) for aviso in avisos) if isinstance(avisos, list) else (),
    )


def normalizar_base_100(valores: Sequence[float]) -> tuple[float, ...]:
    """Rebaseia a série no primeiro valor, que passa a valer 100.

    É o que permite comparar num mesmo eixo uma ação de R$ 4 e o Ibovespa de
    200 000 pontos: o que aparece é a variação percentual acumulada.

    Args:
        valores: Série de preços ou níveis.

    Returns:
        A série rebaseada.

    Raises:
        MatrizInvalida: Se a série estiver vazia ou começar em valor não positivo.
    """
    if not valores:
        raise MatrizInvalida("não há valores para rebasear")
    base = valores[0]
    if not math.isfinite(base) or base <= 0:
        raise MatrizInvalida(f"o primeiro valor precisa ser positivo para rebasear: {base}")
    return tuple(valor / base * 100.0 for valor in valores)


def retornos_log(valores: Sequence[float]) -> tuple[float, ...]:
    """Calcula os log-retornos diários de uma série.

    Args:
        valores: Série de preços ou níveis, em ordem cronológica.

    Returns:
        ``n - 1`` log-retornos. Um par com valor não positivo vira ``nan`` em vez
        de derrubar o painel; os gráficos deixam lacuna e as estatísticas ignoram.

    Raises:
        MatrizInvalida: Se a série estiver vazia.
    """
    if not valores:
        raise MatrizInvalida("não há valores para calcular retorno")
    retornos: list[float] = []
    for anterior, atual in pairwise(valores):
        if anterior > 0 and atual > 0 and math.isfinite(anterior) and math.isfinite(atual):
            retornos.append(math.log(atual / anterior))
        else:
            retornos.append(math.nan)
    return tuple(retornos)


def retorno_acumulado(valores: Sequence[float]) -> float:
    """Retorno total do período, ``último / primeiro - 1``."""
    if len(valores) < 2 or valores[0] <= 0:
        return math.nan
    return valores[-1] / valores[0] - 1.0


def volatilidade_anualizada(
    valores: Sequence[float], *, periodos_por_ano: int = DIAS_UTEIS_POR_ANO
) -> float:
    """Desvio-padrão amostral dos log-retornos, anualizado.

    Returns:
        A volatilidade, ou ``nan`` se houver menos de dois retornos válidos.
    """
    validos = [valor for valor in retornos_log(valores) if math.isfinite(valor)]
    if len(validos) < 2:
        return math.nan
    return statistics.stdev(validos) * math.sqrt(periodos_por_ano)


def estatisticas(nome: str, valores: Sequence[float]) -> Estatisticas:
    """Resume uma série no período: nível, faixa, retorno e volatilidade.

    Raises:
        MatrizInvalida: Se a série estiver vazia.
    """
    if not valores:
        raise MatrizInvalida(f"a série {nome!r} está vazia")
    finitos = [valor for valor in valores if math.isfinite(valor)]
    return Estatisticas(
        nome=nome,
        observacoes=len(valores),
        primeiro=valores[0],
        ultimo=valores[-1],
        minimo=min(finitos) if finitos else math.nan,
        maximo=max(finitos) if finitos else math.nan,
        retorno_total=retorno_acumulado(valores),
        volatilidade_anual=volatilidade_anualizada(valores),
    )


def correlacao(primeira: Sequence[float], segunda: Sequence[float]) -> float:
    """Correlação de Pearson entre os log-retornos de duas séries.

    A correlação é calculada sobre **retornos**, não sobre níveis: duas séries de
    preço com tendência aparecem quase perfeitamente correlacionadas mesmo sem
    relação alguma, o que é o caso clássico de regressão espúria.

    Args:
        primeira: Primeira série de níveis.
        segunda: Segunda série de níveis, de mesmo tamanho.

    Returns:
        A correlação, ou ``nan`` se houver menos de dois pares válidos, se os
        tamanhos diferirem ou se alguma das séries tiver variância nula.
    """
    if len(primeira) != len(segunda):
        return math.nan
    pares = [
        (a, b)
        for a, b in zip(retornos_log(primeira), retornos_log(segunda), strict=True)
        if math.isfinite(a) and math.isfinite(b)
    ]
    if len(pares) < 2:
        return math.nan
    xs = [a for a, _ in pares]
    ys = [b for _, b in pares]
    if statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        return math.nan
    return statistics.correlation(xs, ys)


def pares_correlacao(
    series: Mapping[str, Sequence[float]],
) -> tuple[tuple[str, str, float], ...]:
    """Calcula a correlação de todos os pares distintos de um conjunto de séries.

    Returns:
        Triplas ``(primeira, segunda, valor)`` em ordem lexicográfica, prontas
        para virar um mapa de calor.
    """
    nomes = sorted(series)
    return tuple(
        (nomes[i], nomes[j], correlacao(series[nomes[i]], series[nomes[j]]))
        for i in range(len(nomes))
        for j in range(i + 1, len(nomes))
    )


def contar_situacoes(
    imputacoes: Sequence[Imputacao], nomes: Sequence[str], observacoes: int
) -> dict[str, dict[str, int]]:
    """Conta, por série, quantas células de cada situação existem na matriz.

    O que não está na lista de imputações veio da fonte, então ``observado`` é
    calculado por diferença — é o complemento, não uma contagem do arquivo.

    Args:
        imputacoes: Lista lida de ``*_imputacoes.csv``.
        nomes: Séries da matriz.
        observacoes: Número de datas da matriz.

    Returns:
        Para cada série, um dicionário com as chaves de :data:`SITUACOES`.
    """
    contagem: dict[str, dict[str, int]] = {nome: dict.fromkeys(SITUACOES, 0) for nome in nomes}
    for imputacao in imputacoes:
        alvo = contagem.get(imputacao.serie)
        if alvo is None or imputacao.situacao not in alvo:
            continue
        alvo[imputacao.situacao] += 1
    for alvo in contagem.values():
        alvo["observado"] = max(0, observacoes - alvo["repetido"] - alvo["retrocidido"])
    return contagem


def _converter_data(texto: str, numero: int) -> date:
    """Converte a data ISO de uma linha, com erro que diz qual linha falhou."""
    try:
        return date.fromisoformat(texto.strip())
    except ValueError as erro:
        raise MatrizInvalida(
            f"linha {numero}: data fora do formato ISO (aaaa-mm-dd): {texto!r}"
        ) from erro


def _converter_valor(texto: str, numero: int, nome: str) -> float:
    """Converte o valor de uma célula, com erro que diz qual linha e coluna falharam."""
    try:
        numero_valor = float(texto)
    except ValueError as erro:
        raise MatrizInvalida(
            f"linha {numero}, coluna {nome!r}: valor não numérico: {texto!r}"
        ) from erro
    if not math.isfinite(numero_valor):
        raise MatrizInvalida(f"linha {numero}, coluna {nome!r}: valor não finito: {texto!r}")
    return numero_valor


def _texto(valor: object) -> str:
    """Converte um campo opcional dos metadados em texto."""
    return valor if isinstance(valor, str) else ""


def _inteiro(valor: object) -> int:
    """Converte um campo opcional dos metadados em inteiro."""
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else 0
