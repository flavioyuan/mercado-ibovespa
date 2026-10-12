"""Testes da composição do Ibovespa: validação, parsing e leitura de arquivo."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from mercado_ibovespa.composicao import (
    COMPOSICAO_EMBUTIDA,
    CodigoInvalido,
    ComposicaoInvalida,
    carregar_composicao,
    combinar,
    montar_url_b3,
    normalizar_codigo,
    parsear_composicao_b3,
)

from .conftest import COMPOSICAO_TESTE, payload_b3


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("PETR4", "PETR4"),
        ("petr4", "PETR4"),
        ("  VALE3  ", "VALE3"),
        ("petr4.sa", "PETR4"),
        ("BPAC11", "BPAC11"),
        # Regressão: o código do próprio Ibovespa tem dígito no meio.
        ("B3SA3", "B3SA3"),
    ],
)
def test_normalizar_codigo_aceita_formato_da_b3(bruto: str, esperado: str) -> None:
    assert normalizar_codigo(bruto) == esperado


@pytest.mark.parametrize("bruto", ["PETR", "ABCD123", "PETR4X", "", "1234", "PE TR4", "../etc"])
def test_normalizar_codigo_rejeita_o_resto(bruto: str) -> None:
    with pytest.raises(CodigoInvalido):
        normalizar_codigo(bruto)


def test_composicao_embutida_e_inteiramente_valida() -> None:
    assert len(COMPOSICAO_EMBUTIDA) == 76
    assert len(set(COMPOSICAO_EMBUTIDA)) == 76
    for codigo in COMPOSICAO_EMBUTIDA:
        assert normalizar_codigo(codigo) == codigo


def test_combinar_remove_repeticao_e_preserva_ordem() -> None:
    resultado = combinar([["PETR4", "VALE3"], ["vale3", "B3SA3"], ["PETR4"]])
    assert resultado == ("PETR4", "VALE3", "B3SA3")


def test_parsear_composicao_b3() -> None:
    assert parsear_composicao_b3(payload_b3()) == tuple(sorted(COMPOSICAO_TESTE))


def test_parsear_composicao_b3_ignora_codigo_fora_do_padrao() -> None:
    payload = json.dumps({"results": [{"cod": "PETR4"}, {"cod": "NAO-E-CODIGO"}, {"cod": "VALE3"}]})
    assert parsear_composicao_b3(payload) == ("PETR4", "VALE3")


def test_parsear_composicao_b3_ignora_item_sem_cod() -> None:
    payload = json.dumps({"results": [{"cod": "PETR4"}, {"asset": "SEM CODIGO"}]})
    assert parsear_composicao_b3(payload) == ("PETR4",)


@pytest.mark.parametrize(
    "payload",
    ["{nao json", "[]", "{}", json.dumps({"results": []}), json.dumps({"results": "x"})],
)
def test_parsear_composicao_b3_recusa_payload_malformado(payload: str) -> None:
    with pytest.raises(ComposicaoInvalida):
        parsear_composicao_b3(payload)


def test_parsear_composicao_b3_recusa_lista_sem_codigo_valido() -> None:
    with pytest.raises(ComposicaoInvalida):
        parsear_composicao_b3(json.dumps({"results": [{"cod": "!!!"}]}))


def test_montar_url_b3_codifica_o_parametro_em_base64() -> None:
    url = montar_url_b3()
    assert url.startswith("https://sistemaswebb3-listados.b3.com.br/")
    parametro = url.rstrip("/").rsplit("/", 1)[-1]
    dados = json.loads(base64.b64decode(parametro).decode("utf-8"))
    assert dados["index"] == "IBOV"
    assert dados["language"] == "pt-br"


def test_montar_url_b3_recusa_indice_fora_do_allowlist() -> None:
    with pytest.raises(ComposicaoInvalida):
        montar_url_b3("../../../etc/passwd")


def test_carregar_composicao_csv_com_cabecalho(tmp_path: Path) -> None:
    arquivo = tmp_path / "carteira.csv"
    arquivo.write_text("cod,asset\nPETR4,Petrobras\nVALE3,Vale\n", encoding="utf-8")
    assert carregar_composicao(arquivo) == ("PETR4", "VALE3")


def test_carregar_composicao_csv_sem_cabecalho(tmp_path: Path) -> None:
    arquivo = tmp_path / "carteira.csv"
    arquivo.write_text("PETR4\nVALE3\n", encoding="utf-8")
    assert carregar_composicao(arquivo) == ("PETR4", "VALE3")


def test_carregar_composicao_json_lista(tmp_path: Path) -> None:
    arquivo = tmp_path / "carteira.json"
    arquivo.write_text(json.dumps(["PETR4", "petr4.sa", "VALE3"]), encoding="utf-8")
    assert carregar_composicao(arquivo) == ("PETR4", "VALE3")


def test_carregar_composicao_json_no_formato_da_b3(tmp_path: Path) -> None:
    arquivo = tmp_path / "carteira.json"
    arquivo.write_text(payload_b3(), encoding="utf-8")
    assert carregar_composicao(arquivo) == tuple(sorted(COMPOSICAO_TESTE))


def test_carregar_composicao_texto_ignora_comentarios(tmp_path: Path) -> None:
    arquivo = tmp_path / "carteira.txt"
    arquivo.write_text("# carteira\nPETR4\n\nVALE3\n", encoding="utf-8")
    assert carregar_composicao(arquivo) == ("PETR4", "VALE3")


def test_carregar_composicao_recusa_arquivo_ausente(tmp_path: Path) -> None:
    with pytest.raises(ComposicaoInvalida):
        carregar_composicao(tmp_path / "nao_existe.csv")


def test_carregar_composicao_recusa_arquivo_vazio(tmp_path: Path) -> None:
    arquivo = tmp_path / "vazio.csv"
    arquivo.write_text("   \n", encoding="utf-8")
    with pytest.raises(ComposicaoInvalida):
        carregar_composicao(arquivo)


def test_carregar_composicao_recusa_json_sem_codigo_valido(tmp_path: Path) -> None:
    arquivo = tmp_path / "carteira.json"
    arquivo.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ComposicaoInvalida):
        carregar_composicao(arquivo)
