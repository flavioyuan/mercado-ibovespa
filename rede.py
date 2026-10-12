"""Download HTTP: a única parte do programa que toca a rede.

Responsabilidades: montar a requisição, impor tempo limite, manter a verificação
TLS ligada, limitar o tamanho da resposta, classificar a falha (parâmetro
recusado, sem dados, ou problema transitório) e repetir apenas o que faz sentido
repetir.

O mesmo downloader serve às duas fontes do programa — a API de gráfico do Yahoo
Finance e o SGS do Banco Central —, porque as duas respondem JSON em HTTPS e as
duas merecem o mesmo tratamento de tempo limite, repetição e validação de tamanho.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.client import HTTPException
from typing import Protocol

logger = logging.getLogger(__name__)

AGENTE_HTTP = "mercado-ibovespa/0.1 (script de pesquisa)"
TEMPO_LIMITE_PADRAO_S = 20.0
TENTATIVAS_PADRAO = 3
ESPERA_BASE_S = 1.0
# A resposta da B3 e a do Yahoo cabem folgadamente neste limite; ele existe para
# que uma resposta hostil ou truncada não consuma memória sem limite.
TAMANHO_MAXIMO_RESPOSTA_BYTES = 16 * 1024 * 1024
LIMITE_MENSAGEM_API = 300
STATUS_SEM_DADOS = 404
STATUS_RETENTAVEIS = frozenset({408, 425, 429})


class FalhaDeRede(Exception):
    """Problema transitório de comunicação: timeout, conexão, 5xx ou corpo ilegível."""


class RecusaDaApi(Exception):
    """A API recusou a consulta por causa dos parâmetros enviados (HTTP 4xx)."""

    def __init__(self, status: int, detalhe: str) -> None:
        mensagem = f"a API recusou a consulta (HTTP {status})"
        if detalhe:
            mensagem = f"{mensagem}: {detalhe}"
        super().__init__(mensagem)
        self.status = status
        self.detalhe = detalhe


class SemDados(Exception):
    """A fonte não tem dados para o que foi pedido (HTTP 404)."""


class Downloader(Protocol):
    """Assinatura do downloader, para permitir injeção nos testes."""

    def __call__(self, url: str, *, timeout: float) -> str: ...


def baixar(url: str, *, timeout: float = TEMPO_LIMITE_PADRAO_S) -> str:
    """Baixa ``url`` e devolve o corpo como texto.

    Args:
        url: URL montada pelos módulos de fonte, nunca texto livre do usuário.
        timeout: Tempo limite da conexão e da leitura, em segundos.

    Returns:
        O corpo da resposta decodificado como UTF-8.

    Raises:
        RecusaDaApi: A API recusou os parâmetros (HTTP 4xx que não seja 404).
        SemDados: Não há dados para o pedido (HTTP 404).
        FalhaDeRede: Timeout, erro de conexão, 5xx, corpo grande demais ou não UTF-8.
    """
    requisicao = urllib.request.Request(
        url,
        headers={"User-Agent": AGENTE_HTTP, "Accept": "application/json"},
    )
    # Verificação de certificado e de hostname sempre ligada: jamais desligar.
    contexto = ssl.create_default_context()
    try:
        # A URL vem dos montadores de URL: base https constante mais um código,
        # símbolo ou parâmetro já validado. Não há esquema variável.
        with urllib.request.urlopen(  # nosec B310
            requisicao, timeout=timeout, context=contexto
        ) as resposta:
            # urlopen é anotado como Any no typeshed; fixar bytes evita que o Any
            # se propague para o retorno desta função.
            dados: bytes = resposta.read(TAMANHO_MAXIMO_RESPOSTA_BYTES + 1)
    except urllib.error.HTTPError as erro:
        # HTTPError é subclasse de URLError/OSError: precisa vir antes do except geral.
        raise _erro_da_api(erro) from erro
    except OSError as erro:
        # Cobre URLError, TimeoutError, ConnectionError, SSLError e afins.
        raise FalhaDeRede(f"não foi possível completar a requisição: {erro}") from erro

    if len(dados) > TAMANHO_MAXIMO_RESPOSTA_BYTES:
        raise FalhaDeRede(f"resposta maior que o limite de {TAMANHO_MAXIMO_RESPOSTA_BYTES} bytes")
    try:
        return dados.decode("utf-8")
    except UnicodeDecodeError as erro:
        raise FalhaDeRede("a resposta não é texto UTF-8 válido") from erro


def baixar_com_tentativas(
    url: str,
    *,
    tentativas: int = TENTATIVAS_PADRAO,
    timeout: float = TEMPO_LIMITE_PADRAO_S,
    dormir: Callable[[float], None] = time.sleep,
    baixar_uma: Downloader = baixar,
) -> str:
    """Baixa a URL repetindo falhas transitórias com espera exponencial.

    Args:
        url: URL da consulta.
        tentativas: Número máximo de tentativas; precisa ser ao menos 1.
        timeout: Tempo limite de cada tentativa, em segundos.
        dormir: Função de espera, injetável para os testes.
        baixar_uma: Downloader usado em cada tentativa.

    Returns:
        O corpo da resposta da primeira tentativa bem-sucedida.

    Raises:
        ValueError: Se ``tentativas`` for menor que 1.
        RecusaDaApi: Imediatamente, sem repetir (o problema é o pedido, não a rede).
        SemDados: Imediatamente, sem repetir.
        FalhaDeRede: Se todas as tentativas falharem.
    """
    if tentativas < 1:
        raise ValueError(f"tentativas deve ser ao menos 1, recebido {tentativas}")

    ultima: FalhaDeRede | None = None
    for tentativa in range(1, tentativas + 1):
        try:
            return baixar_uma(url, timeout=timeout)
        except FalhaDeRede as erro:
            ultima = erro
            if tentativa == tentativas:
                break
            espera = ESPERA_BASE_S * 2 ** (tentativa - 1)
            logger.warning(
                "tentativa %d/%d falhou (%s); aguardando %.0fs",
                tentativa,
                tentativas,
                erro,
                espera,
            )
            dormir(espera)

    raise FalhaDeRede(f"falha após {tentativas} tentativa(s): {ultima}") from ultima


def extrair_mensagem(corpo: str) -> str:
    """Extrai uma mensagem curta e de linha única do corpo de erro da API.

    As APIs respondem erros em JSON (``{"error": ...}``, ``{"erro": {"detail": ...}}``)
    ou em texto puro. O conteúdo é externo e não confiável: é reduzido a uma linha
    e truncado antes de aparecer em qualquer log.

    Args:
        corpo: Corpo bruto da resposta de erro.

    Returns:
        Mensagem sanitizada, ou string vazia se nada de útil for encontrado.
    """
    try:
        dados = json.loads(corpo)
    except json.JSONDecodeError:
        return _sanitizar(corpo)

    if isinstance(dados, dict):
        for chave in ("error", "mensagem", "message", "detail"):
            valor = dados.get(chave)
            if isinstance(valor, str) and valor.strip():
                return _sanitizar(valor)
        aninhado = dados.get("erro")
        if isinstance(aninhado, dict):
            detalhe = aninhado.get("detail")
            if isinstance(detalhe, str) and detalhe.strip():
                return _sanitizar(detalhe)
    return _sanitizar(corpo)


def _sanitizar(texto: str) -> str:
    """Reduz o texto a uma linha e limita o tamanho."""
    return " ".join(texto.split())[:LIMITE_MENSAGEM_API]


def _erro_da_api(erro: urllib.error.HTTPError) -> Exception:
    """Classifica um erro HTTP da API na exceção correspondente."""
    detalhe = extrair_mensagem(_ler_corpo(erro))
    sufixo = f": {detalhe}" if detalhe else ""
    if erro.code == STATUS_SEM_DADOS:
        return SemDados(f"a fonte não tem dados para este pedido (HTTP 404){sufixo}")
    if 400 <= erro.code < 500 and erro.code not in STATUS_RETENTAVEIS:
        return RecusaDaApi(erro.code, detalhe)
    return FalhaDeRede(f"HTTP {erro.code}{sufixo}")


def _ler_corpo(erro: urllib.error.HTTPError) -> str:
    """Lê o corpo de uma resposta de erro sem deixar outra falha escapar."""
    try:
        return erro.read(TAMANHO_MAXIMO_RESPOSTA_BYTES).decode("utf-8", errors="replace")
    except (OSError, HTTPException):
        return ""
