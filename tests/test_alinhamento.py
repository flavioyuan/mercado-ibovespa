"""Testes do alinhamento: o requisito de "mesmo número de observações"."""

from __future__ import annotations

from datetime import date

import pytest

from mercado_ibovespa.alinhamento import (
    Estrategia,
    Fonte,
    GradeInvalida,
    Situacao,
    alinhar,
    alinhar_conjunto,
    montar_grade,
)
from mercado_ibovespa.modelo import Ponto

D1, D2, D3, D4, D5 = (
    date(2026, 1, 2),
    date(2026, 1, 5),
    date(2026, 1, 6),
    date(2026, 1, 7),
    date(2026, 1, 8),
)


def _pontos(*pares: tuple[date, float]) -> tuple[Ponto, ...]:
    return tuple(Ponto(dia, valor) for dia, valor in pares)


def test_montar_grade_uniao_preserva_todas_as_datas() -> None:
    grade = montar_grade(
        {"A": _pontos((D1, 1.0), (D2, 2.0)), "B": _pontos((D3, 3.0))},
        Estrategia.UNIAO,
    )
    assert grade == (D1, D2, D3)


def test_montar_grade_interseccao_so_datas_comuns() -> None:
    grade = montar_grade(
        {"A": _pontos((D1, 1.0), (D2, 2.0), (D3, 3.0)), "B": _pontos((D2, 9.0), (D3, 9.0))},
        Estrategia.INTERSECCAO,
    )
    assert grade == (D2, D3)


def test_montar_grade_indice_usa_a_serie_de_referencia() -> None:
    grade = montar_grade(
        {"IBOVESPA": _pontos((D1, 1.0), (D3, 3.0)), "A": _pontos((D2, 2.0))},
        Estrategia.INDICE,
        referencia="IBOVESPA",
    )
    assert grade == (D1, D3)


def test_montar_grade_indice_exige_referencia_existente() -> None:
    with pytest.raises(GradeInvalida):
        montar_grade({"A": _pontos((D1, 1.0))}, Estrategia.INDICE, referencia="IBOVESPA")


def test_montar_grade_recusa_serie_sem_observacao() -> None:
    with pytest.raises(GradeInvalida, match="sem nenhuma observação"):
        montar_grade({"A": _pontos((D1, 1.0)), "B": ()}, Estrategia.UNIAO)


def test_montar_grade_recusa_interseccao_vazia() -> None:
    with pytest.raises(GradeInvalida, match="nenhuma data em comum"):
        montar_grade({"A": _pontos((D1, 1.0)), "B": _pontos((D2, 2.0))}, Estrategia.INTERSECCAO)


def test_montar_grade_recusa_conjunto_vazio() -> None:
    with pytest.raises(GradeInvalida):
        montar_grade({}, Estrategia.UNIAO)


def test_alinhar_repete_ultimo_valor_nas_lacunas() -> None:
    serie = alinhar("A", "teste", (D1, D2, D3), _pontos((D1, 10.0), (D3, 30.0)))
    assert serie.valores == (10.0, 10.0, 30.0)
    assert serie.situacoes == (Situacao.OBSERVADO, Situacao.REPETIDO, Situacao.OBSERVADO)
    assert (serie.observadas, serie.repetidas, serie.retrocedidas) == (2, 1, 0)


def test_alinhar_repete_o_ultimo_valor_ate_o_fim_da_grade() -> None:
    serie = alinhar("A", "teste", (D1, D2, D3, D4), _pontos((D1, 10.0), (D2, 20.0)))
    assert serie.valores == (10.0, 20.0, 20.0, 20.0)
    assert serie.repetidas == 2


def test_alinhar_retrocede_a_lacuna_inicial() -> None:
    # Série que só começa em D3: as duas datas anteriores não têm valor para
    # repetir, então repetem o primeiro — e ficam marcadas como retrocedidas.
    serie = alinhar("A", "teste", (D1, D2, D3, D4), _pontos((D3, 30.0), (D4, 40.0)))
    assert serie.valores == (30.0, 30.0, 30.0, 40.0)
    assert serie.situacoes[:2] == (Situacao.RETROCIDIDO, Situacao.RETROCIDIDO)
    assert (serie.observadas, serie.repetidas, serie.retrocedidas) == (2, 0, 2)
    assert serie.inicio_real == D3


