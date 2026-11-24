"""
Passo 3 do bot: DECIDE onde colocar a peca. Nao le a tela e nao aperta teclas.

v4: busca ataque no versus ou pontos no solo/Blitz.
  - tabela de ataque/pontos, B2B e combo.
  - opener lookahead prioriza DT Cannon (TSD seguido de TST), TKI e Perfect Clear quando alcancaveis;
    a busca tambem preserva Perfect Clears de 1, 2 ou 4 linhas no meio da partida.
  - a abertura evita enterrar S/Z verticalmente antes de existir estrutura para encaixa-los.
  - DT Cannon: https://tetris.wiki/DT_Cannon

decidir(pilha, mao, hold, proximas, permitir_hold=True, b2b=0, combo=0,
        bag_seguinte=None, abertura=None, linhas_lixo=0) -> Jogada ou None
  Jogada(usar_hold, letra, giros, coluna_caixa, celulas, teclas, spin, limpas, b2b, combo, valor)
    teclas : ("toque", nome) | ("das", "esq"/"dir") | ("sd", distancia_em_linhas)
    b2b, combo : estado DEPOIS da jogada (o bot passa de volta na proxima)
Testes: `py -3.9 -m unittest discover -s tests -p "test_*.py"`.
Rode `py decisor.py` para a simulacao longa.
"""
from collections import namedtuple, deque
from functools import lru_cache
from inspect import signature
import math
import random
import time

import openers

LINHAS, COLUNAS = 20, 10
CHEIA = (1 << COLUNAS) - 1
POP = [bin(i).count("1") for i in range(1 << COLUNAS)]
SPAWN = {  # orientacao de nascimento (SRS), dentro da caixa de lado n
    "I": (4, [(1, 0), (1, 1), (1, 2), (1, 3)]),
    "O": (2, [(0, 0), (0, 1), (1, 0), (1, 1)]),
    "T": (3, [(0, 1), (1, 0), (1, 1), (1, 2)]),
    "S": (3, [(0, 1), (0, 2), (1, 0), (1, 1)]),
    "Z": (3, [(0, 0), (0, 1), (1, 1), (1, 2)]),
    "J": (3, [(0, 0), (1, 0), (1, 1), (1, 2)]),
    "L": (3, [(0, 2), (1, 0), (1, 1), (1, 2)]),
}
COL_SPAWN = {"I": 3, "O": 4, "T": 3, "S": 3, "Z": 3, "J": 3, "L": 3}  # coluna da esquerda da caixa
LINHA_SPAWN = 0          # linha (0 = topo lido) da caixa da peca ao nascer (SPAWN_LINHAS do tetrio_projeto)
# ---------- ajustes ----------
USAR_SPINS = True        # False: sem T-spin (--sem-spin no jogar.py)
MODO = "ataque"          # TETR.IO multiplayer por padrao; use "blitz" para jogo solo
DAS_MIN = 6              # DAS 9F (150 ms): so compensa em parede com 6+ colunas (toque ~25 ms cada)
PESO_VALOR = 6.5         # favorece ataques enviados, inclusive T-spins e tetrises
CUSTO_ABERTURA_SZ_VERTICAL = 1.0
CUSTO_CLEAR_PEQUENO = {1: 11.0, 2: 7.0, 3: 3.0}   # clear seco so e desperdicio com a pilha baixa; a partir de ALTURA_DOWNSTACK ele baixa a pilha e vale a pena
CUSTO_USO_I = 8.0
BONUS_I_PAREDE = 1.5
BONUS_I_SOBRE_APOIO = 1.5
BONUS_PERFECT_CLEAR = 5.0
BONUS_PCO_POTENCIAL = 30.0
BONUS_TSPIN_SETUP = {1: 16.0, 2: 29.0, 3: 42.0}  # clear full alcancavel por SRS
FATOR_ATAQUE_TSPIN_SETUP = 0.2
BONUS_TSPIN_SETUP_STAGE = {1: 6.0, 2: 13.0}  # valor de cada estagio do slot, sempre limitado pela altura
TSPIN_SETUP_CANDIDATES = 2  # candidatos do beam que recebem busca SRS generica
TSPIN_SETUP_GEOMETRY_CANDIDATES = 2
FINAL_EXACT_CANDIDATES_PER_ROOT = 1
BONUS_TKI_SETUP = 8.0
BONUS_TKI_CLEAR = 35.0
BONUS_DT_SETUP = 10.0
BONUS_DT_SLOT = 6.0
BONUS_DT_CANNON = 45.0
CUSTO_CLEAR_MOLDE = 40.0   # clear sem ataque pode destruir o encaixe do DT
BONUS_SAVE_T_MOLDE = 45.0   # no molde do DT os dois T sao reservados para o TSD e o TST
CUSTO_GASTAR_T = 18.0       # penaliza jogar T sem T-spin full quando ja existe slot de TSD montado
ALTURA_ALVO = 4             # superficie de trabalho: a pilha de um jogador forte fica baixa
ALTURA_DOWNSTACK = 7        # daqui para cima a altura manda: nenhum setup paga o risco
ALTURA_MANOBRA = 10         # teto de manobra: acima disto so baixar a pilha importa
ALTURA_PERIGO = LINHAS - 3  # altura em que os filtros de B2B/clear fraco param de agir
BONUS_TSPIN = 34.0        # T-spin cheio vale alem do ataque tabelado (e antes de guardar o slot)
BONUS_TETRIS = 6.0        # quad puro: fecha B2B com 4 linhas e paga o custo de manter o poco
FATOR_SEM_T = 0.6        # conserva setups de TSD enquanto se constroi sem T visivel
W = dict(
    soma=0.26,           # altura somada das colunas: manter a pilha baixa e o objetivo principal
    buraco=32.0,         # nao compensa enterrar buracos por um clear comum
    coberto=5.0,         # blocos que ficam acima de celulas vazias
    trans=0.12,          # transicoes de linha (pilha "esburacada" na horizontal)
    rug=0.65,            # rugosidade (degraus entre colunas); degrau >= 3 pesa mais
    dip=1.8,             # coluna funda e estreita no meio (so aceita I)
    altura=3.0,          # por linha acima de ALTURA_ALVO (cresce com a potencia 1.5)
    altura_max=0.5,      # penalidade incremental por altura total
    poco_largo=3.0,      # 3-wide/4-wide sao dependencias: canal largo custa caro
    dependencia=3.0,     # por peca que nao encaixa sem aumentar os buracos
    slot=58.0,           # o slot de TSD e o objetivo principal do empilhamento
    b2b=7.0,             # por nivel de B2B mantido (ate 8): a cadeia e o que paga o ataque
    poco_extra=6.0,      # cada poço de 1 coluna alem do principal (so o I cobre)
)
ALTURA_SEGURA = 10       # altura a partir da qual o bot para de reservar o T para setup
CUSTO_POCO_PROFUNDO = 8.0  # por casa acima de 2 num poco de 1 coluna (so o I cobre)
CUSTO_BURACO_ESTREITO = 6.0  # celula de buraco cercada dos dois lados: so peca vertical fecha
CUSTO_QUEBRA_B2B = 16.0  # por nivel de B2B perdido ao limpar sem spin (1-3 linhas)
ESCALA_QUEBRA_B2B = {1: 1.0, 2: 0.8, 3: 0.6}   # clears maiores ainda tem valor de downstack
ESTAGIO = (0.0, 0.55, 1.0, 1.7)   # progresso do slot de TSD: pronto (3) vale bem mais que o quad
FATOR_BONUS_TSS = 0.5    # fracao do BONUS_TSPIN pago por um T-spin single (menor = espera o TSD)
CUSTO_HOLD = 0.2         # preferencia leve por nao usar o hold
CUSTO_TECLA = 0.004      # desconto por acao de tecla (so desempata)
OPENERS = True           # molde de DT Cannon (openers.py) no inicio da partida
HORIZONTE_ABERTURA = 4   # abertura curta por padrao; moldes ativos usam horizonte proprio
JANELA_ABERTURA = 14     # fase de opener padrao do TETR.IO
PESO_MOLDE = 0.25          # orienta o opener sem sobrepor ataques fortes alcancaveis
HORIZONTE_MOLDE = 5      # pecas de lookahead enquanto o tabuleiro ainda cabe no molde
LARGURA = (3, 2, 2)      # 4 pecas de horizonte com alternativas: planeja o setup, nao so a peca atual
LARGURA_PCO = (2, 1, 1, 1, 1)  # cinco pecas continuam no horizonte de abertura
Jogada = namedtuple("Jogada", "usar_hold letra giros coluna_caixa celulas teclas spin limpas b2b combo valor",
                    defaults=(None, "", 0, 0, 0, False, 0.0))

# ---------- pecas ----------
ROT = {}  # letra -> [celulas da caixa em cada orientacao 0..3 (horario a partir do spawn)]
for _l, (_n, _c) in SPAWN.items():
    _lst, _cc = [], list(_c)
    for _ in range(4):
        _lst.append(tuple(_cc))
        _cc = [(c, _n - 1 - r) for r, c in _cc]
    ROT[_l] = _lst

_TSPIN_CLEAR_TARGETS = {}
for _k, _cells in enumerate(ROT["T"]):
    _row_masks = [0] * (max(r for r, _ in _cells) + 1)
    for _r, _c in _cells:
        _row_masks[_r] |= 1 << _c
    for _bc in range(COLUNAS - 2):
        for _r, _mask in enumerate(_row_masks):
            if _mask:
                _TSPIN_CLEAR_TARGETS.setdefault(_mask << _bc, []).append(
                    (_k, _r, _bc)
                )
_TSPIN_CLEAR_TARGETS = {
    missing: tuple(targets) for missing, targets in _TSPIN_CLEAR_TARGETS.items()
}


def _mascara_forma(cels):
    linhas = {}
    for r, c in cels:
        linhas[r] = linhas.get(r, 0) | (1 << c)
    minc, maxc = min(c for _, c in cels), max(c for _, c in cels)
    formas = {}
    for bc in range(-minc, COLUNAS - maxc):
        formas[bc] = tuple((r, mascara << bc if bc >= 0 else mascara >> -bc)
                           for r, mascara in linhas.items())
    return formas


MASCARAS_CELS = {cels: _mascara_forma(cels) for formas in ROT.values() for cels in formas}
_OFFSET_LINHAS = LINHAS * 2
_BITS_FORMAS = {
    cels: sum(1 << (r * COLUNAS + c) for r, c in cels)
    for cels in MASCARAS_CELS
}
_MAX_LINHA_FORMA = {
    cels: max(r for r, _ in cels)
    for cels in MASCARAS_CELS
}


