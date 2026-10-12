"""Teste de integração: baixa de verdade das três fontes.

Excluído da execução padrão (``-m 'not integracao'``) porque depende de rede.
Rode com::

    uv run pytest -m integracao -v
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from mercado_ibovespa import composicao, rede, sgs, yahoo

pytestmark = pytest.mark.integracao

FIM = date.today()
INICIO = FIM - timedelta(days=30)
JANELA = (INICIO, FIM)
TEMPO_LIMITE = 30.0


def _baixar(url: str) -> str:
    return rede.baixar_com_tentativas(url, tentativas=2, timeout=TEMPO_LIMITE)


def test_selic_do_sgs_tem_valores_plausiveis() -> None:
    serie = sgs.obter_serie("selic")
    payload = _baixar(sgs.montar_url(serie.codigo, *JANELA))
    pontos = sgs.parsear_observacoes(payload, janela=JANELA, valor_maximo=serie.valor_maximo)
    assert len(pontos) > 10
    assert all(1.0 < ponto.valor < 40.0 for ponto in pontos)


def test_cambio_do_sgs_tem_valores_plausiveis() -> None:
    serie = sgs.obter_serie("cambio")
    payload = _baixar(sgs.montar_url(serie.codigo, *JANELA))
    pontos = sgs.parsear_observacoes(payload, janela=JANELA, valor_maximo=serie.valor_maximo)
    assert len(pontos) > 10
    assert all(1.0 < ponto.valor < 20.0 for ponto in pontos)


@pytest.mark.parametrize("simbolo", ["PETR4.SA", yahoo.SIMBOLO_INDICE])
def test_yahoo_devolve_barras_para_acao_e_indice(simbolo: str) -> None:
    payload = _baixar(yahoo.montar_url(simbolo, *JANELA))
    pontos = yahoo.parsear_serie(payload, campo=yahoo.CAMPO_AJUSTADO, janela=JANELA)
    assert len(pontos) > 10
    assert all(ponto.valor > 0 for ponto in pontos)
    datas = [ponto.data for ponto in pontos]
    assert datas == sorted(datas)
    assert len(datas) == len(set(datas))


def test_composicao_da_b3_tem_acoes_da_bolsa() -> None:
    payload = _baixar(composicao.montar_url_b3())
    codigos = composicao.parsear_composicao_b3(payload)
    assert len(codigos) >= 50
    assert "PETR4" in codigos or "VALE3" in codigos
    assert all(composicao.PADRAO_CODIGO.match(codigo) for codigo in codigos)
