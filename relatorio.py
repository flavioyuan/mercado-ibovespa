"""Resumo estatístico do conjunto alinhado e avisos de qualidade.

O resumo descreve **os valores que foram efetivamente gravados**, incluindo os
preenchidos por repetição — é com eles que o modelo econométrico vai trabalhar.
As contagens por situação (:class:`~mercado_ibovespa.alinhamento.Situacao`) ficam
ao lado justamente para que o efeito do preenchimento fique visível.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from mercado_ibovespa.alinhamento import ConjuntoAlinhado, SerieAlinhada
from mercado_ibovespa.janela import dias_corridos

# Casas decimais do resumo, para não poluir o relatório com ruído de ponto
# flutuante.
CASAS_DECIMAIS = 6

# Abaixo desta cobertura, falta dado de verdade — não é só feriado.
COBERTURA_MINIMA = 0.70

# Defasagem tolerada entre a última observação real e o fim da janela.
DEFASAGEM_MAXIMA_DIAS = 7


@dataclass(frozen=True, slots=True)
class Resumo:
    """Estatísticas descritivas de uma série alinhada."""

    nome: str
    origem: str
    total: int
    observadas: int
    repetidas: int
    retrocedidas: int
    inicio_real: date
    fim_real: date
    minimo: float
    maximo: float
    media: float
    primeiro: float
    ultimo: float

    @property
    def cobertura(self) -> float:
        """Fração da grade que veio de fato da fonte, entre 0 e 1."""
        return self.observadas / self.total if self.total else 0.0

    def linha(self) -> str:
        """Descrição de uma linha, para o log de diagnóstico."""
        return (
            f"{self.nome}: {self.total} observações "
            f"({self.observadas} publicadas, {self.repetidas} repetidas, "
            f"{self.retrocedidas} retrocedidas) | "
            f"último {self.ultimo:.4f}, média {self.media:.4f}, "
            f"faixa [{self.minimo:.4f}, {self.maximo:.4f}]"
        )

    def para_dicionario(self) -> dict[str, object]:
        """Converte o resumo em estrutura serializável em JSON."""
        return {
            "nome": self.nome,
            "origem": self.origem,
            "total": self.total,
            "observadas": self.observadas,
            "repetidas": self.repetidas,
            "retrocedidas": self.retrocedidas,
            "cobertura": round(self.cobertura, 6),
            "inicio_real": self.inicio_real.isoformat(),
            "fim_real": self.fim_real.isoformat(),
            "minimo": self.minimo,
            "maximo": self.maximo,
            "media": self.media,
            "primeiro": self.primeiro,
            "ultimo": self.ultimo,
        }


def resumir(serie: SerieAlinhada) -> Resumo:
    """Calcula as estatísticas descritivas de uma série alinhada.

    Args:
        serie: Série com um valor por data da grade.

    Returns:
        O resumo, com as estatísticas sobre todos os valores gravados.

    Raises:
        ValueError: Se a série estiver vazia.
    """
    if not serie.valores:
        raise ValueError(f"a série {serie.nome!r} está vazia")
    valores = serie.valores
    return Resumo(
        nome=serie.nome,
        origem=serie.origem,
        total=serie.total,
        observadas=serie.observadas,
        repetidas=serie.repetidas,
        retrocedidas=serie.retrocedidas,
        inicio_real=serie.inicio_real,
        fim_real=serie.fim_real,
        minimo=min(valores),
        maximo=max(valores),
        media=round(statistics.fmean(valores), CASAS_DECIMAIS),
        primeiro=valores[0],
        ultimo=valores[-1],
    )


def resumir_conjunto(conjunto: ConjuntoAlinhado) -> tuple[Resumo, ...]:
    """Resume todas as séries do conjunto, na ordem em que aparecem."""
    return tuple(resumir(serie) for serie in conjunto.series)


def gerar_avisos(
    conjunto: ConjuntoAlinhado,
    resumos: tuple[Resumo, ...],
    *,
    cobertura_minima: float = COBERTURA_MINIMA,
    defasagem_maxima_dias: int = DEFASAGEM_MAXIMA_DIAS,
) -> tuple[str, ...]:
    """Aponta problemas de qualidade que o usuário precisa saber antes de modelar.

    Args:
        conjunto: O conjunto alinhado.
        resumos: Resumos das séries, na mesma ordem de ``conjunto``.
        cobertura_minima: Cobertura abaixo da qual a série é considerada faltante.
        defasagem_maxima_dias: Defasagem tolerada entre o último dado real e o fim
            da janela.

    Returns:
        Mensagens de aviso, possivelmente vazias.
    """
    avisos: list[str] = []
    if not conjunto.grade:
        return ("o calendário comum ficou vazio",)

    fim = conjunto.grade[-1]
    for resumo in resumos:
        if resumo.cobertura < cobertura_minima:
            avisos.append(
                f"{resumo.nome}: cobertura baixa — apenas {resumo.cobertura:.1%} da grade "
                f"veio da fonte; o restante repete a última observação"
            )
        if resumo.retrocedidas:
            avisos.append(
                f"{resumo.nome}: {resumo.retrocedidas} observação(ões) no início da janela "
                f"foram retrocedidas (o papel ainda não tinha preço em "
                f"{conjunto.grade[0]}); há olhar para o futuro nesse trecho"
            )
        defasagem = (fim - resumo.fim_real).days
        if defasagem > defasagem_maxima_dias:
            avisos.append(
                f"{resumo.nome}: o último dado real é de {resumo.fim_real}, "
                f"{defasagem} dias antes do fim da janela ({fim})"
            )
    return tuple(avisos)


def montar_metadados(
    *,
    janela: tuple[date, date],
    conjunto: ConjuntoAlinhado,
    resumos: tuple[Resumo, ...],
    avisos: tuple[str, ...],
    composicao: Mapping[str, object],
    gerado_em: datetime,
) -> dict[str, object]:
    """Monta o bloco de metadados que acompanha a matriz de dados.

    O JSON é a proveniência do arquivo: sem ele, um CSV de 80 colunas não diz de
    onde veio, qual foi a janela, quanto de cada série foi preenchido por
    repetição nem quais avisos o download gerou.

    Args:
        janela: Janela pedida, ``(inicio, fim)``.
        conjunto: Conjunto alinhado que foi gravado.
        resumos: Resumos das séries, na mesma ordem do conjunto.
        avisos: Avisos de qualidade.
        composicao: Descrição de como a lista de ações foi obtida.
        gerado_em: Momento da geração.

    Returns:
        Estrutura serializável em JSON.
    """
    inicio, fim = janela
    grade = conjunto.grade
    return {
        "gerado_em": gerado_em.isoformat(timespec="seconds"),
        "janela": {
            "inicio": inicio.isoformat(),
            "fim": fim.isoformat(),
            "dias_corridos": dias_corridos(inicio, fim),
        },
        "calendario": {
            "estrategia": conjunto.estrategia.value,
            "primeira_data": grade[0].isoformat(),
            "ultima_data": grade[-1].isoformat(),
            "observacoes": conjunto.observacoes,
        },
        "composicao": dict(composicao),
        "series": [resumo.para_dicionario() for resumo in resumos],
        "avisos": list(avisos),
    }
