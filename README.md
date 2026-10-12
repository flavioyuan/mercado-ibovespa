# mercado-ibovespa

Baixa e alinha, numa janela parametrizável, as séries diárias de:

- **todas as ações que compõem o Ibovespa**, mais o **índice** (`^BVSP`);
- a **taxa Selic** efetiva anualizada;
- o **câmbio BRL/USD** (dólar comercial de venda).

Todas as séries saem num **calendário comum, com exatamente o mesmo número de
observações**. Quando falta a publicação de uma data, a lacuna é preenchida
**repetindo a última observação** — *last observation carried forward*. É a base
de dados para os modelos econométricos de ações, juros e câmbio.

## De onde vem cada série

| Série | Fonte | Identificação | Unidade | Frequência |
| --- | --- | --- | --- | --- |
| Ações do Ibovespa | Yahoo Finance | `PETR4.SA`, `VALE3.SA`, ... | R$ (ajustado) | dias de pregão |
| Índice Ibovespa | Yahoo Finance | `^BVSP` | pontos | dias de pregão |
| Selic | SGS/BCB | série **1178** — Selic efetiva anualizada base 252 | % ao ano | dias úteis |
| Câmbio | SGS/BCB | série **1** — dólar americano comercial (venda) | R$/US$ | dias úteis |
| Composição do índice | B3 | `GetPortfolioDay` (`IBOV`) | — | por rebalanceamento |

Três decisões de fonte que valem registro:

1. **Selic 1178**, e não a meta do Copom (432). A 1178 é a taxa efetiva
   anualizada, na unidade em que o mercado cita juros, e cada ponto é uma
   observação real de mercado — enquanto a meta é um degrau repetido inclusive em
   fins de semana, ruim para modelagem. É a mesma série usada pelo projeto
   `taxa-juros` deste workspace, o que mantém os dois estudos consistentes.
2. **Câmbio série 1** (dólar comercial de venda), a referência para conversão e
   para modelagem cambial.
3. **Preço ajustado** (*adjusted close*) por padrão, e não o preço de fechamento
   bruto: o ajuste corrige proventos e desdobramentos, e sem ele um dividendo
   apareceria como perda de capital no retorno. Use `--campo close` para o bruto.

## Uso

