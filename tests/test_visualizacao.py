"""Testes da lógica pura do painel: leitura da matriz e cálculos dos gráficos.

Nada aqui importa streamlit, pandas ou altair — é o que mantém a suíte do painel
rodando offline e sem interface.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pytest

from mercado_ibovespa import visualizacao as vis

CABECALHO = "data,IBOVESPA,PETR4,SELIC,CAMBIO\n"
LINHAS = (
    "2026-01-02,100000.0,10.0,13.65,5.20\n"
    "2026-01-05,101000.0,11.0,13.65,5.25\n"
    "2026-01-06,100500.0,10.5,13.90,5.30\n"
)


@pytest.fixture
def arquivo_matriz(tmp_path: Path) -> Path:
    """Uma matriz pequena e válida, com quatro séries de naturezas diferentes."""
    caminho = tmp_path / "mercado_2026-01-02_2026-01-06.csv"
    caminho.write_text(CABECALHO + LINHAS, encoding="utf-8")
    return caminho


def _escrever(tmp_path: Path, conteudo: str) -> Path:
    caminho = tmp_path / "matriz.csv"
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


# --- leitura da matriz -----------------------------------------------------


def test_carregar_matriz(arquivo_matriz: Path) -> None:
    matriz = vis.carregar_matriz(arquivo_matriz)
    assert matriz.nomes == ("IBOVESPA", "PETR4", "SELIC", "CAMBIO")
    assert matriz.observacoes == 3
    assert matriz.periodo == (date(2026, 1, 2), date(2026, 1, 6))
    assert matriz.valores("PETR4") == (10.0, 11.0, 10.5)
    assert matriz.caminho == arquivo_matriz


def test_acoes_exclui_indice_e_macro(arquivo_matriz: Path) -> None:
    assert vis.carregar_matriz(arquivo_matriz).acoes() == ("PETR4",)


def test_carregar_matriz_recusa_arquivo_ausente(tmp_path: Path) -> None:
    with pytest.raises(vis.MatrizInvalida, match="não encontrado"):
        vis.carregar_matriz(tmp_path / "nao_existe.csv")


@pytest.mark.parametrize(
    ("conteudo", "trecho"),
    [
        ("", "vazio"),
        ("valor,PETR4\n", "cabeçalho"),
        ("data\n", "cabeçalho"),
        ("data,PETR4\ndata,PETR4\n", "formato ISO"),
        ("data,,PETR4\n2026-01-02,1.0,2.0\n", "sem nome"),
        ("data,PETR4,PETR4\n2026-01-02,1.0,2.0\n", "repetidas"),
        ("data,PETR4\n02/01/2026,1.0\n", "formato ISO"),
        ("data,PETR4\n2026-01-02,abc\n", "não numérico"),
        ("data,PETR4\n2026-01-02,inf\n", "não finito"),
        ("data,PETR4,SELIC\n2026-01-02,1.0\n", "campos para"),
        ("data,PETR4\n2026-01-06,1.0\n2026-01-02,2.0\n", "ordem cronológica"),
        ("data,PETR4\n2026-01-02,1.0\n2026-01-02,2.0\n", "repetidas"),
    ],
)
def test_carregar_matriz_recusa_arquivo_invalido(
    tmp_path: Path, conteudo: str, trecho: str
) -> None:
    with pytest.raises(vis.MatrizInvalida, match=trecho):
        vis.carregar_matriz(_escrever(tmp_path, conteudo))


def test_matriz_recusa_serie_com_tamanho_diferente(arquivo_matriz: Path) -> None:
    matriz = vis.carregar_matriz(arquivo_matriz)
    with pytest.raises(vis.MatrizInvalida, match="valores para"):
        vis.Matriz(
            caminho=matriz.caminho,
            datas=matriz.datas,
            series={"PETR4": (1.0, 2.0)},
        )


def test_matriz_vazia_nao_tem_periodo(tmp_path: Path) -> None:
    vazia = vis.Matriz(caminho=tmp_path, datas=(), series={})
    assert vazia.observacoes == 0
    with pytest.raises(vis.MatrizInvalida, match="nenhuma data"):
        _ = vazia.periodo


def test_recortar_inclui_as_pontas(arquivo_matriz: Path) -> None:
    matriz = vis.carregar_matriz(arquivo_matriz)
    recorte = matriz.recortar(date(2026, 1, 5), date(2026, 1, 6))
    assert recorte.datas == (date(2026, 1, 5), date(2026, 1, 6))
    assert recorte.valores("PETR4") == (11.0, 10.5)
    # O recorte vale para todas as séries, mantendo o alinhamento.
    assert all(len(valores) == 2 for valores in recorte.series.values())


def test_recortar_recusa_intervalo_vazio(arquivo_matriz: Path) -> None:
    matriz = vis.carregar_matriz(arquivo_matriz)
    with pytest.raises(vis.MatrizInvalida, match="posterior"):
        matriz.recortar(date(2026, 1, 6), date(2026, 1, 2))
    with pytest.raises(vis.MatrizInvalida, match="não há observações"):
        matriz.recortar(date(2020, 1, 1), date(2020, 12, 31))


def test_selecionar_series(arquivo_matriz: Path) -> None:
    matriz = vis.carregar_matriz(arquivo_matriz).selecionar(["PETR4", "SELIC"])
    assert matriz.nomes == ("PETR4", "SELIC")
    assert matriz.observacoes == 3


def test_selecionar_recusa_selecao_vazia_ou_desconhecida(arquivo_matriz: Path) -> None:
    matriz = vis.carregar_matriz(arquivo_matriz)
    with pytest.raises(vis.MatrizInvalida, match="vazia"):
        matriz.selecionar([])
    with pytest.raises(vis.MatrizInvalida, match="não encontradas"):
        matriz.selecionar(["XPTO3"])


def test_obter_serie_inexistente_falha(arquivo_matriz: Path) -> None:
    with pytest.raises(KeyError):
        vis.carregar_matriz(arquivo_matriz).valores("XPTO3")


def test_listar_matrizes_ignora_imputacoes_e_ordena(tmp_path: Path) -> None:
    (tmp_path / "mercado_2024-01-01_2025-01-01.csv").write_text("data,A\n", encoding="utf-8")
    (tmp_path / "mercado_2026-01-01_2027-01-01.csv").write_text("data,A\n", encoding="utf-8")
    (tmp_path / "mercado_2026-01-01_2027-01-01_imputacoes.csv").write_text(
        "data,serie,situacao\n", encoding="utf-8"
    )
    (tmp_path / "outro.csv").write_text("data,A\n", encoding="utf-8")

    nomes = [arquivo.name for arquivo in vis.listar_matrizes(tmp_path)]
    assert nomes == [
        "mercado_2026-01-01_2027-01-01.csv",
        "mercado_2024-01-01_2025-01-01.csv",
    ]


def test_listar_matrizes_em_diretorio_ausente(tmp_path: Path) -> None:
    assert vis.listar_matrizes(tmp_path / "nao_existe") == ()


def test_caminhos_derivados(arquivo_matriz: Path) -> None:
    assert vis.caminho_imputacoes(arquivo_matriz).name.endswith("_imputacoes.csv")
    assert vis.caminho_metadados(arquivo_matriz).suffix == ".json"


# --- transformações --------------------------------------------------------


def test_normalizar_base_100() -> None:
    assert vis.normalizar_base_100([50.0, 100.0, 25.0]) == (100.0, 200.0, 50.0)


def test_normalizar_base_100_recusa_serie_invalida() -> None:
    with pytest.raises(vis.MatrizInvalida, match="não há valores"):
        vis.normalizar_base_100([])
    with pytest.raises(vis.MatrizInvalida, match="positivo"):
        vis.normalizar_base_100([0.0, 1.0])


def test_retornos_log() -> None:
    retornos = vis.retornos_log([100.0, 110.0, 121.0])
    assert len(retornos) == 2
    assert retornos[0] == pytest.approx(math.log(1.1))
    assert retornos[1] == pytest.approx(math.log(1.1))


def test_retornos_log_marca_valor_nao_positivo_como_nan() -> None:
    retornos = vis.retornos_log([100.0, 0.0, 50.0])
    # Os dois pares envolvem o zero, então nenhum dos dois tem retorno definido.
    assert all(math.isnan(retorno) for retorno in retornos)


def test_retornos_log_recusa_serie_vazia() -> None:
    with pytest.raises(vis.MatrizInvalida):
        vis.retornos_log([])


def test_retorno_acumulado() -> None:
    assert vis.retorno_acumulado([100.0, 150.0]) == pytest.approx(0.5)
    assert math.isnan(vis.retorno_acumulado([100.0]))


def test_volatilidade_anualizada_valor_conhecido() -> None:
    # Log-retornos +9,53 %, -9,53 %, +9,53 %: desvio-padrão amostral de 0,1100547
    # ao dia, que anualizado por raiz de 252 dá 1,7471.
    assert vis.volatilidade_anualizada([100.0, 110.0, 100.0, 110.0]) == pytest.approx(
        1.747067, rel=1e-5
    )


def test_volatilidade_anualizada_com_poucos_pontos() -> None:
    assert math.isnan(vis.volatilidade_anualizada([100.0]))
    # Dois pontos dão um único log-retorno: não há desvio-padrão amostral.
    assert math.isnan(vis.volatilidade_anualizada([100.0, 110.0]))
    # Já três pontos sem variação dão volatilidade zero, não indefinida.
    assert vis.volatilidade_anualizada([100.0, 100.0, 100.0]) == pytest.approx(0.0)


def test_estatisticas(arquivo_matriz: Path) -> None:
    matriz = vis.carregar_matriz(arquivo_matriz)
    resumo = vis.estatisticas("PETR4", matriz.valores("PETR4"))
    assert resumo.nome == "PETR4"
    assert resumo.observacoes == 3
    assert resumo.primeiro == 10.0
    assert resumo.ultimo == 10.5
    assert resumo.minimo == 10.0
    assert resumo.maximo == 11.0
    assert resumo.retorno_total == pytest.approx(0.05)
    dicionario = resumo.para_dicionario()
    assert dicionario["série"] == "PETR4"


def test_estatisticas_recusa_serie_vazia() -> None:
    with pytest.raises(vis.MatrizInvalida, match="vazia"):
        vis.estatisticas("A", [])


# --- correlação ------------------------------------------------------------


def test_correlacao_de_series_identicas_e_um() -> None:
    serie = [100.0, 110.0, 105.0, 120.0]
    assert vis.correlacao(serie, serie) == pytest.approx(1.0)


def test_correlacao_de_series_opostas_e_menos_um() -> None:
    subindo = [100.0, 110.0, 105.0, 120.0]
    # O recíproco tem exatamente os log-retornos negados, então a correlação é -1.
    descendo = [100.0 / valor for valor in subindo]
    assert vis.correlacao(subindo, descendo) == pytest.approx(-1.0)


def test_correlacao_indefinida_vira_nan() -> None:
    assert math.isnan(vis.correlacao([1.0, 2.0], [1.0, 2.0, 3.0]))  # tamanhos diferentes
    assert math.isnan(vis.correlacao([100.0], [100.0]))  # sem retorno
    assert math.isnan(vis.correlacao([100.0, 100.0, 100.0], [1.0, 2.0, 3.0]))  # variância nula


def test_pares_correlacao_cobre_todos_os_pares_distintos() -> None:
    a = [1.0, 2.0, 3.0, 5.0]
    series = {"A": a, "B": list(a), "C": [1.0 / valor for valor in a]}
    pares = vis.pares_correlacao(series)
    assert [(x, y) for x, y, _ in pares] == [("A", "B"), ("A", "C"), ("B", "C")]
    assert pares[0][2] == pytest.approx(1.0)
    assert pares[1][2] == pytest.approx(-1.0)
    assert pares[2][2] == pytest.approx(-1.0)


# --- imputações e proveniência ---------------------------------------------


def test_carregar_imputacoes(tmp_path: Path) -> None:
    caminho = tmp_path / "mercado_2026-01-02_2026-01-06_imputacoes.csv"
    caminho.write_text(
        "data,serie,situacao\n"
        "2026-01-05,IBOVESPA,repetido\n"
        "2026-01-02,NATU3,retrocidido\n"
        "linha,corrompida,aqui\n",
        encoding="utf-8",
    )
    imputacoes = vis.carregar_imputacoes(caminho)
    # A linha corrompida é descartada: é relatório de qualidade, não dado.
    assert len(imputacoes) == 2
    assert imputacoes[0] == vis.Imputacao(date(2026, 1, 5), "IBOVESPA", "repetido")


def test_carregar_imputacoes_ausente(tmp_path: Path) -> None:
    assert vis.carregar_imputacoes(tmp_path / "nao_existe.csv") == ()


def test_contar_situacoes(arquivo_matriz: Path) -> None:
    matriz = vis.carregar_matriz(arquivo_matriz)
    imputacoes = (
        vis.Imputacao(date(2026, 1, 5), "PETR4", "repetido"),
        vis.Imputacao(date(2026, 1, 2), "PETR4", "retrocidido"),
        # Série fora da matriz e situação desconhecida são ignoradas.
        vis.Imputacao(date(2026, 1, 5), "XPTO3", "repetido"),
        vis.Imputacao(date(2026, 1, 5), "SELIC", "inventado"),
    )
    contagens = vis.contar_situacoes(imputacoes, matriz.nomes, matriz.observacoes)

    assert contagens["PETR4"] == {"observado": 1, "repetido": 1, "retrocidido": 1}
    assert contagens["SELIC"] == {"observado": 3, "repetido": 0, "retrocidido": 0}
    assert "XPTO3" not in contagens


def test_carregar_proveniencia(tmp_path: Path) -> None:
    caminho = tmp_path / "meta.json"
    caminho.write_text(
        json.dumps(
            {
                "gerado_em": "2026-10-06T22:00:00",
                "calendario": {"estrategia": "uniao", "observacoes": 502},
                "composicao": {"fonte": "B3", "acoes": 76},
                "avisos": ["NATU3: cobertura baixa"],
            }
        ),
        encoding="utf-8",
    )
    proveniencia = vis.carregar_proveniencia(caminho)
    assert proveniencia is not None
    assert proveniencia.estrategia == "uniao"
    assert proveniencia.observacoes == 502
    assert proveniencia.acoes == 76
    assert proveniencia.avisos == ("NATU3: cobertura baixa",)


@pytest.mark.parametrize(
    "conteudo",
    ["", "{nao json", "[]", "{}", '{"avisos": "nao e lista", "composicao": 3}'],
)
def test_carregar_proveniencia_tolera_arquivo_ruim(tmp_path: Path, conteudo: str) -> None:
    caminho = tmp_path / "meta.json"
    caminho.write_text(conteudo, encoding="utf-8")
    proveniencia = vis.carregar_proveniencia(caminho)
    # Ou devolve None, ou devolve uma estrutura com os campos vazios — nunca quebra.
    if proveniencia is not None:
        assert proveniencia.avisos == ()
        assert proveniencia.observacoes == 0


def test_carregar_proveniencia_ausente(tmp_path: Path) -> None:
    assert vis.carregar_proveniencia(tmp_path / "nao_existe.json") is None