def _bits_tabuleiro(linhas):
    bits = 0
    for r, mascara in enumerate(linhas):
        bits |= mascara << ((r + _OFFSET_LINHAS) * COLUNAS)
    return bits


def _livre_bits(tabuleiro, cels, br, bc):
    if MASCARAS_CELS[cels].get(bc) is None:
        return False
    maxr = _MAX_LINHA_FORMA[cels]
    if br + maxr >= LINHAS:
        return False
    if br + maxr < 0:
        return True
    deslocamento = (br + _OFFSET_LINHAS) * COLUNAS + bc
    return not (tabuleiro & (_BITS_FORMAS[cels] << deslocamento))


def _formas(letra):
    """[(giros, minc, largura, altura, celulas_normalizadas, mascaras_por_linha)] sem formas repetidas."""
    vistos, saida = set(), []
    for k, cels in enumerate(ROT[letra]):
        minr = min(r for r, _ in cels)
        minc = min(c for _, c in cels)
        norm = tuple(sorted((r - minr, c - minc) for r, c in cels))
        if norm not in vistos:
            vistos.add(norm)
            w = max(c for _, c in norm) + 1
            h = max(r for r, _ in norm) + 1
            masc = [0] * h
            for r, c in norm:
                masc[r] |= 1 << c
            saida.append((k, minc, w, h, norm, tuple(masc)))
    return saida


FORMAS = {letra: _formas(letra) for letra in SPAWN}

# kicks SRS (JLSTZ) em (dx, dy para cima); aqui convertidos para (d_linha, d_coluna)
_K = {
    (0, 1): [(0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)],
    (1, 0): [(0, 0), (1, 0), (1, -1), (0, 2), (1, 2)],
    (1, 2): [(0, 0), (1, 0), (1, -1), (0, 2), (1, 2)],
    (2, 1): [(0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)],
    (2, 3): [(0, 0), (1, 0), (1, 1), (0, -2), (1, -2)],
    (3, 2): [(0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)],
    (3, 0): [(0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)],
    (0, 3): [(0, 0), (1, 0), (1, 1), (0, -2), (1, -2)],
}
KICKS = {k: [(-dy, dx) for dx, dy in v] for k, v in _K.items()}
_KI = {
    (0, 1): [(0, 0), (-2, 0), (1, 0), (-2, -1), (1, 2)],
    (1, 0): [(0, 0), (2, 0), (-1, 0), (2, 1), (-1, -2)],
    (1, 2): [(0, 0), (-1, 0), (2, 0), (-1, 2), (2, -1)],
    (2, 1): [(0, 0), (1, 0), (-2, 0), (1, -2), (-2, 1)],
    (2, 3): [(0, 0), (2, 0), (-1, 0), (2, 1), (-1, -2)],
    (3, 2): [(0, 0), (-2, 0), (1, 0), (-2, -1), (1, 2)],
    (3, 0): [(0, 0), (1, 0), (-2, 0), (1, -2), (-2, 1)],
    (0, 3): [(0, 0), (-1, 0), (2, 0), (-1, 2), (2, -1)],
}
KICKS_I = {k: [(-dy, dx) for dx, dy in v] for k, v in _KI.items()}
FRENTE = {0: ((0, 0), (0, 2)), 1: ((0, 2), (2, 2)), 2: ((2, 0), (2, 2)), 3: ((0, 0), (2, 0))}
RANK_SPIN = {"": 0, "mini": 1, "full": 2}

# ---------- sistema de ataque do TETR.IO ----------
ATAQUE_BASE = {1: 0.0, 2: 1.0, 3: 2.0, 4: 4.0}
ATAQUE_FULL = {1: 2.0, 2: 4.0, 3: 6.0}
ATAQUE_MINI = {1: 0.0, 2: 1.0}
PONTOS_BASE = {1: 1.0, 2: 3.0, 3: 5.0, 4: 8.0}          # blitz (em centenas de pontos)
PONTOS_FULL = {1: 8.0, 2: 12.0, 3: 16.0}
PONTOS_MINI = {1: 2.0, 2: 4.0, 3: 8.0}


def transicao(b2b, combo, spin, n, perfect_clear=False):
    """(b2b_novo, combo_novo, valor) depois de travar uma peca que limpou n linhas.
    combo = quantas limpezas seguidas ja ocorreram antes desta. valor = ataque (ou centenas de pontos)."""
    if n == 0:
        return b2b, 0, 0.0
    dificil = bool(spin) or n == 4 or (perfect_clear and MODO == "ataque")
    if MODO == "blitz":
        if spin == "full":
            base = PONTOS_FULL.get(n, 16.0)
        elif spin == "mini":
            base = PONTOS_MINI.get(n, 8.0)
        else:
            base = PONTOS_BASE[n]
        if dificil and b2b >= 1:
            base *= 1.5
        valor = base + 0.5 * combo
    else:
        if spin == "full":
            base = ATAQUE_FULL.get(n, 6.0)
        elif spin == "mini":
            base = ATAQUE_MINI.get(n, 1.0)
        else:
            base = ATAQUE_BASE[n]
        if dificil and b2b >= 1:
            base += 1.0
        if base > 0:
            valor = base * (1.0 + 0.25 * combo)
        elif combo >= 2:
            valor = math.log(1.0 + 1.25 * combo)
        else:
            valor = 0.0
        if not dificil and b2b >= 4:   # descarga do Surge acumulado no B2B Charging
            valor += b2b
        valor = float(math.floor(valor))  # arredondamento DOWN padrao nos modos multiplayer
    novo_b2b = b2b + 1 if dificil else 0
    return novo_b2b, combo + 1, valor


def _valor_planejamento(n, spin, valor):
    """Usa o ataque efetivamente enviado, inclusive doubles/triples e combos."""
    return valor


def _bonus_tspin(spin, n):
    """T-spins cheios valem mais que o ataque tabelado; o triplo e o alvo principal."""
    if spin != "full":
        return 0.0
    return BONUS_TSPIN * (1.5 if n == 3 else 1.0 if n == 2 else FATOR_BONUS_TSS)

def _ajuste_i(letra, colocacao, linhas_antes):
    """Guarda I para o quad quando seguro; pequenos bonus favorecem usos sem cavar buracos."""
    if letra != "I":
        return 0.0
    _, _, _, _, k, _, celulas, _, _, n, _ = colocacao
    ajuste = 0.0
    if n < 4:
        topo = next((r for r, mascara in enumerate(linhas_antes) if mascara), LINHAS)
        altura = LINHAS - topo
        urgencia = min(1.0, max(0.0, (altura - ALTURA_SEGURA) / 5.0))
        ajuste -= CUSTO_USO_I * (1.0 - urgencia)
    if celulas:
        colunas = [c for _, c in celulas]
        if k % 2 and (min(colunas) == 0 or max(colunas) == COLUNAS - 1):
            ajuste += BONUS_I_PAREDE
        elif not k % 2 and all(r + 1 < LINHAS and linhas_antes[r + 1] & (1 << c)
                                for r, c in celulas):
            ajuste += BONUS_I_SOBRE_APOIO
    return ajuste

def _custo_quebra_b2b(n, spin, b2b, altura, perfect_clear=False):
    """Limpar 1-3 linhas sem spin com B2B ativo zera a cadeia: o ataque acumulado
    (que cresce ate 8) vale mais que o alivio imediato da pilha baixa.
    Com b2b < 4 a cadeia ainda esta em carga (Surge so descarrega em b2b >= 4), entao
    reiniciar antes disso custa mais caro: era o B2B cancelado cedo para montar TSD."""
    if not n or spin or n == 4 or b2b < 1 or perfect_clear:
        return 0.0
    if altura >= ALTURA_PERIGO:   # zona de risco: B2B deixa de importar (hora de limpar a pilha)
        return 0.0
    inicio = ALTURA_DOWNSTACK + 2
    urgencia = max(0.0, min(1.0, (altura - inicio) / 4.0))
    carga = 1.0 + 0.5 * max(0, 4 - b2b)
    return (CUSTO_QUEBRA_B2B * carga * ESCALA_QUEBRA_B2B.get(n, 1.0) * min(b2b, 8)
            * (1.0 - urgencia))


def _filtrar_quebra_b2b(colocacoes, b2b, linhas):
    """Evita gastar um B2B seguro em clear normal que nao envia ataque."""
    if b2b < 1 or _altura_max(linhas) >= ALTURA_PERIGO:
        return colocacoes
    mantem_b2b = [c for c in colocacoes
                  if not c[9] or c[7] or c[9] == 4 or c[0] > 0]
    return mantem_b2b or colocacoes


def _filtrar_clear_fraco(colocacoes, linhas, b2b=0):
    """Evita clears secos de 1-3 linhas salvo risco imediato de topo."""
    if _altura_max(linhas) >= ALTURA_PERIGO:
        return colocacoes
    bons = [c for c in colocacoes
            if not c[9] or c[7] or c[9] == 4 or not any(c[1])]
    return bons or colocacoes


def _custo_clear_pequeno(n, spin, valor, linhas):
    """Penaliza clears pequenos de pouco ataque, inclusive minis sem ataque."""
    if (MODO != "ataque" or spin == "full" or n not in CUSTO_CLEAR_PEQUENO
            or valor >= 4):
        return 0.0
    topo = next((r for r, mascara in enumerate(linhas) if mascara), LINHAS)
    altura = LINHAS - topo
    if altura >= ALTURA_DOWNSTACK:
        return 0.0
    urgencia = max(0.0, min(1.0, (altura - ALTURA_ALVO)
                            / float(max(1, ALTURA_DOWNSTACK - ALTURA_ALVO))))
    return CUSTO_CLEAR_PEQUENO[n] * (1.0 - urgencia)


def _limpar(cels):
    """Remove linhas completas de um conjunto de celulas. Retorna (novas_celulas, n_linhas)."""
    cont = {}
    for r, _ in cels:
        cont[r] = cont.get(r, 0) + 1
    cheias = {r for r, n in cont.items() if n == COLUNAS}
    if not cheias:
        return cels, 0
    return frozenset((r + sum(1 for f in cheias if f > r), c) for r, c in cels if r not in cheias), len(cheias)


