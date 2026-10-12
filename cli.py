"""Interface de linha de comando: orquestra download, alinhamento e gravação.

O programa é a junção de três fontes e de um calendário comum:

1. a **composição** do Ibovespa (B3 ao vivo, com *fallback* para a lista embutida);
2. as **ações e o índice**, no Yahoo Finance;
3. a **Selic** e o **câmbio**, no SGS do Banco Central;
4. o **alinhamento** de tudo num calendário único, com o mesmo número de
   observações por série.

Uso típico::

    mercado-ibovespa -v
    mercado-ibovespa --meses 12 --saida dados/12m
    mercado-ibovespa --inicio 2024-01-02 --fim 2025-12-31
    mercado-ibovespa --calendario indice --campo close

O horário de referência "hoje" é o do sistema; todo o resto é determinístico e
recebe as datas por parâmetro, o que mantém a lógica pura testável.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import NoReturn

from mercado_ibovespa import alinhamento, composicao, rede, relatorio, saida, sgs, yahoo
from mercado_ibovespa.alinhamento import Estrategia, Fonte, GradeInvalida
from mercado_ibovespa.composicao import CodigoInvalido, ComposicaoInvalida
from mercado_ibovespa.janela import JanelaInvalida, dias_corridos, resolver_janela
from mercado_ibovespa.rede import Downloader, RecusaDaApi, SemDados
from mercado_ibovespa.sgs import RespostaInvalida as RespostaInvalidaSgs

logger = logging.getLogger(__name__)

# Códigos de saída. Cada um tem um significado único, para que um script chamador
# saiba o que fazer sem interpretar a mensagem.
SAIDA_OK = 0
SAIDA_ENTRADA = 1
SAIDA_REDE = 2
SAIDA_DADOS = 3
SAIDA_ESCRITA = 4

FORMATOS_DE_DATA = ("%d/%m/%Y", "%Y-%m-%d")
COMPOSICAO_AO_VIVO = "b3"
COMPOSICAO_EMBUTIDA = "embutida"
CASAS_DECIMAIS_MINIMAS = 0
CASAS_DECIMAIS_MAXIMAS = 15
TENTATIVAS_MAXIMAS = 10

_EPILOGO = """\
códigos de saída:
  0  sucesso
  1  entrada inválida (data, janela, opções ou arquivo de composição)
  2  falha de rede ou de comunicação com B3, Yahoo Finance ou SGS/BCB
  3  dados ausentes ou fora do formato esperado
  4  falha ao gravar os arquivos de saída

exemplos:
  mercado-ibovespa -v
  mercado-ibovespa --meses 6 --saida dados/6m
  mercado-ibovespa --inicio 01/01/2022 --fim 31/12/2024 --calendario interseccao
  mercado-ibovespa --arquivo-composicao carteira_2023.csv --campo close
