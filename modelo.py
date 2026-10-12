"""Modelo de dados compartilhado pelas fontes.

Uma série baixada é sempre uma sequência de :class:`Ponto` — uma data e um valor
— independentemente de a fonte ser o Yahoo Finance (ações e índice) ou o SGS do
Banco Central (Selic e câmbio). Manter um tipo único é o que permite alinhar
todas as séries no mesmo calendário sem que o módulo de alinhamento conheça
detalhe algum das fontes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Ponto:
    """Uma observação de uma série: a data e o valor publicado naquela data."""

    data: date
    valor: float
