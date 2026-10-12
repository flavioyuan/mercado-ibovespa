"""Ações do Ibovespa e o índice, via API de gráfico do Yahoo Finance.

A fonte é ``/v8/finance/chart``, que devolve OHLC histórico sem exigir autenticação
e traz o *adjusted close* — o preço corrigido por proventos e desdobramentos. É o
*adjusted close* que serve para calcular retornos, porque um dividendo não é uma
perda de capital.

Mapeamento dos símbolos:

- ação da B3 ``PETR4``  -> ``PETR4.SA``
- índice Ibovespa       -> ``^BVSP``

Este módulo é puro: monta a URL e converte um payload **já baixado** em
observações validadas. O payload é entrada não confiável e passa por checagem de
estrutura, de tipo, de plausibilidade e de janela antes de virar dado.

Sobre o fuso: o Yahoo carimba cada barra diária com o horário de abertura do
pregão no fuso da bolsa (para a B3, 13:00 UTC = 10:00 em America/Sao_Paulo), e a
data que interessa é a data **local da bolsa**. A conversão usa o
``exchangeTimezoneName`` do próprio payload, com queda para UTC se o fuso vier
ausente ou desconhecido.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mercado_ibovespa.composicao import SUFIXO_B3, normalizar_codigo
from mercado_ibovespa.modelo import Ponto

logger = logging.getLogger(__name__)

URL_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}"
FONTE = "Yahoo Finance"
SIMBOLO_INDICE = "^BVSP"
NOME_INDICE = "IBOVESPA"
INTERVALO = "1d"
CAMPO_AJUSTADO = "adjclose"
CAMPO_BRUTO = "close"
CAMPOS = (CAMPO_AJUSTADO, CAMPO_BRUTO)
LIMITE_MENSAGEM_ERRO = 200
UM_DIA = timedelta(days=1)


class RespostaInvalida(ValueError):
    """A resposta do Yahoo não tem o formato, o conteúdo ou a janela esperados."""


def simbolo_de_codigo(codigo: str) -> str:
    """Converte um código da B3 no símbolo usado pelo Yahoo.

    Args:
        codigo: Código de negociação, como ``PETR4`` (com ou sem ``.SA``).

    Returns:
        O símbolo do Yahoo, como ``PETR4.SA``.

    Raises:
        CodigoInvalido: Se o código não tiver o formato da B3.
    """
    return f"{normalizar_codigo(codigo)}{SUFIXO_B3}"


def montar_url(
    simbolo: str,
    inicio: date,
    fim: date,
    url_base: str = URL_BASE,
) -> str:
    """Monta a URL de consulta de um símbolo em uma janela.

    Args:
        simbolo: Símbolo do Yahoo (``PETR4.SA``, ``^BVSP``).
        inicio: Primeira data da janela, inclusive.
        fim: Última data da janela, inclusive.
        url_base: Modelo da URL, com o marcador ``{simbolo}``.

    Returns:
        A URL completa com ``period1``/``period2`` em *epoch* UTC. O fim é
        estendido em um dia para que a barra da própria data final entre.

    Note:
        O símbolo é escapado com :func:`urllib.parse.quote` e só chega aqui depois
        de passar pela validação de formato da B3 ou ser a constante do índice.
    """
    periodo_inicio = int(datetime.combine(inicio, time.min, tzinfo=UTC).timestamp())
    periodo_fim = int(datetime.combine(fim + UM_DIA, time.min, tzinfo=UTC).timestamp())
    escapado = quote(simbolo, safe="")
    return (
        f"{url_base.format(simbolo=escapado)}"
        f"?period1={periodo_inicio}&period2={periodo_fim}&interval={INTERVALO}"
    )


def parsear_serie(
    payload: str,
    *,
    campo: str = CAMPO_AJUSTADO,
    janela: tuple[date, date] | None = None,
) -> list[Ponto]:
    """Converte o payload JSON do Yahoo em observações validadas e ordenadas.

    Args:
        payload: Corpo da resposta HTTP, já decodificado como texto.
        campo: ``adjclose`` (padrão) para o preço ajustado, ou ``close``.
        janela: Janela pedida; quando informada, observação fora dela é rejeitada.

    Returns:
        Observações em ordem cronológica crescente. Dias sem negociação (barra com
        ``null``) são simplesmente omitidos: viram lacuna, que o alinhamento
        preenche repetindo a última observação.

    Raises:
        RespostaInvalida: Se o payload não for o JSON esperado, se o Yahoo
            devolver erro, se não houver barras, se o campo pedido não existir, se
            os tamanhos não baterem, ou se houver valor não numérico, não finito,
            não positivo ou fora da janela.

    Note:
        Barras repetidas na mesma data — o Yahoo acrescenta uma cotação ao vivo do
        dia corrente — são colapsadas, mantendo a mais recente.
    """
    if campo not in CAMPOS:
        raise RespostaInvalida(f"campo inválido: {campo!r}; esperado um de {CAMPOS}")

    try:
        bruto: Any = json.loads(payload)
    except json.JSONDecodeError as erro:
        raise RespostaInvalida(
            f"resposta não é JSON válido: {erro.msg} (linha {erro.lineno})"
        ) from erro

    if not isinstance(bruto, dict):
        raise RespostaInvalida(f"resposta deveria ser um objeto JSON, veio {type(bruto).__name__}")
    corpo = bruto.get("chart")
    if not isinstance(corpo, dict):
        raise RespostaInvalida("a resposta não trouxe o objeto 'chart'")
    if corpo.get("error"):
        raise RespostaInvalida(f"o Yahoo devolveu erro: {_mensagem_erro(corpo['error'])}")

    resultados = corpo.get("result")
    if not isinstance(resultados, list) or not resultados:
        raise RespostaInvalida("o Yahoo não retornou nenhum resultado para o símbolo")

    resultado = resultados[0]
    if not isinstance(resultado, dict):
        raise RespostaInvalida(
            f"resultado deveria ser um objeto JSON, veio {type(resultado).__name__}"
        )

    carimbos = resultado.get("timestamp")
    if not isinstance(carimbos, list) or not carimbos:
        raise RespostaInvalida("o Yahoo não retornou barras para a janela pedida")

    valores = _extrair_valores(resultado, campo)
    if len(valores) != len(carimbos):
        raise RespostaInvalida(
            f"o Yahoo devolveu {len(carimbos)} datas e {len(valores)} valores de {campo!r}"
        )

    fuso = _resolver_fuso(resultado)
    pontos: list[Ponto] = []
    for indice, (carimbo, valor) in enumerate(zip(carimbos, valores, strict=True)):
        if valor is None:
            # Barra sem negociação (feriado local, papel suspenso): é lacuna, e a
            # lacuna quem resolve é o alinhamento, não este parser.
            continue
        pontos.append(
            Ponto(_converter_data(carimbo, fuso, indice), _converter_valor(valor, indice))
        )

    if not pontos:
        raise RespostaInvalida(f"a série não tem nenhum valor de {campo!r} na janela pedida")
    pontos.sort(key=lambda ponto: ponto.data)
    pontos = _colapsar_datas_repetidas(pontos)
    if janela is not None:
        _rejeitar_fora_da_janela(pontos, janela)
    return pontos


def _extrair_valores(resultado: dict[str, Any], campo: str) -> list[Any]:
    """Extrai a lista de valores do campo pedido dentro de ``indicators``."""
    indicadores = resultado.get("indicators")
    if not isinstance(indicadores, dict):
        raise RespostaInvalida("a resposta não trouxe o objeto 'indicators'")

    if campo == CAMPO_BRUTO:
        blocos = indicadores.get("quote")
        if not isinstance(blocos, list) or not blocos:
            raise RespostaInvalida("a resposta não trouxe o bloco 'quote'")
        bloco = blocos[0]
        valores = bloco.get("close") if isinstance(bloco, dict) else None
    else:
        blocos = indicadores.get(CAMPO_AJUSTADO)
        if not isinstance(blocos, list) or not blocos:
            raise RespostaInvalida(
                "a resposta não trouxe o bloco 'adjclose'; use --campo close para o preço bruto"
            )
        bloco = blocos[0]
        valores = bloco.get(CAMPO_AJUSTADO) if isinstance(bloco, dict) else None

    if not isinstance(valores, list):
        raise RespostaInvalida(f"o campo {campo!r} não é uma lista de valores")
    return valores


def _resolver_fuso(resultado: dict[str, Any]) -> ZoneInfo | timezone:
    """Descobre o fuso da bolsa a partir do payload, com queda para UTC."""
    meta = resultado.get("meta")
    if isinstance(meta, dict):
        nome = meta.get("exchangeTimezoneName")
        if isinstance(nome, str) and nome:
            try:
                return ZoneInfo(nome)
            except (ZoneInfoNotFoundError, ValueError):
                # Fuso desconhecido nesta máquina: UTC mantém a data correta para
                # a B3, cujas barras diárias abrem às 13:00 UTC.
                return UTC
    return UTC


def _converter_data(carimbo: Any, fuso: ZoneInfo | timezone, indice: int) -> date:
    """Converte o carimbo *epoch* em data local da bolsa."""
    if isinstance(carimbo, bool) or not isinstance(carimbo, (int, float)):
        raise RespostaInvalida(f"barra {indice}: carimbo de tempo não numérico: {carimbo!r}")
    try:
        return datetime.fromtimestamp(float(carimbo), tz=fuso).date()
    except (OverflowError, OSError, ValueError) as erro:
        raise RespostaInvalida(f"barra {indice}: carimbo de tempo inválido: {carimbo!r}") from erro


def _converter_valor(valor: Any, indice: int) -> float:
    """Converte o valor para ``float`` e recusa preço não positivo."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise RespostaInvalida(f"barra {indice}: preço de tipo inesperado: {valor!r}")
    numero = float(valor)
    if not math.isfinite(numero):
        raise RespostaInvalida(f"barra {indice}: preço não finito: {valor!r}")
    if numero <= 0:
        raise RespostaInvalida(f"barra {indice}: preço não positivo: {numero}")
    return numero