def para_linhas(pilha):
    linhas = [0] * LINHAS
    for r, c in pilha:
        if 0 <= r < LINHAS:
            linhas[r] |= 1 << c
    return linhas


def _colocacoes(linhas, letra):
    """Posicoes finais por queda reta: (giros, coluna_caixa, x, y, mascaras, celulas_norm).

    Usada pela busca aproximada e pela contagem de dependencia (sem giros/kicks).
    """
    topo = next((i for i, v in enumerate(linhas) if v), LINHAS)
    for k, minc, w, h, norm, masc in FORMAS[letra]:
        for x in range(COLUNAS - w + 1):
            ms = [m << x for m in masc]
            y = topo - h
            while True:
                ny, livre = y + 1, True
                for dr, m in enumerate(ms):
                    rr = ny + dr
                    if rr < 0:
                        continue
                    if rr >= LINHAS or linhas[rr] & m:
                        livre = False
                        break
                if not livre:
                    break
                y = ny
            if y >= 0:
                yield k, x - minc, x, y, ms, norm


def colocacoes(pilha, letra):
    """Compatibilidade: gera (giros, coluna_caixa, celulas) fisicamente alcancaveis."""
    for k, col, _, _, cels, _, _ in colocacoes_alcancaveis(para_linhas(pilha), letra):
        yield k, col, frozenset(cels)


# ---------- gerador de movimentos alcancaveis (BFS/SRS para todas as pecas) ----------
def _livre(linhas, cels, br, bc):
    mascaras = MASCARAS_CELS[cels].get(bc)
    if mascaras is None:
        return False
    for dr, mascara in mascaras:
        rr = br + dr
        if rr >= LINHAS or (rr >= 0 and linhas[rr] & mascara):
            return False
    return True


def _ocupada(linhas, r, c):
    if c < 0 or c >= COLUNAS or r >= LINHAS:
        return True
    return r >= 0 and bool((linhas[r] >> c) & 1)


def _kicks(letra, k, nk):
    return KICKS_I[(k, nk)] if letra == "I" else KICKS[(k, nk)]


def _tentar_rotacao(linhas, letra, k, br, bc, nome, verificar=None):
    """Aplica uma rotacao SRS; 180 usa kick zero como no controle legado do bot."""
    nk = (k + (1 if nome == "cw" else 3 if nome == "ccw" else 2)) % 4
    testes = [(0, 0)] if nome == "180" else _kicks(letra, k, nk)
    for idx, (dr, dc) in enumerate(testes):
        livre = (verificar(nk, br + dr, bc + dc) if verificar
                 else _livre(linhas, ROT[letra][nk], br + dr, bc + dc))
        if livre:
            ultimo_kick = nome != "180" and idx == 4
            return nk, br + dr, bc + dc, (2 if ultimo_kick else 1)
    return None


def _tipo_spin(linhas, k, br, bc, f):
    """Classifica T-spin por corners/front-corners SRS e upgrade do quinto kick."""
    if f == 0:
        return ""
    n = sum(_ocupada(linhas, br + r, bc + c) for r, c in ((0, 0), (0, 2), (2, 0), (2, 2)))
    if n < 3:
        return ""
    frente = sum(_ocupada(linhas, br + r, bc + c) for r, c in FRENTE[k])
    return "full" if (frente == 2 or f == 2) else "mini"


def _spin_all_mini(linhas, letra, k, br, bc, f, verificar=None):
    """All-Mini+ (padrao do TETR.IO): peca nao-T so conta spin quando, depois da rotacao,
    fica imovel: nao anda para a esquerda, direita, baixo E nem para cima. O teste de cima
    (overhang por cima da peca) e o que define o spin — girar no ultimo segundo com espaco
    livre em cima NAO e spin. Fonte: tetrio.wiki.gg/wiki/Spins#Spin_Detection."""
    if not USAR_SPINS or MODO != "ataque" or letra in ("T", "O") or not f:
        return ""
    if verificar is None:
        livre = lambda nr, nc: _livre(linhas, ROT[letra][k], nr, nc)
    else:
        livre = lambda nr, nc: verificar(k, nr, nc)
    if (not livre(br, bc - 1)
            and not livre(br, bc + 1)
            and not livre(br + 1, bc)
            and not livre(br - 1, bc)):
        return "mini"
    return ""


def _rota_i_antes_do_deslocamento(linhas, k, coluna, celulas_alvo):
    """Prefere girar o I no spawn e so depois mover; evita kick falhar apos soft drop."""
    giro = {1: "cw", 3: "ccw"}.get(k)
    if giro is None:
        return None
    estado = _tentar_rotacao(linhas, "I", 0, LINHA_SPAWN, COL_SPAWN["I"], giro)
    if estado is None:
        return None
    nk, br, bc, _ = estado
    coluna_inicial = bc
    passo = 1 if coluna > bc else -1
    while bc != coluna:
        if not _livre(linhas, ROT["I"][nk], br, bc + passo):
            return None
        bc += passo
    while _livre(linhas, ROT["I"][nk], br + 1, bc):
        br += 1
    finais = frozenset((br + r, bc + c) for r, c in ROT["I"][nk])
    if finais != frozenset(celulas_alvo):
        return None
    direcao = "L" if coluna < coluna_inicial else "R"
    return [giro] + [direcao] * abs(coluna - coluna_inicial)


def _acoes_para_teclas(linhas, acoes, letra="T"):
    """Converte caminho BFS em teclas, agrupando deslocamentos contiguos e soft drop."""
    cels = ROT[letra]
    k, br, bc = 0, LINHA_SPAWN, COL_SPAWN[letra]
    teclas, i = [], 0
    while i < len(acoes):
        a = acoes[i]
        if a in ("L", "R"):
            j = i
            while j < len(acoes) and acoes[j] == a:
                j += 1
            n, dc = j - i, (-1 if a == "L" else 1)
            m = 0
            while _livre(linhas, cels[k], br, bc + dc * (m + 1)):
                m += 1
            nome = "esq" if a == "L" else "dir"
            if n == m and n >= DAS_MIN:
                teclas.append(("das", nome))
            else:
                teclas += [("toque", nome)] * n
            bc += dc * n
            i = j
            continue
        if a == "D":
            inicio_br = br
            while _livre(linhas, cels[k], br + 1, bc):
                br += 1
            if i != len(acoes) - 1:
                teclas.append(("sd", br - inicio_br))
        else:
            estado = _tentar_rotacao(linhas, letra, k, br, bc, a)
            if estado is None:
                raise AssertionError("caminho BFS contem uma rotacao bloqueada")
            k, br, bc, _ = estado
            teclas.append(("toque", a))
        i += 1
    return teclas


def colocacoes_alcancaveis(linhas, letra, parar_no_primeiro_tspin_clear=False):
    """Retorna posicoes alcançaveis desde o spawn; setups podem parar no primeiro T-spin clear."""
    cels = ROT[letra]
    tabuleiro_bits = _bits_tabuleiro(linhas)
    ini = (0, LINHA_SPAWN, COL_SPAWN[letra], 0)
    livres = {}
    primeiro_tspin_clear = None

    def livre(k, br, bc):
        chave = (k, br, bc)
        if chave not in livres:
            livres[chave] = _livre_bits(tabuleiro_bits, cels[k], br, bc)
        return livres[chave]

    if not livre(*ini[:3]):
        return []
    pai = {ini: None}
    fila = deque([ini])
    achados = {}
    while fila:
        s = fila.popleft()
        k, br, bc, f = s

        y = br
        while livre(k, y + 1, bc):
            y += 1
        if letra == "T":
            spin = _tipo_spin(linhas, k, br, bc, f) if y == br and USAR_SPINS else ""
        else:
            spin = (_spin_all_mini(linhas, letra, k, y, bc, f, verificar=livre)
                    if y == br else "")
        chave = (k, y, bc)
        antigo = achados.get(chave)
        if antigo is None or RANK_SPIN[spin] > RANK_SPIN[antigo[0]]:
            achados[chave] = (spin, s)
        if parar_no_primeiro_tspin_clear and spin == "full":
            celulas = tuple((br + r, bc + c) for r, c in cels[k])
            topo = min(r for r, _ in celulas)
            mascaras = [0] * (max(r for r, _ in celulas) - topo + 1)
            for r, c in celulas:
                mascaras[r - topo] |= 1 << c
            if _aplicar(linhas, topo, mascaras)[1]:
                primeiro_tspin_clear = chave
                break

        for dc, nome in ((-1, "L"), (1, "R")):
            if livre(k, br, bc + dc):
                ns = (k, br, bc + dc, 0)
                if ns not in pai:
                    pai[ns] = (s, nome)
                    fila.append(ns)
        if y != br:
            ns = (k, y, bc, 0)
            if ns not in pai:
                pai[ns] = (s, "D")
                fila.append(ns)
        if letra != "O":
            for nome in ("cw", "ccw", "180"):
                estado = _tentar_rotacao(linhas, letra, k, br, bc, nome, livre)
                if estado is not None:
                    nk, nr, nc, nf = estado
                    ns = (nk, nr, nc, nf)
                    if ns not in pai:
                        pai[ns] = (s, nome)
                        fila.append(ns)

    saida = []
    resultados = ([(primeiro_tspin_clear, achados[primeiro_tspin_clear])]
                  if primeiro_tspin_clear is not None else achados.items())
    for (k, br, bc), (spin, s) in resultados:
        acoes, cur = [], s
        while pai[cur] is not None:
            cur, nome = pai[cur]
            acoes.append(nome)
        acoes.reverse()
        cel_abs = tuple((br + r, bc + c) for r, c in cels[k])
        if letra == "I" and k in (1, 3) and not spin:
            rota_segura = _rota_i_antes_do_deslocamento(linhas, k, bc, cel_abs)
            if rota_segura is not None:
                acoes = rota_segura
        y = min(r for r, _ in cel_abs)
        if y < 0:
            continue
        ms = [0] * (max(r for r, _ in cel_abs) - y + 1)
        for r, c in cel_abs:
            ms[r - y] |= 1 << c
        saida.append((k, bc, y, ms, cel_abs, spin, acoes))
    return saida


@lru_cache(maxsize=2048)
def _colocacoes_cacheadas(linhas, letra, usar_spins, linha_spawn, tipo_spin_fn, rotacao_fn):
    return tuple(colocacoes_alcancaveis(linhas, letra))


RAPIDO = False  # pecas que nao sao T em pilha sem buracos: queda reta em vez de BFS (rota so na jogada escolhida)