"""


class EntradaInvalida(ValueError):
    """Os argumentos de linha de comando não formam um pedido válido."""


class _AnalisadorDeArgumentos(argparse.ArgumentParser):
    """Analisador que trata erro de uso como entrada inválida (código 1).

    Sem isso, o ``argparse`` encerraria com código 2, que neste programa significa
    falha de rede — e o chamador não teria como distinguir os dois casos.
    """

    def error(self, message: str) -> NoReturn:
        raise EntradaInvalida(message)


def _data(texto: str) -> date:
    """Converte ``dd/mm/aaaa`` ou ``aaaa-mm-dd`` em :class:`datetime.date`."""
    for formato in FORMATOS_DE_DATA:
        try:
            return datetime.strptime(texto.strip(), formato).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"data inválida: {texto!r}; use dd/mm/aaaa ou aaaa-mm-dd")


def _interpretar_argumentos(argv: Sequence[str] | None) -> argparse.Namespace:
    """Monta o analisador e interpreta a linha de comando."""
    analisador = _AnalisadorDeArgumentos(
        prog="mercado-ibovespa",
        description=(
            "Baixa e alinha as séries diárias do Ibovespa, de todas as ações que o "
            "compõem, da taxa Selic e do câmbio BRL/USD, com o mesmo número de "
            "observações em todas as séries."
        ),
        epilog=_EPILOGO,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analisador.add_argument(
        "--meses",
        type=int,
        default=24,
        metavar="N",
        help="horizonte em meses até --fim (1 a 120; padrão: 24)",
    )
    analisador.add_argument(
        "--inicio",
        type=_data,
        default=None,
        metavar="DATA",
        help="data inicial explícita; substitui --meses (dd/mm/aaaa ou aaaa-mm-dd)",
    )
    analisador.add_argument(
        "--fim",
        type=_data,
        default=None,
        metavar="DATA",
        help="data final (padrão: hoje, no fuso local)",
    )
    analisador.add_argument(
        "--composicao",
        choices=(COMPOSICAO_AO_VIVO, COMPOSICAO_EMBUTIDA),
        default=COMPOSICAO_AO_VIVO,
        help="origem da lista de ações: B3 ao vivo ou lista embutida (padrão: b3)",
    )
    analisador.add_argument(
        "--arquivo-composicao",
        type=Path,
        default=None,
        metavar="CAMINHO",
        help="CSV, JSON ou texto com os códigos; substitui --composicao",
    )
    analisador.add_argument(
        "--tickers",
        nargs="+",
        default=[],
        metavar="CODIGO",
        help="códigos extras a incluir além da composição (ex.: CVCB3)",
    )
    analisador.add_argument(
        "--calendario",
        choices=[estrategia.value for estrategia in Estrategia],
        default=Estrategia.UNIAO.value,
        help="calendário comum: uniao (padrão), interseccao ou indice",
    )
    analisador.add_argument(
        "--campo",
        choices=yahoo.CAMPOS,
        default=yahoo.CAMPO_AJUSTADO,
        help="preço das ações e do índice: adjclose (padrão) ou close",
    )
    analisador.add_argument(
        "--saida",
        type=Path,
        default=Path("dados"),
        metavar="DIR",
        help="diretório dos arquivos de saída (padrão: dados)",
    )
    analisador.add_argument(
        "--casas-decimais",
        type=int,
        default=saida.CASAS_DECIMAIS,
        metavar="N",
        help=f"casas decimais da matriz (0 a {CASAS_DECIMAIS_MAXIMAS}; "
        f"padrão: {saida.CASAS_DECIMAIS})",
    )
    analisador.add_argument(
        "--timeout",
        type=float,
        default=rede.TEMPO_LIMITE_PADRAO_S,
        metavar="SEGUNDOS",
        help=f"tempo limite por requisição (padrão: {rede.TEMPO_LIMITE_PADRAO_S:.0f})",
    )
    analisador.add_argument(
        "--tentativas",
        type=int,
        default=rede.TENTATIVAS_PADRAO,
        metavar="N",
        help=f"tentativas em falha transitória (1 a {TENTATIVAS_MAXIMAS}; "
        f"padrão: {rede.TENTATIVAS_PADRAO})",
    )
    analisador.add_argument(
        "--ignorar-ausentes",
        action="store_true",
        help="segue sem as séries que não têm dados, em vez de falhar",
    )
    analisador.add_argument(
        "--dry-run",
        action="store_true",
        help="baixa e resume, mas não grava arquivo nenhum",
    )
    analisador.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="só avisos e erros no log",
    )
    return analisador.parse_args(argv)


def _configurar_log(quiet: bool) -> None:
    """Liga o log de diagnóstico; a informação vai para stderr."""
    logging.basicConfig(
        level=logging.WARNING if quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _hoje() -> date:
    """Data de hoje no fuso local — a única leitura de relógio do programa."""
    return date.today()


def _validar_argumentos(argumentos: argparse.Namespace) -> None:
    """Valida as faixas numéricas que o ``argparse`` não checa.

    Raises:
        EntradaInvalida: Se alguma opção estiver fora da faixa aceita.
    """
    if not CASAS_DECIMAIS_MINIMAS <= argumentos.casas_decimais <= CASAS_DECIMAIS_MAXIMAS:
        raise EntradaInvalida(
            f"--casas-decimais deve estar entre {CASAS_DECIMAIS_MINIMAS} e "
            f"{CASAS_DECIMAIS_MAXIMAS}, recebido {argumentos.casas_decimais}"
        )
    if argumentos.timeout <= 0:
        raise EntradaInvalida(f"--timeout deve ser positivo, recebido {argumentos.timeout}")
    if not 1 <= argumentos.tentativas <= TENTATIVAS_MAXIMAS:
        raise EntradaInvalida(
            f"--tentativas deve estar entre 1 e {TENTATIVAS_MAXIMAS}, "
            f"recebido {argumentos.tentativas}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada do programa.

    Args:
        argv: Linha de comando sem o nome do programa; usa :data:`sys.argv` se
            for ``None``.

    Returns:
        O código de saída: 0 sucesso, 1 entrada inválida, 2 falha de rede,
        3 dados inválidos ou ausentes, 4 falha de gravação.
    """
    try:
        argumentos = _interpretar_argumentos(argv)
    except EntradaInvalida as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return SAIDA_ENTRADA

    _configurar_log(argumentos.quiet)
    try:
        return _executar(argumentos)
    except EntradaInvalida as erro:
        logger.error("entrada inválida: %s", erro)
        return SAIDA_ENTRADA
    except JanelaInvalida as erro:
        logger.error("janela inválida: %s", erro)
        return SAIDA_ENTRADA
    except (CodigoInvalido, ComposicaoInvalida) as erro:
        logger.error("composição inválida: %s", erro)
        return SAIDA_ENTRADA
    except (rede.FalhaDeRede, RecusaDaApi) as erro:
        logger.error("falha de comunicação: %s", erro)
        return SAIDA_REDE
    except (SemDados, GradeInvalida, RespostaInvalidaSgs, yahoo.RespostaInvalida) as erro:
        logger.error("dados indisponíveis ou inválidos: %s", erro)
        return SAIDA_DADOS
    except saida.EscritaFalhou as erro:
        logger.error("falha ao gravar: %s", erro)
        return SAIDA_ESCRITA


