"""Testes da escrita dos arquivos de saída."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from mercado_ibovespa.alinhamento import Estrategia, Fonte, alinhar_conjunto
from mercado_ibovespa.modelo import Ponto
from mercado_ibovespa.saida import (
    CaminhosSaida,
    EscritaFalhou,
    caminhos_saida,
    escrever_imputacoes,
    escrever_json,
    escrever_matriz,
)

D1, D2, D3 = date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)


def _conjunto() -> object:
    return alinhar_conjunto(
        (
            Fonte("IBOVESPA", "yahoo", (Ponto(D1, 100.0), Ponto(D3, 102.0))),
            Fonte("PETR4", "yahoo", (Ponto(D1, 10.5), Ponto(D2, 11.0), Ponto(D3, 11.5))),
            Fonte("SELIC", "bcb", (Ponto(D1, 13.65), Ponto(D2, 13.65), Ponto(D3, 13.9))),
        ),
        Estrategia.UNIAO,
    )


def test_caminhos_saida_levam_a_janela_no_nome(tmp_path: Path) -> None:
    caminhos = caminhos_saida(tmp_path, date(2024, 10, 6), date(2026, 10, 6))
    assert isinstance(caminhos, CaminhosSaida)
    assert caminhos.matriz.name == "mercado_2024-10-06_2026-10-06.csv"
    assert caminhos.imputacoes.name == "mercado_2024-10-06_2026-10-06_imputacoes.csv"
    assert caminhos.metadados.name == "mercado_2024-10-06_2026-10-06.json"


def test_escrever_matriz_tem_uma_linha_por_data_e_uma_coluna_por_serie(
    tmp_path: Path,
) -> None:
    destino = tmp_path / "matriz.csv"
    escrever_matriz(destino, _conjunto())  # type: ignore[arg-type]

    with destino.open(encoding="utf-8") as arquivo:
        linhas = list(csv.reader(arquivo))
    assert linhas[0] == ["data", "IBOVESPA", "PETR4", "SELIC"]
    assert len(linhas) == 4  # cabeçalho + 3 datas
    assert all(len(linha) == 4 for linha in linhas)
    assert [linha[0] for linha in linhas[1:]] == ["2026-01-02", "2026-01-05", "2026-01-06"]
    # IBOVESPA não tem observação em D2: repete a de D1.
    assert linhas[2][1] == "100.0000000000"


def test_escrever_matriz_respeita_as_casas_decimais(tmp_path: Path) -> None:
    destino = tmp_path / "matriz.csv"
    escrever_matriz(destino, _conjunto(), casas_decimais=2)  # type: ignore[arg-type]
    texto = destino.read_text(encoding="utf-8")
    assert "10.50" in texto
    assert "10.5000" not in texto


def test_escrever_imputacoes_so_lista_o_que_nao_foi_publicado(tmp_path: Path) -> None:
    destino = tmp_path / "imputacoes.csv"
    escrever_imputacoes(destino, _conjunto())  # type: ignore[arg-type]

    with destino.open(encoding="utf-8") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    assert all(linha["situacao"] != "observado" for linha in linhas)
    assert any(linha["data"] == "2026-01-05" and linha["serie"] == "IBOVESPA" for linha in linhas)
    # PETR4 e SELIC são completas: nenhuma linha delas.
    assert not any(linha["serie"] == "PETR4" for linha in linhas)
    assert not any(linha["serie"] == "SELIC" for linha in linhas)


def test_escrever_json_grava_utf8_sem_escapar(tmp_path: Path) -> None:
    destino = tmp_path / "meta.json"
    escrever_json(destino, {"situação": "ok", "n": 1})
    texto = destino.read_text(encoding="utf-8")
    assert "situação" in texto
    assert json.loads(texto) == {"situação": "ok", "n": 1}
    assert texto.endswith("\n")


def test_escrita_e_atomica_e_nao_deixa_temporario(tmp_path: Path) -> None:
    destino = tmp_path / "matriz.csv"
    escrever_matriz(destino, _conjunto())  # type: ignore[arg-type]
    restos = [item.name for item in tmp_path.iterdir() if item.name != "matriz.csv"]
    assert restos == []


def test_escrita_falha_quando_o_destino_e_impossivel(tmp_path: Path) -> None:
    arquivo = tmp_path / "arquivo"
    arquivo.write_text("sou um arquivo, não um diretório", encoding="utf-8")
    with pytest.raises(EscritaFalhou):
        escrever_json(arquivo / "sub" / "meta.json", {"a": 1})


def test_escrita_cria_o_diretorio_de_saida(tmp_path: Path) -> None:
    destino = tmp_path / "novo" / "sub" / "meta.json"
    escrever_json(destino, {"a": 1})
    assert destino.is_file()


def test_escrever_matriz_nao_deixa_arquivo_truncado_em_falha(tmp_path: Path) -> None:
    class ConjuntoRuim:
        @property
        def nomes(self) -> tuple[str, ...]:
            raise RuntimeError("falha no meio da escrita")

    destino = tmp_path / "matriz.csv"
    destino.write_text("conteudo anterior\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        escrever_matriz(destino, ConjuntoRuim())  # type: ignore[arg-type]
    # O destino anterior continua intacto e nenhum temporário sobrou.
    assert destino.read_text(encoding="utf-8") == "conteudo anterior\n"
    assert [item.name for item in tmp_path.iterdir()] == ["matriz.csv"]