def _pilha_aberta(linhas):
    """Sem celula vazia coberta e com folga ate o spawn: toda queda reta e alcancavel."""
    cobertas = 0
    for v in linhas:
        if ~v & cobertas & CHEIA:
            return False
        cobertas |= v
    return not any(linhas[:6])


@lru_cache(maxsize=4096)
def _colocacoes_rapidas(linhas, letra):
    saida = []
    for k, col, x, y, ms, norm in _colocacoes(list(linhas), letra):
        saida.append((k, col, y, ms, tuple((y + r, x + c) for r, c in norm), "", None))
    return tuple(saida)


def _get_colocacoes(linhas, letra, rapido=None):
    linhas = tuple(linhas)
    if (RAPIDO if rapido is None else rapido) and letra != "T" and _pilha_aberta(linhas):
        return _colocacoes_rapidas(linhas, letra)
    return _colocacoes_cacheadas(linhas, letra, USAR_SPINS, LINHA_SPAWN, _tipo_spin, _tentar_rotacao)


def colocacoes_T(linhas):
    """Compatibilidade com chamadores existentes: BFS de alcancabilidade para T."""
    return colocacoes_alcancaveis(linhas, "T")


# ---------- avaliacao do tabuleiro ----------
def _aplicar(linhas, y, ms):
    novas = list(linhas)
    for dr, m in enumerate(ms):
        novas[y + dr] |= m
    if CHEIA in novas:
        resto = [v for v in novas if v != CHEIA]
        n = LINHAS - len(resto)
        return [0] * n + resto, n
    return novas, 0


@lru_cache(maxsize=65536)
def _slots_detalhes_cache(linhas):
    """Retorna TSDs prontos, melhor estagio e quantos slots validos podem ser construidos.
    1 = linha com UM buraco e 3 casas livres em cima; 2 = linha de cima completa menos essas 3;
    3 = pronto (uma das pontas tem o teto/overhang)."""
    prontos = 0
    estagio = 0
    setups = 0
    for r1 in range(1, LINHAS):
        e = ~linhas[r1] & CHEIA
        if not e or e & (e - 1):
            continue
        x = e.bit_length() - 2          # buraco do talo em x+1
        if x < 0 or x > 7:
            continue
        m = 7 << x
        cima = linhas[r1 - 1]
        if cima & m:
            continue
        setups += 1
        est = 1
        if (~cima & CHEIA) == m:
            est = 2
            if r1 >= 2:
                acima = linhas[r1 - 2]
                a, b, meio = (acima >> x) & 1, (acima >> (x + 2)) & 1, (acima >> (x + 1)) & 1
                if a ^ b and not meio:
                    est = 3
                    prontos += 1
        if est > estagio:
            estagio = est
    return prontos, estagio, setups


@lru_cache(maxsize=65536)
def _slots_por_estagio_cache(linhas):
    """Conta slots de TSD por estagio. Um slot de estagio 1 e so um buraco com espaco
    aberto em cima (comum por acidente); estagio 2/3 ja tem o vao de tres colunas, ou seja,
    e um setup de verdade e a casa do talo e intencional."""
    por_estagio = [0, 0, 0, 0]
    for r1 in range(1, LINHAS):
        e = ~linhas[r1] & CHEIA
        if not e or e & (e - 1):
            continue
        x = e.bit_length() - 2
        if x < 0 or x > 7:
            continue
        m = 7 << x
        cima = linhas[r1 - 1]
        if cima & m:
            continue
        est = 1
        if (~cima & CHEIA) == m:
            est = 2
            if r1 >= 2:
                acima = linhas[r1 - 2]
                a, b, meio = (acima >> x) & 1, (acima >> (x + 2)) & 1, (acima >> (x + 1)) & 1
                if a ^ b and not meio:
                    est = 3
        por_estagio[est] += 1
    return tuple(por_estagio)


def _slots_por_estagio(linhas):
    return _slots_por_estagio_cache(linhas if isinstance(linhas, tuple) else tuple(linhas))


def _slots_detalhes(linhas):
    """Versao cacheada (o mesmo tabuleiro e reavaliado muitas vezes na busca)."""
    return _slots_detalhes_cache(linhas if isinstance(linhas, tuple) else tuple(linhas))


def _tem_tspin_clear_geometrico(linhas):
    """Pre-filtra T-spins com clear por cantos e linha completa antes da busca SRS."""
    tabuleiro_bits = _bits_tabuleiro(linhas)
    for row, mascara in enumerate(linhas):
        targets = _TSPIN_CLEAR_TARGETS.get(CHEIA ^ mascara, ())
        for k, row_offset, bc in targets:
            br = row - row_offset
            celulas = ROT["T"][k]
            if (br < 0 or br + _MAX_LINHA_FORMA[celulas] >= LINHAS
                    or not _livre_bits(tabuleiro_bits, celulas, br, bc)
                    or not _tipo_spin(linhas, k, br, bc, 1)):
                continue
            return True
    return False


def _altura_colunas(linhas):
    alturas = [0] * COLUNAS
    for r, mascara in enumerate(linhas):
        while mascara:
            bit = mascara & -mascara
            col = bit.bit_length() - 1
            if not alturas[col]:
                alturas[col] = LINHAS - r
            mascara ^= bit
    return alturas


@lru_cache(maxsize=131072)
def _buracos_cache(linhas):
    acima = 0
    total = 0
    for mascara in linhas:
        total += POP[~mascara & acima & CHEIA]
        acima |= mascara
    return total


def _buracos(linhas):
    return _buracos_cache(linhas if isinstance(linhas, tuple) else tuple(linhas))


def _slots_planejados(linhas):
    """Slots de TSD com o vao de tres colunas ja formado (estagios 2 e 3). A casa do talo
    e intencional e nao conta como buraco; um simples buraco aberto (estagio 1) conta."""
    if not USAR_SPINS:
        return 0
    por_estagio = _slots_por_estagio(linhas)
    return por_estagio[2] + por_estagio[3]


def _buracos_efetivos(linhas):
    """Buracos reais: desconta a casa intencional de cada slot de TSD ja formado.
    Sem isso, comecar um slot custava 32 pontos por buraco e o bot nunca chegava ao T-spin."""
    total = _buracos(linhas)
    if total:
        total = max(0, total - _slots_planejados(
            linhas if isinstance(linhas, tuple) else tuple(linhas)))
    return total


def _sobreposicoes(linhas):
    abaixo_vazio = 0
    total = 0
    for mascara in reversed(linhas):
        total += POP[mascara & abaixo_vazio]
        abaixo_vazio |= ~mascara & CHEIA
    return total


_CACHE_DEPENDENCIA = {}


def _pecas_que_encaixam_sem_buraco(linhas):
    """Conta quantos tipos tem uma colocacao sem novos buracos."""
    chave = tuple(linhas)
    if chave in _CACHE_DEPENDENCIA:
        return _CACHE_DEPENDENCIA[chave]
    buracos_atuais = _buracos_efetivos(linhas)
    encaixam = 0
    for letra in "IOTSZJL":
        for _, _, _, y, mascaras, _ in _colocacoes(linhas, letra):
            depois, _ = _aplicar(linhas, y, mascaras)
            if _buracos_efetivos(depois) <= buracos_atuais:
                encaixam += 1
                break
    if len(_CACHE_DEPENDENCIA) > 8192:
        _CACHE_DEPENDENCIA.clear()
    _CACHE_DEPENDENCIA[chave] = encaixam
    return encaixam


def _contar_pocos_um(alturas):
    """Quantos pocos de 1 coluna (profundidade >= 2) a pilha tem."""
    return sum(1 for p in _profundidades_poco_um(alturas) if p >= 2)


def _filtrar_pocos_novos(linhas, colocacoes):
    """Permite UM poco de 1 coluna (o do Tetris), mas nao um segundo: com dois pocos so
    o I cobre um por vez, e a pilha vira uma parede de colunas vazias que nao baixa mais.
    So age quando ainda nao existe poco, para nao impedir o bot de usar o que ja tem."""
    if _contar_pocos_um(_altura_colunas(linhas)) >= 1:
        return colocacoes
    limpas = [c for c in colocacoes
              if _contar_pocos_um(_altura_colunas(c[1])) <= 1]
    return limpas or colocacoes


def _filtrar_tspin_obrigatorio(colocacoes, letra):
    """Com um T-spin full de duas ou tres linhas alcancavel, ele e a jogada.
    E o comportamento de quem faz T-spin toda bag: o T nunca e gasto deitado
    quando existe o encaixe do spin."""
    if letra != "T" or not USAR_SPINS:
        return colocacoes
    spins = [c for c in colocacoes if c[7] == "full" and c[9] >= 2]
    return spins or colocacoes


def _filtrar_buracos_novos(linhas, colocacoes):
    """Evita criar buracos quando outra colocacao do passo nao os cria."""
    buracos_atuais = _buracos_efetivos(linhas)
    limpas = [c for c in colocacoes if _buracos_efetivos(c[1]) <= buracos_atuais]
    return limpas or colocacoes


def _penalidade_pocos_largos(alturas):
    """Penaliza depressoes de largura maior que uma coluna."""
    melhor = 0.0
    for inicio in range(1, COLUNAS - 1):
        for fim in range(inicio + 2, COLUNAS):
            profundidade = min(alturas[inicio - 1], alturas[fim]) - max(alturas[inicio:fim])
            if profundidade > 0:
                largura = fim - inicio
                melhor = max(melhor, W["poco_largo"] * (largura - 1) * min(profundidade, 4))
    return melhor


def _penalidade_dependencia(linhas):
    encaixam = _pecas_que_encaixam_sem_buraco(linhas)
    return -W["dependencia"] * (7 - encaixam) - 4.0 * max(0, 4 - encaixam) ** 2


def _penalidade_buracos_estreitos(linhas):
    """Buraco cercado dos dois lados numa coluna de 1 de largura: so peca vertical (I)
    ou um T-spin fecha. E a dependencia que mata a partida — cobra caro."""
    total = 0
    coberto = 0
    for mascara in linhas:
        livre = (~mascara) & CHEIA
        if livre:
            estreitos = (livre
                         & (((mascara << 1) | 1) & CHEIA)
                         & (((mascara >> 1) | (1 << (COLUNAS - 1))) & CHEIA))
            if estreitos & coberto:      # so buraco: tem bloco acima na coluna
                total += bin(estreitos & coberto).count("1")
        coberto |= mascara
    return CUSTO_BURACO_ESTREITO * total


