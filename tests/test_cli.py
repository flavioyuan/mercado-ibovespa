"""Testes da CLI: orquestração, códigos de saída e arquivos gravados.

Todos usam o :class:`DownloaderFalso`, então a suíte roda sem rede.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path

import pytest

from mercado_ibovespa import cli, saida
from mercado_ibovespa.rede import FalhaDeRede, RecusaDaApi, SemDados

from .conftest import ARGUMENTOS_JANELA, DIAS, DownloaderFalso


def _argumentos(tmp_path: Path, *extras: str) -> argparse.Namespace:
    return cli._interpretar_argumentos([*ARGUMENTOS_JANELA, "--saida", str(tmp_path), *extras])


def _ler_matriz(caminho: Path) -> tuple[list[str], list[list[str]]]:
    with caminho.open(encoding="utf-8") as arquivo:
        linhas = list(csv.reader(arquivo))
    return linhas[0], linhas[1:]


def test_executar_grava_matriz_com_o_mesmo_numero_de_observacoes(tmp_path: Path) -> None:
    downloader = DownloaderFalso()
    codigo = cli._executar(_argumentos(tmp_path), baixar_uma=downloader)

    assert codigo == cli.SAIDA_OK
    caminhos = saida.caminhos_saida(tmp_path, DIAS[0], DIAS[-1])
    cabecalho, linhas = _ler_matriz(caminhos.matriz)

    # IBOVESPA + 3 ações (em ordem alfabética) + SELIC + CAMBIO.
    assert cabecalho == ["data", "IBOVESPA", "B3SA3", "PETR4", "VALE3", "SELIC", "CAMBIO"]
    assert len(linhas) == len(DIAS)
    assert all(len(linha) == len(cabecalho) for linha in linhas)
    assert caminhos.metadados.is_file() and caminhos.imputacoes.is_file()


def test_executar_consulta_as_tres_fontes(tmp_path: Path) -> None:
    downloader = DownloaderFalso()
    cli._executar(_argumentos(tmp_path), baixar_uma=downloader)

    assert any("listados.b3.com.br" in url for url in downloader.urls)
    assert any("finance.yahoo.com" in url for url in downloader.urls)
    assert any("bcdata.sgs.1178" in url for url in downloader.urls)
    assert any(
        "bcdata.sgs.1?" in url or url.endswith("dataFinal=09/01/2026") for url in downloader.urls
    )


def test_executar_metadados_descrevem_a_janela(tmp_path: Path) -> None:
    downloader = DownloaderFalso()
    cli._executar(_argumentos(tmp_path), baixar_uma=downloader)

    caminhos = saida.caminhos_saida(tmp_path, DIAS[0], DIAS[-1])
    metadados = json.loads(caminhos.metadados.read_text(encoding="utf-8"))
    assert metadados["janela"]["inicio"] == DIAS[0].isoformat()
    assert metadados["calendario"]["observacoes"] == len(DIAS)
    assert metadados["composicao"]["fonte"].startswith("B3")
    assert len(metadados["series"]) == 6
    assert {serie["nome"] for serie in metadados["series"]} >= {"IBOVESPA", "SELIC", "CAMBIO"}


def test_dry_run_nao_grava_nada(tmp_path: Path) -> None:
    codigo = cli._executar(_argumentos(tmp_path, "--dry-run"), baixar_uma=DownloaderFalso())
    assert codigo == cli.SAIDA_OK
    assert list(tmp_path.iterdir()) == []


def test_serie_ausente_falha_por_padrao(tmp_path: Path) -> None:
    downloader = DownloaderFalso(ausentes=("VALE3.SA",))
    with pytest.raises(SemDados, match="VALE3"):
        cli._executar(_argumentos(tmp_path), baixar_uma=downloader)
    assert list(tmp_path.iterdir()) == []


def test_serie_ausente_e_ignorada_com_a_opcao(tmp_path: Path) -> None:
    downloader = DownloaderFalso(ausentes=("VALE3.SA",))
    codigo = cli._executar(_argumentos(tmp_path, "--ignorar-ausentes"), baixar_uma=downloader)
    assert codigo == cli.SAIDA_OK

    caminhos = saida.caminhos_saida(tmp_path, DIAS[0], DIAS[-1])
    cabecalho, _ = _ler_matriz(caminhos.matriz)
    assert "VALE3" not in cabecalho
    metadados = json.loads(caminhos.metadados.read_text(encoding="utf-8"))
    assert metadados["composicao"]["ausentes"] == ["VALE3"]


def test_fallback_para_a_lista_embutida_quando_a_b3_falha(tmp_path: Path) -> None:
    downloader = DownloaderFalso(composicao_erro=True)
    codigo = cli._executar(_argumentos(tmp_path), baixar_uma=downloader)

    assert codigo == cli.SAIDA_OK
    caminhos = saida.caminhos_saida(tmp_path, DIAS[0], DIAS[-1])
    metadados = json.loads(caminhos.metadados.read_text(encoding="utf-8"))
    assert "fallback" in metadados["composicao"]["fonte"]
    assert metadados["composicao"]["acoes"] == 76


def test_composicao_embutida_evita_a_b3(tmp_path: Path) -> None:
    downloader = DownloaderFalso()
    cli._executar(_argumentos(tmp_path, "--composicao", "embutida"), baixar_uma=downloader)
    assert not any("listados.b3.com.br" in url for url in downloader.urls)


def test_arquivo_de_composicao_substitui_a_b3(tmp_path: Path) -> None:
    carteira = tmp_path / "carteira.csv"
    carteira.write_text("cod\nPETR4\n", encoding="utf-8")
    downloader = DownloaderFalso()
    codigo = cli._executar(
        _argumentos(tmp_path, "--arquivo-composicao", str(carteira)),
        baixar_uma=downloader,
    )
    assert codigo == cli.SAIDA_OK
    assert not any("listados.b3.com.br" in url for url in downloader.urls)

    caminhos = saida.caminhos_saida(tmp_path, DIAS[0], DIAS[-1])
    cabecalho, _ = _ler_matriz(caminhos.matriz)
    assert cabecalho == ["data", "IBOVESPA", "PETR4", "SELIC", "CAMBIO"]


def test_tickers_extras_sao_acrescentados(tmp_path: Path) -> None:
    codigo = cli._executar(
        _argumentos(tmp_path, "--tickers", "CVCB3"), baixar_uma=DownloaderFalso()
    )
    assert codigo == cli.SAIDA_OK
    caminhos = saida.caminhos_saida(tmp_path, DIAS[0], DIAS[-1])
    cabecalho, _ = _ler_matriz(caminhos.matriz)
    assert "CVCB3" in cabecalho


def test_ticker_extra_invalido_e_recusado(tmp_path: Path) -> None:
    from mercado_ibovespa.composicao import CodigoInvalido

    with pytest.raises(CodigoInvalido):
        cli._executar(
            _argumentos(tmp_path, "--tickers", "NAO-VALIDO"), baixar_uma=DownloaderFalso()
        )


def test_calendario_indice_usa_as_datas_do_ibovespa(tmp_path: Path) -> None:
    # O SGS publica em D3, mas o "índice" não tem esse dia.
    dias_mercado = (DIAS[0], DIAS[1], DIAS[3], DIAS[4], DIAS[5])
    downloader = DownloaderFalso(dias_mercado=dias_mercado, dias_sgs=DIAS)
    codigo = cli._executar(_argumentos(tmp_path, "--calendario", "indice"), baixar_uma=downloader)
    assert codigo == cli.SAIDA_OK

    caminhos = saida.caminhos_saida(tmp_path, DIAS[0], DIAS[-1])
    _, linhas = _ler_matriz(caminhos.matriz)
    assert len(linhas) == len(dias_mercado)
    assert DIAS[2].isoformat() not in [linha[0] for linha in linhas]


def test_calendario_interseccao(tmp_path: Path) -> None:
    dias_mercado = (DIAS[0], DIAS[1], DIAS[2])
    downloader = DownloaderFalso(dias_mercado=dias_mercado, dias_sgs=DIAS)
    codigo = cli._executar(
        _argumentos(tmp_path, "--calendario", "interseccao"), baixar_uma=downloader
    )
    assert codigo == cli.SAIDA_OK
    caminhos = saida.caminhos_saida(tmp_path, DIAS[0], DIAS[-1])
    _, linhas = _ler_matriz(caminhos.matriz)
    assert [linha[0] for linha in linhas] == [dia.isoformat() for dia in dias_mercado]


def test_campo_close_e_aceito(tmp_path: Path) -> None:
    codigo = cli._executar(_argumentos(tmp_path, "--campo", "close"), baixar_uma=DownloaderFalso())
    assert codigo == cli.SAIDA_OK


def test_repeticao_configuravel(tmp_path: Path) -> None:
    codigo = cli._executar(
        _argumentos(tmp_path, "--tentativas", "1", "--timeout", "5"),
        baixar_uma=DownloaderFalso(),
    )
    assert codigo == cli.SAIDA_OK


# --- códigos de saída ------------------------------------------------------


def test_main_devolve_um_para_janela_invalida() -> None:
    # Falha antes de qualquer acesso à rede.
    assert cli.main(["--meses", "0"]) == cli.SAIDA_ENTRADA
    assert cli.main(["--inicio", "2030-01-01", "--fim", "2026-01-01"]) == cli.SAIDA_ENTRADA


def test_main_devolve_um_para_opcao_desconhecida() -> None:
    assert cli.main(["--opcao-que-nao-existe"]) == cli.SAIDA_ENTRADA


def test_main_devolve_um_para_data_malformada() -> None:
    assert cli.main(["--inicio", "31/02/2026"]) == cli.SAIDA_ENTRADA


def test_main_devolve_um_para_faixa_numerica_invalida(tmp_path: Path) -> None:
    assert cli.main(["--meses", "1", "--tentativas", "99"]) == cli.SAIDA_ENTRADA
    assert cli.main(["--meses", "1", "--timeout", "0"]) == cli.SAIDA_ENTRADA
    assert cli.main(["--meses", "1", "--casas-decimais", "99"]) == cli.SAIDA_ENTRADA


def test_main_devolve_dois_para_falha_de_rede(monkeypatch: pytest.MonkeyPatch) -> None:
    def explodir(*_: object, **__: object) -> int:
        raise FalhaDeRede("sem rede")

    monkeypatch.setattr(cli, "_executar", explodir)
    assert cli.main(["--meses", "1"]) == cli.SAIDA_REDE


def test_main_devolve_dois_para_recusa_da_api(monkeypatch: pytest.MonkeyPatch) -> None:
    def explodir(*_: object, **__: object) -> int:
        raise RecusaDaApi(400, "parâmetro inválido")

    monkeypatch.setattr(cli, "_executar", explodir)
    assert cli.main(["--meses", "1"]) == cli.SAIDA_REDE


def test_main_devolve_tres_para_dados_ausentes(monkeypatch: pytest.MonkeyPatch) -> None:
    def explodir(*_: object, **__: object) -> int:
        raise SemDados("sem dados para PETR4")

    monkeypatch.setattr(cli, "_executar", explodir)
    assert cli.main(["--meses", "1"]) == cli.SAIDA_DADOS


def test_main_devolve_quatro_para_falha_de_escrita(monkeypatch: pytest.MonkeyPatch) -> None:
    def explodir(*_: object, **__: object) -> int:
        raise saida.EscritaFalhou("disco cheio")

    monkeypatch.setattr(cli, "_executar", explodir)
    assert cli.main(["--meses", "1"]) == cli.SAIDA_ESCRITA


def test_main_aceita_help_como_sucesso(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as saida:
        cli.main(["--help"])
    assert saida.value.code == 0
    assert "códigos de saída" in capsys.readouterr().out


def test_main_mostra_erro_de_uso_em_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    # O erro de interpretação dos argumentos vai direto para stderr.
    assert cli.main(["--opcao-que-nao-existe"]) == cli.SAIDA_ENTRADA
    assert "erro:" in capsys.readouterr().err


def test_main_registra_erro_de_janela(caplog: pytest.LogCaptureFixture) -> None:
    # O erro de execução passa pelo logger, não pelo print.
    with caplog.at_level(logging.ERROR):
        assert cli.main(["--meses", "0"]) == cli.SAIDA_ENTRADA
    assert any("janela inválida" in registro.message for registro in caplog.records)