def _colapsar_datas_repetidas(pontos: list[Ponto]) -> list[Ponto]:
    """Colapsa barras que caem na mesma data, mantendo a de carimbo mais recente.

    Acontece com o índice: além da barra do pregão, o Yahoo acrescenta uma cotação
    ao vivo carimbada com o horário corrente, que no fuso da B3 cai na **mesma
    data** da barra do dia. Como as duas descrevem o mesmo pregão, ficar com as
    duas quebraria o alinhamento; fica a de carimbo mais recente, que é a
    informação mais nova daquele dia.

    Args:
        pontos: Observações já ordenadas por data; a ordem original é preservada
            dentro de cada data, então a última de cada grupo é a mais recente.

    Returns:
        As observações sem data repetida, em ordem cronológica.
    """
    datas = {ponto.data for ponto in pontos}
    if len(datas) == len(pontos):
        return pontos

    por_data: dict[date, Ponto] = {}
    for ponto in pontos:
        por_data[ponto.data] = ponto
    colapsados = sorted(por_data.values(), key=lambda ponto: ponto.data)
    logger.warning(
        "o Yahoo devolveu %d barra(s) em datas já cobertas (cotação ao vivo do dia "
        "corrente); mantida a mais recente em cada data",
        len(pontos) - len(colapsados),
    )
    return colapsados


def _rejeitar_fora_da_janela(pontos: list[Ponto], janela: tuple[date, date]) -> None:
    """Falha se alguma observação cair fora da janela pedida."""
    inicio, fim = janela
    for ponto in pontos:
        if not inicio <= ponto.data <= fim:
            raise RespostaInvalida(
                f"observação {ponto.data} está fora da janela pedida ({inicio} a {fim})"
            )


def _mensagem_erro(erro: Any) -> str:
    """Reduz o objeto de erro do Yahoo a uma linha curta e legível."""
    if isinstance(erro, dict):
        partes = [str(erro[chave]) for chave in ("code", "description") if erro.get(chave)]
        if partes:
            return " ".join(" ".join(partes).split())[:LIMITE_MENSAGEM_ERRO]
    return " ".join(str(erro).split())[:LIMITE_MENSAGEM_ERRO]