O projeto é gerenciado pelo [uv](https://docs.astral.sh/uv/) e **não tem
dependências de runtime** — só a biblioteca padrão:

```console
$ uv sync
$ uv run mercado-ibovespa -v
```

```console
INFO mercado_ibovespa.cli: janela: 2024-10-06 a 2026-10-06 (731 dias corridos)
INFO mercado_ibovespa.cli: composição: 76 ações — B3 — GetPortfolioDay (carteira do dia)
INFO mercado_ibovespa.cli: [1/77] IBOVESPA: 498 observações
...
INFO mercado_ibovespa.cli: SELIC: 502 observações (Selic efetiva anualizada base 252 — SGS 1178, % ao ano)
INFO mercado_ibovespa.cli: CAMBIO: 502 observações (Dólar americano comercial (venda) — SGS 1, R$/US$)
INFO mercado_ibovespa.cli: alinhamento: 79 séries x 502 observações (calendário 'uniao', de 2024-10-07 a 2026-10-06)
INFO mercado_ibovespa.cli:   IBOVESPA: 502 observações (498 publicadas, 4 repetidas, 0 retrocedidas) | último 205835.2969, média 152622.7317, faixa [118533.0000, 206912.0000]
...
WARNING mercado_ibovespa.cli: NATU3: 178 observação(ões) no início da janela foram retrocedidas (o papel ainda não tinha preço em 2024-10-07); há olhar para o futuro nesse trecho
INFO mercado_ibovespa.cli: gravado: dados/mercado_2024-10-06_2026-10-06.csv
INFO mercado_ibovespa.cli: gravado: dados/mercado_2024-10-06_2026-10-06_imputacoes.csv
INFO mercado_ibovespa.cli: gravado: dados/mercado_2024-10-06_2026-10-06.json
```

(Transcrição real de uma execução em 06/10/2026.)

### Opções

| Opção | Padrão | Descrição |
| --- | --- | --- |
| `--meses N` | `24` | Horizonte em meses até `--fim` (1 a 120) |
| `--inicio DATA` | — | Data inicial explícita; substitui `--meses` |
| `--fim DATA` | hoje | Data final, no fuso local |
| `--composicao {b3,embutida}` | `b3` | Carteira do dia na B3 ou lista congelada no código |
| `--arquivo-composicao CAMINHO` | — | CSV/JSON/texto com os códigos; substitui `--composicao` |
| `--tickers CODIGO...` | — | Códigos extras além da composição |
| `--calendario {uniao,interseccao,indice}` | `uniao` | Como escolher o calendário comum |
| `--campo {adjclose,close}` | `adjclose` | Preço ajustado ou bruto |
| `--saida DIR` | `dados` | Diretório dos arquivos |
| `--casas-decimais N` | `10` | Casas decimais da matriz (0 a 15) |
| `--timeout SEGUNDOS` | `20` | Tempo limite por requisição |
| `--tentativas N` | `3` | Tentativas em falha transitória (1 a 10) |
| `--ignorar-ausentes` | desligado | Segue sem as séries sem dados, em vez de falhar |
| `--dry-run` | desligado | Baixa e resume, mas não grava nada |
| `-q`, `--quiet` | desligado | Só avisos e erros no log |

Datas são aceitas como `dd/mm/aaaa` ou `aaaa-mm-dd`. Como a janela entra no nome
dos arquivos, vários horizontes convivem no mesmo diretório:

```console
$ uv run mercado-ibovespa --meses 12 --saida dados/12m
$ uv run mercado-ibovespa --inicio 01/01/2022 --fim 31/12/2024 --saida dados/longo
$ uv run mercado-ibovespa --calendario indice --campo close
```

## Painel gráfico (Streamlit)

Para **observar a evolução das séries**, o projeto traz um painel que lê a matriz
alinhada e desenha os gráficos. Ele não baixa nada: primeiro rode o downloader,
depois o painel.

```console
$ uv run --extra painel streamlit run src/mercado_ibovespa/painel.py
```

```console
  You can now view your Streamlit app in your browser.
  URL: http://127.0.0.1:8501
```

O `streamlit`, o `altair` e o `pandas` ficam no extra opcional `painel`: quem só
quer baixar os dados continua sem nenhuma dependência de runtime.

### As quatro abas

| Aba | O que mostra |
| --- | --- |
| **Evolução** | A série de cada ativo ao longo do tempo, com resumo do período (nível, faixa, retorno e volatilidade) |
| **Retornos** | Retorno logarítmico acumulado, distribuição dos retornos diários e ranking de retorno e risco |
| **Correlação** | Mapa de calor das correlações entre os retornos das séries escolhidas |
| **Qualidade dos dados** | Quanto de cada série foi publicado pela fonte e quanto foi preenchido por repetição, mais os avisos e a proveniência da execução |

### Controles

- **Diretório e arquivo** — a lista traz todas as matrizes `mercado_*.csv` do
  diretório, da janela mais recente para a mais antiga. Como a janela entra no nome
  do arquivo, comparar horizontes diferentes é trocar de item na lista.
- **Período** — recorte de datas dentro do arquivo escolhido.
- **Séries** — quais colunas entram nos gráficos.
- **Valores** — `Base 100`, que rebaseia cada série no primeiro dia do recorte, ou
  `Valor original`, com a escala logarítmica opcional.

Rebasear em 100 é o que resolve o problema de escala deste conjunto: o Ibovespa
está em 200 000 pontos, o câmbio em R$ 5 e a Selic em % ao ano. Com cada série
começando em 100, o eixo passa a ser variação percentual acumulada e as três ficam
comparáveis na mesma linha.

A correlação é calculada sobre **log-retornos diários**, nunca sobre os níveis de
preço: duas séries com tendência apareceriam quase perfeitamente correlacionadas
mesmo sem relação alguma — o caso clássico de regressão espúria.

### Onde fica a lógica

Toda a conta está em `src/mercado_ibovespa/visualizacao.py`, que é **pura**: não
importa `pandas`, `altair` nem `streamlit`. Ler o CSV, rebasear, calcular retorno,
volatilidade anualizada e correlação são funções testáveis offline, e o painel
(`painel.py`) só monta widgets e desenha. Os testes do painel usam o `AppTest` do
próprio Streamlit e são pulados automaticamente quando o extra não está instalado.

## O contrato central: mesmo número de observações

O problema é que as fontes não publicam nos mesmos dias: a B3 não abre em 24 e 31
de dezembro, o BCB publica nesses dias, e um papel que entrou no índice no meio da
janela simplesmente não tem preço no começo dela. Em vez de tentar fazer cada
fonte coincidir, o programa inverte o problema:

1. escolhe um **calendário comum** (`montar_grade`);
2. reindexa **todas** as séries nele, repetindo a última observação nas lacunas
   (`alinhar`).

O resultado tem, por construção, **uma linha por data do calendário e uma coluna
por série** — 79 séries × 502 observações na execução de exemplo.

### O calendário comum

| Estratégia | O que é | Observações (janela de 24 meses) |
| --- | --- | --- |
| `uniao` **(padrão)** | União das datas observadas em todas as séries | **502** |
| `interseccao` | Só datas presentes em **todas** as séries | 498 |
| `indice` | As datas do próprio `^BVSP` (calendário de pregão da B3) | 498 |

A união é o padrão porque **não descarta nenhuma observação de nenhuma fonte**. Em
dados da B3 ela é melhor do que uma grade de segunda a sexta: como toda ação só é
negociada em pregão, a união das datas é o calendário real de negociação — o
programa **não inventa linha em feriado**.

O efeito colateral aparece nas 4 datas que existem no BCB mas não têm pregão:
**24/12/2024, 31/12/2024, 24/12/2025 e 31/12/2025**. Nelas as 77 séries de mercado
repetem a véspera (marcadas como `repetido`). São 4 de 502 observações (0,8 %). Se
isso incomodar o modelo, use `--calendario indice` ou `--calendario interseccao`
para ficar só com os 498 pregões.

### Como cada valor é marcado

| `situacao` | Significado |
| --- | --- |
| `observado` | Valor publicado pela fonte naquela data |
| `repetido` | Lacuna preenchida com o **último** valor observado |
| `retrocidido` | Lacuna **no início** da série, antes da primeira observação: repetiu-se o **primeiro** valor |

O caso `retrocidido` merece atenção porque é o único com **olhar para o futuro**:
não existe "último valor" antes do primeiro dado, então a lacuna inicial recebe o
primeiro preço — e isso é uma extrapolação para trás. Na execução de exemplo,
**NATU3** tem 178 datas retrocedidas (o papel só passou a ter preço em meados de
2025): quem estimar retornos deve filtrar essas linhas ou usar
`--arquivo-composicao` para excluir o papel. O programa avisa sempre que isso
acontece.

### Arquivos gerados

Três arquivos por execução, todos com a janela no nome:

**`mercado_<inicio>_<fim>.csv`** — a matriz larga, pronta para o modelo:

```csv
data,IBOVESPA,ABEV3,ALOS3,...,YDUQ3,SELIC,CAMBIO
2024-10-07,132018.0000000000,11.5270271301,18.8349876404,...,10.6500000000,5.4626000000
2024-10-08,131512.0000000000,11.4482545853,18.8016910553,...,10.6500000000,5.5197000000
```
Colunas: `data` (ISO 8601), `IBOVESPA`, as ações em ordem alfabética, `SELIC` e
`CAMBIO`. Valores com 10 casas decimais, ajustáveis em `--casas-decimais`.

**`mercado_<inicio>_<fim>_imputacoes.csv`** — as células que não vieram de
publicação, no formato longo. É a lista para filtrar quando o preenchimento
incomodar (484 linhas na execução de exemplo):

```csv
data,serie,situacao
2024-12-24,IBOVESPA,repetido
2024-12-31,IBOVESPA,repetido
2025-12-24,IBOVESPA,repetido
2025-12-31,IBOVESPA,repetido
2024-12-24,ABEV3,repetido
...
2024-10-07,NATU3,retrocidido
```

As linhas saem agrupadas por série, na mesma ordem das colunas da matriz.

**`mercado_<inicio>_<fim>.json`** — a proveniência: janela, calendário, como a
composição foi obtida, resumo por série (publicadas, repetidas, retrocedidas,
cobertura, mínimo, máximo, média, último, primeira e última data real) e os
avisos.

### Códigos de saída

| Código | Significado |
| --- | --- |
| `0` | Sucesso |
| `1` | Entrada inválida: data, janela, opção ou arquivo de composição |
| `2` | Falha de rede ou de comunicação com B3, Yahoo Finance ou SGS/BCB |
| `3` | Dados ausentes ou fora do formato esperado |
| `4` | Falha ao gravar os arquivos de saída |

## A composição do Ibovespa

O Ibovespa é rebalanceado a cada quatro meses, então "as ações que compõem o
índice" só faz sentido com uma data de referência. O programa resolve isso em três
níveis:

1. **`--composicao b3` (padrão)** — consulta o `GetPortfolioDay` da B3 e usa a
   carteira do dia (76 ações em 06/10/2026). É a fonte oficial.
2. **`--arquivo-composicao CAMINHO`** — CSV, JSON ou texto com os códigos. É a
   saída para quem precisa da composição de uma data passada ou da **união das
   carteiras do período**, que a B3 não expõe por API.
3. **`--composicao embutida`** — a carteira de `2026-10-06` congelada em
   `src/mercado_ibovespa/composicao.py`. É também o *fallback* automático quando a
   B3 está fora do ar, e o que torna a execução reproduzível.

Todo código é validado contra o formato da B3 (`^[A-Z][A-Z0-9]{3}\d{1,2}$`, que
aceita desde `PETR4` até `B3SA3`) antes de virar símbolo do Yahoo.

## O que o script valida

As respostas das três APIs são **entrada não confiável** e passam por checagem
antes de virar arquivo:

- **SGS** — JSON bem formado; lista não vazia de `{data, valor}`; data
  `dd/mm/aaaa` existente no calendário; valor numérico, finito, positivo e dentro
  do plausível para a série (guarda específica: 1000 % a.a. para a Selic, R$ 100
  para o câmbio); sem datas repetidas; nada fora da janela.
- **Yahoo** — JSON bem formado; erro do Yahoo propagado com a mensagem; barras
  presentes; `adjclose` disponível quando pedido; datas e valores com o mesmo
  tamanho; preço finito e positivo; carimbo de tempo convertido para a data local
  da bolsa; nada fora da janela.
- **B3** — JSON bem formado; lista de ativos presente; código no formato da bolsa
  (o resto é descartado e contado).

Falhas de rede são repetidas até `--tentativas` vezes com espera exponencial
(1 s, 2 s, ...), mas erros de pedido (HTTP 400/404) **não** são repetidos —
insistir não mudaria a resposta. Um símbolo sem dados não derruba a execução: ele
é registrado e, no fim, ou o programa falha listando todos, ou segue sem eles com
`--ignorar-ausentes`.

## Avisos que o script emite

- **Cobertura baixa** — menos de 70 % da grade veio de fato da fonte.
- **Retroação** — houve lacuna no início da série preenchida com o primeiro valor
  (o caso NATU3, com olhar para o futuro).
- **Defasagem** — a última observação real é mais de 7 dias anterior ao fim da
  janela.

## Limitações conhecidas

- **Viés de sobrevivência na composição.** A carteira do dia contém só as 76 ações
  de hoje: papéis que saíram do índice durante a janela não aparecem. Para
  eliminá-lo, monte um arquivo com a união das carteiras do período e passe em
  `--arquivo-composicao`.
- **Datas só do BCB.** Com o calendário `uniao`, as 4 datas de 24/12 e 31/12
  existem na matriz com preço repetido para as 77 séries de mercado. Veja
  `--calendario indice` e `--calendario interseccao`.
- **Última linha possivelmente parcial.** O Yahoo devolve a barra do pregão em
  andamento; se a execução ocorrer durante o pregão, a última observação não é um
  fechamento. O Yahoo às vezes anexa também uma cotação ao vivo carimbada com o
  horário corrente; o parser colapsa as duas na mesma data, ficando com a mais
  recente.
- **Repetir o último valor cria trechos de variância zero**, principalmente nos
  feriados encadeados (Carnaval, Corpus Christi, fim de ano). Para análises
  sensíveis a isso, cruze com a lista de imputações e filtre `situacao`.
- **A Selic é um degrau** (% ao ano, publicada em dias úteis) e o câmbio é um
  nível; as três famílias de séries estão em escalas diferentes (pontos de índice,
  R$ por ação, % a.a., R$/US$). Para modelar em conjunto, o caminho natural é
  trabalhar com log-retornos — que este script não calcula, ele entrega os níveis.
- **Feriados não são identificados.** O calendário vem das datas observadas, não
  de uma tabela de feriados: um feriado da B3 em que o BCB publica aparece como
  data com preço repetido, não como data ausente.
- **O painel é somente leitura.** Ele visualiza a matriz gravada e não estima
  modelos: não há regressão, VAR, cointegração nem GARCH. A estimação é o passo
  seguinte, sobre o mesmo CSV.

## Desenvolvimento

```console
$ uv run pytest                           # 238 testes, sem rede
$ uv run --extra painel pytest            # 247, incluindo os 9 do painel
$ uv run pytest -m integracao -v          # 5 testes de integração, baixam de verdade
$ uv run --extra painel pytest --cov      # cobertura (95,4 %)
$ uv run ruff check . && uv run ruff format --check .
$ uv run --extra painel mypy src tests
$ uv run bandit -c pyproject.toml -r src
```

A lógica pura (`janela.py`, `composicao.py`, `sgs.py`, `yahoo.py`,
`alinhamento.py`, `relatorio.py`, `visualizacao.py`, `modelo.py`) não toca em rede,
disco nem relógio; o download vive em `rede.py`, a gravação em `saida.py`, os
gráficos em `painel.py` e a orquestração em `cli.py`. Os testes de parsing usam
payloads no formato real das três APIs e os testes de CLI injetam um *downloader*
falso — a suíte padrão roda offline.