def _executar(
    argumentos: argparse.Namespace,
    *,
    baixar_uma: Downloader = rede.baixar,
) -> int:
    """Executa o download completo e a gravação.

    Args:
        argumentos: Opções já interpretadas.
        baixar_uma: Downloader de uma tentativa; injetável para os testes.

    Returns:
        O código de saída.

    Raises:
        EntradaInvalida, JanelaInvalida, CodigoInvalido, ComposicaoInvalida,
        FalhaDeRede, RecusaDaApi, SemDados, GradeInvalida, RespostaInvalida,
        EscritaFalhou: Conforme a etapa que falhar; ``main`` traduz cada uma no
        código de saída correspondente.
    """
    _validar_argumentos(argumentos)

    fim = argumentos.fim if argumentos.fim is not None else _hoje()
    inicio, fim = resolver_janela(fim=fim, inicio=argumentos.inicio, meses=argumentos.meses)
    janela = (inicio, fim)
    logger.info("janela: %s a %s (%d dias corridos)", inicio, fim, dias_corridos(inicio, fim))

    codigos, info_composicao = _resolver_composicao(
        argumentos,
        baixar_uma=baixar_uma,
    )
    if argumentos.tickers:
        extras = composicao.combinar([argumentos.tickers])
        codigos = composicao.combinar([codigos, extras])
        logger.info("acrescentadas %d ação(ões) via --tickers", len(extras))
    info_composicao["acoes"] = len(codigos)
    logger.info("composição: %d ações — %s", len(codigos), info_composicao["fonte"])

    fontes, ausentes = _baixar_mercado(
        codigos,
        janela,
        campo=argumentos.campo,
        baixar_uma=baixar_uma,
        timeout=argumentos.timeout,
        tentativas=argumentos.tentativas,
        ignorar_ausentes=argumentos.ignorar_ausentes,
    )
    info_composicao["ausentes"] = list(ausentes)

    fontes.extend(
        _baixar_macro(
            (sgs.SERIE_SELIC, sgs.SERIE_CAMBIO),
            janela,
            baixar_uma=baixar_uma,
            timeout=argumentos.timeout,
            tentativas=argumentos.tentativas,
        )
    )

    conjunto = alinhamento.alinhar_conjunto(
        fontes,
        Estrategia(argumentos.calendario),
        referencia=yahoo.NOME_INDICE,
    )
    resumos = relatorio.resumir_conjunto(conjunto)
    avisos = relatorio.gerar_avisos(conjunto, resumos)

    logger.info(
        "alinhamento: %d séries x %d observações (calendário '%s', de %s a %s)",
        len(resumos),
        conjunto.observacoes,
        conjunto.estrategia.value,
        conjunto.grade[0],
        conjunto.grade[-1],
    )
    for resumo in resumos:
        logger.info("  %s", resumo.linha())
    for aviso in avisos:
        logger.warning("%s", aviso)

    if argumentos.dry_run:
        logger.info("--dry-run: nenhum arquivo foi gravado")
        return SAIDA_OK

    caminhos = saida.caminhos_saida(argumentos.saida, inicio, fim)
    saida.escrever_matriz(caminhos.matriz, conjunto, casas_decimais=argumentos.casas_decimais)
    saida.escrever_imputacoes(caminhos.imputacoes, conjunto)
    saida.escrever_json(
        caminhos.metadados,
        relatorio.montar_metadados(
            janela=janela,
            conjunto=conjunto,
            resumos=resumos,
            avisos=avisos,
            composicao=info_composicao,
            gerado_em=datetime.now(),
        ),
    )
    logger.info("gravado: %s", caminhos.matriz)
    logger.info("gravado: %s", caminhos.imputacoes)
    logger.info("gravado: %s", caminhos.metadados)
    return SAIDA_OK


