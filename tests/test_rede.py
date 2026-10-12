"""Testes da borda HTTP: classificação de erro e política de repetição."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

import pytest

from mercado_ibovespa import rede
from mercado_ibovespa.rede import (
    FalhaDeRede,
    RecusaDaApi,
    SemDados,
    baixar,
    baixar_com_tentativas,
    extrair_mensagem,
)


def _erro_http(
    codigo: int, corpo: bytes = b"", url: str = "https://exemplo.invalido/x"
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, codigo, "erro", Message(), io.BytesIO(corpo))


def test_extrair_mensagem_de_json_com_error() -> None:
    assert extrair_mensagem('{"error": "símbolo desconhecido"}') == "símbolo desconhecido"


def test_extrair_mensagem_de_json_aninhado() -> None:
    assert extrair_mensagem('{"erro": {"detail": "janela inválida"}}') == "janela inválida"


def test_extrair_mensagem_de_texto_puro_em_uma_linha() -> None:
    assert extrair_mensagem("linha 1\nlinha 2") == "linha 1 linha 2"


def test_extrair_mensagem_trunca_texto_longo() -> None:
    assert len(extrair_mensagem("x" * 5000)) == rede.LIMITE_MENSAGEM_API


def test_extrair_mensagem_de_json_sem_mensagem_util() -> None:
    assert extrair_mensagem('{"outro": 1}') == '{"outro": 1}'


@pytest.mark.parametrize(
    ("codigo", "esperado"),
    [
        (404, SemDados),
        (400, RecusaDaApi),
        (422, RecusaDaApi),
        (500, FalhaDeRede),
        (503, FalhaDeRede),
        (429, FalhaDeRede),
    ],
)
def test_classificacao_de_erro_http(codigo: int, esperado: type[Exception]) -> None:
    erro = rede._erro_da_api(_erro_http(codigo, b'{"error": "detalhe"}'))
    assert isinstance(erro, esperado)


def test_baixar_com_tentativas_repete_falha_transitoria() -> None:
    tentativas: list[int] = []

    def baixar_uma(url: str, *, timeout: float) -> str:
        tentativas.append(1)
        if len(tentativas) < 3:
            raise FalhaDeRede("caiu")
        return "ok"

    vistos: list[float] = []
    resultado = baixar_com_tentativas("https://x", dormir=vistos.append, baixar_uma=baixar_uma)
    assert resultado == "ok"
    assert len(tentativas) == 3
    assert vistos == [1.0, 2.0]  # espera exponencial


def test_baixar_com_tentativas_desiste_apos_o_limite() -> None:
    def baixar_uma(url: str, *, timeout: float) -> str:
        raise FalhaDeRede("sempre cai")

    with pytest.raises(FalhaDeRede, match="falha após 2 tentativa"):
        baixar_com_tentativas(
            "https://x", tentativas=2, dormir=lambda _: None, baixar_uma=baixar_uma
        )


@pytest.mark.parametrize("erro", [SemDados("404"), RecusaDaApi(400, "ruim")])
def test_baixar_com_tentativas_nao_repete_erro_de_pedido(erro: Exception) -> None:
    chamadas: list[int] = []

    def baixar_uma(url: str, *, timeout: float) -> str:
        chamadas.append(1)
        raise erro

    with pytest.raises(type(erro)):
        baixar_com_tentativas("https://x", dormir=lambda _: None, baixar_uma=baixar_uma)
    assert len(chamadas) == 1


@pytest.mark.parametrize("tentativas", [0, -1])
def test_baixar_com_tentativas_recusa_limite_invalido(tentativas: int) -> None:
    with pytest.raises(ValueError):
        baixar_com_tentativas("https://x", tentativas=tentativas)


def test_baixar_usa_o_downloader_padrao_e_devolve_texto(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resposta:
        def __enter__(self) -> Resposta:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, limite: int) -> bytes:
            return json.dumps({"ok": True}).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resposta())
    assert json.loads(baixar("https://x", timeout=1.0)) == {"ok": True}


@pytest.mark.parametrize(
    ("erro", "esperado"),
    [
        (_erro_http(404), SemDados),
        (_erro_http(400), RecusaDaApi),
        (_erro_http(500), FalhaDeRede),
    ],
)
def test_baixar_traduz_erro_http(
    monkeypatch: pytest.MonkeyPatch, erro: Exception, esperado: type[Exception]
) -> None:
    def urlopen(*_: object, **__: object) -> object:
        raise erro

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(esperado):
        baixar("https://x", timeout=1.0)


def test_baixar_traduz_falha_de_conexao(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(*_: object, **__: object) -> object:
        raise TimeoutError("estourou o tempo")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(FalhaDeRede):
        baixar("https://x", timeout=1.0)


def test_baixar_recusa_resposta_grande_demais(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resposta:
        def __enter__(self) -> Resposta:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, limite: int) -> bytes:
            return b"x" * (limite + 1)

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resposta())
    with pytest.raises(FalhaDeRede, match="limite"):
        baixar("https://x", timeout=1.0)


def test_baixar_recusa_resposta_nao_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resposta:
        def __enter__(self) -> Resposta:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, limite: int) -> bytes:
            return b"\xff\xfe\x00\x00"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resposta())
    with pytest.raises(FalhaDeRede, match="UTF-8"):
        baixar("https://x", timeout=1.0)
