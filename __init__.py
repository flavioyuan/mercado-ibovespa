"""Download e alinhamento das séries do Ibovespa, da Selic e do câmbio.

O pacote baixa, numa janela parametrizável, as séries diárias de:

- todas as ações que compõem o Ibovespa, mais o índice (``^BVSP``) — Yahoo Finance;
- a taxa Selic efetiva anualizada — SGS/BCB, série 1178;
- o dólar comercial de venda (BRL/USD) — SGS/BCB, série 1.

Todas as séries saem alinhadas num calendário comum, com **exatamente o mesmo
número de observações**, repetindo a última observação nas lacunas. O ponto de
entrada é :func:`mercado_ibovespa.cli.main`.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