def _profundidades_poco_um(alturas):
    """Profundidade de cada poco de 1 coluna (coluna mais baixa que as duas vizinhas).
    Bordas contam com a unica vizinha. 0 = coluna nao e poco."""
    profundidades = []
    for i in range(COLUNAS):
        if i == 0:
            profundidade = alturas[1] - alturas[0]
        elif i == COLUNAS - 1:
            profundidade = alturas[-2] - alturas[-1]
        else:
            profundidade = min(alturas[i - 1], alturas[i + 1]) - alturas[i]
        profundidades.append(max(0, profundidade))
    return profundidades


def _penalidade_pocos_um(alturas):
    """Poco de 1 coluna = dependencia de peca I. Cada casa acima de 2 custa caro
    (profundidade 6 = -32), inclusive nas bordas, para o bot nao apostar em Tetris.
    A partir do SEGUNDO poco, cada casa custa mais W["poco_extra"]: o I cobre um por
    vez, entao poco extra e dependencia dobrada (antes esse peso era documentado e
    nao usado, e o bot deixava varias colunas vazias)."""
    total = 0.0
    pocos = 0
    for profundidade in _profundidades_poco_um(alturas):
        if profundidade >= 2:
            pocos += 1
            if pocos > 1:
                total += W["poco_extra"] * min(profundidade, 4)
        if profundidade >= 3:
            total += CUSTO_POCO_PROFUNDO * min(profundidade - 2, 6)
    return total


def _fator_headroom(altura_maxima):
    """Quanto de um setup ainda vale: cheio com a pilha em ALTURA_ALVO, zero em ALTURA_MANOBRA.
    Mantem a construcao de T-spin subordinada ao empilhamento baixo, como no jogo real."""
    if altura_maxima <= ALTURA_ALVO:
        return 1.0
    return max(0.0, min(1.0, (ALTURA_MANOBRA - altura_maxima)
                        / float(ALTURA_MANOBRA - ALTURA_ALVO)))


def _avaliar_impl(linhas, b2b, combo, ctx, calcular_dependencia,
                  calcular_estrategia=True):
    """Nota do tabuleiro; ctx indica T/I alcancaveis no lookahead e o molde de abertura."""
    cobertas = 0
    buracos = 0
    soma = 0
    alt = [0] * COLUNAS
    maxh = 0
    trans = 0
    for r in range(LINHAS):
        v = linhas[r]
        if cobertas:
            buracos += POP[~v & cobertas & CHEIA]
        if v or maxh:
            if not maxh:
                maxh = LINHAS - r
            trans += POP[(v ^ (v >> 1)) & 511] + 2 - (v & 1) - (v >> 9)
        novos = v & ~cobertas
        if novos:
            h = LINHAS - r
            soma += POP[novos] * h
            while novos:
                b = novos & -novos
                alt[b.bit_length() - 1] = h
                novos ^= b
            cobertas |= v

    # poco na borda (para quad): coluna 0 ou 9 pelo menos 2 mais baixa que a vizinha
    poco = -1
    d9, d0 = alt[8] - alt[9], alt[1] - alt[0]
    if d9 >= 2 and d9 >= d0:
        poco = 9
    elif d0 >= 2:
        poco = 0
    rug = 0
    dips = 0
    for i in range(COLUNAS - 1):
        if (i == 8 and poco == 9) or (i == 0 and poco == 0):
            continue
        d = alt[i] - alt[i + 1]
        if d < 0:
            d = -d
        rug += d if d < 3 else d * 1.7
    for i in range(1, COLUNAS - 1):
        a = alt[i]
        if alt[i - 1] - a >= 2 and alt[i + 1] - a >= 2:
            dips += 1

    tem_T = ctx[0]
    especial = 0.0
    prontos, est, setups = _slots_detalhes(linhas)
    planejados = _slots_planejados(linhas)
    if planejados:
        buracos = max(0, buracos - min(buracos, planejados))
    if est:
        fator_t = 1.0 if tem_T else FATOR_SEM_T
        tsd = W["slot"] * ESTAGIO[est] * fator_t
        tsd += BONUS_DT_SLOT * min(max(setups - 1, 0), 2) * fator_t
        if tsd > especial:
            especial = tsd

    # Setup so compensa com pilha baixa: um slot em pilha alta nao paga o risco de topout.
    especial *= _fator_headroom(maxh)

    nota = (especial - W["soma"] * soma - W["buraco"] * buracos - W["trans"] * trans
            - W["rug"] * rug - W["dip"] * dips)
    if len(ctx) > 2 and ctx[2] and openers.ativo(linhas):
        nota += PESO_MOLDE * openers.aderencia_ativa(linhas)
    if maxh > ALTURA_ALVO:
        nota -= W["altura"] * (maxh - ALTURA_ALVO) ** 1.5
    if b2b:
        nota += W["b2b"] * (b2b if b2b < 8 else 8) * _fator_headroom(maxh)
    nota -= W["coberto"] * max(0, _sobreposicoes(linhas) - (prontos if USAR_SPINS else 0))
    if calcular_dependencia:
        nota += _penalidade_dependencia(linhas)
    nota -= W["altura_max"] * maxh
    if calcular_estrategia:
        nota -= _penalidade_pocos_largos(alt)
        nota -= _penalidade_pocos_um(alt)
    nota -= _penalidade_buracos_estreitos(linhas)
    return nota


def _avaliar(linhas, b2b, combo, ctx):
    return _avaliar_impl(linhas, b2b, combo, ctx, True, True)


def _avaliar_rapido(linhas, b2b, combo, ctx):
    """Avalia estados intermediarios com recursos caros adiados para as folhas."""
    return _avaliar_impl(linhas, b2b, combo, ctx, False, False)


_CACHE_AVALIAR = {}   # notas de tabuleiro reaproveitadas entre decisoes seguidas


def _altura_max(linhas):
    """Altura da coluna mais alta (0 = tabuleiro vazio)."""
    topo = next((r for r, mascara in enumerate(linhas) if mascara), LINHAS)
    return LINHAS - topo


def _custo_simples(letra, k, col):
    return (0 if k == 0 else 1) + abs(col - COL_SPAWN[letra])


def _expandir(linhas, b2b, combo, letra, cache, completo, aproximado_t=False):
    """Usa colocacoes exatas na raiz/perto, e aproximadas em continuacoes distantes."""
    aproximado = not completo and (letra != "T" or aproximado_t)
    chave = (letra, tuple(linhas), "lookahead") if aproximado else (letra, tuple(linhas))
    lista = cache.get(chave)
    if lista is None:
        if aproximado:
            colocacoes = _colocacoes_rapidas(tuple(linhas), letra)
        elif letra != "T" and _pilha_aberta(linhas):
            geometricas = _colocacoes_rapidas(tuple(linhas), letra)
            pode_limpar = any(
                linhas[y + dr] | mascara == CHEIA
                for _, _, y, mascaras, _, _, _ in geometricas
                for dr, mascara in enumerate(mascaras)
            )
            colocacoes = (geometricas if letra == "O" or not pode_limpar
                          else _get_colocacoes(linhas, letra))
        else:
            colocacoes = _get_colocacoes(linhas, letra)
        lista = cache[chave] = list(colocacoes)
    saida = []
    for k, col, y, ms, cels, sp, acoes in lista:
        depois, n = _aplicar(linhas, y, ms)
        if n:
            perfect_clear = MODO == "ataque" and not any(depois)
            b2, c2, val = transicao(b2b, combo, sp, n, perfect_clear)
            if perfect_clear:
                val += BONUS_PERFECT_CLEAR
        else:
            b2, c2, val = b2b, 0, 0.0
        celulas = cels if completo else None
        saida.append((val, depois, b2, c2, k, col, celulas, sp, acoes, n,
                      (len(acoes) if acoes is not None else _custo_simples(letra, k, col)) + 1))
    return saida


def _horizonte_abertura(linhas):
    """Use visible pieces for opening setup; shorten deeper-stack searches to stay responsive."""
    if not any(linhas):
        return 3
    ocupadas = sum(POP[linha] for linha in linhas)
    topo = next((r for r, mascara in enumerate(linhas) if mascara), LINHAS)
    if ocupadas <= 12 and LINHAS - topo <= 4:
        return 4
    return 3


def _bonus_abertura_tsd(linhas, ctx):
    """Early TSD setup bonus for TKI-like starts and stacked DT-style slots."""
    tem_t, tem_i = ctx[0], ctx[1]
    if not USAR_SPINS:
        return 0.0
    fator_t = 1.0 if tem_t else FATOR_SEM_T
    _, estagio, setups = _slots_detalhes(linhas)
    tki = BONUS_TKI_SETUP * ESTAGIO[estagio] * (1.0 if tem_i else 0.65)
    dt = BONUS_DT_SETUP * min(max(setups - 1, 0), 2)
    return (tki + dt) * fator_t


def _bonus_clear_abertura(spin, n):
    """Reward an early full T-spin double; board quality is scored separately."""
    return BONUS_TKI_CLEAR if spin == "full" and n == 2 else 0.0


def _bonus_dt_cannon(abertura, ja_fez_tsd, letra, spin, n):
    """Reward a reachable T-spin triple after the opener's T-spin double."""
    if abertura and ja_fez_tsd and letra == "T" and spin == "full" and n == 3:
        return BONUS_DT_CANNON
    return 0.0


def _tem_perfect_clear_no_proximo(linhas, letra, cache):
    ocupadas = sum(POP[linha] for linha in linhas)
    alvo = {6: 1, 16: 2, 36: 4}.get(ocupadas)
    if alvo is None or any(linhas[:LINHAS - alvo]):
        return False

    if letra is None:
        return False
    letras = (letra,) if isinstance(letra, str) else tuple(dict.fromkeys(letra))
    if not letras:
        return False
    chave = ("pc_next", letras, tuple(linhas))
    if chave in cache:
        return cache[chave]
    for peca in letras:
        chave_colocacoes = (peca, tuple(linhas))
        colocacoes = cache.get(chave_colocacoes)
        if colocacoes is None:
            colocacoes = cache[chave_colocacoes] = list(_get_colocacoes(linhas, peca))
        for _, _, y, mascaras, _, _, _ in colocacoes:
            depois, n = _aplicar(linhas, y, mascaras)
            if n and not any(depois):
                cache[chave] = True
                return True
    cache[chave] = False
    return False


