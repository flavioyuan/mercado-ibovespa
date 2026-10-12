"""Aritmética de janelas de datas, sem I/O e sem relógio.

Tudo aqui é determinístico: as funções recebem as datas por parâmetro, o que
permite testar bordas como 31 de março menos um mês e o limite de dez anos que o
SGS impõe às séries diárias.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

MESES_PADRAO = 24
# O SGS recusa consultas de mais de 10 anos em séries diárias. Como a Selic e o
# câmbio saem de lá, esse é o teto do programa inteiro.
MESES_MAXIMOS = 120
UM_DIA = timedelta(days=1)


class JanelaInvalida(ValueError):
    """A janela pedida é impossível ou excede o que as fontes aceitam."""


def subtrair_meses(referencia: date, meses: int) -> date:
    """Recua ``meses`` meses a partir de ``referencia``, ajustando o dia.

    Args:
        referencia: Data de partida.
        meses: Quantidade de meses a recuar (pode ser zero).

    Returns:
        A data recuada; se o dia não existir no mês de destino, usa o último dia
        daquele mês (31/03 menos um mês é 28/02, ou 29/02 em ano bissexto).

    Raises:
        JanelaInvalida: Se ``meses`` for negativo.
    """
    if meses < 0:
        raise JanelaInvalida(f"meses não pode ser negativo: {meses}")
    total = referencia.year * 12 + (referencia.month - 1) - meses
    ano, mes_zero = divmod(total, 12)
    mes = mes_zero + 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, min(referencia.day, ultimo_dia))


def dias_corridos(inicio: date, fim: date) -> int:
    """Conta dias corridos entre duas datas, inclusive nas duas pontas.

    Raises:
        JanelaInvalida: Se a data final for anterior à inicial.
    """
    if fim < inicio:
        raise JanelaInvalida(f"data final {fim} anterior à inicial {inicio}")
    return (fim - inicio).days + 1


def resolver_janela(
    *,
    fim: date,
    inicio: date | None,
    meses: int,
) -> tuple[date, date]:
    """Define a janela de consulta a partir do fim, do início ou do horizonte.

    O intervalo é um parâmetro de primeira classe: ``--meses`` cobre os horizontes
    usuais (24 meses por padrão) e ``--inicio``/``--fim`` permitem qualquer janela
    explícita, desde que caiba no limite das fontes.

    Args:
        fim: Data final da janela.
        inicio: Data inicial explícita; quando informada, ``meses`` é ignorado.
        meses: Horizonte em meses, usado quando ``inicio`` é ``None``.

    Returns:
        A tupla ``(inicio, fim)`` já validada.

    Raises:
        JanelaInvalida: Se o horizonte estiver fora de ``[1, MESES_MAXIMOS]``, se
            o início for posterior ao fim, ou se a janela exceder o máximo que o
            SGS aceita para séries diárias.
    """
    if not 1 <= meses <= MESES_MAXIMOS:
        raise JanelaInvalida(
            f"horizonte deve estar entre 1 e {MESES_MAXIMOS} meses, recebido {meses}"
        )
    comeco = inicio if inicio is not None else subtrair_meses(fim, meses)
    if comeco > fim:
        raise JanelaInvalida(f"data inicial {comeco} é posterior à data final {fim}")
    limite = subtrair_meses(fim, MESES_MAXIMOS)
    if comeco < limite:
        raise JanelaInvalida(
            f"as fontes aceitam no máximo {MESES_MAXIMOS} meses por consulta em séries "
            f"diárias; {comeco} é anterior a {limite}"
        )
    return comeco, fim
