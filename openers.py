"""
Openers do bot: molde do DT Cannon (TSD seguido de TST) usado como TERMO DE AVALIACAO.

Referencia: https://tetris.wiki/Double_Triple_Cannon  (TSD com o chute do TST, depois TST).

Em vez de decorar sequencias de pecas, o DT e descrito como um molde de celulas:
  R = blocos do molde, E = casas do encaixe (devem ficar vazias), Z = poucas sobras permitidas.
O decisor soma aderencia(linhas) a nota do tabuleiro enquanto ativo(linhas) for verdadeiro; a busca em
feixe (beam) continua livre para colocar qualquer peca em qualquer lugar fisicamente alcancavel, entao
nunca trava: se a fila nao ajuda, o molde simplesmente perde peso e o jogo segue normal.

Fase 0: monta ate o TSD (T girado dentro do encaixe, 2 linhas). Fase 1: sobra o molde do TST (3 linhas).
O molde e testado nos dois lados (espelhado) e a fase e deduzida do tabuleiro (sem estado guardado).
"""
import time
from functools import lru_cache

LINHAS, COLUNAS = 20, 10
BASE = LINHAS - 7                  # primeira linha absoluta do molde (7 linhas, a ultima e o chao)
PESO_BLOCO = 1.4                   # por celula do molde ja preenchida
PESO_BLOCO_TSD = 2.6               # celulas necessarias para o TSD (linhas 4-6 e o beiral (3,6))
PESO_ENCAIXE = 7.0                 # por casa do encaixe ocupada
PESO_FORA = 3.0                    # por celula fora do molde (alem de EXTRAS_MAX)
EXTRAS_MAX = 3
BONUS_MOLDE_PRONTO = 20.0          # TSD pronto: linhas 4-6 completas (menos o encaixe) e beiral

DT_MOLDE = (
    ".....XX...",
    "......X...",
    "XXXXX.XXXX",
    "XXXX..XXXX",
    "XXXX...XXX",
    "XXXXX.XXXX",
    "XXXXX.XXXX",
)
DT_ENCAIXE0 = ((2, 5), (3, 4), (3, 5), (4, 4), (4, 5), (4, 6), (5, 5), (6, 5))
DT_ENCAIXE1 = ((4, 5), (5, 4), (5, 5), (6, 5))
DT_TSD = ((4, 4), (4, 5), (4, 6), (5, 5))      # celulas finais do T no TSD
DT_TST = ((4, 5), (5, 4), (5, 5), (6, 5))      # ... e no TST (apos o TSD)
DT_EXTRAS = tuple((r, c) for r in (0, 1) for c in (0, 1, 7, 8, 9))  # fora do corredor de entrada do T


def _masc(celulas, espelho):
    """{linha_absoluta: mascara} de celulas relativas (r, c)."""
    m = {}
    for r, c in celulas:
        c = COLUNAS - 1 - c if espelho else c
        m[r + BASE] = m.get(r + BASE, 0) | (1 << c)
    return m


def _molde(espelho):
    blocos = [(r, c) for r, lin in enumerate(DT_MOLDE) for c, ch in enumerate(lin) if ch == "X"]
    r1 = [(r + 2, c) for r, c in blocos if r < 4] + [(r, c) for r, c in blocos if r == 6]
    x1 = [(r + 2, c) for r, c in DT_EXTRAS]
    fases = []
    for bl, enc, ext in ((blocos, DT_ENCAIXE0, DT_EXTRAS), (r1, DT_ENCAIXE1, x1)):
        critico = [(r, c) for r, c in bl if r >= 4 or (r, c) == (3, 6)]
        resto = [x for x in bl if x not in critico]
        mc, mr, me, mz = (_masc(s, espelho) for s in (critico, resto, enc, ext))
        linhas = sorted(set(mc) | set(mr) | set(me) | set(mz))
        fases.append(tuple((r, mc.get(r, 0), mr.get(r, 0), me.get(r, 0), mz.get(r, 0)) for r in linhas))
    return tuple(fases)


MOLDES = (_molde(False), _molde(True))
_POP = [bin(i).count("1") for i in range(1 << COLUNAS)]
_TOPO = BASE - 3          # acima disso nada deve existir (corredor do T)


