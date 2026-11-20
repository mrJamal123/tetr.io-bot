# Bot de TETR.IO para Windows

Bot experimental em Python que reconhece o tabuleiro, hold e fila do TETR.IO
por captura de tela, calcula uma jogada e pode enviar as teclas correspondentes.

> **Uso:** somente em partidas Solo ou Custom para teste. Não use em partidas
> multiplayer/rankeadas nem de forma que viole as regras do TETR.IO.

## Estado do projeto

O código atual contém:

- Leitura visual do tabuleiro 20x10, peça ativa, hold e próximas peças com
  `mss` e `numpy`.
- Busca de jogadas com hold, beam search, regras SRS, T-spins, openers e
  planejamento de Perfect Clear.
- Execução de teclas pelo Windows `SendInput` e modo de simulação `--seco`.
- Testes automatizados e um benchmark offline de estratégias/beam.

As metas de decisão abaixo de 30 ms, 2+ PPS reais e os valores competitivos de
APM/APP são **objetivos, não resultados garantidos**. O desempenho real depende
da máquina, da captura de tela, da fila de peças e do tempo de envio das teclas.
O modelo de ataque também deve ser validado contra a versão atual das regras do
jogo antes de ser considerado fiel ao TETR.IO Tetra League S2.

## Requisitos

- Windows 10/11.
- Python 3.9.
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
rodam em threads; `--envio-sincrono` volta ao envio travando o laco.


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

## Topo

O tabuleiro modelado tem 20 linhas. A altura 17 ativa a avaliação de emergência,
mas não declara fim de jogo: o decisor continua enquanto existir uma colocação
alcançável. Uma pilha de altura 18 ainda pode ser jogada; o teste cobre um I que
limpa quatro linhas nessa situação. Se não houver rota e a tela ainda mostrar
uma pilha de altura 17 ou mais com a peça ativa e a fila reconhecidas, o bot
envia um hard drop de emergência uma única vez por avanço da fila e aguarda
confirmação; não repete a tecla em loop.

## Testes e benchmark

Execute a suíte:

```powershell
py -3.9 -m unittest discover -s tests -p "test_*.py"
```

Compare os beams e modos do simulador offline:

```powershell
py -3.9 benchmark_decisor.py --pecas 10 --seeds 0
```

O benchmark percorre várias larguras de beam e modos, então pode demorar em
máquinas mais lentas. Compare versões com as mesmas sementes e parâmetros.
Ele já reporta ataque por peça, clears, spins, B2B, combos, altura e buracos;
PPS/APM equivalentes e estatísticas específicas de garbage ainda precisam ser
adicionados para medir todas as metas propostas.

## Estrutura

| Arquivo | Responsabilidade |
| --- | --- |
| `player.py` | Entrada do bot |
| `jogar.py` | Controle do ciclo ao vivo, hold e envio de teclas |
| `tetrio_projeto.py` | Captura e leitura da tela |
| `decisor.py` | Simulação de tabuleiro e escolha de jogada |
| `openers.py` | Moldes e reconhecimento de openers |
| `probability.py` | Utilitários de probabilidade da bag |
| `benchmark_decisor.py` | Comparação offline de beams |
| `medir_ataque.py` | Ferramenta auxiliar para medir ataque |
| `tests/` | Testes automatizados |
| [`docs/PROMPT_PROXIMA_IA.md`](docs/PROMPT_PROXIMA_IA.md) | Prompt de continuidade para a próxima IA |

## Mudanças recentes (foco em T-spin)

- O avaliador tratava o overhang de um slot de TSD pronto como buraco (penalidade
  de 32 + 5 por célula coberta, e o filtro `_filtrar_buracos_novos` descartava a
  jogada). Por isso o slot nunca chegava ao estágio 3 e o bot quase só fazia
  Tetris. Agora `_buracos_efetivos` desconta a casa coberta pelo overhang de cada
  slot pronto (só com spins ligados).
- `ESTAGIO` passou de `(0, 0.4, 0.7, 1.0)` para `(0, 0.5, 0.9, 1.6)` e `W["slot"]`
  de 30 para 45, para o slot pronto valer mais que o poço de quad.