def _resolver_composicao(
    argumentos: argparse.Namespace,
    *,
    baixar_uma: Downloader,
) -> tuple[tuple[str, ...], dict[str, object]]:
    """Obtém a lista de ações a baixar e descreve de onde ela veio.

    Args:
        argumentos: Opções já interpretadas.
        baixar_uma: Downloader de uma tentativa.

    Returns:
        Os códigos e o bloco de proveniência que vai para os metadados.

    Raises:
        ComposicaoInvalida: Se o arquivo indicado não puder ser lido.
        CodigoInvalido: Se ``--tickers`` trouxer código fora do formato da B3.
    """
    if argumentos.arquivo_composicao is not None:
        codigos = composicao.carregar_composicao(argumentos.arquivo_composicao)
        proveniencia: dict[str, object] = {
            "fonte": "arquivo",
            "arquivo": str(argumentos.arquivo_composicao),
            "data_referencia": None,
            "observacao": (
                "a carteira do Ibovespa muda a cada quatro meses; um arquivo próprio "
                "permite usar a composição de uma data passada ou a união das carteiras "
                "do período, o que reduz o viés de sobrevivência"
            ),
        }
        return codigos, proveniencia

    embutida: dict[str, object] = {
        "fonte": "lista embutida no código",
        "arquivo": None,
        "data_referencia": composicao.DATA_REFERENCIA_EMBUTIDA,
        "observacao": (
            "carteira congelada na data de referência; use --composicao b3 para a "
            "carteira do dia ou --arquivo-composicao para uma carteira histórica"
        ),
    }
    if argumentos.composicao == COMPOSICAO_EMBUTIDA:
        return composicao.COMPOSICAO_EMBUTIDA, embutida

    try:
        payload = rede.baixar_com_tentativas(
            composicao.montar_url_b3(),
            tentativas=argumentos.tentativas,
            timeout=argumentos.timeout,
            baixar_uma=baixar_uma,
        )
        codigos = composicao.parsear_composicao_b3(payload)
    except (rede.FalhaDeRede, RecusaDaApi, SemDados, ComposicaoInvalida) as erro:
        logger.warning(
            "não foi possível obter a composição na B3 (%s); usando a lista embutida de %s",
            erro,
            composicao.DATA_REFERENCIA_EMBUTIDA,
        )
        return composicao.COMPOSICAO_EMBUTIDA, {
            **embutida,
            "fonte": "lista embutida no código (fallback da B3)",
            "motivo_fallback": str(erro),
        }

    carta_corrente: dict[str, object] = {
        "fonte": "B3 — GetPortfolioDay (carteira do dia)",
        "arquivo": None,
        "data_referencia": None,
        "observacao": (
            "carteira corrente: ações que saíram do índice antes desta data não "
            "aparecem (viés de sobrevivência); use --arquivo-composicao para corrigir"
        ),
    }
    return codigos, carta_corrente