def _avalia(linhas, fase_molde):
    """(aderencia, fora, pronto) de um molde numa fase."""
    nota = 0.0
    dentro = 0
    pronto = True
    for r, mc, mr, me, mz in fase_molde:
        v = linhas[r]
        a, b = v & mc, v & mr
        nota += PESO_BLOCO_TSD * _POP[a] + PESO_BLOCO * _POP[b] - PESO_ENCAIXE * _POP[v & me]
        dentro += _POP[a] + _POP[b] + _POP[v & mz]
        if a != mc:
            pronto = False
    return nota, dentro, pronto


def aderencia(linhas):
    """Nota do molde DT (maior = mais parecido), maximizada entre lado/fase."""
    return _aderencia_cached(tuple(linhas))[0]


@lru_cache(maxsize=8192)
def _aderencia_cached(linhas):
    total = sum(_POP[v] for v in linhas)
    if not total:
        return 0.0, True
    melhor = -1e9
    cabe = False
    for molde in MOLDES:
        for fase in molde:
            nota, dentro, pronto = _avalia(linhas, fase)
            fora = total - dentro
            if fora <= EXTRAS_MAX:
                cabe = True
            if fora > EXTRAS_MAX:
                nota -= PESO_FORA * (fora - EXTRAS_MAX)
            if pronto:
                nota += BONUS_MOLDE_PRONTO
            if nota > melhor:
                melhor = nota
    return melhor, cabe


def aderencia_ativa(linhas):
    """Nota do molde somente enquanto o tabuleiro ainda cabe nele."""
    nota, cabe = _aderencia_cached(tuple(linhas))
    return nota if cabe else 0.0


def ativo(linhas):
    """True se o tabuleiro ainda cabe no molde (nenhuma peca fora dele alem das sobras)."""
    return _aderencia_cached(tuple(linhas))[1]


# =============================================================================
# Biblioteca de ROTAS por peca (o que funciona de verdade em jogo)
# =============================================================================
# O molde acima so mede "parecenca de celulas" e nunca completa um TSD (ver
# README). Aqui a abordagem e outra: para a BAG QUE ESTA NA TELA, uma busca em
# feixe monta a sequencia exata de colocacoes (letra + celulas) que termina num
# T-spin conferido pelo proprio decisor (BFS/SRS). Se nao existir rota com as
# pecas da vez, o planejador devolve None e o bot joga normal - nunca trava.
#
# Os openers famosos entram como RECOMENDACAO por peca inicial, que e a
# restricao que nao se negocia (ver harddrop/four.lol):
#   - I cedo      -> TKI-3 (TSD com o I deitado no meio)
#   - L+S / J+L   -> DT Cannon (base que aceita o T deitado e o TST depois)
#   - J+Z / T+Z   -> DT Cannon (variantes de base)
#   - qualquer bag-> TSD com a bag atual (fallback)
ALTURA_MAX_OPENER = 6      # a pilha do opener inteiro cabe nisto
BURACOS_MAX_OPENER = 2
NOS_TILING = 60000         # orcamento de nos do tiling (corta o tempo de planejamento)
TEMPO_MAX_PLANEJAMENTO = 0.60   # segundos: teto de relogio para escolher o opener da rodada


def _decisor():
    """Import tardio: decisor importa este modulo no topo (evita ciclo)."""
    import decisor
    return decisor


def nome_do_opener(pecas):
    """Rotulo do opener que a bag permite (so informativo, usado no log)."""
    primeiras = set(pecas[:3])
    if "I" in primeiras:
        return "TKI-3"
    if ({"L", "S"} <= primeiras or {"J", "L"} <= primeiras
            or {"J", "Z"} <= primeiras or {"T", "Z"} <= primeiras):
        return "DT Cannon"
    return "TSD com a bag atual"


def eh_dt_cannon(nome):
    return nome == "DT Cannon"


def _tsd_agora(linhas):
    """Celulas do T que fazem TSD nesta pilha, ou None."""
    d = _decisor()
    for _k, _col, y, ms, cels, sp, _acoes in d.colocacoes_T(linhas):
        if sp != "full":
            continue
        _depois, n = d._aplicar(linhas, y, ms)
        if n == 2:
            return tuple(cels)
    return None


def _tst_agora(linhas):
    """Celulas do T que fazem TST (T-spin triple) nesta pilha, ou None."""
    d = _decisor()
    melhor = None
    for _k, _col, y, ms, cels, sp, _acoes in d.colocacoes_T(linhas):
        if sp != "full":
            continue
        _depois, n = d._aplicar(linhas, y, ms)
        if n == 3:
            # prefere o TST mais "embutido" (mais baixo/estavel) se houver varios
            if melhor is None or max(r for r, _c in cels) > max(r for r, _c in melhor):
                melhor = tuple(cels)
    return melhor