- Novo `FATOR_BONUS_TSS`; a antiga espera de 200 ms após limpar linhas (antes
  configurável por `--espera-limpar`) foi removida.
- Medição offline (16 seeds x 250 peças, mesmas seeds antes/depois; não representa
  APM/PPS reais): sem pressão, ataque por peça 0,385 -> 0,459, TSD 0 -> 77,
  sobreviventes 15/16 -> 16/16; com pressão 0,12, 0,368 -> 0,420, TSD 0 -> 69,
  sobreviventes 11/16 -> 13/16. Latência p50 ~4 ms -> ~5 ms, p95 ~8 -> ~11 ms.
- A métrica `holes_created` do simulador conta o overhang do slot como buraco,
  então ela subiu de propósito.

## Mudanças recentes (início de rodada e estilo)

- **Começo de partida travado (corrigido).** A peça ativa nasce dentro do recorte do
  tabuleiro (`SPAWN_LINHAS`), então `Bot.observar_inicio_rodada` exigia "nenhuma peça em
  voo" e praticamente nunca confirmava o início. Pior: qualquer sobra colorida da tela
  (contagem, anúncio de rodada, partículas) junto da peça fazia `RastreadorPilha`
  devolver `sem_sync`, deixando `pilha=None` para sempre — e `Bot.passo` retorna antes
  de decidir quando não há pilha nem modelo. O bot ficava parado até a fila avançar
  sozinha (ou seja, até uma peça sua ser jogada).
  - `RastreadorPilha._sincronizar` agora reconhece a peça de spawn mesmo com outras
    cores na tela (`_peca_do_spawn`), descarta sobras acima da faixa de spawn e, quando
    não há nada abaixo da faixa, trata a leitura como pilha vazia (evento `sync_topo`).
  - `observar_inicio_rodada` aceita a peça nas linhas de spawn como início de rodada e
    volta a detectar rodada nova depois da primeira (modelo zerado + tabuleiro vazio).
  - `passo` guarda a peça inicial (HOLD INICIAL) assim que a tela mostra tabuleiro
    vazio, sem esperar os 1,5 s de confirmação.
- **Estilo de jogo: ajustes testados e revertidos.** Nenhum peso novo passou no
  benchmark, então as constantes ficaram como estavam (nada declarado como melhoria):
  - `BONUS_TSPIN_SETUP_STAGE` `{1:3, 2:6}` -> `{1:5, 2:9}` (construir o slot de TSD
    mais cedo): 0,400 -> 0,397 de ataque por peça em 5 seeds x 60 peças (neutro).
  - `ALTURA_SEGURA` 12 -> 10 (jogar mais baixo): altura máxima 13 -> 11 e buracos
    novos 12 -> 11, mas ataque 136 -> 132 em 10 seeds x 40 peças (-3%, dentro do ruído
    por seed, que varia de 7 a 20 unidades de ataque por rodada).
  - `W["rug"]` 0,65 -> 0,9 e `W["dip"]` 1,8 -> 2,4 (harmonia de campo): ataque
    0,358 -> 0,283 em 3 seeds x 40 peças (pior).
  Conclusão: para mudar o padrão de jogo de verdade (TSD contínuo estilo jogador de
  alto nível) falta planejamento de cadeia, não ajuste de peso — ver Próximos objetivos.

## Spins como o TETR.IO conta (medido)

