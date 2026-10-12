"""Fixtures compartilhadas: payloads fixos e um downloader falso.

A suíte padrão roda **offline**: nenhum teste toca a rede. Os payloads abaixo são
recortes no formato real das três APIs, e o :class:`DownloaderFalso` substitui o
``rede.baixar`` nos testes de CLI.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, time
from urllib.parse import unquote

import pytest

from mercado_ibovespa.rede import SemDados

# Janela curta e fixa: seis dias úteis em janeiro de 2026.
INICIO = date(2026, 1, 2)
FIM = date(2026, 1, 9)
DIAS = (
    date(2026, 1, 2),
    date(2026, 1, 5),
    date(2026, 1, 6),
    date(2026, 1, 7),
    date(2026, 1, 8),
    date(2026, 1, 9),
)

# Abertura do pregão da B3 (10:00 em America/Sao_Paulo) expressa em UTC.
HORA_BARRA = time(13, 0)

COMPOSICAO_TESTE = ("PETR4", "VALE3", "B3SA3")

# Valores plausíveis por código do SGS, dentro do limite de cada série.
VALORES_SGS = {1178: 13.65, 1: 5.20}

ARGUMENTOS_JANELA = ["--inicio", INICIO.isoformat(), "--fim", FIM.isoformat()]


def payload_b3(codigos: tuple[str, ...] = COMPOSICAO_TESTE) -> str:
    """Resposta mínima do ``GetPortfolioDay`` da B3."""
    return json.dumps(
        {"results": [{"cod": codigo, "asset": f"EMPRESA {codigo}"} for codigo in codigos]},
        ensure_ascii=False,
    )


def payload_yahoo(
    datas: tuple[date, ...],
    valores: list[float],
    *,
    tz_meta: str | None = "America/Sao_Paulo",
) -> str:
    """Resposta mínima da API de gráfico do Yahoo, com ``close`` e ``adjclose``."""
    carimbos = [int(datetime.combine(dia, HORA_BARRA, tzinfo=UTC).timestamp()) for dia in datas]
    meta: dict[str, object] = {"currency": "BRL", "symbol": "TESTE.SA"}
    if tz_meta is not None:
        meta["exchangeTimezoneName"] = tz_meta
    return json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "meta": meta,
                        "timestamp": carimbos,
                        "indicators": {
                            "quote": [{"close": list(valores)}],
                            "adjclose": [{"adjclose": list(valores)}],
                        },
                    }
                ],
                "error": None,
            }
        }
    )


def payload_sgs(pares: list[tuple[date, float]]) -> str:
    """Resposta mínima do SGS: lista de ``{"data", "valor"}``."""
    return json.dumps(
        [{"data": dia.strftime("%d/%m/%Y"), "valor": f"{valor:.6f}"} for dia, valor in pares]
    )


def valores_de(simbolo: str, quantidade: int) -> list[float]:
    """Série sintética determinística e positiva para um símbolo."""
    base = 10.0 + sum(ord(caractere) for caractere in simbolo) % 50
    return [round(base + indice, 4) for indice in range(quantidade)]


class DownloaderFalso:
    """Downloader determinístico que devolve payloads fixos e registra as URLs."""

    def __init__(
        self,
        *,
        ausentes: tuple[str, ...] = (),
        dias_mercado: tuple[date, ...] = DIAS,
        dias_sgs: tuple[date, ...] = DIAS,
        composicao_erro: bool = False,
        mercado_erro: bool = False,
    ) -> None:
        self.urls: list[str] = []
        self.ausentes = set(ausentes)
        self.dias_mercado = dias_mercado
        self.dias_sgs = dias_sgs
        self.composicao_erro = composicao_erro
        self.mercado_erro = mercado_erro

    def __call__(self, url: str, *, timeout: float) -> str:
        self.urls.append(url)
        if "listados.b3.com.br" in url:
            if self.composicao_erro:
                raise SemDados("B3 indisponível (simulado)")
            return payload_b3()
        if "finance.yahoo.com" in url:
            if self.mercado_erro:
                raise SemDados("Yahoo indisponível (simulado)")
            simbolo = unquote(url.split("/chart/", 1)[1].split("?", 1)[0])
            if simbolo in self.ausentes:
                raise SemDados(f"{simbolo} sem dados (simulado)")
            return payload_yahoo(self.dias_mercado, valores_de(simbolo, len(self.dias_mercado)))
        if "api.bcb.gov.br" in url:
            encontrado = re.search(r"bcdata\.sgs\.(\d+)", url)
            if encontrado is None:
                raise AssertionError(f"URL do SGS sem código de série: {url}")
            codigo = int(encontrado.group(1))
            base = VALORES_SGS[codigo]
            return payload_sgs(
                [(dia, base + indice / 100) for indice, dia in enumerate(self.dias_sgs)]
            )
        raise AssertionError(f"URL inesperada no downloader falso: {url}")


@pytest.fixture
def downloader_falso() -> DownloaderFalso:
    """Um :class:`DownloaderFalso` com a composição de teste."""
    return DownloaderFalso()
