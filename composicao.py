"""Composição do Ibovespa: de onde sai a lista de ações a baixar.

A carteira do Ibovespa é rebalanceada a cada quatro meses, então "as ações que
compõem o índice" só faz sentido com uma data de referência. Este módulo resolve
isso de três formas, em ordem de preferência definida pela CLI:

1. **B3 ao vivo** — ``GetPortfolioDay`` devolve a carteira do dia em JSON. É a
   fonte oficial e a única que reflete o rebalanceamento mais recente.
2. **Arquivo do usuário** — ``--arquivo-composicao`` aceita CSV, JSON ou texto.
   É a saída para quem precisa da composição de uma data passada, ou da união das
   carteiras do período, que a B3 não expõe por API.
3. **Lista embutida** — a carteira de :data:`DATA_REFERENCIA_EMBUTIDA`, congelada
   no código. Serve de *fallback* quando a B3 está fora do ar e é o que torna a
   execução reproduzível.

Todo código de negociação é validado contra o formato da B3
(``^[A-Z][A-Z0-9]{3}\\d{1,2}$``, que aceita desde ``PETR4`` até ``B3SA3``) antes de
virar símbolo do Yahoo. A resposta da B3 é entrada não confiável: nada que não
case com o padrão entra no *allowlist*.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Código de negociação da B3: quatro caracteres (o primeiro é letra; os demais
# podem ser letra ou dígito) seguidos de um ou dois dígitos do tipo de papel.
# O caso que impede um padrão ingênuo de "quatro letras" é B3SA3, que tem dígito
# no meio do código.
PADRAO_CODIGO = re.compile(r"^[A-Z][A-Z0-9]{3}\d{1,2}$")

# Sufixo que o Yahoo usa para a B3. Fica aqui porque é a mesma convenção de
# mercado que o padrão acima.
SUFIXO_B3 = ".SA"

# Endpoint oficial da carteira do dia. O parâmetro vai em base64 na própria URL,
# como a B3 exige.
URL_B3 = (
    "https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/GetPortfolioDay/{parametros}"
)
INDICE_B3 = "IBOV"
TAMANHO_PAGINA_B3 = 200
# *Allowlist* dos índices aceitos: o valor entra no JSON que vira base64, então não
# pode ser texto livre.
INDICES_B3 = frozenset({INDICE_B3})

# Data de referência da lista embutida, no formato ISO. A carteira muda a cada
# quatro meses; este carimbo diz exatamente qual carteira está congelada abaixo.
DATA_REFERENCIA_EMBUTIDA = "2026-10-06"

# Carteira do Ibovespa na DATA_REFERENCIA_EMBUTIDA, conforme o GetPortfolioDay da
# B3 (76 códigos, ordem alfabética). É um *fallback*: a CLI tenta a B3 primeiro.
COMPOSICAO_EMBUTIDA: tuple[str, ...] = (
    "ABEV3",
    "ALOS3",
    "ASAI3",
    "AURE3",
    "AXIA3",
    "AZZA3",
    "B3SA3",
    "BBAS3",
    "BBDC3",
    "BBDC4",
    "BBSE3",
    "BEEF3",
    "BPAC11",
    "BRAP4",
    "BRAV3",
    "CEAB3",
    "CMIG4",
    "CMIN3",
    "COGN3",
    "CPFE3",
    "CPLE3",
    "CSAN3",
    "CSMG3",
    "CSNA3",
    "CURY3",
    "CXSE3",
    "CYRE3",
    "DIRR3",
    "EGIE3",
    "EMBJ3",
    "ENEV3",
    "ENGI11",
    "EQTL3",
    "FLRY3",
    "GGBR4",
    "GOAU4",
    "HAPV3",
    "HYPE3",
    "IGTI11",
    "ISAE4",
    "ITSA4",
    "ITUB4",
    "KLBN11",
    "LREN3",
    "MBRF3",
    "MGLU3",
    "MOTV3",
    "MRVE3",
    "MULT3",
    "NATU3",
    "PETR3",
    "PETR4",
    "POMO4",
    "PRIO3",
    "PSSA3",
    "RADL3",
    "RAIL3",
    "RDOR3",
    "RENT3",
    "SANB11",
    "SBSP3",
    "SMFT3",
    "SUZB3",
    "TAEE11",
    "TEND3",
    "TIMS3",
    "TOTS3",
    "UGPA3",
    "USIM5",
    "VALE3",
    "VAMO3",
    "VBBR3",
    "VIVA3",
    "VIVT3",
    "WEGE3",
    "YDUQ3",
)


class CodigoInvalido(ValueError):
    """O texto recebido não é um código de negociação válido da B3."""


class ComposicaoInvalida(ValueError):
    """A lista de composição está vazia ou em formato inesperado."""


def montar_url_b3(indice: str = INDICE_B3, url_base: str = URL_B3) -> str:
    """Monta a URL do ``GetPortfolioDay`` para um índice da B3.

    Args:
        indice: Sigla do índice; precisa estar em :data:`INDICES_B3`.
        url_base: Modelo da URL, com o marcador ``{parametros}``.

    Returns:
        A URL completa, com o parâmetro codificado em base64.

    Raises:
        ComposicaoInvalida: Se a sigla não estiver no *allowlist*.

    Note:
        O JSON do parâmetro é montado aqui, com a sigla já validada, e codificado
        em base64: nenhum texto livre do usuário chega à URL.
    """
    if indice not in INDICES_B3:
        raise ComposicaoInvalida(
            f"índice fora do allowlist: {indice!r}; disponíveis: {', '.join(sorted(INDICES_B3))}"
        )
    parametro = {
        "language": "pt-br",
        "pageNumber": 1,
        "pageSize": TAMANHO_PAGINA_B3,
        "index": indice,
    }
    codificado = base64.b64encode(
        json.dumps(parametro, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return url_base.format(parametros=codificado) + "/"


def normalizar_codigo(bruto: str) -> str:
    """Valida e normaliza um código de negociação da B3.

    Args:
        bruto: Código como veio do usuário ou da B3, com ou sem o sufixo ``.SA``.

    Returns:
        O código em maiúsculas, sem o sufixo.

    Raises:
        CodigoInvalido: Se, depois de normalizado, o texto não casar com
            :data:`PADRAO_CODIGO`.
    """
    limpo = bruto.strip().upper()
    if limpo.endswith(SUFIXO_B3):
        limpo = limpo[: -len(SUFIXO_B3)]
    if not PADRAO_CODIGO.match(limpo):
        raise CodigoInvalido(
            f"código inválido: {bruto!r}; esperado o formato da B3, como PETR4 ou BPAC11"
        )
    return limpo


def combinar(listas: Iterable[Iterable[str]]) -> tuple[str, ...]:
    """Une listas de códigos, sem repetição e preservando a ordem de entrada."""
    vistos: dict[str, None] = {}
    for lista in listas:
        for codigo in lista:
            vistos.setdefault(normalizar_codigo(codigo), None)
    return tuple(vistos)


def parsear_composicao_b3(payload: str) -> tuple[str, ...]:
    """Extrai os códigos da resposta do ``GetPortfolioDay`` da B3.

    Args:
        payload: Corpo JSON devolvido pela B3.

    Returns:
        Os códigos válidos, em ordem alfabética e sem repetição.

    Raises:
        ComposicaoInvalida: Se o payload não for o JSON esperado, se a lista de
            ativos estiver vazia ou se nenhum código for válido.
    """
    try:
        bruto: Any = json.loads(payload)
    except json.JSONDecodeError as erro:
        raise ComposicaoInvalida(
            f"a resposta da B3 não é JSON válido: {erro.msg} (linha {erro.lineno})"
        ) from erro

    if not isinstance(bruto, dict):
        raise ComposicaoInvalida(
            f"a resposta da B3 deveria ser um objeto JSON, veio {type(bruto).__name__}"
        )
    resultados = bruto.get("results")
    if not isinstance(resultados, list) or not resultados:
        raise ComposicaoInvalida("a resposta da B3 não trouxe a lista 'results' com ativos")

    validos: list[str] = []
    descartados = 0
    for item in resultados:
        # A B3 pode devolver entradas de outros mercados; só código no formato
        # da bolsa passa, e o que não passa é contado, não silenciado.
        codigo = item.get("cod") if isinstance(item, dict) else None
        if not isinstance(codigo, str):
            descartados += 1
            continue
        try:
            validos.append(normalizar_codigo(codigo))
        except CodigoInvalido:
            descartados += 1

    if not validos:
        raise ComposicaoInvalida(
            f"nenhum dos {len(resultados)} ativos da resposta da B3 tem código válido"
        )
    if descartados:
        # Não é erro: a resposta da B3 é paginada e mistura campos auxiliares.
        # Registrar mantém o descarte visível em vez de silencioso.
        logger.warning(
            "B3: %d entrada(s) sem código de negociação válido foram ignoradas", descartados
        )
    # A B3 devolve os ativos numa ordem própria (não alfabética); ordenar aqui
    # deixa a ordem das colunas do arquivo independente disso.
    return tuple(sorted(combinar([validos])))


def carregar_composicao(caminho: Path) -> tuple[str, ...]:
    """Lê uma composição de um arquivo CSV, JSON ou texto simples.

    Formatos aceitos:

    - **CSV** — usa a coluna ``cod``/``ticker``/``codigo`` quando existe; senão, a
      primeira coluna, com ou sem cabeçalho.
    - **JSON** — uma lista de strings, ou o próprio payload da B3 (objeto com
      ``results``).
    - **Texto** — um código por linha.

    Args:
        caminho: Arquivo a ler.

    Returns:
        Os códigos válidos, sem repetição e na ordem em que aparecem.

    Raises:
        ComposicaoInvalida: Se o arquivo não existir, não for um arquivo regular,
            estiver vazio ou não tiver nenhum código válido.
    """
    resolvido = caminho.expanduser().resolve()
    if not resolvido.is_file():
        raise ComposicaoInvalida(f"arquivo de composição não encontrado: {resolvido}")

    try:
        conteudo = resolvido.read_text(encoding="utf-8")
    except OSError as erro:
        raise ComposicaoInvalida(f"não foi possível ler {resolvido}: {erro}") from erro

    if not conteudo.strip():
        raise ComposicaoInvalida(f"o arquivo de composição está vazio: {resolvido}")

    if resolvido.suffix.lower() == ".json":
        return _ler_json(conteudo, resolvido)
    codigos = list(_extrair_de_texto(conteudo, separador="," if _e_csv(resolvido) else None))
    if not codigos:
        raise ComposicaoInvalida(f"nenhum código válido encontrado em {resolvido}")
    return combinar([codigos])


def _ler_json(conteudo: str, caminho: Path) -> tuple[str, ...]:
    """Lê uma composição em JSON, aceitando lista de strings ou payload da B3."""
    try:
        bruto: Any = json.loads(conteudo)
    except json.JSONDecodeError as erro:
        raise ComposicaoInvalida(f"{caminho} não é JSON válido: {erro.msg}") from erro

    if isinstance(bruto, dict):
        try:
            return parsear_composicao_b3(conteudo)
        except ComposicaoInvalida as erro:
            raise ComposicaoInvalida(f"{caminho}: {erro}") from erro
    if not isinstance(bruto, list):
        raise ComposicaoInvalida(
            f"{caminho} deveria conter uma lista de códigos ou um objeto da B3"
        )
    codigos = [normalizar_codigo(item) for item in bruto if isinstance(item, str)]
    if not codigos:
        raise ComposicaoInvalida(f"nenhum código válido encontrado em {caminho}")
    return combinar([codigos])


def _e_csv(caminho: Path) -> bool:
    """Diz se o arquivo deve ser lido como CSV pela extensão."""
    return caminho.suffix.lower() in {".csv", ".tsv"}


def _extrair_de_texto(conteudo: str, *, separador: str | None) -> Iterator[str]:
    """Varre o conteúdo linha a linha, aceitando cabeçalho e ignorando o resto."""
    for numero, linha in enumerate(conteudo.splitlines(), start=1):
        texto = linha.strip()
        if not texto or texto.startswith("#"):
            continue
        if separador is None:
            candidatos = [texto]
        else:
            candidatos = [campo.strip().strip('"').strip("'") for campo in texto.split(separador)]
            if numero == 1 and not any(_parece_codigo(campo) for campo in candidatos):
                # Cabeçalho sem nenhum código: linha descartada.
                continue
        for candidato in candidatos:
            if _parece_codigo(candidato):
                yield candidato


def _parece_codigo(texto: str) -> bool:
    """Diz se o texto tem o formato de um código da B3, sem lançar exceção."""
    limpo = texto.strip().upper()
    if limpo.endswith(SUFIXO_B3):
        limpo = limpo[: -len(SUFIXO_B3)]
    return bool(PADRAO_CODIGO.match(limpo))
