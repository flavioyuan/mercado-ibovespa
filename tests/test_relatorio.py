"""Testes do resumo estatístico, dos metadados e dos avisos."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from mercado_ibovespa.alinhamento import Estrategia, Fonte, alinhar_conjunto
from mercado_ibovespa.modelo import Ponto
from mercado_ibovespa.relatorio import (
    gerar_avisos,
    montar_metadados,
    resumir,
    resumir_conjunto,
)

D1, D2, D3, D4 = (
    date(2026, 1, 2),
    date(2026, 1, 5),
    date(2026, 1, 6),
    date(2026, 1, 7),
)


def _pontos(*pares: tuple[date, float]) -> tuple[Ponto, ...]:
    return tuple(Ponto(dia, valor) for dia, valor in pares)


def test_resumir_calcula_estatisticas_dos_valores_gravados() -> None:
    conjunto = alinhar_conjunto(
        (
            # A segunda série cobre as quatro datas, então a união tem 4 dias.
            Fonte("IBOVESPA", "teste", _pontos((D1, 1.0), (D2, 2.0), (D3, 3.0), (D4, 4.0))),
            Fonte("A", "teste", _pontos((D1, 10.0), (D2, 20.0), (D4, 40.0))),
        )
    )
    resumo = resumir(conjunto.obter("A"))
    assert resumo.total == 4
    assert resumo.observadas == 3
    assert resumo.repetidas == 1
    assert resumo.primeiro == 10.0
    assert resumo.ultimo == 40.0
    assert resumo.minimo == 10.0
    assert resumo.maximo == 40.0
    # A média inclui o valor repetido: (10+20+20+40)/4.
    assert resumo.media == pytest.approx(22.5)
    assert resumo.cobertura == pytest.approx(0.75)
    assert resumo.inicio_real == D1
    assert resumo.fim_real == D4


def test_resumo_para_dicionario_e_serializavel() -> None:
    conjunto = alinhar_conjunto(
        (
            Fonte(
                "A",
                "teste",
                _pontos(
                    (D1, 1.0),
                ),
            ),
        )
    )
    dicionario = resumir(conjunto.obter("A")).para_dicionario()
    assert dicionario["nome"] == "A"
    assert dicionario["inicio_real"] == "2026-01-02"
    assert isinstance(dicionario["media"], float)


def test_resumir_recusa_serie_vazia() -> None:
    conjunto = alinhar_conjunto(
        (
            Fonte(
                "A",
                "teste",
                _pontos(
                    (D1, 1.0),
                ),
            ),
        )
    )
    serie = conjunto.obter("A")
    vazia = type(serie)(
        nome=serie.nome,
        origem=serie.origem,
        grade=(),
        valores=(),
        situacoes=(),
        inicio_real=serie.inicio_real,
        fim_real=serie.fim_real,
        pontos_originais=0,
    )
    with pytest.raises(ValueError):
        resumir(vazia)


def test_resumir_conjunto_preserva_a_ordem() -> None:
    conjunto = alinhar_conjunto(
        (
            Fonte(
                "A",
                "x",
                _pontos(
                    (D1, 1.0),
                ),
            ),
            Fonte(
                "B",
                "y",
                _pontos(
                    (D1, 2.0),
                ),
            ),
        )
    )
    assert [resumo.nome for resumo in resumir_conjunto(conjunto)] == ["A", "B"]


def test_gerar_avisos_sem_problemas() -> None:
    fontes = tuple(
        Fonte(nome, "teste", _pontos((D1, 1.0), (D2, 2.0), (D3, 3.0), (D4, 4.0)))
        for nome in ("IBOVESPA", "SELIC")
    )
    conjunto = alinhar_conjunto(fontes, Estrategia.UNIAO)
    assert gerar_avisos(conjunto, resumir_conjunto(conjunto)) == ()


def test_gerar_avisos_de_cobertura_baixa_e_retroacao() -> None:
    conjunto = alinhar_conjunto(
        (
            Fonte("IBOVESPA", "x", _pontos((D1, 1.0), (D2, 2.0), (D3, 3.0), (D4, 4.0))),
            # Só existe na última data: 3 retrocedidas e cobertura de 25 %.
            Fonte(
                "NOVA3",
                "y",
                _pontos(
                    (D4, 9.0),
                ),
            ),
        )
    )
    avisos = gerar_avisos(conjunto, resumir_conjunto(conjunto))
    assert any("cobertura baixa" in aviso and "NOVA3" in aviso for aviso in avisos)
    assert any("retrocedidas" in aviso and "NOVA3" in aviso for aviso in avisos)


def test_gerar_avisos_de_defasagem() -> None:
    # A janela vai até D1+10 dias, mas ANTIGA só tem dado em D1: 10 dias de
    # defasagem, acima do limite de 7.
    conjunto = alinhar_conjunto(
        (
            Fonte("IBOVESPA", "x", _pontos((D1, 1.0), (D1 + timedelta(days=10), 2.0))),
            Fonte(
                "ANTIGA",
                "y",
                _pontos(
                    (D1, 5.0),
                ),
            ),
        )
    )
    avisos = gerar_avisos(conjunto, resumir_conjunto(conjunto))
    assert any("último dado real" in aviso and "ANTIGA" in aviso for aviso in avisos)


def test_montar_metadados_descreve_a_janela_e_as_series() -> None:
    conjunto = alinhar_conjunto(
        (
            Fonte("IBOVESPA", "yahoo", _pontos((D1, 1.0), (D2, 2.0))),
            Fonte("SELIC", "bcb", _pontos((D1, 13.0), (D2, 13.5))),
        )
    )
    resumos = resumir_conjunto(conjunto)
    metadados = montar_metadados(
        janela=(D1, D2),
        conjunto=conjunto,
        resumos=resumos,
        avisos=("aviso",),
        composicao={"fonte": "teste"},
        gerado_em=datetime(2026, 1, 10, 12, 0, 0),
    )
    assert metadados["janela"] == {
        "inicio": "2026-01-02",
        "fim": "2026-01-05",
        "dias_corridos": 4,
    }
    calendario = metadados["calendario"]
    assert isinstance(calendario, dict)
    assert calendario["estrategia"] == "uniao"
    assert calendario["observacoes"] == 2
    assert isinstance(metadados["series"], list)
    assert len(metadados["series"]) == 2
    assert metadados["avisos"] == ["aviso"]
    assert metadados["gerado_em"] == "2026-01-10T12:00:00"


def test_gerar_avisos_com_grade_vazia_e_tratado() -> None:
    conjunto = alinhar_conjunto(
        (
            Fonte(
                "A",
                "x",
                _pontos(
                    (D1, 1.0),
                ),
            ),
        )
    )
    vazio = type(conjunto)(grade=(), estrategia=Estrategia.UNIAO, series=conjunto.series)
    assert gerar_avisos(vazio, ()) == ("o calendário comum ficou vazio",)
