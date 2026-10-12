"""Alinhamento de todas as séries no mesmo calendário.

É aqui que mora o requisito central do estudo: **todas as séries ficam com o mesmo
número de observações**. O caminho é inverter o problema — em vez de tentar fazer
cada fonte publicar nos mesmos dias, escolhe-se um calendário comum e reindexa-se
todo mundo nele:

1. :func:`montar_grade` produz a lista de datas do calendário comum.
2. :func:`alinhar` devolve, para cada série, exatamente um valor por data da
   grade — o publicado, quando existe, ou a **última observação** repetida
   (*last observation carried forward*).

A grade padrão é a **união** das datas observadas em todas as séries. Para dados
da B3 isso é melhor do que uma grade de segunda a sexta: como toda ação só é
negociada em pregão, a união das datas é o calendário real de negociação, sem
inventar linhas em feriado. As alternativas são a **interseção** (só datas comuns
a todas as séries) e o **índice** (as datas do próprio Ibovespa).

Três situações marcam cada valor da saída:

==============  ==========================================================
``observado``   o valor foi publicado naquela data
``repetido``    lacuna preenchida com o último valor observado
``retrocidido`` lacuna no início da série, antes da primeira observação:
                repetiu-se o **primeiro** valor, o que é uma extrapolação
                para trás e olha o futuro
==============  ==========================================================

O marcador ``retrocidido`` existe para que esse caso — que é o de uma ação que
entrou no índice no meio da janela — não passe despercebido: ele cria
previsibilidade artificial nos primeiros dias da série e deve ser filtrado por
quem for estimar retornos.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from mercado_ibovespa.modelo import Ponto


class GradeInvalida(ValueError):
    """Não foi possível montar ou preencher o calendário comum."""


class Estrategia(StrEnum):
    """Como o calendário comum é escolhido."""

    UNIAO = "uniao"
    INTERSECCAO = "interseccao"
    INDICE = "indice"


class Situacao(StrEnum):
    """A origem de um valor dentro da grade."""

    OBSERVADO = "observado"
    REPETIDO = "repetido"
    RETROCIDIDO = "retrocidido"


@dataclass(frozen=True, slots=True)
class Fonte:
    """Uma série baixada, antes do alinhamento."""

    nome: str
    origem: str
    pontos: tuple[Ponto, ...]


@dataclass(frozen=True, slots=True)
class SerieAlinhada:
    """Uma série com exatamente um valor por data da grade."""

    nome: str
    origem: str
    grade: tuple[date, ...]
    valores: tuple[float, ...]
    situacoes: tuple[Situacao, ...]
    inicio_real: date
    fim_real: date
    pontos_originais: int

    @property
    def total(self) -> int:
        """Número de observações da série — sempre o tamanho da grade."""
        return len(self.valores)

    @property
    def observadas(self) -> int:
        """Quantos valores vieram de publicação da fonte."""
        return sum(1 for situacao in self.situacoes if situacao is Situacao.OBSERVADO)

    @property
    def repetidas(self) -> int:
        """Quantas lacunas foram preenchidas repetindo o último valor."""
        return sum(1 for situacao in self.situacoes if situacao is Situacao.REPETIDO)

    @property
    def retrocedidas(self) -> int:
        """Quantas lacunas iniciais foram preenchidas com o primeiro valor."""
        return sum(1 for situacao in self.situacoes if situacao is Situacao.RETROCIDIDO)

    @property
    def cobertura(self) -> float:
        """Fração da grade que veio de fato da fonte, entre 0 e 1."""
        return self.observadas / self.total if self.total else 0.0


@dataclass(frozen=True, slots=True)
class ConjuntoAlinhado:
    """Todas as séries no mesmo calendário, com a mesma contagem de observações."""

    grade: tuple[date, ...]
    estrategia: Estrategia
    series: tuple[SerieAlinhada, ...]

    @property
    def observacoes(self) -> int:
        """Número de observações por série — o mesmo para todas."""
        return len(self.grade)

    @property
    def nomes(self) -> tuple[str, ...]:
        """Nomes das séries, na ordem de entrada."""
        return tuple(serie.nome for serie in self.series)

    def obter(self, nome: str) -> SerieAlinhada:
        """Busca uma série alinhada pelo nome.

        Raises:
            KeyError: Se o nome não estiver no conjunto.
        """
        for serie in self.series:
            if serie.nome == nome:
                return serie
        raise KeyError(nome)


def montar_grade(
    pontos_por_serie: Mapping[str, Sequence[Ponto]],
    estrategia: Estrategia,
    *,
    referencia: str | None = None,
) -> tuple[date, ...]:
    """Monta o calendário comum a partir das datas observadas nas séries.

    Args:
        pontos_por_serie: Séries baixadas, por nome.
        estrategia: ``uniao``, ``interseccao`` ou ``indice``.
        referencia: Nome da série que define o calendário quando a estratégia é
            ``indice`` (na prática, ``IBOVESPA``).

    Returns:
        As datas do calendário, em ordem cronológica.

    Raises:
        GradeInvalida: Se não houver séries, se alguma não tiver observação na
            janela, se a interseção for vazia, ou se a estratégia ``indice`` for
            usada sem uma referência existente.
    """
    if not pontos_por_serie:
        raise GradeInvalida("não há séries para montar o calendário comum")

    conjuntos = {
        nome: {ponto.data for ponto in pontos} for nome, pontos in pontos_por_serie.items()
    }
    vazias = sorted(nome for nome, datas in conjuntos.items() if not datas)
    if vazias:
        raise GradeInvalida("séries sem nenhuma observação na janela: " + ", ".join(vazias))

    if estrategia is Estrategia.UNIAO:
        grade: set[date] = set()
        for datas in conjuntos.values():
            grade |= datas
    elif estrategia is Estrategia.INTERSECCAO:
        iterador = iter(conjuntos.values())
        grade = set(next(iterador))
        for datas in iterador:
            grade &= datas
        if not grade:
            raise GradeInvalida(
                "as séries não têm nenhuma data em comum na janela; "
                "use --calendario uniao para preservar todas as observações"
            )
    else:
        if referencia is None or referencia not in conjuntos:
            raise GradeInvalida(
                f"o calendário do índice precisa da série {referencia!r} entre as baixadas"
            )
        grade = set(conjuntos[referencia])

    return tuple(sorted(grade))


def alinhar(
    nome: str,
    origem: str,
    grade: Sequence[date],
    pontos: Sequence[Ponto],
) -> SerieAlinhada:
    """Reindexa uma série na grade, repetindo a última observação nas lacunas.

    Args:
        nome: Nome da série na saída.
        origem: Identificação da fonte, para os metadados.
        grade: Datas do calendário comum, em ordem cronológica.
        pontos: Observações baixadas, sem datas repetidas.

    Returns:
        A série com um valor por data da grade e a situação de cada valor.

    Raises:
        GradeInvalida: Se a grade ou a lista de pontos estiver vazia, ou se houver
            datas repetidas nos pontos.

    Note:
        Pontos fora da grade (por exemplo, uma data que só aparece em outra série,
        quando o calendário é a interseção) continuam contando como "último valor
        conhecido" para as datas seguintes da grade.
    """
    if not grade:
        raise GradeInvalida("a grade está vazia")
    if not pontos:
        raise GradeInvalida(f"a série {nome!r} não tem nenhuma observação na janela")

    ordenados = sorted(pontos, key=lambda ponto: ponto.data)
    por_data = {ponto.data: ponto.valor for ponto in ordenados}
    if len(por_data) != len(ordenados):
        raise GradeInvalida(f"a série {nome!r} tem datas repetidas")

    # Lacuna antes da primeira observação não tem "último valor" para repetir;
    # nesse caso repete-se o primeiro, e a marca registra a extrapolação.
    primeiro_valor = ordenados[0].valor

    valores: list[float] = []
    situacoes: list[Situacao] = []
    ultimo_valor: float | None = None
    seguinte = 0
    for dia in grade:
        # Avança por todas as observações até esta data — inclusive as que ficaram
        # fora da grade — para que o valor repetido seja o mais recente conhecido.
        while seguinte < len(ordenados) and ordenados[seguinte].data <= dia:
            ultimo_valor = ordenados[seguinte].valor
            seguinte += 1

        publicado = por_data.get(dia)
        if publicado is not None:
            valores.append(publicado)
            situacoes.append(Situacao.OBSERVADO)
        elif ultimo_valor is not None:
            valores.append(ultimo_valor)
            situacoes.append(Situacao.REPETIDO)
        else:
            valores.append(primeiro_valor)
            situacoes.append(Situacao.RETROCIDIDO)

    return SerieAlinhada(
        nome=nome,
        origem=origem,
        grade=tuple(grade),
        valores=tuple(valores),
        situacoes=tuple(situacoes),
        inicio_real=ordenados[0].data,
        fim_real=ordenados[-1].data,
        pontos_originais=len(ordenados),
    )


def alinhar_conjunto(
    fontes: Sequence[Fonte],
    estrategia: Estrategia = Estrategia.UNIAO,
    *,
    referencia: str | None = None,
) -> ConjuntoAlinhado:
    """Alinha todas as séries no mesmo calendário.

    Args:
        fontes: Séries baixadas.
        estrategia: Como escolher o calendário comum.
        referencia: Série que define o calendário quando a estratégia é ``indice``.

    Returns:
        O conjunto alinhado: uma grade e uma série por fonte, todas com o mesmo
        número de observações.

    Raises:
        GradeInvalida: Se não houver fontes, se houver nomes repetidos, ou nas
            condições descritas em :func:`montar_grade` e :func:`alinhar`.
    """
    if not fontes:
        raise GradeInvalida("não há séries para alinhar")
    nomes = [fonte.nome for fonte in fontes]
    if len(set(nomes)) != len(nomes):
        raise GradeInvalida("nomes de série repetidos: " + ", ".join(sorted(nomes)))

    grade = montar_grade(
        {fonte.nome: fonte.pontos for fonte in fontes}, estrategia, referencia=referencia
    )
    series = tuple(alinhar(fonte.nome, fonte.origem, grade, fonte.pontos) for fonte in fontes)
    return ConjuntoAlinhado(grade=grade, estrategia=estrategia, series=series)
