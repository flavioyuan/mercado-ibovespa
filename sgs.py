"""Séries macroeconômicas do SGS/BCB: a Selic e o câmbio BRL/USD.

O SGS (Sistema Gerenciador de Séries Temporais do Banco Central) é a fonte oficial
das duas séries macro do estudo:

===========  =========  ==========================================  =============
Chave        Código     O que é                                     Unidade
===========  =========  ==========================================  =============
``selic``    1178       Selic efetiva anualizada base 252           % ao ano
``cambio``   1          Dólar americano comercial (venda)           R$/US$
===========  =========  ==========================================  =============

A Selic efetiva (1178) é a taxa que o mercado usa como benchmark de renda fixa e
já vem anualizada, na unidade em que a taxa é citada. O câmbio (1) é a cotação de
venda do dólar comercial, publicada em todo dia útil — a série que serve de
referência para conversão e para modelagem cambial.

A série 1178 é a mesma adotada no projeto ``taxa-juros`` deste workspace, o que
mantém os dois estudos consistentes.

Este módulo é puro: monta a URL e converte um payload **já baixado** em
observações validadas. Nada aqui toca a rede nem o relógio, o que permite testar
todo o parsing com payloads fixos — inclusive os casos ruins. A resposta do BCB é
entrada não confiável e cada campo é checado quanto a tipo, formato,
plausibilidade e pertencimento à janela pedida.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from mercado_ibovespa.modelo import Ponto

FORMATO_DATA_API = "%d/%m/%Y"
URL_BASE = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"
FONTE = "BCB/SGS"


class RespostaInvalida(ValueError):
    """A resposta do SGS não tem o formato, o conteúdo ou a janela esperados."""


@dataclass(frozen=True, slots=True)
class SerieMacro:
    """Uma série do SGS disponível para download.

    ``valor_maximo`` é uma guarda de plausibilidade específica da série: pega
    número mal interpretado, não limita a economia.
    """

    chave: str
    codigo: int
    nome: str
    unidade: str
    valor_maximo: float

    @property
    def origem(self) -> str:
        """Identificação da fonte, usada nos metadados de saída."""
        return f"{FONTE} {self.codigo}"

    def __str__(self) -> str:
        return f"{self.nome} — SGS {self.codigo}, {self.unidade}"


# *Allowlist*: só é possível pedir uma série listada aqui, e o ``codigo`` é sempre
# um inteiro nosso, nunca texto do usuário.
CATALOGO: dict[str, SerieMacro] = {
    serie.chave: serie
    for serie in (
        SerieMacro(
            chave="selic",
            codigo=1178,
            nome="Selic efetiva anualizada base 252",
            unidade="% ao ano",
            valor_maximo=1000.0,
        ),
        SerieMacro(
            chave="cambio",
            codigo=1,
            nome="Dólar americano comercial (venda)",
            unidade="R$/US$",
            valor_maximo=100.0,
        ),
    )
}

SERIE_SELIC = "selic"
SERIE_CAMBIO = "cambio"


def obter_serie(chave: str) -> SerieMacro:
    """Busca uma série do catálogo pela chave, ignorando maiúsculas e espaços.

    Args:
        chave: Identificador da série, como ``selic`` ou ``cambio``.

    Returns:
        A série correspondente.

    Raises:
        KeyError: Se a chave não estiver no catálogo.
    """
    return CATALOGO[chave.strip().lower()]


def montar_url(codigo: int, inicio: date, fim: date, url_base: str = URL_BASE) -> str:
    """Monta a URL de consulta de uma série do SGS em uma janela.

    Args:
        codigo: Código da série no SGS.
        inicio: Primeira data da janela.
        fim: Última data da janela.
        url_base: Modelo da URL, com o marcador ``{codigo}``.

    Returns:
        A URL completa, com as datas no formato ``dd/mm/aaaa`` que a API exige.

    Note:
        As datas vêm de objetos :class:`datetime.date` formatados aqui dentro e o
        código é um inteiro do catálogo: não há texto livre interpolado na consulta.
    """
    parametros = (
        f"formato=json"
        f"&dataInicial={inicio.strftime(FORMATO_DATA_API)}"
        f"&dataFinal={fim.strftime(FORMATO_DATA_API)}"
    )
    return f"{url_base.format(codigo=codigo)}?{parametros}"


def parsear_observacoes(
    payload: str,
    *,
    janela: tuple[date, date] | None = None,
    valor_maximo: float,
) -> list[Ponto]:
    """Converte o payload JSON do SGS em observações validadas e ordenadas.

    Args:
        payload: Corpo da resposta HTTP, já decodificado como texto.
        janela: Janela pedida; quando informada, observação fora dela é rejeitada.
        valor_maximo: Limite de plausibilidade absoluto para o valor da série.

    Returns:
        Observações em ordem cronológica crescente.

    Raises:
        RespostaInvalida: Se o payload não for JSON, não for uma lista de objetos
            ``{"data", "valor"}``, estiver vazio, tiver datas repetidas ou
            inválidas, valores não numéricos, não finitos ou implausíveis, ou
            observações fora da janela pedida.
    """
    try:
        bruto: Any = json.loads(payload)
    except json.JSONDecodeError as erro:
        raise RespostaInvalida(
            f"resposta não é JSON válido: {erro.msg} (linha {erro.lineno})"
        ) from erro

    if not isinstance(bruto, list):
        raise RespostaInvalida(f"resposta deveria ser uma lista JSON, veio {type(bruto).__name__}")
    if not bruto:
        raise RespostaInvalida("o SGS não retornou observações para a janela pedida")

    pontos = [_converter(item, indice, valor_maximo) for indice, item in enumerate(bruto)]
    pontos.sort(key=lambda ponto: ponto.data)
    _rejeitar_datas_repetidas(pontos)
    if janela is not None:
        _rejeitar_fora_da_janela(pontos, janela)
    return pontos


def _converter(item: Any, indice: int, valor_maximo: float) -> Ponto:
    """Converte um item da lista JSON em :class:`Ponto`."""
    if not isinstance(item, dict):
        raise RespostaInvalida(
            f"observação {indice}: esperado objeto JSON, veio {type(item).__name__}"
        )
    data_bruta = item.get("data")
    valor_bruto = item.get("valor")
    if not isinstance(data_bruta, str) or valor_bruto is None:
        raise RespostaInvalida(
            f"observação {indice}: campos 'data' e 'valor' ausentes ou mal tipados"
        )
    return Ponto(
        data=_converter_data(data_bruta, indice),
        valor=_converter_valor(valor_bruto, indice, valor_maximo),
    )


def _converter_data(texto: str, indice: int) -> date:
    """Converte ``dd/mm/aaaa`` em :class:`datetime.date`."""
    try:
        return datetime.strptime(texto.strip(), FORMATO_DATA_API).date()
    except ValueError as erro:
        raise RespostaInvalida(
            f"observação {indice}: data fora do formato dd/mm/aaaa: {texto!r}"
        ) from erro


def _converter_valor(valor: Any, indice: int, valor_maximo: float) -> float:
    """Converte o valor para ``float``, aceitando vírgula como separador decimal."""
    # bool é subclasse de int: true/false do JSON não são valores válidos.
    if isinstance(valor, bool):
        raise RespostaInvalida(f"observação {indice}: valor booleano não é número: {valor!r}")
    if isinstance(valor, (int, float)):
        numero = float(valor)
    elif isinstance(valor, str):
        try:
            numero = float(valor.strip().replace(",", "."))
        except ValueError as erro:
            raise RespostaInvalida(f"observação {indice}: valor não numérico: {valor!r}") from erro
    else:
        raise RespostaInvalida(f"observação {indice}: valor de tipo inesperado: {valor!r}")

    if not math.isfinite(numero):
        raise RespostaInvalida(f"observação {indice}: valor não finito: {valor!r}")
    if numero <= 0 or numero > valor_maximo:
        raise RespostaInvalida(
            f"observação {indice}: valor fora do plausível para a série "
            f"(0, {valor_maximo}]: {numero}"
        )
    return numero


def _rejeitar_datas_repetidas(pontos: list[Ponto]) -> None:
    """Falha se houver duas observações na mesma data."""
    if len({ponto.data for ponto in pontos}) != len(pontos):
        raise RespostaInvalida("a resposta traz datas repetidas; os dados não são confiáveis")


def _rejeitar_fora_da_janela(pontos: list[Ponto], janela: tuple[date, date]) -> None:
    """Falha se alguma observação cair fora da janela pedida."""
    inicio, fim = janela
    for ponto in pontos:
        if not inicio <= ponto.data <= fim:
            raise RespostaInvalida(
                f"observação {ponto.data} está fora da janela pedida ({inicio} a {fim})"
            )
