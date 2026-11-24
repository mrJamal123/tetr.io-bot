# Bot de TETR.IO para Windows

Bot experimental em Python que reconhece o tabuleiro, hold e fila do TETR.IO
por captura de tela, calcula uma jogada e pode enviar as teclas correspondentes.

> **Uso:** somente em partidas Solo ou Custom para teste. Não use em partidas
> multiplayer/ranqueadas nem de forma que viole as regras do TETR.IO.

## Estado do projeto

- Leitura visual do tabuleiro 20x10, peça ativa, hold e próximas peças com
  `mss` e `numpy`.
- Busca de jogadas com hold, beam search, regras SRS, T-spins, openers e
  planejamento de Perfect Clear.
- Execução de teclas pelo Windows `SendInput` e modo de simulação `--seco`.
- Testes automatizados e um benchmark offline de estratégias/beam.

As metas de latência abaixo de 30 ms, 2+ PPS reais e os valores competitivos de
APM/APP são **objetivos, não resultados garantidos**. O desempenho ao vivo
depende da máquina, da captura de tela, da fila de peças e do tempo de envio
das teclas. O modelo de ataque do simulador é uma aproximação e não foi
validado contra a versão atual das regras do TETR.IO.

## Requisitos

- Windows 10/11.
- Python 3.9+.
- Dependências Python listadas em `requirements.txt`.
- TETR.IO aberto e com os controles configurados conforme a seção abaixo para
  executar o bot ao vivo.

## Instalação

No PowerShell, na pasta do projeto:

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Uso

Comece pelo modo seguro, que calcula e mostra as jogadas sem pressionar teclas:

```powershell
py -3.9 player.py --seco
```

Para executar em uma partida Solo/Custom:

```powershell
py -3.9 player.py
```

Por padrão, a leitura usa o tabuleiro central. Para o layout esquerdo:

```powershell
py -3.9 player.py --layout esquerda
```

O arquivo `jogar_esquerda.bat` também inicia esse layout. Outras opções úteis:

```powershell
py -3.9 player.py --modo blitz --seco
py -3.9 player.py --sem-especular --seco
py -3.9 player.py --envio-sincrono --seco
py -3.9 tetrio_projeto.py --debug-board
```

O ritmo aparece no `jogar_log.txt`: `pps=` a cada STATUS e, no fim,
`pps_medio`, `pps_15s`, `atk_peca` e `atk_segundo`. A captura e o envio de teclas
rodam em threads; `--envio-sincrono` volta ao envio travando o laço.

Antes do modo ao vivo, confirme que os controles do jogo correspondem ao bot:

| Ação | Tecla |
| --- | --- |
| Esquerda / direita | Numpad 1 / Numpad 3 |
| Soft drop / hard drop | Numpad 2 / Espaço |
| Girar anti-horário / horário / 180° | Z / Numpad 5 / A |
| Hold | C |

O jogo precisa estar em foco. As coordenadas e cores da captura são calibradas
para o layout de tela configurado em `tetrio_projeto.py`; use as opções de
diagnóstico antes de alterar essa configuração.

## Início de cada partida

O bot detecta a partida nova (tabuleiro vazio + hold vazio + fila nova) e mostra
`>>> NOVA PARTIDA detectada: 3... 2... 1... aguardando o GO` e depois
`>>> GO! Jogando`. Não aperta nenhuma tecla durante a contagem. Se a detecção
automática falhar, **F8** reinicia o estado do bot, **F7** pausa e **F9** encerra.

Uma pilha de altura 17 ou mais não encerra a partida: o decisor continua enquanto
existir colocação alcançável. Se não houver rota e a tela ainda mostrar essa pilha
com peça ativa e fila reconhecidas, o bot envia um hard drop de emergência uma
única vez por avanço da fila e aguarda confirmação (não repete a tecla em loop).

## Estratégia atual

Uma única política vale do início ao fim da partida; não existe "modo de
emergência" que troque ataque por sobrevivência.