def _bonus_tspin_setup_estagio(linhas, pecas_visiveis):
    """Progresso do slot de TSD. Com o T a vista vale cheio; sem ele, vale descontado
    (a bag sempre traz um T, e o slot precisa estar montado quando ele chegar)."""
    if not USAR_SPINS:
        return 0.0
    _, estagio, setups = _slots_detalhes(linhas)
    if estagio not in BONUS_TSPIN_SETUP_STAGE:
        return 0.0
    fator = 1.0 if "T" in pecas_visiveis else FATOR_SEM_T
    return (BONUS_TSPIN_SETUP_STAGE[estagio] * min(setups, 2) * fator
            * _fator_headroom(_altura_max(linhas)))


def _bonus_tspin_clear(linhas, pecas_visiveis, b2b, combo, cache):
    """Pontua um T-spin full com clear, alcancavel por SRS, fora de moldes especificos."""
    if not USAR_SPINS or "T" not in pecas_visiveis:
        return 0.0
    chave = ("tspin_clear_setup", tuple(linhas), b2b, combo, MODO)
    if chave in cache:
        return cache[chave]
    chave_geometria = ("tspin_clear_geometria", tuple(linhas))
    geometria = cache.get(chave_geometria)
    if geometria is None:
        geometria = cache[chave_geometria] = _tem_tspin_clear_geometrico(linhas)
    if not geometria:
        cache[chave] = 0.0
        return 0.0
    chave_colocacoes = ("T", tuple(linhas))
    colocacoes = cache.get(chave_colocacoes)
    if colocacoes is None:
        chave_rapida = ("tspin_setup_colocacoes", tuple(linhas))
        colocacoes = cache.get(chave_rapida)
        if colocacoes is None:
            colocacoes = cache[chave_rapida] = colocacoes_alcancaveis(
                linhas, "T", parar_no_primeiro_tspin_clear=True)
    melhor = 0.0
    for _, _, y, mascaras, _, spin, _ in colocacoes:
        if spin != "full":
            continue
        depois, n = _aplicar(linhas, y, mascaras)
        if not n:
            continue
        ataque = transicao(b2b, combo, spin, n)[2]
        if MODO == "ataque" and ataque <= 0:
            continue
        bonus_base = BONUS_TSPIN_SETUP.get(n, 0.0)
        potencial = bonus_base + FATOR_ATAQUE_TSPIN_SETUP * (
            PESO_VALOR * ataque + _bonus_tspin(spin, n))
        melhor = max(melhor, potencial)
    cache[chave] = melhor
    return melhor


def _busca(linhas, b2b, combo, pecas, ctx, cache, larguras=None, peca_final=None,
           abertura=False, reservas=()):
    """Beam search com disponibilidade futura de T/I recalculada apos cada colocacao."""
    larguras = LARGURA if larguras is None else larguras
    tsd_abertura = abertura and len(pecas) >= 4

    def contexto_depois(proxima):
        futuras = list(pecas[proxima:])
        if peca_final:
            futuras.append(peca_final)
        disponiveis = futuras + list(reservas)
        return ("T" in disponiveis, "I" in disponiveis, len(ctx) > 2 and ctx[2])

    def avaliar(board, b2, c2, ctx_futuro, completa=False):
        chave = (tuple(board), b2, c2, ctx_futuro, MODO, completa)
        v = _CACHE_AVALIAR.get(chave)
        if v is None:
            if len(_CACHE_AVALIAR) > 60000:
                _CACHE_AVALIAR.clear()
            avaliador = _avaliar if completa else _avaliar_rapido
            v = _CACHE_AVALIAR[chave] = avaliador(board, b2, c2, ctx_futuro)
        return v

    def custo_gastar_t(letra, spin, n, linhas_ref):
        """Desincentiva usar o T fora de T-spin quando ha slot de TSD pronto/em construcao."""
        if letra != "T" or spin == "full" or not USAR_SPINS:
            return 0.0
        if _altura_max(linhas_ref) >= ALTURA_PERIGO:
            return 0.0
        _, estagio, _ = _slots_detalhes(linhas_ref)
        if estagio >= 2:
            return CUSTO_GASTAR_T
        return 0.0

    def refinar_setups_tspin(estados, visiveis):
        if not USAR_SPINS or "T" not in visiveis:
            return estados
        indices = sorted(range(len(estados)), key=lambda idx: estados[idx][0], reverse=True)
        verificadas = 0
        geometria_verificada = 0
        for idx in indices:
            if geometria_verificada >= TSPIN_SETUP_GEOMETRY_CANDIDATES:
                break
            _, _, _, board, b2, c2, _ = estados[idx]
            chave_geometria = ("tspin_clear_geometria", tuple(board))
            geometria = cache.get(chave_geometria)
            if geometria is None:
                geometria = cache[chave_geometria] = _tem_tspin_clear_geometrico(board)
            geometria_verificada += 1
            if not geometria:
                continue
            verificadas += 1
            bonus = _bonus_tspin_clear(board, visiveis, b2, c2, cache)
            if bonus:
                nota, s, raiz_i, board, b2, c2, tsd = estados[idx]
                estados[idx] = (nota + bonus, s + bonus, raiz_i, board, b2, c2, tsd)
            if verificadas >= TSPIN_SETUP_CANDIDATES:
                break
        return estados

    molde = len(ctx) > 2 and ctx[2]
    # Sem modo de emergencia: empilhar baixo e limpar acima de ALTURA_DOWNSTACK e o
    # comportamento normal do bot, nao uma reacao a perigo.
    urgente = _altura_max(linhas) >= ALTURA_DOWNSTACK
    raiz = _expandir(linhas, b2b, combo, pecas[0], cache, True)
    if not raiz:
        return None
    raiz = _filtrar_buracos_novos(linhas, raiz)
    raiz = _filtrar_pocos_novos(linhas, raiz)
    if not urgente:   # com a pilha baixa ainda vale proteger B2B de clears secos
        raiz = _filtrar_quebra_b2b(raiz, b2b, linhas)
        raiz = _filtrar_clear_fraco(raiz, linhas, b2b)
    raiz = _filtrar_tspin_obrigatorio(raiz, pecas[0])
    alturas_iniciais = _altura_colunas(linhas)
    frente = []
    peca_depois = pecas[1] if len(pecas) > 1 else peca_final
    visiveis_depois = tuple(dict.fromkeys(
        ([peca_depois] if peca_depois else []) + list(reservas)))
    for i, c in enumerate(raiz):
        s = (PESO_VALOR * _valor_planejamento(c[9], c[7], c[0])
             + _bonus_tspin(c[7], c[9])
             + (BONUS_TETRIS if c[9] == 4 and not c[7] else 0.0)
             - _custo_clear_pequeno(c[9], c[7], c[0], c[1])
             - _custo_quebra_b2b(c[9], c[7], b2b, max(alturas_iniciais),
                                not any(c[1]))
             - CUSTO_TECLA * c[10]
             + _ajuste_i(pecas[0], c, linhas)
             + (_bonus_clear_abertura(c[7], c[9]) if tsd_abertura else 0.0)
             - custo_gastar_t(pecas[0], c[7], c[9], linhas)
             - (CUSTO_CLEAR_MOLDE if molde and c[9] and c[7] != "full" and c[0] <= 0
                else 0.0))
        if not any(linhas) and pecas[0] in ("S", "Z") and c[4] % 2 and not c[9]:
            s -= CUSTO_ABERTURA_SZ_VERTICAL
        ja_fez_tsd = pecas[0] == "T" and c[7] == "full" and c[9] == 2
        ctx_futuro = contexto_depois(1)
        s += _bonus_tspin_setup_estagio(c[1], visiveis_depois)
        if visiveis_depois and _tem_perfect_clear_no_proximo(c[1], visiveis_depois, cache):
            s += BONUS_PCO_POTENCIAL
        frente.append((s + avaliar(c[1], c[2], c[3], ctx_futuro,
                                   completa=len(pecas) == 1),
                       s, i, c[1], c[2], c[3], ja_fez_tsd))
    frente = refinar_setups_tspin(frente, visiveis_depois)
    melhor1 = max(frente, key=lambda f: f[0])
    for d in range(1, len(pecas)):
        frente.sort(key=lambda f: -f[0])
        largura = larguras[d - 1] if d - 1 < len(larguras) else 1
        frente = frente[:largura]
        nova = []
        peca_depois = pecas[d + 1] if d == len(pecas) - 2 else (
            peca_final if d == len(pecas) - 1 else None)
        visiveis_depois = tuple(dict.fromkeys(
            ([peca_depois] if peca_depois else []) + list(reservas)))
        ctx_futuro = contexto_depois(d + 1)
        for _, s, i, board, b2, c2, ja_fez_tsd in frente:
            urgente_board = _altura_max(board) >= ALTURA_DOWNSTACK
            molde_board = molde and openers.ativo(board)
            alturas_board = _altura_colunas(board)
            continuacoes = _expandir(board, b2, c2, pecas[d], cache, False,
                                     aproximado_t=d >= 2)
            continuacoes = _filtrar_buracos_novos(board, continuacoes)
            continuacoes = _filtrar_tspin_obrigatorio(continuacoes, pecas[d])
            if not urgente_board:
                continuacoes = _filtrar_clear_fraco(continuacoes, board, b2)
                continuacoes = _filtrar_quebra_b2b(continuacoes, b2, board)
            for c in continuacoes:
                s2 = (s + PESO_VALOR * _valor_planejamento(c[9], c[7], c[0])
                      + _bonus_tspin(c[7], c[9])
                      + (BONUS_TETRIS if c[9] == 4 and not c[7] else 0.0)
                      - _custo_clear_pequeno(c[9], c[7], c[0], c[1])
                      - _custo_quebra_b2b(c[9], c[7], b2, max(alturas_board),
                                         not any(c[1]))
                      + _ajuste_i(pecas[d], c, board)
                      + (_bonus_clear_abertura(c[7], c[9]) if tsd_abertura else 0.0)
                      + _bonus_dt_cannon(tsd_abertura, ja_fez_tsd, pecas[d], c[7], c[9])
                      - custo_gastar_t(pecas[d], c[7], c[9], board))
                if molde_board and c[9] and c[7] != "full" and c[0] <= 0:
                    s2 -= CUSTO_CLEAR_MOLDE
                if molde_board and pecas[d] == "T" and not (c[7] == "full" and c[9] >= 2):
                    s2 -= BONUS_SAVE_T_MOLDE * 0.6
                s2 += _bonus_tspin_setup_estagio(c[1], visiveis_depois)
                if visiveis_depois and _tem_perfect_clear_no_proximo(
                        c[1], visiveis_depois, cache):
                    s2 += BONUS_PCO_POTENCIAL
                nota = s2 + avaliar(c[1], c[2], c[3], ctx_futuro,
                                    completa=False)
                if tsd_abertura:
                    nota += _bonus_abertura_tsd(c[1], ctx_futuro)
                novo_tsd = ja_fez_tsd or (
                    pecas[d] == "T" and c[7] == "full" and c[9] == 2)
                nova.append((nota, s2, i, c[1], c[2], c[3], novo_tsd))
        if not nova:
            break
        # Refina genericamente apenas quando T esta prestes a ser usado.
        if ((d == len(pecas) - 2 and peca_depois == "T")
                or (d == len(pecas) - 1 and peca_final == "T")):
            nova = refinar_setups_tspin(nova, visiveis_depois)
        if d == len(pecas) - 1:
            contagens = {}
            refinadas = []
            for estado in sorted(nova, key=lambda item: item[0], reverse=True):
                nota, s, raiz_i, board, b2, c2, tsd = estado
                contagem = contagens.get(raiz_i, 0)
                if contagem >= FINAL_EXACT_CANDIDATES_PER_ROOT:
                    continue
                contagens[raiz_i] = contagem + 1
                nota_exata = s + avaliar(board, b2, c2, ctx_futuro, completa=True)
                refinadas.append((nota_exata, s, raiz_i, board, b2, c2, tsd))
            nova = refinadas
        frente = nova
    if len(pecas) > 1 and frente:
        melhor = max(frente, key=lambda f: f[0])
        return melhor[0], raiz[melhor[2]]
    return melhor1[0], raiz[melhor1[2]]