def _tst_geometrico(linhas):
    return _tst_geometrico_cached(tuple(linhas))


@lru_cache(maxsize=8192)
def _tst_geometrico_cached(linhas):
    """Pre-filtra TSTs por geometria antes do BFS caro de alcancabilidade."""
    d = _decisor()
    if not d._tem_tspin_clear_geometrico(linhas):
        return False
    forma = d.ROT["T"]
    for k, cells in enumerate(forma):
        h = max(r for r, _c in cells) + 1
        w = max(c for _r, c in cells) + 1
        for br in range(d.LINHAS - h + 1):
            for bc in range(d.COLUNAS - w + 1):
                if (d._livre(linhas, cells, br, bc)
                        and not d._livre(linhas, cells, br + 1, bc)
                        and (d._tipo_spin(linhas, k, br, bc, 1) == "full"
                             or d._tipo_spin(linhas, k, br, bc, 2) == "full")):
                    placed = tuple((br + r, bc + c) for r, c in cells)
                    y = min(r for r, _c in placed)
                    masks = [0] * (max(r for r, _c in placed) - y + 1)
                    for r, c in placed:
                        masks[r - y] |= 1 << c
                    if d._aplicar(linhas, y, masks)[1] == 3:
                        return True
    return False


def _nota(linhas):
    """Guia a busca: progresso de slot de TSD vale mais que pilha baixa."""
    d = _decisor()
    prontos, estagio, setups = d._slots_detalhes(linhas)
    return (12.0 * d.ESTAGIO[estagio] + 2.0 * min(setups, 3) + 1.5 * prontos
            - 2.0 * d._altura_max(linhas) - 3.0 * d._buracos(linhas))


def _nota_tst(linhas):
    """Guia a busca da continuacao para TST: favorece slots fortes e pilha controlada."""
    d = _decisor()
    prontos, estagio, setups = d._slots_detalhes(linhas)
    # TST costuma exigir overhang mais alto/definido; usar o proprio avaliador ajuda.
    return (14.0 * d.ESTAGIO[estagio] + 3.0 * min(setups, 4) + 2.0 * prontos
            - 1.5 * d._altura_max(linhas) - 3.5 * d._buracos(linhas))


def _rota_tst_alcancavel(linhas, rota):
    """Confere a rota inteira com o mesmo BFS usado para enviar cada colocacao."""
    d = _decisor()
    board = tuple(linhas)
    for letra, celulas in rota:
        if d.jogada_para(board, letra, celulas) is None:
            return False
        topo = min(r for r, _c in celulas)
        ms = [0] * (max(r for r, _c in celulas) - topo + 1)
        for r, c in celulas:
            ms[r - topo] |= 1 << c
        board, _ = d._aplicar(board, topo, ms)
        board = tuple(board)
    return True


def _celulas(letra, k, y, x):
    """Celulas absolutas de uma peca na rotacao k com a caixa em (y, x)."""
    return tuple((y + dr, x + dc) for dr, dc in _decisor().ROT[letra][k])


_ALVOS = []


def buscar_rota(linhas, pecas, profundidade=6, largura=20):
    """Busca em feixe (fallback e gerador da biblioteca de alvos).

    Usada para descobrir tabuleiros de TSD que sejam REALIZAVEIS: cada alvo da
    biblioteca embutida em `ALVOS_TSD` saiu de uma rota encontrada aqui.
    """
    d = _decisor()
    fila = [p for p in pecas if p in d.SPAWN and p != "T"][:profundidade]
    if not fila or d._altura_max(linhas) > 1:
        return None, None
    nome = nome_do_opener(fila)
    estados = [(0.0, tuple(linhas), ())]
    orcamento = [NOS_TILING]
    for letra in fila:
        novos = []
        for _n, board, rota in estados:
            for _k, _col, y, ms, cels, _sp, _acoes in d._colocacoes_rapidas(board, letra):
                depois, n = d._aplicar(board, y, ms)
                depois = tuple(depois)
                if n or d._altura_max(depois) > ALTURA_MAX_OPENER:
                    continue
                if d._buracos(depois) > BURACOS_MAX_OPENER:
                    continue
                rota2 = rota + ((letra, tuple(cels)),)
                # o BFS do T e caro: so roda quando o tabuleiro tem slot pronto
                if d._slots_detalhes(depois)[0] and _tsd_agora(depois) is not None:
                    return nome, rota2
                novos.append((_nota(depois), depois, rota2))
        orcamento[0] -= len(novos)
        if not novos or orcamento[0] <= 0:
            return None, None
        novos.sort(key=lambda e: -e[0])
        estados = novos[:largura]
    return None, None