- **Altura é o objetivo principal.** `W["soma"] = 0,26` e `W["altura"] = 3,0`,
  com a rampa começando em `ALTURA_ALVO = 4`. `ALTURA_DOWNSTACK = 7` é a altura
  a partir da qual limpar linhas, mesmo sem spin, passa a ser a jogada correta;
  `ALTURA_MANOBRA = 10` zera qualquer valor de setup.
- **Slot de TSD é construído o tempo todo.** `_buracos_efetivos` perdoa a casa
  intencional do slot nos estágios 2 e 3, `_bonus_tspin_setup_estagio` vale
  mesmo sem T à vista (fator `FATOR_SEM_T`), sempre limitado pelo headroom, e
  `CUSTO_GASTAR_T` evita gastar o T deitado com slot montado.
- **T-spin obrigatório quando está na mesa.** `_filtrar_tspin_obrigatorio`
  restringe as jogadas do T aos T-spins full de 2–3 linhas quando algum é
  alcançável por SRS.
- **B2B protegido só com a pilha baixa.** Abaixo de `ALTURA_DOWNSTACK` os
  filtros `_filtrar_clear_fraco` e `_filtrar_quebra_b2b` seguram clears secos de
  1–3 linhas; acima disso eles não agem e `CUSTO_QUEBRA_B2B` cai a zero em
  `ALTURA_PERIGO`.
- **Um poço de 1 coluna, no máximo.** `_penalidade_pocos_um` cobra
  `CUSTO_POCO_PROFUNDO = 8` por casa acima de 2 e `W["poco_extra"] = 6` a partir
  do segundo poço; `_filtrar_pocos_novos` recusa a colocação que cria o segundo.
- **Sem dependência de I ou de combo.** Não há poço de borda nem 4-wide: o bot
  não espera o I para atacar e não sacrifica a pilha para emendar clears fracos.
- **Busca com plano.** `LARGURA = (3, 2, 2)`: quatro peças de horizonte com
  alternativas em vez de uma linha única; `LARGURA_PCO = (2, 1, 1, 1, 1)` na
  abertura quando a bag está confirmada.

O detalhe de cada constante está em `decisor.py`, com comentários junto delas.

## Testes e benchmark

Suíte (164 testes):

```powershell
py -3.9 -m unittest discover -s tests -p "test_*.py"
```

Benchmark offline (sem o jogo). Sem `--largura`/`--modo` ele varre 8 beams nos
dois modos, o que demora; para medir uma configuração só:

```powershell
py -3.9 benchmark_decisor.py --pecas 300 --seeds 0 1 2 3 4 5 `
    --pressao 0 --modo ataque --largura 3,2,2