# ---------- decisao ----------
def _jogada_reserva(linhas, letra, b2b=0, combo=0, usar_hold=False):
    """Ultimo recurso quando a busca nao devolve jogada: pega qualquer colocacao alcancavel,
    preferindo a que deixa a pilha mais baixa e com menos buracos. Sem isto o bot parava de
    jogar no topo (o log repetia "SEM JOGADA" e a partida acabava parada)."""
    melhor = None
    for k, col, y, ms, cels, sp, acoes in _get_colocacoes(linhas, letra):
        depois, n = _aplicar(linhas, y, ms)
        chave = (_altura_max(depois), _buracos(depois), -n)
        if melhor is None or chave < melhor[0]:
            melhor = (chave, k, col, cels, sp, acoes, n)
    if melhor is None:
        return None
    _, k, col, cels, sp, acoes, n = melhor
    b2n, c2n, val = transicao(b2b, combo, sp, n)
    teclas = _acoes_para_teclas(linhas, acoes, letra)
    teclas.append(("toque", "drop"))
    return Jogada(usar_hold, letra, k, col, frozenset(cels), teclas, sp, n, b2n, c2n, val)


def jogada_para(linhas, letra, celulas, usar_hold=False, b2b=0, combo=0):
    """Jogada que coloca `letra` exatamente nas `celulas` dadas, com a rota do BFS.

    Usada pelas rotas de opener (openers.planejar): a rota diz ONDE a peca vai, e
    esta funcao acha COMO chegar la (giros/kicks/DAS) e monta as teclas. Devolve
    None quando a colocacao nao e alcancavel com o estado atual do tabuleiro.
    """
    alvo = frozenset(celulas)
    for k, col, y, ms, cels, sp, acoes in colocacoes_alcancaveis(linhas, letra):
        if frozenset(cels) != alvo:
            continue
        _depois, n = _aplicar(linhas, y, ms)
        b2n, c2n, val = transicao(b2b, combo, sp, n)
        teclas = _acoes_para_teclas(linhas, acoes, letra)
        teclas.append(("toque", "drop"))
        return Jogada(usar_hold, letra, k, col, alvo, teclas, sp, n, b2n, c2n, val)
    return None


def decidir(pilha, mao, hold, proximas, permitir_hold=True, b2b=0, combo=0,
            bag_seguinte=None, abertura=None, linhas_lixo=0, _rapido=True):
    """Escolhe jogada; a janela de opener e informada pelo chamador.

    `linhas_lixo` e aceito para o laco ao vivo, mas nao entra na nota: a politica e a mesma
    com ou sem lixo, e quem limita a altura e o custo de altura (ver ALTURA_DOWNSTACK).
    """
    global RAPIDO
    if not _rapido:   # repeticao sem a busca rapida (quando a rota fisica da escolhida falha)
        antigo, RAPIDO = RAPIDO, False
        try:
            return decidir(pilha, mao, hold, proximas, permitir_hold, b2b, combo,
                           bag_seguinte, abertura, linhas_lixo)
        finally:
            RAPIDO = antigo
    linhas = para_linhas(pilha)
    prox = list(proximas)
    visiveis = {mao, *prox}
    if hold:
        visiveis.add(hold)
    if bag_seguinte:
        visiveis.add(bag_seguinte)
    fase_abertura = (not any(linhas) if abertura is None else bool(abertura))
    molde = (OPENERS and fase_abertura and "T" in visiveis
             and openers.ativo(linhas))
    ctx = ("T" in visiveis, "I" in visiveis, molde)

    cache = {}
    opcoes = []
    horizonte = (max(_horizonte_abertura(linhas), HORIZONTE_ABERTURA) if fase_abertura
                 else _horizonte_abertura(linhas))
    if (not fase_abertura and ctx[0] and len(prox) >= 3
            and _altura_max(linhas) < ALTURA_SEGURA):
        horizonte = max(horizonte, 4)
    # Reservar T so dentro do molde do DT (onde ele e o TSD/TST do opener). Fora dele nao
    # ha desconto por jogar T sem clear: era isso que segurava o T no hold com slot pronto.
    save_t = BONUS_SAVE_T_MOLDE if molde else 0.0
    if molde and any(linhas):
        horizonte = HORIZONTE_MOLDE
    profundidade = horizonte - 1
    usar_bag = (not any(linhas) and len(prox) == 5 and bag_seguinte in SPAWN)
    larguras = LARGURA_PCO if not any(linhas) and len(prox) >= 4 else LARGURA
    pecas_a = [mao] + (prox if usar_bag else prox[:profundidade])
    larguras_busca = larguras + (5,) if usar_bag else larguras
    a = _busca(linhas, b2b, combo, pecas_a, ctx, cache, larguras_busca,
               bag_seguinte if usar_bag else None, fase_abertura,
               reservas=(hold,) if hold else ())
    if a:
        nota = a[0]
        if fase_abertura and mao == "T" and (a[1][7] != "full" or a[1][9] == 0):
            nota -= save_t
        opcoes.append((nota, False, mao, a[1]))
    outra = hold if hold else (prox[0] if prox else None)
    if outra and permitir_hold:
        if usar_bag and hold:
            seguintes = prox
        elif usar_bag:
            seguintes = prox[1:] + [bag_seguinte]
        else:
            seguintes = prox[:profundidade] if hold else prox[1:horizonte]
        b = _busca(linhas, b2b, combo, [outra] + seguintes, ctx, cache, larguras_busca,
                   bag_seguinte if usar_bag and hold else None, fase_abertura, reservas=(mao,))
        if b:
            nota = b[0] - CUSTO_HOLD - CUSTO_TECLA
            if fase_abertura and mao == "T":
                nota += save_t
            elif (fase_abertura and hold == "T" and outra == "T"
                  and (b[1][7] != "full" or b[1][9] == 0)):
                nota -= save_t
            opcoes.append((nota, True, outra, b[1]))
    if opcoes:
        buracos_atuais = _buracos(linhas)
        sem_buracos_novos = [o for o in opcoes if _buracos(o[3][1]) <= buracos_atuais]
        if sem_buracos_novos:
            opcoes = sem_buracos_novos
    if not opcoes:
        # Nenhuma jogada avaliada: em vez de parar de jogar, joga a colocacao alcancavel que
        # mais baixa a pilha (pode abrir espaco para sair do topo).
        reserva = _jogada_reserva(linhas, mao, b2b, combo)
        if reserva is None and permitir_hold and hold:
            reserva = _jogada_reserva(linhas, hold, b2b, combo, usar_hold=True)
        return reserva
    _, usar, letra, c = max(opcoes, key=lambda o: o[0])
    val, depois, b2n, c2n, k, col, cels, sp, acoes, n, _ = c
    if acoes is None:   # jogada escolhida na busca rapida: confere a rota fisica (BFS) so dela
        alvo = frozenset(cels)
        for k2, col2, _, _, cel2, sp2, ac2 in colocacoes_alcancaveis(linhas, letra):
            if frozenset(cel2) == alvo:
                k, col, sp, acoes = k2, col2, sp2, ac2
                break
        else:
            return decidir(pilha, mao, hold, proximas, permitir_hold, b2b, combo, bag_seguinte,
                           abertura, linhas_lixo, _rapido=False)
    teclas = _acoes_para_teclas(linhas, acoes, letra)
    teclas.append(("toque", "drop"))
    if usar:
        teclas.insert(0, ("toque", "hold"))
    return Jogada(usar, letra, k, col, frozenset(cels), teclas, sp, n, b2n, c2n, val)


# ---------- verificacao independente: executa as teclas numa fisica simples ----------
def executar_teclas(pilha, letra, teclas):
    """Interpreta as teclas (como o jogo) e devolve (celulas_finais, spin)."""
    linhas = para_linhas(pilha)
    k, br, bc, f = 0, LINHA_SPAWN, COL_SPAWN[letra], 0
    cels = ROT[letra]
    for t in teclas:
        nome = t[0] if t[0] != "toque" else t[1]
        if t[0] == "toque" and t[1] == "hold":
            continue
        if nome in ("esq", "dir") or t[0] == "das":
            lado = t[1]
            dc = -1 if lado == "esq" else 1
            passos = 99 if t[0] == "das" else 1
            while passos and _livre(linhas, cels[k], br, bc + dc):
                bc += dc
                f = 0
                passos -= 1
        elif t[0] == "sd":
            while _livre(linhas, cels[k], br + 1, bc):
                br += 1
                f = 0
        elif t[0] == "toque" and t[1] in ("cw", "ccw", "180"):
            estado = _tentar_rotacao(linhas, letra, k, br, bc, t[1])
            if estado is not None:
                k, br, bc, f = estado
        elif t[0] == "toque" and t[1] == "drop":
            while _livre(linhas, cels[k], br + 1, bc):
                br += 1
                f = 0
            sp = (_tipo_spin(linhas, k, br, bc, f) if letra == "T"
                  else _spin_all_mini(linhas, letra, k, br, bc, f))
            return frozenset((br + r, bc + c) for r, c in cels[k]), sp
    return None, ""


