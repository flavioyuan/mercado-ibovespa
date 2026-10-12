"""Smoke tests do painel Streamlit, executados pelo próprio AppTest.

O ``streamlit`` é dependência opcional: sem ele instalado, o módulo inteiro é
pulado e a suíte principal segue rodando só com a biblioteca padrão. Para rodar
estes testes::

    uv run --extra painel pytest tests/test_painel.py -v
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="o painel depende do extra 'painel'")

from streamlit.testing.v1 import AppTest

from mercado_ibovespa import painel
from mercado_ibovespa import visualizacao as vis

TEMPO_LIMITE = 120

MATRIZ = (
    "data,IBOVESPA,PETR4,VALE3,SELIC,CAMBIO\n"
    "2026-01-02,100000.0,10.0,20.0,13.65,5.20\n"
    "2026-01-05,101000.0,11.0,21.0,13.65,5.25\n"
    "2026-01-06,100500.0,10.5,20.5,13.90,5.30\n"
    "2026-01-07,102000.0,11.5,22.0,13.90,5.35\n"
)


def _preparar_dados(diretorio: Path) -> Path:
    """Cria uma matriz pequena com os arquivos que acompanham a execução."""
    diretorio.mkdir(parents=True, exist_ok=True)
    base = diretorio / "mercado_2026-01-02_2026-01-07"
    base.with_suffix(".csv").write_text(MATRIZ, encoding="utf-8")
    (diretorio / f"{base.name}_imputacoes.csv").write_text(
        "data,serie,situacao\n2026-01-05,PETR4,repetido\n2026-01-02,VALE3,retrocidido\n",
        encoding="utf-8",
    )
    base.with_suffix(".json").write_text(
        '{"gerado_em": "2026-01-08T10:00:00",'
        ' "calendario": {"estrategia": "uniao", "observacoes": 4},'
        ' "composicao": {"fonte": "B3", "acoes": 3},'
        ' "avisos": ["VALE3: 1 observação no início da janela foi retrocedida"]}',
        encoding="utf-8",
    )
    return base.with_suffix(".csv")


def _abrir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, com_dados: bool = True) -> AppTest:
    """Roda o painel com o diretório de trabalho isolado no tmp_path."""
    monkeypatch.chdir(tmp_path)
    if com_dados:
        _preparar_dados(tmp_path / painel.DIRETORIO_PADRAO)
    aplicacao = AppTest.from_file(str(painel.__file__), default_timeout=TEMPO_LIMITE)
    aplicacao.run()
    return aplicacao


def test_painel_abre_e_desenha_as_quatro_abas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aplicacao = _abrir(tmp_path, monkeypatch)

    assert not aplicacao.exception
    assert [aba.label for aba in aplicacao.tabs] == [
        "Evolução",
        "Retornos",
        "Correlação",
        "Qualidade dos dados",
    ]
    assert aplicacao.title[0].value == "Evolução do Ibovespa, das ações, da Selic e do câmbio"


def test_painel_desenha_graficos_e_tabelas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aplicacao = _abrir(tmp_path, monkeypatch)

    assert not aplicacao.exception
    assert aplicacao.sidebar.multiselect[0].value == [
        "IBOVESPA",
        "PETR4",
        "VALE3",
        "SELIC",
        "CAMBIO",
    ]
    assert len(aplicacao.subheader) == 7
    assert len(aplicacao.dataframe) == 3


def test_painel_sem_dados_orienta_o_uso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aplicacao = _abrir(tmp_path, monkeypatch, com_dados=False)

    assert not aplicacao.exception
    assert any("Nenhuma matriz alinhada" in aviso.value for aviso in aplicacao.warning)
    assert "uv run mercado-ibovespa" in aplicacao.code[0].value


def test_painel_troca_entre_base_100_e_valor_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aplicacao = _abrir(tmp_path, monkeypatch)

    aplicacao.sidebar.radio[0].set_value(painel.MODO_ORIGINAL).run()
    assert not aplicacao.exception
    # No modo original aparece o controle de escala logarítmica.
    assert [caixa.label for caixa in aplicacao.sidebar.checkbox] == ["Escala logarítmica"]

    aplicacao.sidebar.checkbox[0].set_value(True).run()
    assert not aplicacao.exception

    aplicacao.sidebar.radio[0].set_value(painel.MODO_BASE_100).run()
    assert not aplicacao.exception
    assert aplicacao.sidebar.checkbox == []


def test_painel_com_uma_unica_serie_avisa_na_correlacao(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aplicacao = _abrir(tmp_path, monkeypatch)

    aplicacao.sidebar.multiselect[0].set_value(["PETR4"]).run()
    assert not aplicacao.exception
    assert any("ao menos duas séries" in mensagem.value for mensagem in aplicacao.info)


def test_painel_restringe_o_periodo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aplicacao = _abrir(tmp_path, monkeypatch)

    aplicacao.sidebar.date_input[0].set_value((date(2026, 1, 5), date(2026, 1, 6))).run()
    assert not aplicacao.exception
    assert any("2 observações" in legenda.value for legenda in aplicacao.caption)


def test_painel_mostra_avisos_da_execucao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aplicacao = _abrir(tmp_path, monkeypatch)

    assert not aplicacao.exception
    assert len(aplicacao.expander) == 1
    textos = [bloco.value for bloco in aplicacao.expander[0].markdown]
    assert any("B3" in texto and "uniao" in texto for texto in textos)
    assert any("retrocedida" in texto for texto in textos)


def test_painel_mostra_as_imputacoes_na_aba_de_qualidade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aplicacao = _abrir(tmp_path, monkeypatch)

    assert not aplicacao.exception
    # A tabela de qualidade é a terceira do painel (Evolução, Retornos, Qualidade).
    tabela = aplicacao.dataframe[2].value.set_index("série")
    assert tabela.loc["PETR4", "repetido"] == 1
    assert tabela.loc["VALE3", "retrocidido"] == 1
    # São 4 datas: uma célula preenchida derruba a cobertura para 75 %.
    assert tabela.loc["VALE3", "cobertura"] == pytest.approx(0.75)
    assert tabela.loc["IBOVESPA", "cobertura"] == pytest.approx(1.0)


def test_painel_explica_quando_nao_ha_metadados(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    base = _preparar_dados(tmp_path / painel.DIRETORIO_PADRAO)
    vis.caminho_metadados(base).unlink()

    aplicacao = AppTest.from_file(str(painel.__file__), default_timeout=TEMPO_LIMITE)
    aplicacao.run()

    assert not aplicacao.exception
    assert any("metadados" in mensagem.value for mensagem in aplicacao.info)
