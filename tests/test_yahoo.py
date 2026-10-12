"""Testes do parsing da API de gráfico do Yahoo Finance."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time

import pytest

from mercado_ibovespa import yahoo
from mercado_ibovespa.composicao import CodigoInvalido
from mercado_ibovespa.yahoo import RespostaInvalida, montar_url, parsear_serie, simbolo_de_codigo

from .conftest import DIAS, payload_yahoo, valores_de

JANELA = (DIAS[0], DIAS[-1])


def test_simbolo_de_codigo_usa_sufixo_da_b3() -> None:
    assert simbolo_de_codigo("PETR4") == "PETR4.SA"
    assert simbolo_de_codigo("petr4.sa") == "PETR4.SA"
    assert simbolo_de_codigo("B3SA3") == "B3SA3.SA"


def test_simbolo_de_codigo_rejeita_codigo_invalido() -> None:
    with pytest.raises(CodigoInvalido):
        simbolo_de_codigo("NAO-E-CODIGO")


def test_montar_url_cobre_a_data_final() -> None:
    url = montar_url("PETR4.SA", date(2026, 1, 2), date(2026, 1, 9))
    assert "interval=1d" in url
    periodo1 = int(url.split("period1=")[1].split("&")[0])
    periodo2 = int(url.split("period2=")[1].split("&")[0])
    assert datetime.fromtimestamp(periodo1, tz=UTC).date() == date(2026, 1, 2)
    # O fim é estendido em um dia para que a barra da data final entre.
    assert datetime.fromtimestamp(periodo2, tz=UTC).date() == date(2026, 1, 10)


def test_montar_url_escapa_o_simbolo_do_indice() -> None:
    url = montar_url(yahoo.SIMBOLO_INDICE, date(2026, 1, 2), date(2026, 1, 9))
    assert "%5EBVSP" in url


def test_parsear_serie_com_adjclose() -> None:
    valores = valores_de("PETR4.SA", len(DIAS))
    pontos = parsear_serie(payload_yahoo(DIAS, valores), janela=JANELA)
    assert [ponto.data for ponto in pontos] == list(DIAS)
    assert [ponto.valor for ponto in pontos] == pytest.approx(valores)


def test_parsear_serie_aceita_campo_close() -> None:
    valores = valores_de("PETR4.SA", len(DIAS))
    pontos = parsear_serie(payload_yahoo(DIAS, valores), campo="close", janela=JANELA)
    assert len(pontos) == len(DIAS)


def test_parsear_serie_recusa_campo_desconhecido() -> None:
    with pytest.raises(RespostaInvalida, match="campo inválido"):
        parsear_serie(payload_yahoo(DIAS, valores_de("X", len(DIAS))), campo="volume")


def test_parsear_serie_pula_barras_nulas() -> None:
    valores = valores_de("PETR4.SA", len(DIAS))
    valores[2] = None  # type: ignore[call-overload]
    pontos = parsear_serie(payload_yahoo(DIAS, valores), janela=JANELA)
    assert len(pontos) == len(DIAS) - 1
    assert DIAS[2] not in [ponto.data for ponto in pontos]


def test_parsear_serie_usa_o_fuso_da_bolsa() -> None:
    # Os carimbos do payload são 13:00 UTC; com o fuso da B3 a data é a mesma;
    # com UTC também, mas o campo de fuso precisa ser lido sem quebrar.
    valores = valores_de("PETR4.SA", len(DIAS))
    pontos = parsear_serie(payload_yahoo(DIAS, valores, tz_meta=None), janela=JANELA)
    assert [ponto.data for ponto in pontos] == list(DIAS)


def test_parsear_serie_recusa_fuso_desconhecido_sem_quebrar() -> None:
    valores = valores_de("PETR4.SA", len(DIAS))
    payload = payload_yahoo(DIAS, valores, tz_meta="Marte/Olympus")
    pontos = parsear_serie(payload, janela=JANELA)
    assert [ponto.data for ponto in pontos] == list(DIAS)


def test_parsear_serie_colapsa_cotacao_ao_vivo_do_mesmo_dia() -> None:
    # O Yahoo devolve a barra do pregão e mais uma cotação ao vivo carimbada com
    # o horário corrente, que cai na mesma data da barra.
    valores = valores_de("^BVSP", len(DIAS))
    payload = json.loads(payload_yahoo(DIAS, valores))
    resultado = payload["chart"]["result"][0]
    resultado["timestamp"].append(
        int(datetime.combine(DIAS[-1], time(23, 30), tzinfo=UTC).timestamp())
    )
    for bloco in ("quote", "adjclose"):
        chave = "close" if bloco == "quote" else "adjclose"
        resultado["indicators"][bloco][0][chave].append(9999.0)

    pontos = parsear_serie(json.dumps(payload), janela=JANELA)
    assert len(pontos) == len(DIAS)
    # Fica a de carimbo mais recente.
    assert pontos[-1].data == DIAS[-1]
    assert pontos[-1].valor == pytest.approx(9999.0)


@pytest.mark.parametrize(
    "payload",
    [
        "{nao json",
        "[]",
        json.dumps({"chart": {"result": [], "error": None}}),
        json.dumps({"chart": {"result": None, "error": None}}),
        json.dumps({"chart": {"result": [{"timestamp": []}], "error": None}}),
        json.dumps({"chart": {"result": [{}], "error": None}}),
        json.dumps({"chart": None}),
    ],
)
def test_parsear_serie_recusa_payload_malformado(payload: str) -> None:
    with pytest.raises(RespostaInvalida):
        parsear_serie(payload, janela=JANELA)


def test_parsear_serie_propaga_erro_do_yahoo() -> None:
    payload = json.dumps(
        {
            "chart": {
                "result": None,
                "error": {
                    "code": "Not Found",
                    "description": "No data found, symbol may be delisted",
                },
            }
        }
    )
    with pytest.raises(RespostaInvalida, match="delisted"):
        parsear_serie(payload, janela=JANELA)


def test_parsear_serie_recusa_adjclose_ausente() -> None:
    payload = json.loads(payload_yahoo(DIAS, valores_de("X", len(DIAS))))
    del payload["chart"]["result"][0]["indicators"]["adjclose"]
    with pytest.raises(RespostaInvalida, match="adjclose"):
        parsear_serie(json.dumps(payload))


def test_parsear_serie_recusa_tamanhos_incompativeis() -> None:
    payload = json.loads(payload_yahoo(DIAS, valores_de("X", len(DIAS))))
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"].append(1.0)
    with pytest.raises(RespostaInvalida, match="datas e"):
        parsear_serie(json.dumps(payload), campo="close")


@pytest.mark.parametrize("valor", [0, -1.0])
def test_parsear_serie_recusa_preco_nao_positivo(valor: float) -> None:
    valores = valores_de("X", len(DIAS))
    valores[0] = valor
    with pytest.raises(RespostaInvalida, match="não positivo"):
        parsear_serie(payload_yahoo(DIAS, valores), janela=JANELA)


def test_parsear_serie_recusa_observacao_fora_da_janela() -> None:
    valores = valores_de("X", len(DIAS))
    with pytest.raises(RespostaInvalida, match="fora da janela"):
        parsear_serie(payload_yahoo(DIAS, valores), janela=(DIAS[2], DIAS[-1]))