# ---------- simulacao (sem o jogo) ----------
def _sacola(rnd):
    s = list("IOTSZJL")
    rnd.shuffle(s)
    return s


def _contar_linhas_lixo(celulas):
    por_linha = {}
    for r, _c in celulas:
        por_linha[r] = por_linha.get(r, 0) + 1
    return sum(1 for n in por_linha.values() if n >= 5)


def simular(n_pecas=500, seed=0, decidir_fn=None, verificar=False, pressao=0.0):
    """Joga n_pecas. pressao = linhas de garbage por peca (o ataque do bot cancela garbage pendente).
    Tambem mede altura/buracos, clears, spins validos, B2B/combo e abandono do opener.
    PPS/APM-equivalentes usam o tempo de simulacao offline e nao representam velocidade real de jogo.
    Um opener so conta como tentativa com T visivel dentro da janela; e abandonado ao sair
    do molde ou fechar a janela antes do TSD seguido de TST.
    decidir_fn pode receber dez argumentos (incluindo lixo recebido), nove, sete ou quatro legados."""
    decidir_fn = decidir_fn or decidir
    try:
        assinatura = signature(decidir_fn)
    except (TypeError, ValueError):
        quantidade_argumentos = 10
    else:
        quantidade_argumentos = 4
        for quantidade in (10, 9, 7, 4):
            try:
                assinatura.bind(*(None,) * quantidade)
            except TypeError:
                continue
            quantidade_argumentos = quantidade
            break
    rnd = random.Random(seed)
    fila, pilha, hold = [], frozenset(), None
    celulas_lixo = set()
    b2b = combo = 0
    est = dict(pecas=0, linhas=0, valor=0.0, quads=0, tsd=0, tst=0, tss=0,
               singles=0, doubles=0, triples=0, valid_spins=0,
               b2b_continuations=0, b2b_breaks=0, max_b2b=0,
               combo_continuations=0, max_combo=0, max_height=0,
               holes_created=0, max_holes=0, opener_attempts=0,
               opener_abandoned=0, perfect_clears=0, teclas=0, garbage=0, vivo=True)
    est["garbage_rows_peak"] = 0
    pendente, buraco = 0.0, rnd.randrange(COLUNAS)
    buracos_total = 0
    alturas_jogo = []
    inicio = time.perf_counter()
    pecas_abertura = 0
    opener_tentado = False
    opener_tsd = opener_completo = False
    opener_saiu_do_molde = False

    def medir_pilha(board):
        linhas = para_linhas(board)
        cobertas = buracos = 0
        for linha in linhas:
            buracos += POP[~linha & cobertas & CHEIA]
            cobertas |= linha
        topo = next((r for r, linha in enumerate(linhas) if linha), LINHAS)
        return LINHAS - topo, buracos

    for _ in range(n_pecas):
        while len(fila) < 8:
            fila += _sacola(rnd)
        mao = fila.pop(0)
        hold_vazio = hold is None
        abertura = pecas_abertura < JANELA_ABERTURA
        linhas_lixo = _contar_linhas_lixo(celulas_lixo)
        est["garbage_rows_peak"] = max(est["garbage_rows_peak"], linhas_lixo)
        if (OPENERS and abertura and not opener_tentado
                and "T" in (mao, hold, *fila[:5])
                and openers.ativo(para_linhas(pilha))):
            opener_tentado = True
            est["opener_attempts"] += 1
        if quantidade_argumentos == 10:
            j = decidir_fn(pilha, mao, hold, fila[:5], True, b2b, combo,
                           None, abertura, linhas_lixo)
        elif quantidade_argumentos == 9:
            j = decidir_fn(pilha, mao, hold, fila[:5], True, b2b, combo,
                           None, abertura)
        elif quantidade_argumentos == 7:
            j = decidir_fn(pilha, mao, hold, fila[:5], True, b2b, combo)
        else:
            j = decidir_fn(pilha, mao, hold, fila[:5])
        if j is None:
            est["vivo"] = False
            break
        if j.usar_hold:
            if hold is None:
                hold, mao = mao, fila.pop(0)
            else:
                hold, mao = mao, hold
            if mao != j.letra:
                raise AssertionError("hold devolveu peca diferente da planejada")
        pecas_abertura += 1 + int(j.usar_hold and hold_vazio)
        if verificar and j.teclas:
            cels, sp = executar_teclas(pilha, j.letra, j.teclas)
            if cels != j.celulas or sp != j.spin:
                raise AssertionError(f"teclas nao reproduzem a jogada: {j.letra} {j.teclas}")
        _, buracos_antes = medir_pilha(pilha)
        board_antes_clear = pilha | j.celulas
        linhas_completas = {r for r, mascara in enumerate(para_linhas(board_antes_clear))
                            if mascara == CHEIA}
        celulas_lixo = {
            (r + sum(1 for linha in linhas_completas if linha > r), c)
            for r, c in celulas_lixo if r not in linhas_completas
        }
        pilha, n = _limpar(board_antes_clear)
        altura, buracos = medir_pilha(pilha)
        est["max_height"] = max(est["max_height"], altura)
        alturas_jogo.append(altura)
        est["holes_created"] += max(0, buracos - buracos_antes)
        est["max_holes"] = max(est["max_holes"], buracos)
        if n and not pilha:
            est["perfect_clears"] += 1
        if n == 1:
            est["singles"] += 1
        elif n == 2:
            est["doubles"] += 1
        elif n == 3:
            est["triples"] += 1
        perfect_clear = bool(n and not pilha)
        difficult = bool(j.spin and n) or n == 4 or (
            perfect_clear and MODO == "ataque")
        if difficult and b2b:
            est["b2b_continuations"] += 1
        elif n and b2b:
            est["b2b_breaks"] += 1
        if n and combo:
            est["combo_continuations"] += 1
        if j.spin and n:
            est["valid_spins"] += 1
        if opener_tentado and not opener_saiu_do_molde and abertura:
            if j.letra == "T" and j.spin == "full" and n == 2:
                opener_tsd = True
            elif opener_tsd and j.letra == "T" and j.spin == "full" and n == 3:
                opener_completo = True
        if (opener_tentado and not opener_completo
                and (pecas_abertura >= JANELA_ABERTURA
                     or not openers.ativo(para_linhas(pilha)))):
            opener_saiu_do_molde = True
        est["pecas"] += 1
        est["teclas"] += len(j.teclas)
        est["linhas"] += n
        b2b, combo, val = transicao(
            b2b, combo, j.spin, n, perfect_clear and MODO == "ataque")
        est["max_b2b"] = max(est["max_b2b"], b2b)
        est["max_combo"] = max(est["max_combo"], combo)
        if perfect_clear and MODO == "ataque":
            val += BONUS_PERFECT_CLEAR
        est["valor"] += val
        if n == 4:
            est["quads"] += 1
        elif j.spin == "full" and n == 2:
            est["tsd"] += 1
        elif j.spin == "full" and n == 3:
            est["tst"] += 1
        elif j.spin and n == 1:
            est["tss"] += 1
        if pressao:
            pendente += pressao
            if MODO == "ataque":
                pendente = max(0.0, pendente - val)
            while pendente >= 1.0:
                pendente -= 1.0
                if rnd.random() < 0.3:
                    buraco = rnd.randrange(COLUNAS)
                if any(r == 0 for r, _ in pilha):
                    est["vivo"] = False
                    break
                pilha = frozenset((r - 1, c) for r, c in pilha) | frozenset(
                    (LINHAS - 1, c) for c in range(COLUNAS) if c != buraco)
                celulas_lixo = {(r - 1, c) for r, c in celulas_lixo}
                celulas_lixo.update((LINHAS - 1, c) for c in range(COLUNAS) if c != buraco)
                est["garbage"] += 1
                est["garbage_rows_peak"] = max(
                    est["garbage_rows_peak"], _contar_linhas_lixo(celulas_lixo))
                altura, buracos = medir_pilha(pilha)
                est["max_height"] = max(est["max_height"], altura)
                est["max_holes"] = max(est["max_holes"], buracos)
            if not est["vivo"]:
                break
        buracos_total += medir_pilha(pilha)[1]
    if est["opener_attempts"] and opener_saiu_do_molde and not opener_completo:
        est["opener_abandoned"] = 1
    est["tempo_s"] = time.perf_counter() - inicio
    pecas_medidas = max(est["pecas"], 1)
    tempo_medicao = max(est["tempo_s"], 1e-9)
    est["pps_equivalente"] = est["pecas"] / tempo_medicao
    est["apm_equivalente"] = est["valor"] * 60.0 / tempo_medicao
    est["app"] = est["valor"] / pecas_medidas
    est["buracos_medios"] = buracos_total / pecas_medidas
    est["altura_media"] = (sum(alturas_jogo) / len(alturas_jogo)) if alturas_jogo else 0.0
    ordenadas = sorted(alturas_jogo)
    est["altura_p95"] = ordenadas[min(len(ordenadas) - 1, int(0.95 * len(ordenadas)))] if ordenadas else 0
    b2b_eventos = est["b2b_continuations"] + est["b2b_breaks"]
    est["b2b_mantido_pct"] = (
        100.0 * est["b2b_continuations"] / b2b_eventos if b2b_eventos else 0.0)
    return est


if __name__ == "__main__":
    import time
    t0 = time.time()
    tot = 0
    for s in range(3):
        e = simular(500, s, verificar=True, pressao=0.1)
        tot += e["pecas"]
        print(f"partida {s}: {e['pecas']}/500 pecas, ataque {e['valor']:.0f}, quads {e['quads']}, "
              f"TSD {e['tsd']}, TST {e['tst']}, APP {e['app']:.2f}, "
              f"PPS-equivalente {e['pps_equivalente']:.2f}, "
              f"APM-equivalente {e['apm_equivalente']:.1f}, "
              f"buracos medios {e['buracos_medios']:.2f}, "
              f"B2B mantido {e['b2b_mantido_pct']:.1f}%, vivo={e['vivo']}")
    print(f"tempo medio por decisao: {(time.time() - t0) / max(tot, 1) * 1000:.1f} ms")
    input("\nEnter para fechar...")