def _baixar_mercado(
    codigos: Sequence[str],
    janela: tuple[date, date],
    *,
    campo: str,
    baixar_uma: Downloader,
    timeout: float,
    tentativas: int,
    ignorar_ausentes: bool,
) -> tuple[list[Fonte], tuple[str, ...]]:
    """Baixa o índice e todas as ações no Yahoo Finance.

    Um símbolo problemático não derruba a execução: ele é registrado e, no fim, ou
    o programa falha listando todos, ou segue sem eles com ``--ignorar-ausentes``.

    Args:
        codigos: Códigos da B3 das ações.
        janela: Janela ``(inicio, fim)``.
        campo: ``adjclose`` ou ``close``.
        baixar_uma: Downloader de uma tentativa.
        timeout: Tempo limite por requisição.
        tentativas: Tentativas por requisição.
        ignorar_ausentes: Se ``True``, segue sem os símbolos sem dados.

    Returns:
        As fontes baixadas e os nomes dos símbolos ausentes.

    Raises:
        SemDados: Se houver símbolo sem dados e ``ignorar_ausentes`` for ``False``.
    """
    inicio, fim = janela
    alvos: list[tuple[str, str]] = [(yahoo.NOME_INDICE, yahoo.SIMBOLO_INDICE)]
    alvos.extend((codigo, yahoo.simbolo_de_codigo(codigo)) for codigo in codigos)

    fontes: list[Fonte] = []
    ausentes: list[str] = []
    total = len(alvos)
    for posicao, (nome, simbolo) in enumerate(alvos, start=1):
        url = yahoo.montar_url(simbolo, inicio, fim)
        try:
            payload = rede.baixar_com_tentativas(
                url, tentativas=tentativas, timeout=timeout, baixar_uma=baixar_uma
            )
            pontos = yahoo.parsear_serie(payload, campo=campo, janela=janela)
        except (SemDados, yahoo.RespostaInvalida) as erro:
            logger.error("%s (%s): sem dados utilizáveis — %s", nome, simbolo, erro)
            ausentes.append(nome)
            continue
        logger.info("[%d/%d] %s: %d observações", posicao, total, nome, len(pontos))
        fontes.append(Fonte(nome=nome, origem=f"{yahoo.FONTE} {simbolo}", pontos=tuple(pontos)))

    if ausentes and not ignorar_ausentes:
        raise SemDados(
            "sem dados para: "
            + ", ".join(ausentes)
            + " (use --ignorar-ausentes para seguir sem essas séries)"
        )
    return fontes, tuple(ausentes)


def _baixar_macro(
    chaves: Sequence[str],
    janela: tuple[date, date],
    *,
    baixar_uma: Downloader,
    timeout: float,
    tentativas: int,
) -> list[Fonte]:
    """Baixa as séries macro (Selic e câmbio) no SGS do Banco Central.

    Args:
        chaves: Chaves das séries no catálogo do SGS.
        janela: Janela ``(inicio, fim)``.
        baixar_uma: Downloader de uma tentativa.
        timeout: Tempo limite por requisição.
        tentativas: Tentativas por requisição.

    Returns:
        As fontes baixadas, nomeadas pela chave em maiúsculas (``SELIC``, ``CAMBIO``).

    Raises:
        KeyError: Se a chave não existir no catálogo.
        FalhaDeRede, RecusaDaApi, SemDados, RespostaInvalida: Conforme a falha.
    """
    inicio, fim = janela
    fontes: list[Fonte] = []
    for chave in chaves:
        serie = sgs.obter_serie(chave)
        payload = rede.baixar_com_tentativas(
            sgs.montar_url(serie.codigo, inicio, fim),
            tentativas=tentativas,
            timeout=timeout,
            baixar_uma=baixar_uma,
        )
        pontos = sgs.parsear_observacoes(payload, janela=janela, valor_maximo=serie.valor_maximo)
        logger.info("%s: %d observações (%s)", serie.chave.upper(), len(pontos), serie)
        fontes.append(Fonte(nome=serie.chave.upper(), origem=serie.origem, pontos=tuple(pontos)))
    return fontes


if __name__ == "__main__":
    sys.exit(main())