Regra do jogo ([wiki](https://tetrio.wiki.gg/wiki/Spins#Spin_Detection)):

- **T-spin**: a última ação foi uma rotação e pelo menos 3 dos 4 cantos diagonais estão
  ocupados; é `full` se os 2 cantos da frente estão ocupados **ou** se usou o 5º kick,
  senão é `mini`.
- **Outras peças** (All-Mini+, padrão desde a Beta 1.5.0): só contam spin se, **depois da
  rotação**, a peça estiver imóvel — não anda para a esquerda, direita, baixo **nem para
  cima**. O teste de cima (overhang) é o que define o spin.

O bot checava esquerda/direita/baixo e **não checava a subida**, então girava no último
segundo em espaço aberto e contava como spin (B2B falsa). Corrigido em `_spin_all_mini`.
Consequência medida (20 seeds x 30 peças):

| | ataque | TSD | B2B +/− | buracos+ | hmax |
| --- | --- | --- | --- | --- | --- |
| com a detecção errada | 0,293 | 17 | 44/9 | 19 | 14 |
| com a regra do TETR.IO | 0,255 | 16 | 8/9 | 21 | 14 |

Ou seja: os 44 “B2B” anteriores eram em boa parte spins falsos que o jogo não pagaria. O
ataque do simulador cai 13% porque a cadeia real é mais difícil — o caminho agora é
construir TSD/TST de verdade, não maquiar rotação.

## B2B só com spin (medido)

Com cadeia ativa e fora da zona de risco, single/double/triple **sem spin** deixam de ser
jogados enquanto existir alternativa (spin, quad ou jogada sem clear): `_filtrar_clear_fraco`.
Na zona de risco (`ALTURA_SEGURA = 12` em diante) o filtro e o custo de quebra somem — ali o
B2B não manda mais, limpa o que der.

Medição (20 seeds x 30 peças):

| | ataque | TSD | quads | clears fracos | buracos+ | hmax | B2B +/− |
| --- | --- | --- | --- | --- | --- | --- | --- |
| sem o filtro | 0,335 | 20 | 3 | 129 | 25 | 14 | 34/18 |
| com o filtro | 0,293 | 17 | 5 | 107 | 19 | 14 | **44/9** |

A cadeia quebra metade das vezes (18 -> 9), menos clear fraco e menos buraco, ao custo de
~12% de ataque no simulador (ele segura a peça esperando spin em vez de despejar single).

## Sem dependência de peça I (medido)

O bot parava de jogar para montar poço de 1 coluna / 4-wide esperando o I — e morria
quando o I não vinha. Agora: `USAR_POCOS_I = False` desliga toda a estratégia de poço
(`_bonus_poco_um`, `_perfil_4wide`, `_4wide_lanes`, `_bonus_pocos_combo` e o filtro de
clear 4-wide na busca), `W["one_wide"]`, `W["four_wide"]`, `W["poco"]` e `W["quad_run"]`
foram a zero, e `_penalidade_pocos_um` passou a cobrar `CUSTO_POCO_PROFUNDO = 8.0` por casa
acima de 2 em **qualquer** coluna de 1 de largura (bordas incluídas).

Medição (20 seeds x 30 peças = 600 peças, mesmas seeds):

| | ataque | TSD | quads | buracos+ | hmax | linhas | B2B +/− |
| --- | --- | --- | --- | --- | --- | --- | --- |
| antes (poço de I valia nota) | 0,277 | 9 | 14 | 12 | 15 | 169 | 28/9 |
| agora (sem dependência de I) | **0,323** | **20** | 3 | 26 | **14** | 177 | 32/18 |

Ou seja: +17% de ataque, TSD 9 -> 20, quad 14 -> 3 (o Tetris de poço praticamente sumiu) e
pilha final mais baixa; o custo é mais buracos criados (12 -> 26) porque ele não reserva
mais canal largo para despejar peça.

## Inicio de rodada, top-out e dependências (medido)

- **Começar sem descer peça.** `Bot._inicio_visual` passou a aceitar tabuleiro vazio **ou** só
  com a peça no spawn mal lida (até 4 células na faixa de spawn), e o `HOLD INICIAL` também
  dispara quando o bot ainda não jogou nada e a tela mostra no máximo 5 linhas (cobre o
  fantasma/ghost lido no fundo). O log agora explica a espera:
  `AGUARDANDO: peca ativa nao identificada (pilha=.., voo=.., hold=.., fila=.., jogadas=..)`.
- **Nunca se declarar morto.** Duas pausas permanentes foram removidas:
  1. `decidir` sem jogada não zera mais as entradas até “nova rodada”: o bot descarta o modelo,
     volta para a leitura da tela e tenta de novo (log a cada 10 tentativas), para sempre.
  2. “fila sem avancar” não pausa mais até reiniciar: se o estado da fila estiver válido
     (`_estado_atual_confiavel`), ele solta o `alvo` e ressincroniza sozinho.
- **Poços de 1 coluna demais.** Cada poço extra (só o I cobre) custa `W["poco_extra"]=6` por
  casa (até 4) e profundidade acima de 4 no principal custa `CUSTO_POCO_FUNDO=2.5`/casa
  (`_penalidade_pocos_um`). Fica isento quando existe canal 4-wide com base (`quatro_pronto`),
  onde a coluna do fundo faz parte da estratégia.
  Medição (20 seeds x 30 peças): ataque 0,277 -> 0,280, TSD 9, quads 14, buracos+ 12, hmax 15,
  linhas 169 -> 171 — neutro offline (o simulador quase não gera 2+ poços simultâneos), com
  teste unitário garantindo o custo (`test_multiple_one_wide_wells_cost_more_than_a_single_one`).

## Clear e B2B (medido)

- **Sem esperar depois de limpar linha.** O `sleep` fixo de 200 ms já não existia nesta
  versão; o que restava era o bot **parar de decidir**: (1) um frame perdido em
  `ultima_limpas` e (2) `passo` retornava False enquanto `usando_modelo` (a animação do
  clear deixa a leitura instável por alguns frames). Agora o clear confirmado não bloqueia:
  a jogada seguinte usa o modelo já confirmado e sai na hora. Continua esperando quando a
  leitura está instável **sem** clear recente (teste novo cobre os dois casos).
- **B2B não é mais cancelado cedo.** `CUSTO_QUEBRA_B2B` 4 -> 10, com multiplicador de
  carga (`1 + 0,5 x (4 - b2b)`) enquanto a cadeia está abaixo de 4 (antes do disparo do
  Surge) e sem penalizar Perfect Clear. Filtro de quebra mantido no antigo: o filtro mais
  restritivo testado subiu buracos (7 -> 8/9) e altura (13 -> 15).
  Medição (20 seeds x 30 peças = 600 peças, mesmas seeds):
  | config | ataque | TSD | quads | buracos+ | hmax | B2B mantido | quebras | linhas |
  | --- | --- | --- | --- | --- | --- | --- | --- | --- |
  | custo 4 (antigo) | 179 | 9 | 14 | 12 | 15 | 61% | 14 | 180 |
  | custo 10 (novo) | 166 | 9 | 14 | 12 | 15 | **76%** | **9** | 169 |
  A cadeia é mantida 15 p.p. mais e quebra 36% menos, ao custo de ~7% de ataque no
  simulador (o bot deixa de despejar clears normais). `CUSTO_QUEBRA_B2B` é o botão único:
  4 = mais ataque, 10 = mais cadeia.

## Otimização e openers (medido)

- **Perfil do decisor** (`cProfile`, 20 peças, caches quentes): `colocacoes_alcancaveis`
  (BFS exato do T) = ~46% do tempo; `_tentar_rotacao` 0,26 s; `_buracos` 9.935 chamadas;
  `_slots_detalhes` 8.806 chamadas. Latência por CPU time (seeds 0-4 x 20 peças):
  p50 ~62 ms, p95 ~125 ms; em wall-clock o p50 oscila 70-265 ms conforme a carga da
  máquina, então compare sempre CPU time ou métricas, não wall-clock.
- **Micro-otimizações testadas e revertidas:** memoizar `_buracos`/`_slots_detalhes`
  (lru_cache por tabuleiro) e trocar o dict de `livre` do BFS por tabela fixa deram
  CPU total 6,33 s com e sem cache (0% de ganho — os tabuleiros avaliados são quase
  todos distintos, a cache não acerta). Revertidas para não deixar peso morto.
- **Especulação corrigida (ganho real de latência no início da rodada):**
  `Bot._especular` montava a chave com `bag=None`, mas `passo` decide com a bag
  prevista. Como a sétima peça só é dedutível na primeira bag, a jogada especulada era
  **sempre descartada** justamente no começo da partida. Agora a bag entra na chave e na
  chamada: teste `test_bag_prevista_nao_descarta_a_decisao_especulada` dá
  (acertos, erros) = (1, 0); com o código antigo dava (0, 1).
- **Openers: o molde DT nunca completa.** Em 10 seeds x 30 peças o simulador registra
  10 tentativas e **10 abandonos** (0 DT completos) em todas as configurações:
  | config | ataque/peça | TSD | DT completo |
  | --- | --- | --- | --- |
  | padrão (peso 0,25 / horizonte 5) | 0,330 | 6 | 0/10 |
  | peso 0,40 | 0,273 | 4 | 0/10 |
  | horizonte 6 | 0,263 | 6 | 0/10 |
  | peso 0,40 + horizonte 6 | 0,240 | 5 | 0/10 |
  | sem DT (`OPENERS=False`) | 0,330 | 7 | — |
  Aumentar o peso/horizonte piora o ataque; o molde só mede "parecença de células" e a
  busca (feixe 1, horizonte 5) não planeja a ordem de ~7-10 peças que o DT exige.
- **PCO como molde de células: falhou e foi revertido.** Adicionei o molde padrão do
  Perfect Clear Opener (28 células, 4 linhas, conferido peça por peça) com peso 0,7:
  ataque 0,330 -> 0,070, TSD 3 -> 0, PC 0 em 100 decisões. PCO exige ordem exata das
  peças; prêmio por células só faz o bot perseguir um molde que não fecha.
  Conclusão: o próximo passo de opener é uma **biblioteca de rotas por peça** (ordem +
  colocação, com fallback quando a bag não serve), não mais ajuste de avaliação.

## Foco em T-spin (medido)

O bot subia até ~12 linhas e despejava 2 linhas de ataque para depois reconstruir, gastando
clears fracos. Medição (12 seeds x 60 peças = 720 peças, `pressao=0.1`, mesmas seeds):

| config | ataque/peça | TSD | TSS | quad | clears fracos | B2B +/− | buracos+ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| antes | 0,289 | 26 | 0 | 5 | 74 | 11/16 | 29 |
| agora | **0,313** | 24 | 1 | **11** | **66** | **14**/16 | 30 |

O ganho veio de valorizar o T-spin **alcançável por SRS** (busca exata que confirma o clear),
não o slot geométrico: `BONUS_TSPIN_SETUP` 10/18/26 -> 16/29/42, `TSPIN_SETUP_CANDIDATES` 1 -> 2
e `BONUS_TSPIN` 26 -> 34. Quads dobram e B2B é mantido 27% mais nas mesmas seeds.

Testado e **rejeitado** (mesmas 12 seeds; nenhum passou):

- **Cortar o ataque de combo** (multiplicador 0,25 -> 0,10, `W["combo"]` 2,5 -> 0 e sem desconto de
  `_custo_clear_pequeno` para combo): ataque 0,313 -> 0,302 e clears fracos 66 -> 76. As jogadas
  escolhidas são quase as mesmas: o combo não estava consumindo clears no simulador, só o valor
  contábil mudou. Revertido.
- **Bloquear todo clear fraco fora da zona de risco** (`_filtrar_clear_fraco` sem exigir B2B, e
  depois só 1L/2L): ataque 0,279 -> 0,254 e TSD 25 -> 20. Segurar a peça não vira T-spin; vira
  pilha alta e despejo depois. Revertido.
- **Aumentar o valor do slot geométrico** (`W["slot"]` 58 -> 75/85, `BONUS_TSPIN` 32/36): ataque
  0,279 -> 0,226-0,254. O slot sem T disponível só incentiva empilhar. Revertido.
- **`BONUS_QUAD` (bônus fixo por Tetris)**: 0, 4 e 60 deram resultado idêntico — sem poço de I o
  quad já é o clear mais valioso quando aparece. Não ficou no código.
- **`COMBO_FORCADO_MIN = 99`** (nunca forçar clear para manter combo): resultado idêntico ao
  original; a regra praticamente não dispara.

## T não fica mais preso no hold (medido)

Havia dois incentivos que, juntos, faziam o bot montar o slot de TSD e **não usar o T** para
fechá-lo:

- `BONUS_HOLD_T = 8` somado **à opção de segurar o T** quando já existia slot pronto
  (`_slots(...)[0]`), nos dois ramos de `decidir` — o bot era pago para guardar o T;
- `BONUS_SAVE_T_OPENING = 14` descontado de jogar o T sem clear full nos primeiros 14 lances.

Os dois foram **removidos** (junto com `_custo_t_desperdicado`/`CUSTO_T_SEM_CLEAR`, que já
estavam zerados). O desconto de T reservado ficou só dentro do molde do DT (`BONUS_SAVE_T_MOLDE`),
onde o T é o TSD/TST do opener.

Medição (10 seeds x 50 peças):

| pressão | config | ataque/peça | TSD | TSS | quad | B2B + | hmax | vivos |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0,1 | antes | 0,308 | 18 | 0 | 6 | 9 | 15 | 10/10 |
| 0,1 | agora | 0,310 | 16 | 1 | 6 | 9 | 16 | 10/10 |
| 0,3 (lixo) | antes | 0,368 | 17 | 0 | 11 | 9 | 19 | 9/10 |
| 0,3 (lixo) | agora | **0,426** | 14 | 0 | **22** | **18** | 16 | **10/10** |

Com lixo, +16% de ataque, o dobro de quads e de B2B mantido, pico mais baixo e nenhuma partida
perdida; o número de clears difíceis (TSD + quad) sobe de 28 para 36. O TSD isolado não sobe — o
T deixa de ser estacionado e passa a virar quad/B2B —, então o ganho vem de usar o T, não de
esperá-lo.

Também testado e **rejeitado**: baixar o bônus do slot pronto (`ESTAGIO[3]` 1,6 -> 1,0 -> 0,6)
para “pagar” a execução do T-spin derrubou o TSD de 14 para 8 e 2 e o ataque de 0,426 para 0,290.
O bônus do slot é o que faz o bot construir T-spins; mantido em 1,6.

## Pilha alta: tentado, medido e revertido

Duas heurísticas foram aplicadas para o erro de colocação no topo e **revertidas** por piorarem o
jogo:

- `RastreadorPilha` aceitar a leitura inteira como pilha depois de uma janela parada (`sync_alto`,
  ~18 frames) mesmo com bloco nas linhas 0-2. Em tela estática (contagem, replay, leitura suja)
  isso adota o lixo como pilha: o log mostrou `pilha_visual=15` com o tabuleiro quase vazio, e o
  bot passou a decidir sobre um fantasma de 15 linhas — decisões de ~500 ms e `PILHA DIVERGE` a
  cada peça.
- reconciliar o modelo na **primeira** divergência quando a altura passa de 12: uma única leitura
  suja substituía um modelo correto, o modelo divergia de novo no frame seguinte e o bot entrava em
  ciclo de reconciliação (e perdia a jogada especulada).

Voltou a regra antiga: 2 divergências seguidas (ou 12 blocos de diferença) para reconciliar, e
ressincronização só com as 3 linhas de cima vazias.

## Onde o tempo realmente vai (log de 16/11, 132 jogadas)

Com o código como está, o decisor **não** é o gargalo: 115 das 132 jogadas vieram da especulação
(`decisor=0.1ms`) e as 17 frias ficaram em 150 ms de mediana. O que trava a jogada é a leitura:

- 24 eventos `sem_sync` e 26 `LEITURA DA PILHA INCOERENTE` — o rastreador não consegue fechar a
  pilha quando a tela tem bloco nas 3 linhas de cima;
- enquanto a leitura está incoerente o `Bot._pilha` devolve o **modelo** e `passo` se recusa a
  jogar (`if self.usando_modelo and not ...`), então ele fica parado até a leitura voltar;
- é isso que produz os vãos de 1,5-2 s do log (ex.: 83,98 s incoerente → 85,45 s `PILHA DIVERGE`
  → 85,53 s próxima jogada).

Ritmo medido nesse log: 0,35 s de mediana entre jogadas (2,9 PPS instantâneo), 2,04 PPS de média e
46 linhas em 132 peças. A média cai pelos vãos acima e pela pilha alta (9–17 linhas), onde o
`ALTURA_SEGURA = 12` troca o setup de ataque por sobrevivência e o bot passa a fazer 0L.

## Dependências (colunas vazias) e topo (medido)

O bot deixava **vários poços de 1 coluna** na pilha (só o I cobre um por vez), chegava ao
topo e parava de jogar. Causa encontrada: `W["poco_extra"]` estava documentado desde uma
versão anterior mas **não era lido em nenhum lugar** — e a checagem que existia estava
desligada por `USAR_POCOS_I = False`.

Agora:

- `_profundidades_poco_um`/`_contar_pocos_um` medem os poços, e a partir do **segundo** cada
  casa custa `W["poco_extra"] = 6` (independente de `USAR_POCOS_I`). Profundidade acima de 2
  continua custando `CUSTO_POCO_PROFUNDO = 8`/casa.
- `_filtrar_pocos_novos` permite **um** poço (o do Tetris) e descarta a colocação que cria o
  segundo, quando existe alternativa.
- `_jogada_reserva`: se a busca não devolver nenhuma jogada, o bot joga a colocação alcançável
  que deixa a pilha mais baixa/menos buracos (tentando o hold antes, se necessário), em vez de
  ficar parado no topo repetindo `SEM JOGADA`.

Medição (10 seeds x 50 peças):

| pressão | config | ataque/peça | TSD | quad | hmax | buracos+ | vivos |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0,1 | antes | 0,315 | 19 | 6 | **20** | 22 | 9/10 |
| 0,1 | agora | 0,308 | 18 | 6 | **15** | 23 | **10/10** |
| 0,3 (lixo) | antes | 0,345 | 12 | 13 | 19 | 16 | 9/10 |
| 0,3 (lixo) | agora | **0,368** | **17** | 11 | 19 | 21 | 9/10 |

Sem lixo o ganho é altura (20 -> 15 linhas de pico, nenhuma partida morta); com lixo de
versus o ganho é ataque e TSD. Tentativa anterior, **rejeitada**: bloquear *qualquer* poço
novo derrubou o ataque 0,315 -> 0,212 e zerou os quads (sem poço não há Tetris) — daí a regra
ser "um poço, não dois".

## Correções no laço ao vivo

- **Crash `TypeError: frozenset(None)`.** `Bot.passo` usava o retorno de `self._pilha(pil)` sem
  checar `None`, que acontece com leitura incoerente **e** sem modelo — o guarda de entrada só
  olhava `pil.pilha`. Era o `ERRO NO LOOP` do `jogar_log.txt` em 14 s de jogo. Agora o passo
  registra `AGUARDANDO: leitura da pilha instavel e sem modelo` e não decide.
- **Captura com o jogo fora de foco.** O `mss` lê a região da tela, não a janela: com outra janela
  na frente o rastreador adotava pixels alheios (origem dos `sem_sync`/divergências). O laço agora
  pula a captura e a atualização dos rastreadores quando a janela em foco não é o TETR.IO.
- **Sem repetir a mesma jogada.** Depois de `fila sem avancar` o bot reenviava o mesmo plano em
  sequência (o log mostrava 3-4 jogadas idênticas seguidas). Agora espera a fila avançar por até
  1,5 s (`Bot.ESPERA_REPETIR`) antes de reavaliar e tentar de novo.

## Próximos objetivos

- Validar com precisão o ataque, combo, B2B/Surge, All Clear e garbage contra as
  regras atuais do TETR.IO.
- Comparar openers conforme fila, hold e bag; ampliar setups de T-spin e
  downstack sem sacrificar a segurança da pilha.
- Medir APM, PPS equivalente, APP, buracos e continuidade de B2B no simulador.
- Reduzir latência e inputs por peça sem trocar a busca exata das jogadas reais
  por movimentos fisicamente impossíveis.

## Licença

Nenhuma licença foi escolhida ainda. A publicação do código no GitHub, por si
só, não concede permissão para reutilizá-lo; escolha uma licença antes de
aceitar contribuições ou permitir redistribuição.

## Inicio de cada partida

O bot detecta a partida nova (tabuleiro vazio + hold vazio + fila nova) e mostra no console
`>>> NOVA PARTIDA detectada: 3... 2... 1... aguardando o GO` e depois `>>> GO! Jogando`.
Nao aperta nenhuma tecla durante a contagem. Se a deteccao automatica falhar, aperte **F8** para
reiniciar o estado do bot manualmente (F7 pausa, F9 encerra).