def _opcoes_peca_tst(ativa, fila, hold, pode_hold):
    """Proximas pecas que podem ser colocadas respeitando fila e Hold real."""
    fila = tuple(fila)
    if ativa not in _decisor().SPAWN:
        return ()
    opcoes = []
    proxima = fila[0] if fila else None
    fila_depois = fila[1:] if fila else ()
    if ativa != "T":
        opcoes.append((ativa, proxima, fila_depois, hold))
    if pode_hold:
        if hold in _decisor().SPAWN:
            peca = hold
            hold_depois = ativa
            ativa_depois = proxima
            fila_final = fila_depois
        elif fila:
            peca = fila[0]
            hold_depois = ativa
            ativa_depois = fila[1] if len(fila) > 1 else None
            fila_final = fila[2:] if len(fila) > 1 else ()
        else:
            peca = None
            hold_depois = hold
            ativa_depois = None
            fila_final = ()
        if peca in _decisor().SPAWN and peca != "T":
            opcoes.append((peca, ativa_depois, tuple(fila_final), hold_depois))
    return tuple(opcoes)


def _t_disponivel_tst(ativa, fila, hold):
    return ativa == "T" or hold == "T" or (hold is None and fila and fila[0] == "T")


def buscar_rota_tst(linhas, pecas, profundidade=7, largura=48, altura_max=10,
                    tempo_max=2.5, limpezas_max=3, hold_peca=None,
                    hold_estado="vazio"):
    """Busca em feixe por uma continuacao que termina com TST (T-spin triple) possivel.

    Diferente do opener inicial, aceita tabuleiro nao vazio (ex.: apos o TSD da
    1a fase do DT Cannon). Retorna (nome, rota_suporte) onde o tabuleiro apos a
    rota permite `_tst_agora` != None.
    """
    d = _decisor()
    if d._altura_max(linhas) > altura_max:
        return None, None
    d = _decisor()
    pecas = tuple(p for p in pecas if p in d.SPAWN)
    nome = nome_do_opener(pecas)
    if _tst_agora(tuple(linhas)) is not None:
        return nome, ()
    if not pecas:
        return None, None
    hold = hold_peca if hold_estado in ("pronto", "usado") and hold_peca in d.SPAWN else None
    pode_hold = hold_estado != "usado"
    estados = [(0.0, tuple(linhas), (), 0, pecas[0], pecas[1:], hold, pode_hold)]
    prazo = time.perf_counter() + max(0.01, tempo_max)
    orcamento = NOS_TILING
    for _depth in range(profundidade):
        novos = {}
        for _nota, board, rota, limpas, ativa, fila, hold, pode_hold in estados:
            for letra, ativa2, fila2, hold2 in _opcoes_peca_tst(ativa, fila, hold, pode_hold):
                for _k, _col, y, ms, cels, _sp, _acoes in d._colocacoes_rapidas(board, letra):
                    orcamento -= 1
                    if orcamento <= 0 or time.perf_counter() >= prazo:
                        return None, None
                    depois, n = d._aplicar(board, y, ms)
                    depois = tuple(depois)
                    total_limpas = limpas + n
                    if total_limpas > limpezas_max:
                        continue
                    if d._altura_max(depois) > altura_max or d._buracos(depois) > 5:
                        continue
                    rota2 = rota + ((letra, tuple(cels)),)
                    chave = (depois, ativa2, tuple(fila2), hold2)
                    atual = novos.get(chave)
                    if atual is None or total_limpas < atual[2]:
                        novos[chave] = (depois, rota2, total_limpas,
                                        ativa2, tuple(fila2), hold2, True)
        if not novos:
            return None, None
        candidatos = [(_nota_tst(board) - 5.0 * limpas, board, rota, limpas,
                       ativa, fila, hold, pode)
                      for board, rota, limpas, ativa, fila, hold, pode in novos.values()]
        candidatos.sort(key=lambda e: -e[0])
        for _nota, board, rota, _limpas, ativa, fila, hold, _pode in candidatos[:min(24, largura)]:
            if time.perf_counter() >= prazo:
                return None, None
            if (_t_disponivel_tst(ativa, fila, hold)
                    and _tst_geometrico(board) and _tst_agora(board) is not None
                    and _rota_tst_alcancavel(tuple(linhas), rota)):
                return nome, rota
        estados = candidatos[:largura]
    return None, None


