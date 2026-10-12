"""Testes do parsing do SGS: Selic e câmbio."""

from __future__ import annotations

import json
from datetime import date

import pytest

from mercado_ibovespa import sgs
from mercado_ibovespa.sgs import RespostaInvalida, montar_url, obter_serie, parsear_observacoes

JANELA = (date(2026, 1, 1), date(2026, 1, 31))
MAXIMO_SELIC = sgs.CATALOGO["selic"].valor_maximo


def test_catalogo_tem_selic_e_cambio() -> None:
    assert sgs.CATALOGO["selic"].codigo == 1178
    assert sgs.CATALOGO["cambio"].codigo == 1


def test_obter_serie_ignora_caixa_e_espacos() -> None:
    assert obter_serie("  SELIC ").codigo == 1178


def test_obter_serie_desconhecida_falha() -> None:
    with pytest.raises(KeyError):
        obter_serie("dolar-paralelo")


def test_montar_url_usa_datas_no_formato_da_api() -> None:
    url = montar_url(1178, date(2026, 1, 5), date(2026, 2, 4))
    assert "bcdata.sgs.1178" in url
    assert "dataInicial=05/01/2026" in url
    assert "dataFinal=04/02/2026" in url
    assert "formato=json" in url


def test_parsear_observacoes_converte_e_ordena() -> None:
    payload = json.dumps(
        [
            {"data": "05/01/2026", "valor": "13.65"},
            {"data": "02/01/2026", "valor": "13.65"},
            {"data": "06/01/2026", "valor": "13.90"},
        ]
    )
    pontos = parsear_observacoes(payload, janela=JANELA, valor_maximo=MAXIMO_SELIC)
    assert [ponto.data for ponto in pontos] == [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]
    assert pontos[2].valor == pytest.approx(13.90)


def test_parsear_observacoes_aceita_virgula_decimal_e_numero() -> None:
    payload = json.dumps(
        [{"data": "02/01/2026", "valor": "5,4321"}, {"data": "05/01/2026", "valor": 5.5}]
    )
    pontos = parsear_observacoes(payload, valor_maximo=100.0)
    assert [ponto.valor for ponto in pontos] == pytest.approx([5.4321, 5.5])


@pytest.mark.parametrize(
    "payload",
    [
        "{nao json",
        "{}",
        "[]",
        json.dumps([{"data": "02/01/2026"}]),
        json.dumps([{"valor": "1.0"}]),
        json.dumps([{"data": "2026-01-02", "valor": "1.0"}]),
        json.dumps([{"data": "32/01/2026", "valor": "1.0"}]),
        json.dumps([{"data": "02/01/2026", "valor": "abc"}]),
        json.dumps([{"data": "02/01/2026", "valor": True}]),
        json.dumps([{"data": "02/01/2026", "valor": None}]),
        json.dumps([{"data": "02/01/2026", "valor": [1]}]),
        json.dumps([{"data": "02/01/2026", "valor": 0}]),
        json.dumps([{"data": "02/01/2026", "valor": -1.5}]),
        json.dumps([{"data": "02/01/2026", "valor": 5000}]),
        json.dumps(["nao e objeto"]),
        json.dumps(
            [{"data": "02/01/2026", "valor": "1.0"}, {"data": "02/01/2026", "valor": "2.0"}]
        ),
    ],
)
def test_parsear_observacoes_recusa_payload_invalido(payload: str) -> None:
    with pytest.raises(RespostaInvalida):
        parsear_observacoes(payload, valor_maximo=MAXIMO_SELIC)


def test_parsear_observacoes_recusa_valor_nao_finito() -> None:
    payload = json.dumps([{"data": "02/01/2026", "valor": "Infinity"}])
    with pytest.raises(RespostaInvalida):
        parsear_observacoes(payload, valor_maximo=MAXIMO_SELIC)


def test_parsear_observacoes_recusa_observacao_fora_da_janela() -> None:
    payload = json.dumps([{"data": "01/03/2026", "valor": "13.65"}])
    with pytest.raises(RespostaInvalida, match="fora da janela"):
        parsear_observacoes(payload, janela=JANELA, valor_maximo=MAXIMO_SELIC)


def test_parsear_observacoes_sem_janela_aceita_qualquer_data() -> None:
    payload = json.dumps([{"data": "01/03/2026", "valor": "13.65"}])
    assert len(parsear_observacoes(payload, valor_maximo=MAXIMO_SELIC)) == 1


def test_valor_maximo_e_especifico_da_serie() -> None:
    # 500 % a.a. é implausível para o câmbio, ainda que plausível para a Selic.
    payload = json.dumps([{"data": "02/01/2026", "valor": "500"}])
    assert len(parsear_observacoes(payload, valor_maximo=MAXIMO_SELIC)) == 1
    with pytest.raises(RespostaInvalida):
        parsear_observacoes(payload, valor_maximo=100.0)