```

`--pressao` é a taxa de lixo recebido por peça (0 = sem lixo). O relatório traz
ataque por peça (`atk/pc`), linhas, clears por tipo, spins, B2B mantido/quebrado,
altura máxima, altura média, p95 da altura, buracos criados, openers
abandonados, sobreviventes e latência p50/p95 das decisões. `alt_med` e `alt_p95`
são médias das simulações; `altura` e `buracosmax` são o pior caso entre elas.
Compare versões sempre com as mesmas seeds, pressão e largura.

### Números atuais

Comando acima com seeds 0–5 (1800 peças por pressão), medidos em 23/11/2026:

| pressão | peças | vivas | atk/pc | linhas/peça | altura média | p95 altura | pior pico | TSD/TSS/TST | spins/bag | B2B mantido | B2B máx | p50/p95 decisão |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 1800 | 6/6 | 0,325 | 0,39 | 6,1 | 9 | 19 | 34/10/0 | 0,17 | 47,9% | 6 | 9,5/20,9 ms |
| 0,1 | 1800 | 6/6 | 0,277 | 0,40 | 6,2 | 9 | 16 | 21/12/0 | 0,13 | 40,5% | 5 | 9,6/21,1 ms |
| 0,3 | 1557 | 4/6 | 0,353 | 0,52 | 7,1 | 12 | 20 | 15/5/0 | 0,09 | 25,1% | 5 | 9,6/20,8 ms |

Com pressão 0,3 duas partidas terminam antes das 300 peças; `linhas/peça` e
`atk/pc` sobem porque incluem as linhas de lixo limpas. O ataque do simulador é
uma aproximação da tabela do TETR.IO, não uma medida do jogo. A latência varia
entre execuções na mesma máquina (a mesma configuração já deu p50 9,9 / p95
22,6 ms), então compare as métricas de jogo, não o wall-clock.

Leitura dos números: a pilha fica baixa na maior parte do tempo (média 6, p95 9),
mas o pico de 19 mostra que ainda há trechos de empilhamento alto — é isso que
mata as partidas sob lixo. O T-spin por bag (~0,17 sem lixo) ainda não é o
"um TSD por bag" de um jogador de alto nível.

## Limitações conhecidas

- **Falta um planejador de fábrica de TSD.** O slot é construído e executado
  quando aparece (34 TSD em 1800 peças, sem lixo), mas o bot chega ao estágio 3
  em poucas decisões. Para TSD a cada bag é preciso planejar a cadeia de 2–3
  peças, como `openers.planejar` faz para o DT Cannon — não basta ajustar pesos.
- **TST não está ligado.** O tabuleiro de TST testado não gera T-spin triple com
  a tabela de kicks atual; falta confirmar a tabela do TETR.IO antes de premiar
  o slot fundo.
- **O modelo de lixo do simulador é simplificado** (uma linha por unidade de
  pressão, 30% de chance de trocar a coluna do buraco) e o ataque não passa por
  Surge/garbage real.
- **Nada foi validado ao vivo** neste ambiente: não há Windows nem TETR.IO aqui.

## Estrutura

| Arquivo | Responsabilidade |
| --- | --- |
| `player.py` | Entrada do bot |
| `jogar.py` | Controle do ciclo ao vivo, hold e envio de teclas |
| `tetrio_projeto.py` | Captura e leitura da tela |
| `decisor.py` | Simulação de tabuleiro e escolha de jogada |
| `openers.py` | Moldes e reconhecimento de openers |
| `probability.py` | Utilitários de probabilidade da bag |
| `benchmark_decisor.py` | Comparação offline de beams/modos |
| `medir_ataque.py` | Ferramenta auxiliar para medir ataque |
| `tests/` | Testes automatizados |

## Histórico de ajustes rejeitados

Ideias já medidas e descartadas — evite refazê-las. Os números de cada uma
vieram da versão em que foram testadas e não são reproduzíveis pelo benchmark
atual:

- **Bônus de downstack, bônus de progresso de slot e filtro duro de altura**
  pioraram altura ou ataque; a altura já é cobrada pelo avaliador.
- **Bloquear todo clear fraco fora da zona de risco** derrubou o ataque e o TSD:
  segurar a peça não vira T-spin, vira pilha alta e despejo depois.
- **Aumentar o valor do slot geométrico** (sem T disponível) só incentivou
  empilhar; `BONUS_TSPIN_SETUP` alto só compensou quando passou a exigir T-spin
  alcançável por SRS.
- **Cortar o ataque de combo** não mudou as jogadas escolhidas — o combo não
  consumia clears no simulador.
- **PCO como molde de células** derrubou o ataque para perto de zero: o Perfect
  Clear Opener exige a ordem exata das peças, não parecença de células.
- **Molde de DT Cannon com peso/horizonte maior** piorou o ataque e não
  completou nenhum DT; a busca de feixe não planeja a ordem de 7–10 peças.
- **Poço de 1 coluna e 4-wide** como estratégia de ataque: o bot morria quando o
  I não vinha. Bloquear *qualquer* poço novo também foi pior (zerou os Tetris);
  a regra atual é um poço, não dois.
- **Memoizar `_buracos`/`_slots_detalhes`** não deu ganho: os tabuleiros
  avaliados são quase todos distintos.
- **Aceitar leitura "suja" como pilha** (para destravar o topo) adotava lixo de
  tela como tabuleiro; a reconciliação continua exigindo duas divergências.

## Licença

Nenhuma licença foi escolhida ainda. A publicação do código no GitHub, por si
só, não concede permissão para reutilizá-lo; escolha uma licença antes de
aceitar contribuições ou permitir redistribuição.