# Biblioteca de alvos canonicos de TSD, extraida das rotas que a busca em feixe acha
# (alvos_gerar.py, 700 bags) e conferida com o proprio decisor: em todo alvo o T faz
# T-spin "full" de 2 linhas. Formato: (primeira_linha, mascaras das linhas seguintes).
ALVOS_TSD = (
    (17, 356, 967, 1007),
    (16, 1, 947, 995, 1015),
    (16, 2, 463, 967, 1007),
    (16, 4, 134, 995, 1015),
    (16, 4, 518, 995, 1015),
    (16, 4, 998, 995, 1015),
    (16, 8, 463, 967, 1007),
    (16, 8, 505, 1009, 1019),
    (16, 8, 847, 967, 1007),
    (16, 16, 152, 911, 991),
    (16, 16, 536, 911, 991),
    (16, 22, 539, 911, 991),
    (16, 25, 539, 911, 991),
    (16, 32, 97, 967, 1007),
    (16, 32, 100, 967, 1007),
    (16, 35, 103, 967, 1007),
    (16, 56, 313, 1009, 1019),
    (16, 64, 608, 575, 895),
    (16, 64, 638, 575, 895),
    (16, 64, 971, 911, 991),
    (16, 64, 974, 911, 991),
    (16, 100, 614, 995, 1015),
    (16, 112, 626, 575, 895),
    (16, 128, 385, 799, 959),
    (16, 128, 388, 799, 959),
    (16, 131, 205, 967, 1007),
    (16, 131, 391, 799, 959),
    (16, 134, 391, 799, 959),
    (16, 140, 391, 799, 959),
    (16, 194, 79, 967, 1007),
    (16, 196, 230, 995, 1015),
    (16, 512, 823, 799, 959),
    (16, 524, 781, 967, 1007),
    (16, 608, 865, 967, 1007),
    (16, 772, 716, 911, 991),
    (16, 772, 902, 995, 1015),
    (16, 776, 271, 967, 1007),
    (16, 776, 316, 799, 959),
    (16, 784, 920, 911, 991),
    (15, 2, 2, 359, 967, 1007),
    (15, 256, 256, 922, 911, 991),
)


def alvos_tsd():
    """Tabuleiros canonicos de "TSD pronto" (celulas do tabuleiro antes do T)."""
    global _ALVOS
    if not _ALVOS:
        _ALVOS = [frozenset((lo + i, c) for i, m in enumerate(ms) for c in range(COLUNAS)
                            if m >> c & 1)
                  for lo, *ms in ALVOS_TSD]
    return _ALVOS


def _decs_padrao():
    """Decomposicoes para a bag de opener (1 de cada I,O,S,Z,J,L), precomputadas."""
    global _DECS_PRE
    if _DECS_PRE is None:
        _DECS_PRE = [(a, _decomposicoes(a, _LIMITE_BAG)) for a in alvos_tsd()]
        _DECS_PRE.sort(key=lambda x: len(x[0]))
    return _DECS_PRE


# formas das pecas (sem o T), normalizadas, com a letra de origem
_FORMAS = []
_LIMITE_BAG = tuple(sorted((l, 1) for l in "IOSZJL"))  # cada letra sem T aparece 1x na bag
_DECS_PRE = None           # decomposicoes precomputadas para a bag de opener padrao


def _formas():
    global _FORMAS
    if not _FORMAS:
        d = _decisor()
        vistas = set()
        for letra in "IOSZJL":
            for estado in d.ROT[letra]:
                forma = frozenset(estado)
                if forma in vistas:
                    continue
                vistas.add(forma)
                _FORMAS.append((letra, tuple(sorted(forma))))
    return _FORMAS