def test_alinhar_considera_pontos_fora_da_grade_como_ultimo_valor() -> None:
    # D2 não está na grade (interseção, por exemplo), mas o valor dela continua
    # valendo como último conhecido para D3.
    serie = alinhar("A", "teste", (D1, D3), _pontos((D1, 10.0), (D2, 20.0), (D3, 30.0)))
    assert serie.valores == (10.0, 30.0)
    assert serie.observadas == 2
    assert serie.repetidas == 0


def test_alinhar_marca_ponto_fora_da_grade_como_repetido() -> None:
    serie = alinhar("A", "teste", (D1, D3), _pontos((D1, 10.0), (D2, 20.0)))
    assert serie.valores == (10.0, 20.0)
    assert serie.situacoes == (Situacao.OBSERVADO, Situacao.REPETIDO)


def test_alinhar_recusa_grade_vazia() -> None:
    with pytest.raises(GradeInvalida):
        alinhar("A", "teste", (), _pontos((D1, 1.0)))


def test_alinhar_recusa_serie_vazia() -> None:
    with pytest.raises(GradeInvalida):
        alinhar("A", "teste", (D1,), ())


def test_alinhar_recusa_datas_repetidas() -> None:
    with pytest.raises(GradeInvalida, match="datas repetidas"):
        alinhar("A", "teste", (D1, D2), _pontos((D1, 1.0), (D1, 2.0)))


def test_alinhar_conjunto_da_mesmo_tamanho_a_todas_as_series() -> None:
    fontes = (
        Fonte("IBOVESPA", "yahoo", _pontos((D1, 100.0), (D2, 101.0), (D3, 102.0))),
        Fonte("A", "yahoo", _pontos((D1, 10.0), (D3, 12.0))),
        # Série que só existe do meio para o fim da janela.
        Fonte(
            "B",
            "yahoo",
            _pontos(
                (D3, 5.0),
            ),
        ),
        Fonte("SELIC", "bcb", _pontos((D1, 13.0), (D2, 13.0), (D3, 13.5), (D4, 13.5))),
    )
    conjunto = alinhar_conjunto(fontes, Estrategia.UNIAO)

    assert conjunto.observacoes == 4
    assert all(len(serie.valores) == conjunto.observacoes for serie in conjunto.series)
    assert conjunto.obter("B").retrocedidas == 2
    assert conjunto.obter("SELIC").valores == (13.0, 13.0, 13.5, 13.5)


def test_alinhar_conjunto_recusa_nomes_repetidos() -> None:
    fontes = (
        Fonte("A", "yahoo", _pontos((D1, 1.0))),
        Fonte("A", "bcb", _pontos((D1, 2.0))),
    )
    with pytest.raises(GradeInvalida, match="repetidos"):
        alinhar_conjunto(fontes)


def test_alinhar_conjunto_recusa_lista_vazia() -> None:
    with pytest.raises(GradeInvalida):
        alinhar_conjunto(())


def test_cobertura_e_a_fracao_observada() -> None:
    serie = alinhar("A", "teste", (D1, D2, D3, D4), _pontos((D1, 1.0), (D2, 2.0)))
    assert serie.cobertura == pytest.approx(0.5)


def test_obter_serie_desconhecida_falha() -> None:
    conjunto = alinhar_conjunto((Fonte("A", "x", _pontos((D1, 1.0))),))
    with pytest.raises(KeyError):
        conjunto.obter("B")


def test_grade_de_dias_uteis_nao_e_inventada() -> None:
    # A união das datas observadas é o calendário real: um feriado no meio da
    # semana não vira linha.
    grade = montar_grade(
        {"A": _pontos((D1, 1.0), (D5, 2.0))},
        Estrategia.UNIAO,
    )
    assert grade == (D1, D5)
