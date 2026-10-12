"""Testes da aritmética de janelas: determinísticos e sem I/O."""

from __future__ import annotations

from datetime import date

import pytest

from mercado_ibovespa.janela import (
    MESES_MAXIMOS,
    JanelaInvalida,
    dias_corridos,
    resolver_janela,
    subtrair_meses,
)


@pytest.mark.parametrize(
    ("referencia", "meses", "esperado"),
    [
        (date(2026, 10, 6), 24, date(2024, 10, 6)),
        (date(2026, 10, 6), 0, date(2026, 10, 6)),
        (date(2026, 1, 31), 1, date(2025, 12, 31)),
        (date(2026, 3, 31), 1, date(2026, 2, 28)),
        (date(2024, 3, 31), 1, date(2024, 2, 29)),
        (date(2026, 1, 15), 13, date(2024, 12, 15)),
    ],
)
def test_subtrair_meses(referencia: date, meses: int, esperado: date) -> None:
    assert subtrair_meses(referencia, meses) == esperado


def test_subtrair_meses_recusa_valor_negativo() -> None:
    with pytest.raises(JanelaInvalida):
        subtrair_meses(date(2026, 1, 1), -1)


def test_dias_corridos_inclui_as_pontas() -> None:
    assert dias_corridos(date(2026, 1, 1), date(2026, 1, 1)) == 1
    assert dias_corridos(date(2026, 1, 1), date(2026, 1, 31)) == 31


def test_dias_corridos_recusa_ordem_invertida() -> None:
    with pytest.raises(JanelaInvalida):
        dias_corridos(date(2026, 2, 1), date(2026, 1, 1))


def test_resolver_janela_usa_horizonte_em_meses() -> None:
    inicio, fim = resolver_janela(fim=date(2026, 10, 6), inicio=None, meses=24)
    assert (inicio, fim) == (date(2024, 10, 6), date(2026, 10, 6))


def test_resolver_janela_inicio_explicito_tem_prioridade() -> None:
    inicio, fim = resolver_janela(fim=date(2026, 10, 6), inicio=date(2025, 3, 1), meses=24)
    assert (inicio, fim) == (date(2025, 3, 1), date(2026, 10, 6))


@pytest.mark.parametrize("meses", [0, -3, MESES_MAXIMOS + 1])
def test_resolver_janela_recusa_horizonte_fora_da_faixa(meses: int) -> None:
    with pytest.raises(JanelaInvalida):
        resolver_janela(fim=date(2026, 10, 6), inicio=None, meses=meses)


def test_resolver_janela_recusa_inicio_posterior_ao_fim() -> None:
    with pytest.raises(JanelaInvalida):
        resolver_janela(fim=date(2026, 1, 1), inicio=date(2026, 6, 1), meses=24)


def test_resolver_janela_respeita_limite_de_dez_anos() -> None:
    with pytest.raises(JanelaInvalida):
        resolver_janela(fim=date(2026, 10, 6), inicio=date(2000, 1, 1), meses=24)