@lru_cache(maxsize=512)
def _decomposicoes(alvo_t, limites):
    """Formas de cobrir `alvo` com 4-minos, ignorando gravidade.

    `limites` e quantas pecas de cada letra a bag oferece: a enumeracao ja descarta
    decomposicoes impossiveis de servir. Cacheada por (alvo, limites).
    """
    alvo = frozenset(alvo_t)
    saida = []
    teto = dict(limites)

    def rec(restante, acumulado, usadas):
        if not restante:
            saida.append(tuple(acumulado))
            return
        canto = min(restante)
        for letra, forma in _formas():
            if usadas.get(letra, 0) >= teto.get(letra, 0):
                continue
            for ancora in forma:
                dr, dc = canto[0] - ancora[0], canto[1] - ancora[1]
                celulas = frozenset((r + dr, c + dc) for r, c in forma)
                if not celulas <= restante:
                    continue
                usadas[letra] = usadas.get(letra, 0) + 1
                acumulado.append((letra, celulas))
                rec(restante - celulas, acumulado, usadas)
                acumulado.pop()
                usadas[letra] -= 1

    rec(alvo, [], {})
    return tuple(saida)


def _factivel(dec, fila):
    """Da para empilhar as pecas de `dec` na ordem da bag, so com gravidade?

    Cada peca tem de encostar (celula abaixo) no chao ou em peca ja posta. O T nao
    entra na pilha: ele fica guardado para o final.
    """
    pilha = []
    postas = set()

    def rec(i, usadas):
        if all(usadas):
            return list(usadas)
        if i == len(fila):
            return None
        if fila[i] == "T":
            return rec(i + 1, usadas)
        for j, (letra, celulas) in enumerate(dec):
            if usadas[j] or letra != fila[i]:
                continue
            if celulas & postas:
                continue
            # entra caindo de cima: a coluna acima de cada celula tem de estar livre
            if any((r2, c) in postas for r, c in celulas for r2 in range(r)):
                continue
            # e alguma celula tem de bater no chao ou numa peca ja posta (balanco e ok)
            if not any(r + 1 == LINHAS or (r + 1, c) in postas for r, c in celulas):
                continue
            usadas[j] = True
            postas.update(celulas)
            pilha.append((letra, celulas))
            achou = rec(i + 1, usadas)
            if achou is not None:
                return achou
            pilha.pop()
            postas.difference_update(celulas)
            usadas[j] = False
        return None

    if rec(0, [False] * len(dec)) is None:
        return None
    return tuple(pilha)


def planejar(linhas, pecas):
    """Rota de opener para a bag dada, ou (None, None).

    `linhas`  : tabuleiro em mascaras (decisor.para_linhas)
    `pecas`   : ordem real das pecas, comeco pela mao ativa
    Retorna (nome, rota) com rota = ((letra, celulas), ...) terminando no T do TSD.

    Estrategia: tiling de alvos canonicos. Para cada tabuleiro que ja e "TSD pronto"
    (alvos_tsd), tenta cobrir exatamente as celulas dele com as pecas da bag, na ordem.
    A rota so e aceita depois que o proprio decisor confirma o T-spin (BFS/SRS), entao
    alvo mal construido nunca vira jogada.
    """
    d = _decisor()
    if d._altura_max(linhas) > 1:
        return None, None
    fila = [p for p in pecas if p in d.SPAWN]
    if len(fila) < 2:
        return None, None
    nome = nome_do_opener(fila)
    contagem = {}
    for letra in fila:
        if letra != "T":
            contagem[letra] = contagem.get(letra, 0) + 1
    limites = tuple(sorted(contagem.items()))
    # bag de opener padrao (1 de cada): usa decomposicoes precomputadas
    if limites == _LIMITE_BAG:
        candidatos = _decs_padrao()
    else:
        candidatos = [(a, _decomposicoes(a, limites))
                      for a in sorted(alvos_tsd(), key=len)]
    prazo = time.perf_counter() + TEMPO_MAX_PLANEJAMENTO
    for alvo, decs in candidatos:
        if time.perf_counter() > prazo:
            break
        for dec in decs:
            rota = _factivel(dec, fila)
            if rota is None:
                continue
            # confere de verdade: o T tem de fazer T-spin full de 2 linhas aqui
            board = list(linhas)
            for letra, cels in rota:
                for r, c in cels:
                    board[r] |= 1 << c
            board = tuple(board)
            alvo_t = _tsd_agora(board)
            if alvo_t is None:
                continue
            return nome, rota + (("T", alvo_t),)
        if time.perf_counter() > prazo:
            break
    return None, None
