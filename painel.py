"""Painel Streamlit para observar a evolução das ações, do índice, da Selic e do câmbio.

O painel **não baixa nada**: ele lê a matriz alinhada que
:mod:`mercado_ibovespa.cli` grava em ``dados/``. Primeiro rode o downloader, depois
o painel::

    uv run mercado-ibovespa --meses 24
    uv run --extra painel streamlit run src/mercado_ibovespa/painel.py

São quatro abas:

- **Evolução** — a série de preços de cada ativo, no nível original ou rebaseada
  em 100 para comparar ativos de escalas diferentes no mesmo eixo;
- **Retornos** — retorno logarítmico acumulado, distribuição dos retornos diários
  e volatilidade anualizada;
- **Correlação** — mapa de calor das correlações entre os **retornos** (não entre
  os níveis, que produziriam correlação espúria);
- **Qualidade dos dados** — quanto de cada série foi publicado pela fonte e quanto
  foi preenchido por repetição, além dos avisos da execução.

Toda a conta vive em :mod:`mercado_ibovespa.visualizacao`, que é pura e testável;
este módulo só monta widgets e desenha.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Sequence
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from mercado_ibovespa import visualizacao as vis

DIRETORIO_PADRAO = "dados"
SERIES_PADRAO = ("IBOVESPA", "PETR4", "VALE3", "ITUB4", "SELIC", "CAMBIO")
MAXIMO_SERIES_GRAFICO = 15
MAXIMO_SERIES_CORRELACAO = 20

MODO_BASE_100 = "Base 100 (variação acumulada)"
MODO_ORIGINAL = "Valor original"
MODOS = (MODO_BASE_100, MODO_ORIGINAL)

# O parâmetro de largura mudou de ``use_container_width`` para ``width="stretch"``
# ao longo das versões 1.4x do Streamlit. Detectar em vez de fixar evita tanto o
# erro numa versão antiga quanto o aviso de depreciação na nova, e mantém os dois
# ramos explícitos para a checagem de tipos ver cada chamada.
_GRAFICO_ACEITA_WIDTH = "width" in inspect.signature(st.altair_chart).parameters
_TABELA_ACEITA_WIDTH = "width" in inspect.signature(st.dataframe).parameters


def main() -> None:
    """Monta a página do painel."""
    st.set_page_config(
        page_title="Mercado — Ibovespa, ações, Selic e câmbio",
        page_icon="📈",
        layout="wide",
    )
    st.title("Evolução do Ibovespa, das ações, da Selic e do câmbio")

    caminho = _escolher_arquivo()
    if caminho is None:
        return

    matriz = _carregar_matriz(str(caminho))
    recorte, escolhidas, modo, escala_log = _barra_lateral(matriz)
    selecao = recorte.selecionar(escolhidas)

    st.caption(
        f"**{caminho.name}** — {selecao.observacoes} observações "
        f"de {selecao.periodo[0]:%d/%m/%Y} a {selecao.periodo[1]:%d/%m/%Y}, "
        f"{len(selecao.nomes)} de {len(matriz.nomes)} séries selecionadas."
    )

    aba_evolucao, aba_retornos, aba_correlacao, aba_qualidade = st.tabs(
        ["Evolução", "Retornos", "Correlação", "Qualidade dos dados"]
    )
    with aba_evolucao:
        _aba_evolucao(selecao, modo, escala_log)
    with aba_retornos:
        _aba_retornos(selecao)
    with aba_correlacao:
        _aba_correlacao(selecao)
    with aba_qualidade:
        _aba_qualidade(matriz, caminho)


# --- entrada de dados ------------------------------------------------------


@st.cache_data(show_spinner=False)
def _carregar_matriz(caminho: str) -> vis.Matriz:
    """Carrega a matriz uma vez por arquivo, guardando o resultado em cache."""
    return vis.carregar_matriz(Path(caminho))


@st.cache_data(show_spinner=False)
def _carregar_imputacoes(caminho_matriz: str) -> tuple[vis.Imputacao, ...]:
    """Carrega a lista de imputações que acompanha a matriz.

    Recebe o caminho da **matriz** e deriva o arquivo companheiro aqui dentro, para
    que o chamador não possa passar o arquivo errado.
    """
    return vis.carregar_imputacoes(vis.caminho_imputacoes(Path(caminho_matriz)))


@st.cache_data(show_spinner=False)
def _carregar_proveniencia(caminho_matriz: str) -> vis.Proveniencia | None:
    """Carrega os metadados que acompanham a matriz.

    Recebe o caminho da **matriz** e deriva o arquivo companheiro aqui dentro, para
    que o chamador não possa passar o arquivo errado.
    """
    return vis.carregar_proveniencia(vis.caminho_metadados(Path(caminho_matriz)))


def _escolher_arquivo() -> Path | None:
    """Desenha a escolha do diretório e do arquivo de dados.

    Returns:
        O caminho escolhido, ou ``None`` quando não há nenhuma matriz para abrir
        (e nesse caso explica como gerar uma).
    """
    st.sidebar.header("Dados")
    texto = st.sidebar.text_input("Diretório", value=DIRETORIO_PADRAO)
    # O caminho é resolvido para absoluto porque ele é a chave do cache das
    # funções de leitura: com caminho relativo, dois diretórios diferentes que
    # usem ``dados/`` colidiriam na mesma chave.
    diretorio = Path(texto).expanduser().resolve()
    arquivos = vis.listar_matrizes(diretorio)
    if not arquivos:
        st.warning(
            f"Nenhuma matriz alinhada (`mercado_*.csv`) encontrada em `{diretorio}`. "
            "Gere os dados primeiro:"
        )
        st.code(f"uv run mercado-ibovespa --meses 24 --saida {diretorio}", language="console")
        return None

    rotulos = [arquivo.name for arquivo in arquivos]
    escolhido = st.sidebar.selectbox("Arquivo", rotulos, index=0)
    return arquivos[rotulos.index(escolhido)]


def _barra_lateral(matriz: vis.Matriz) -> tuple[vis.Matriz, tuple[str, ...], str, bool]:
    """Desenha os controles de período, séries e escala.

    Args:
        matriz: Matriz completa, usada para saber os limites dos controles.

    Returns:
        O recorte temporal, as séries escolhidas, o modo de exibição e se a escala
        do eixo vertical é logarítmica.
    """
    st.sidebar.header("Recorte")
    inicio, fim = matriz.periodo
    periodo = st.sidebar.date_input("Período", value=(inicio, fim), min_value=inicio, max_value=fim)
    if isinstance(periodo, (tuple, list)) and len(periodo) == 2:
        recorte = matriz.recortar(periodo[0], periodo[1])
    else:
        # O usuário ainda está escolhendo a outra ponta do intervalo.
        recorte = matriz

    st.sidebar.header("Séries")
    padrao = [nome for nome in SERIES_PADRAO if nome in recorte.nomes]
    escolhidas = st.sidebar.multiselect(
        "Séries", recorte.nomes, default=padrao or list(recorte.nomes[:4])
    )
    if not escolhidas:
        st.sidebar.warning("Escolha ao menos uma série; mostrando as quatro primeiras.")
        escolhidas = list(recorte.nomes[:4])
    if len(escolhidas) > MAXIMO_SERIES_GRAFICO:
        st.sidebar.caption(
            f"{len(escolhidas)} séries no gráfico podem deixá-lo ilegível; "
            f"considere reduzir para {MAXIMO_SERIES_GRAFICO}."
        )

    st.sidebar.header("Exibição")
    modo = st.sidebar.radio("Valores", MODOS, index=0)
    escala_log = False
    if modo == MODO_ORIGINAL:
        escala_log = st.sidebar.checkbox("Escala logarítmica", value=False)
    return recorte, tuple(escolhidas), modo, escala_log


# --- desenho ---------------------------------------------------------------


def _mostrar_grafico(grafico: alt.Chart) -> None:
    """Desenha um gráfico Altair ocupando toda a largura da coluna."""
    if _GRAFICO_ACEITA_WIDTH:
        st.altair_chart(grafico, width="stretch")
    else:
        st.altair_chart(grafico, use_container_width=True)


def _mostrar_tabela(dados: object) -> None:
    """Desenha uma tabela sem a coluna de índice, ocupando toda a largura."""
    if _TABELA_ACEITA_WIDTH:
        st.dataframe(dados, width="stretch", hide_index=True)
    else:
        st.dataframe(dados, use_container_width=True, hide_index=True)


# --- montagem dos dados para os gráficos -----------------------------------


def _dados_longos(matriz: vis.Matriz, modo: str) -> pd.DataFrame:
    """Converte a matriz para o formato longo que o Altair consome.

    Returns:
        Um ``DataFrame`` com as colunas ``data``, ``serie`` e ``valor``.
    """
    registros: list[dict[str, object]] = []
    for nome in matriz.nomes:
        valores: Sequence[float] = matriz.valores(nome)
        if modo == MODO_BASE_100:
            valores = vis.normalizar_base_100(valores)
        registros.extend(
            {"data": dia, "serie": nome, "valor": valor}
            for dia, valor in zip(matriz.datas, valores, strict=True)
        )
    return pd.DataFrame.from_records(registros)


def _dados_acumulados(matriz: vis.Matriz) -> pd.DataFrame:
    """Converte cada série no seu retorno logarítmico acumulado, em porcentagem.

    Returns:
        Um ``DataFrame`` com as colunas ``data``, ``serie`` e ``valor``, onde
        lacunas de retorno viram ``NaN`` (e o gráfico, uma interrupção).
    """
    registros: list[dict[str, object]] = []
    for nome in matriz.nomes:
        acumulado = 0.0
        for dia, retorno in zip(
            matriz.datas[1:], vis.retornos_log(matriz.valores(nome)), strict=True
        ):
            if math.isfinite(retorno):
                acumulado += retorno
                registros.append({"data": dia, "serie": nome, "valor": acumulado * 100.0})
            else:
                registros.append({"data": dia, "serie": nome, "valor": math.nan})
    return pd.DataFrame.from_records(registros)


def _tabela_estatisticas(matriz: vis.Matriz) -> pd.DataFrame:
    """Monta a tabela de resumo das séries selecionadas."""
    resumos = [
        vis.estatisticas(nome, matriz.valores(nome)).para_dicionario() for nome in matriz.nomes
    ]
    return pd.DataFrame.from_records(resumos)


def _grafico_linhas(dados: pd.DataFrame, titulo_eixo: str, escala_log: bool) -> alt.Chart:
    """Gráfico de linhas com uma cor por série, em escala linear ou logarítmica."""
    escala = alt.Scale(type="log") if escala_log else alt.Scale(type="linear")
    # A anotação explícita evita que o ``Any`` da cadeia do Altair se propague.
    grafico: alt.Chart = (
        alt.Chart(dados)
        .mark_line(strokeWidth=1.6)
        .encode(
            x=alt.X("data:T", title="Data"),
            y=alt.Y("valor:Q", title=titulo_eixo, scale=escala),
            color=alt.Color("serie:N", title="Série", scale=alt.Scale(scheme="tableau10")),
            tooltip=[
                alt.Tooltip("data:T", title="Data", format="%d/%m/%Y"),
                alt.Tooltip("serie:N", title="Série"),
                alt.Tooltip("valor:Q", title=titulo_eixo, format=",.2f"),
            ],
        )
        .interactive()
    )
    return grafico


def _grafico_cobertura(contagens: pd.DataFrame) -> alt.Chart:
    """Barras horizontais com a fração de cada série que veio da fonte."""
    # A anotação explícita evita que o ``Any`` da cadeia do Altair se propague.
    grafico: alt.Chart = (
        alt.Chart(contagens)
        .mark_bar()
        .encode(
            y=alt.Y("série:N", sort="x", title=None),
            x=alt.X(
                "cobertura:Q",
                title="Fração publicada pela fonte",
                axis=alt.Axis(format="%"),
            ),
            color=alt.Color("cobertura:Q", scale=alt.Scale(scheme="redyellowgreen"), legend=None),
            tooltip=[
                alt.Tooltip("série:N", title="Série"),
                alt.Tooltip("cobertura:Q", title="Cobertura", format=".1%"),
                alt.Tooltip("observado:Q", title="Publicadas"),
                alt.Tooltip("repetido:Q", title="Repetidas"),
                alt.Tooltip("retrocidido:Q", title="Retrocedidas"),
            ],
        )
    )
    return grafico


# --- abas ------------------------------------------------------------------


def _aba_evolucao(matriz: vis.Matriz, modo: str, escala_log: bool) -> None:
    """Série de preços de cada ativo, no nível original ou rebaseada em 100."""
    st.subheader("Evolução das séries")
    if modo == MODO_BASE_100:
        st.caption(
            "Cada série começa em 100 na primeira data do recorte, então a linha "
            "mostra a variação percentual acumulada — é o que permite comparar uma "
            "ação de R$ 4 com o Ibovespa de 200 000 pontos no mesmo eixo."
        )
    else:
        st.caption(
            "Valores como publicados, na unidade de cada série (R$ para as ações, "
            "pontos para o índice, % ao ano para a Selic, R$/US$ para o câmbio)."
        )
    _mostrar_grafico(_grafico_linhas(_dados_longos(matriz, modo), "Valor", escala_log))

    st.subheader("Resumo do período")
    _mostrar_tabela(
        _tabela_estatisticas(matriz).style.format(
            {
                "primeiro": "{:,.2f}",
                "último": "{:,.2f}",
                "mínimo": "{:,.2f}",
                "máximo": "{:,.2f}",
                "retorno no período": "{:+.2%}",
                "volatilidade anual": "{:.2%}",
            }
        )
    )


def _aba_retornos(matriz: vis.Matriz) -> None:
    """Retorno acumulado, distribuição dos retornos diários e volatilidade."""
    st.subheader("Retorno logarítmico acumulado")
    st.caption(
        "Soma dos log-retornos diários, em porcentagem. Usa retorno (e não preço) "
        "porque é a grandeza que se assume aditiva no tempo."
    )
    _mostrar_grafico(_grafico_linhas(_dados_acumulados(matriz), "Retorno acumulado (%)", False))

    st.subheader("Distribuição dos retornos diários")
    escolhida = st.selectbox("Série", matriz.nomes, index=0)
    retornos = [
        retorno for retorno in vis.retornos_log(matriz.valores(escolhida)) if math.isfinite(retorno)
    ]
    if retornos:
        _mostrar_grafico(
            alt.Chart(pd.DataFrame({"retorno": retornos}))
            .mark_bar()
            .encode(
                x=alt.X("retorno:Q", bin=alt.Bin(maxbins=60), title="Log-retorno diário"),
                y=alt.Y("count():Q", title="Dias"),
                tooltip=[alt.Tooltip("count():Q", title="Dias")],
            )
        )
    else:
        st.info("A série escolhida não tem retornos válidos no recorte.")

    st.subheader("Retorno e risco no período")
    tabela = _tabela_estatisticas(matriz)[
        ["série", "retorno no período", "volatilidade anual"]
    ].sort_values("retorno no período", ascending=False)
    _mostrar_tabela(
        tabela.style.format({"retorno no período": "{:+.2%}", "volatilidade anual": "{:.2%}"})
    )


def _aba_correlacao(matriz: vis.Matriz) -> None:
    """Mapa de calor das correlações entre os retornos das séries escolhidas."""
    st.subheader("Correlação entre retornos")
    if len(matriz.nomes) < 2:
        st.info("Selecione ao menos duas séries para ver a correlação.")
        return
    if len(matriz.nomes) > MAXIMO_SERIES_CORRELACAO:
        st.warning(
            f"São {len(matriz.nomes)} séries; o mapa usa as "
            f"{MAXIMO_SERIES_CORRELACAO} primeiras para continuar legível. "
            "Reduza a seleção na barra lateral para escolher outras."
        )
    nomes = matriz.nomes[:MAXIMO_SERIES_CORRELACAO]
    st.caption(
        "Correlação de Pearson entre os **log-retornos diários**. Sobre os níveis de "
        "preço, duas séries com tendência apareceriam quase perfeitamente "
        "correlacionadas mesmo sem relação alguma."
    )

    valores = {nome: matriz.valores(nome) for nome in nomes}
    pares = vis.pares_correlacao(valores)
    registros = [{"primeira": a, "segunda": b, "correlacao": valor} for a, b, valor in pares]
    # O mapa precisa da matriz completa: espelha os pares e preenche a diagonal.
    registros.extend({"primeira": b, "segunda": a, "correlacao": valor} for a, b, valor in pares)
    registros.extend({"primeira": nome, "segunda": nome, "correlacao": 1.0} for nome in nomes)
    dados = pd.DataFrame.from_records(registros)

    _mostrar_grafico(
        alt.Chart(dados)
        .mark_rect()
        .encode(
            x=alt.X("primeira:N", sort=list(nomes), title=None),
            y=alt.Y("segunda:N", sort=list(nomes), title=None),
            color=alt.Color(
                "correlacao:Q",
                title="Correlação",
                scale=alt.Scale(scheme="redblue", domain=[-1, 1]),
            ),
            tooltip=[
                alt.Tooltip("primeira:N", title="Série"),
                alt.Tooltip("segunda:N", title="Com"),
                alt.Tooltip("correlacao:Q", title="Correlação", format="+.3f"),
            ],
        )
        .properties(height=max(320, 26 * len(nomes)))
    )


def _aba_qualidade(matriz: vis.Matriz, caminho: Path) -> None:
    """Mostra quanto de cada série veio da fonte e a proveniência da execução."""
    st.subheader("Quanto de cada série foi publicado")
    st.caption(
        "O alinhamento garante o mesmo número de observações para todas as séries; "
        "esta aba mostra quanto disso é dado real e quanto é repetição da última "
        "observação. `retrocidido` é lacuna no início da série preenchida com o "
        "primeiro valor — o único caso com olhar para o futuro."
    )

    imputacoes = _carregar_imputacoes(str(caminho))
    contagens = vis.contar_situacoes(imputacoes, matriz.nomes, matriz.observacoes)
    tabela = pd.DataFrame.from_records(
        [
            {
                "série": nome,
                "observado": valores["observado"],
                "repetido": valores["repetido"],
                "retrocidido": valores["retrocidido"],
                "cobertura": valores["observado"] / matriz.observacoes,
            }
            for nome, valores in contagens.items()
        ]
    ).sort_values("cobertura")

    _mostrar_grafico(_grafico_cobertura(tabela))
    _mostrar_tabela(tabela.style.format({"cobertura": "{:.1%}"}))

    proveniencia = _carregar_proveniencia(str(caminho))
    if proveniencia is None:
        st.info("Os metadados da execução não foram encontrados ao lado da matriz.")
        return

    with st.expander("Proveniência da execução", expanded=True):
        st.markdown(
            f"- **Gerado em:** {proveniencia.gerado_em}\n"
            f"- **Calendário:** `{proveniencia.estrategia}` "
            f"({proveniencia.observacoes} observações)\n"
            f"- **Composição:** {proveniencia.composicao_fonte} "
            f"({proveniencia.acoes} ações)"
        )
        if proveniencia.avisos:
            st.markdown("**Avisos emitidos no download:**")
            for aviso in proveniencia.avisos:
                st.markdown(f"- {aviso}")
        else:
            st.markdown("Nenhum aviso de qualidade foi emitido no download.")


if __name__ == "__main__":
    main()
