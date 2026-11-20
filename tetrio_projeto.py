"""
Leitor de pecas do TETR.IO por cor na tela: fila, hold, mao (peca ativa) e tabuleiro.

Uso:
  py tetrio_projeto.py                  -> mostra Hold, Mao, Proximas e Pilha em tempo real
  py tetrio_projeto.py --layout esquerda -> le o tabuleiro esquerdo no modo versus
  py tetrio_projeto.py --tabuleiro      -> idem + imprime a pilha a cada mudanca
  py tetrio_projeto.py --compacto       -> tenta redimensionar o console (100x10)
  py tetrio_projeto.py --perfil         -> mede o tempo de cada etapa (salva relatorio_perfil.txt)
  (ao vivo, os eventos vao para leitura_log.txt; os modos debug salvam relatorio_*.txt)
  py tetrio_projeto.py --debug          -> salva PNGs das caixas lidas e mostra as cores medidas
  py tetrio_projeto.py --debug-board    -> le o tabuleiro uma vez (PNG com data/hora + debug_board.png)
  py tetrio_projeto.py --debug-sequence -> grava 30 capturas do tabuleiro para comparar movimento

O layout central continua sendo o padrao. Use --layout esquerda para ler o
tabuleiro esquerdo no modo versus.

Opcoes:
  --atraso S      segundos para voltar para a janela do jogo antes de capturar (debug). Padrao: 3
  --frames N      frames seguidos iguais para aceitar hold/fila. Padrao: 2
  --frames-tab N  frames seguidos iguais para aceitar o tabuleiro. Padrao: 2

Como a pilha e lida: so vale quando a peca ativa acabou de nascer (4 blocos nas
linhas SPAWN_LINHAS e nada acima). Nesse momento a peca nao toca a pilha, entao
as linhas de spawn podem ser apagadas com seguranca. Qualquer outro frame e descartado.

Dependencias: pip install mss numpy
Ctrl+C para sair.
"""
import argparse
import ctypes
from functools import lru_cache
import os
import subprocess
import sys
import time

import numpy as np

# ---------- CONFIGURACAO (ajuste aqui) ----------
CORES = {
    "I": (45, 193, 255),
    "T": (255, 34, 195),
    "Z": (255, 45, 89),
    "L": (255, 128, 44),
    "S": (129, 255, 6),
    "J": (46, 85, 255),
    "O": (255, 193, 44),
}

SLOT_X = 877
SLOT_Y = [196, 266, 336, 411, 486]  # slot 1 (proxima peca) ate slot 5
HOLD = (495, 196)

BOARD_LEFT, BOARD_TOP = 562, 135
BOARD_RIGHT, BOARD_BOTTOM = 802, 617
BOARD_COLS, BOARD_ROWS = 10, 20
MIN_CELL_COLOR_PIXELS = 100  # pixels coloridos (de ~144) para a celula contar como bloco
MAX_DIST_CELULA = 60         # distancia maxima da cor do bloco ate a cor de uma peca
SPAWN_LINHAS = (2, 3)           # linhas (0 = topo do tabuleiro lido) onde a peca nasce.
BOARD_SAMPLES = 6            # pixels amostrados por eixo em cada celula (6x6 = 36 por celula)
PASSO_FILA = 2               # hold/fila: le 1 pixel a cada N (mais rapido; 1 = todos)

# Blocos de lixo (cinza): baixa saturacao e brilho medio. Calibre com --debug-board.
LIXO_SAT_MAX = 25            # (max - min) maximo para ser considerado cinza
LIXO_MIN, LIXO_MAX = 120, 190  # ghost ~110; lixo real ~128 na captura calibrada
MIN_CELL_LIXO_PIXELS = 80    # pixels de lixo (de ~144) dentro da celula para marcar 'G'
FRAMES_TAB = 1               # frames para aceitar o tabuleiro (1: a peca fica pouco tempo no spawn em jogo rapido)
ARQUIVO_LOG = "leitura_log.txt"  # eventos do modo ao vivo (mande este arquivo para analise)

BOX_W, BOX_H = 130, 56  # caixa lida ao redor do centro de cada slot

SCREEN_REF_W, SCREEN_REF_H = 1366, 768
LAYOUTS = {
    "centro": (562, 135, 802, 617),
    "esquerda": (303, 135, 544, 615),
}
LAYOUT_NAME = None

SAT_MIN = 60       # (max - min) do pixel: separa peca colorida do fundo escuro
BRILHO_MIN = 90    # maior canal minimo para ser considerado peca
MIN_PIXELS = 30    # minimo de pixels coloridos na caixa para dizer que ha peca
MAX_DIST = 45      # distancia maxima ate a cor de referencia para aceitar
HOLD_GRAY_MIN = 41
HOLD_GRAY_MAX = 44
MIN_GRAY_PIXELS = 30

FRAMES_ESTAVEIS = 2  # frames seguidos iguais para aceitar uma leitura
MIN_COMBINA = 3      # pecas iguais necessarias para aceitar que a fila avancou
# ------------------------------------------------

CORES_REF = np.asarray(tuple(CORES.values()), dtype=np.float64)


# ---------- Geometria: tudo e lido de UMA captura so ----------
def _caixa(cx, cy):
    left = cx - BOX_W // 2
    top = cy - BOX_H // 2
    return (left, top, left + BOX_W, top + BOX_H)


NOMES = ["hold"] + [f"slot{i + 1}" for i in range(len(SLOT_Y))]
CAIXAS = {"hold": _caixa(*HOLD)}
for _i, _y in enumerate(SLOT_Y):
    CAIXAS[f"slot{_i + 1}"] = _caixa(SLOT_X, _y)
CAIXA_TABULEIRO = (BOARD_LEFT, BOARD_TOP, BOARD_RIGHT, BOARD_BOTTOM)

_todas = list(CAIXAS.values()) + [CAIXA_TABULEIRO]
REG_LEFT = min(c[0] for c in _todas)
REG_TOP = min(c[1] for c in _todas)
REG_RIGHT = max(c[2] for c in _todas)
REG_BOTTOM = max(c[3] for c in _todas)


def recortar(frame, caixa):
    """Pega de dentro do frame capturado a area de uma caixa (coordenadas da tela)."""
    left, top, right, bottom = caixa
    return frame[top - REG_TOP:bottom - REG_TOP, left - REG_LEFT:right - REG_LEFT]


def configurar_layout(nome, monitor):
    """Configura o layout central padrao ou o tabuleiro esquerdo no versus."""
    if nome not in LAYOUTS:
        raise ValueError(f"Layout desconhecido: {nome!r}")
    dimensoes = (monitor["height"], monitor["width"])
    _definir_layout(nome, monitor, dimensoes, LAYOUTS[nome])


def _definir_layout(nome, monitor, dimensoes, retangulo):
    """Atualiza as caixas de leitura para um perfil explicito de tela."""
    global BOARD_LEFT, BOARD_TOP, BOARD_RIGHT, BOARD_BOTTOM
    global SLOT_X, SLOT_Y, HOLD, BOX_W, BOX_H, CAIXAS, CAIXA_TABULEIRO
    global REG_LEFT, REG_TOP, REG_RIGHT, REG_BOTTOM, LAYOUT_NAME
    escala_x = dimensoes[1] / SCREEN_REF_W
    escala_y = dimensoes[0] / SCREEN_REF_H
    origem_x, origem_y = monitor["left"], monitor["top"]
    left, top, right, bottom = retangulo
    BOARD_LEFT = origem_x + round(left * escala_x)
    BOARD_TOP = origem_y + round(top * escala_y)
    BOARD_RIGHT = origem_x + round(right * escala_x)
    BOARD_BOTTOM = origem_y + round(bottom * escala_y)
    hold_offset, slot_offset = (-78, 65) if nome == "esquerda" else (-67, 75)
    HOLD = (BOARD_LEFT + round(hold_offset * escala_x), BOARD_TOP + round(61 * escala_y))
    SLOT_X = BOARD_RIGHT + round(slot_offset * escala_x)
    SLOT_Y = [BOARD_TOP + round(y * escala_y) for y in (61, 131, 201, 276, 351)]
    BOX_W, BOX_H = round(130 * escala_x), round(56 * escala_y)
    CAIXAS = {"hold": _caixa(*HOLD)}
    for i, y in enumerate(SLOT_Y):
        CAIXAS[f"slot{i + 1}"] = _caixa(SLOT_X, y)
    CAIXA_TABULEIRO = (BOARD_LEFT, BOARD_TOP, BOARD_RIGHT, BOARD_BOTTOM)
    todas = list(CAIXAS.values()) + [CAIXA_TABULEIRO]
    REG_LEFT = min(c[0] for c in todas)
    REG_TOP = min(c[1] for c in todas)
    REG_RIGHT = max(c[2] for c in todas)
    REG_BOTTOM = max(c[3] for c in todas)
    LAYOUT_NAME = nome


def capturar(sct):
    """Captura a regiao configurada; sem opcao explicita, preserva o layout central."""
    global LAYOUT_NAME
    if LAYOUT_NAME is None:
        try:
            monitor = sct.monitors[1]
        except (AttributeError, IndexError, KeyError) as exc:
            raise RuntimeError("Nao foi possivel acessar o monitor para configurar a captura.") from exc
        configurar_layout("centro", monitor)
    img = sct.grab({
        "left": REG_LEFT,
        "top": REG_TOP,
        "width": REG_RIGHT - REG_LEFT,
        "height": REG_BOTTOM - REG_TOP,
    })
    return np.asarray(img)[:, :, 2::-1]  # BGRA -> RGB


# ---------- Reconhecimento por cor ----------
def classify(rgb, passo=1):
    """rgb: array (altura, largura, 3). Retorna (peca, cor_mediana, distancia).
    passo > 1 le so 1 pixel a cada 'passo' (mais rapido)."""
    if passo > 1:
        rgb = rgb[::passo, ::passo]
    px = rgb.reshape(-1, 3).astype(np.int16)
    mx = px.max(axis=1)
    mn = px.min(axis=1)
    mask = ((mx - mn) >= SAT_MIN) & (mx >= BRILHO_MIN)
    if mask.sum() < max(1, MIN_PIXELS // (passo * passo)):
        return None, None, None
    med = np.median(px[mask], axis=0)
    dists = np.linalg.norm(CORES_REF - med, axis=1)
    i = int(dists.argmin())
    dist = float(dists[i])
    if dist > MAX_DIST:
        return "?", med, dist
    return list(CORES)[i], med, dist


def count_hold_gray_pixels(rgb, passo=1):
    """Conta pixels no intervalo de cinza configurado para o hold (com passo > 1, por amostra)."""
    px = rgb[::passo, ::passo].reshape(-1, 3)
    gray = np.all((px >= HOLD_GRAY_MIN) & (px <= HOLD_GRAY_MAX), axis=1)
    return int(gray.sum())


def ler_hold_e_fila(frame):
    """
    Le hold e fila do MESMO frame. Retorna (hold, fila) ou None se algo estiver duvidoso.
    hold: "-" (vazio), "GRAY" (usado neste turno) ou a letra da peca.
    fila: tupla com as 5 letras.
    """
    fila = []
    for nome in NOMES[1:]:
        peca, _, _ = classify(recortar(frame, CAIXAS[nome]), PASSO_FILA)
        if peca is None or peca == "?":
            return None
        fila.append(peca)

    arr = recortar(frame, CAIXAS["hold"])
    peca, _, _ = classify(arr, PASSO_FILA)
    if peca == "?":
        return None
    if peca is None:
        cinza = count_hold_gray_pixels(arr, PASSO_FILA)
        hold = "GRAY" if cinza >= MIN_GRAY_PIXELS // (PASSO_FILA ** 2) else "-"
    else:
        hold = peca
    return hold, tuple(fila)


# ---------- Filtro anti-erro para jogo rapido ----------
class Estabilizador:
    """So aceita uma leitura depois que ela se repete N frames seguidos."""

    def __init__(self, n):
        self.n = n
        self.candidata = None
        self.contagem = 0
        self.estavel = None

    def quebrar(self):
        """Interrompe a contagem de frames iguais (mantem o ultimo valor aceito)."""
        self.candidata = None
        self.contagem = 0

    def reiniciar(self):
        """Esquece tudo: a proxima leitura estavel precisa ser confirmada de novo."""
        self.candidata = None
        self.contagem = 0
        self.estavel = None

    def atualizar(self, leitura):
        """Retorna True quando a leitura estavel mudou."""
        if leitura == self.candidata:
            self.contagem += 1
        else:
            self.candidata = leitura
            self.contagem = 1
        if self.contagem >= self.n and leitura != self.estavel:
            self.estavel = leitura
            return True
        return False


# ---------- Mao e hold ----------
def deslocamento(antiga, nova):
    """Quantas pecas sairam da frente da fila (0 = igual). None se nao ha relacao."""
    n = len(antiga)
    for k in range(n - MIN_COMBINA + 1):
        if nova[:n - k] == antiga[k:]:
            return k
    return None


class Rastreador:
    """
    Descobre a peca na mao e a peca guardada a partir de como a fila e o hold mudam.
    Quando nao da para ter certeza, deixa como desconhecido (None) em vez de chutar.

    hold_estado: "vazio", "pronto" (colorido, pode usar) ou "usado" (cinza).
    hold_peca:   letra guardada, ou None (vazio ou desconhecida).
    mao:         peca ativa, ou None se desconhecida.
    No inicio de uma rodada, infere a setima peca da bag se mao + fila confirmarem seis distintas.
    """

    def __init__(self):
        self.fila = None
        self.mao = None
        self.hold_estado = "vazio"
        self.hold_peca = None
        self.avancos = 0  # quantas pecas ja sairam da fila (aumenta quando uma nova peca nasce)
        self.bag_confiavel = False
        self._peca_final_bag = None
        self._peca_final_bag_consumida = False
        self._bag_janela_inicial = False

    def _hold_lido(self, obs):
        if obs == "-":
            self.hold_estado, self.hold_peca = "vazio", None
        elif obs == "GRAY":
            self.hold_estado, self.hold_peca = "usado", None
        else:
            self.hold_estado, self.hold_peca = "pronto", obs

    def atualizar(self, fila, hold_obs):
        anterior = self.fila
        if anterior is None:  # primeira leitura: a mao ainda nao e conhecida
            self.fila = fila
            self._hold_lido(hold_obs)
            return

        k = deslocamento(anterior, fila)
        if k is None:  # fila sem relacao com a anterior (reinicio ou leitura estranha)
            self.avancos += 1
            self.mao = None
            self.fila = fila
            self._hold_lido(hold_obs)
            self.bag_confiavel = False
            self._peca_final_bag = None
            self._bag_janela_inicial = False
            return

        self.avancos += k
        estado, peca, mao = self.hold_estado, self.hold_peca, self.mao

        if hold_obs != "GRAY":
            # Hold livre no fim: a ultima acao foi uma peca travar e nascer outra.
            if k >= 1:
                self.mao = anterior[k - 1]
            self._hold_lido(hold_obs)
        else:
            # Hold usado: a ultima acao foi apertar hold.
            self.hold_estado = "usado"
            if estado == "usado":
                if k == 1:  # travou e trocou com o hold
                    self.hold_peca, self.mao = anterior[0], peca
                elif k >= 2:
                    self.hold_peca, self.mao = None, None
            elif estado == "pronto":
                if k == 0:  # trocou a mao com o hold
                    self.hold_peca, self.mao = mao, peca
                elif k == 1:  # travou e trocou
                    self.hold_peca, self.mao = anterior[0], peca
                else:
                    self.hold_peca, self.mao = None, None
            else:  # hold vazio
                if k == 1:  # guardou a mao e a fila avancou
                    self.hold_peca, self.mao = mao, anterior[0]
                else:
                    self.hold_peca, self.mao = None, None
        self.fila = fila
        if self._peca_final_bag is not None and self.mao == self._peca_final_bag:
            self._peca_final_bag_consumida = True

    def iniciar_rodada(self, fila):
        """Nova partida: zera contadores e hold e parte da fila inicial lida (mao ainda desconhecida)."""
        self.fila = tuple(fila) if fila is not None else None
        self.mao = None
        self.hold_estado, self.hold_peca = "vazio", None
        self.avancos = 0
        self.bag_confiavel = False
        self._peca_final_bag = None
        self._peca_final_bag_consumida = False
        self._bag_janela_inicial = True

    def definir_mao(self, letra):
        """Leitura direta da peca ativa (vista no spawn): vale mais que a deducao pela fila."""
        self.mao = letra
        if (not self._bag_janela_inicial or self.avancos != 0
                or self.fila is None or len(self.fila) != 5):
            return
        conhecidas = {letra, *self.fila}
        if len(conhecidas) != 6 or not conhecidas <= set(CORES):
            return
        self._peca_final_bag = next(peca for peca in CORES if peca not in conhecidas)
        self._peca_final_bag_consumida = False
        self.bag_confiavel = True
        self._bag_janela_inicial = False

    def proxima_peca_da_bag(self, fila=None):
        """Infere a sétima peça somente no início de uma bag totalmente rastreada."""
        if not self.bag_confiavel or self._peca_final_bag_consumida:
            return None
        fila_visivel = self.fila if fila is None else fila
        if fila_visivel is None or self._peca_final_bag in fila_visivel:
            return None
        return self._peca_final_bag

    def texto_hold(self):
        if self.hold_estado == "vazio":
            return "-"
        if self.hold_estado == "pronto":
            return self.hold_peca
        return f"{self.hold_peca or '?'} (cinza)"


# ---------- Tabuleiro ----------
@lru_cache(maxsize=8)
def _indices_amostra_tabuleiro(height, width, amostras):
    """Indices (y, x) dos pixels amostrados em cada celula + limiares ajustados. Calculado uma vez."""
    ys, alturas = [], []
    for row in range(BOARD_ROWS):
        y0 = round(row * height / BOARD_ROWS + height / BOARD_ROWS * 0.25)
        y1 = round((row + 1) * height / BOARD_ROWS - height / BOARD_ROWS * 0.25)
        alturas.append(y1 - y0)
        ys.append(np.floor(y0 + (np.arange(amostras) + 0.5) * (y1 - y0) / amostras).astype(np.intp))

    xs, larguras = [], []
    for col in range(BOARD_COLS):
        x0 = round(col * width / BOARD_COLS + width / BOARD_COLS * 0.25)
        x1 = round((col + 1) * width / BOARD_COLS - width / BOARD_COLS * 0.25)
        larguras.append(x1 - x0)
        xs.append(np.floor(x0 + (np.arange(amostras) + 0.5) * (x1 - x0) / amostras).astype(np.intp))

    areas = np.asarray(alturas)[:, None] * np.asarray(larguras)[None, :]
    escala = amostras * amostras
    # converte "N pixels da celula" para "M amostras", proporcional a area real
    limiar_cor = (MIN_CELL_COLOR_PIXELS * escala + areas - 1) // areas
    limiar_lixo = (MIN_CELL_LIXO_PIXELS * escala + areas - 1) // areas
    return np.asarray(ys), np.asarray(xs), limiar_cor, limiar_lixo


def classify_board(rgb, linhas=BOARD_ROWS):
    """
    Retorna matriz (linhas x 10) de caracteres:
      '#' = bloco colorido, 'G' = bloco de lixo (cinza), '.' = vazia.
    Vetorizado: le so BOARD_SAMPLES x BOARD_SAMPLES pixels por celula, sem lacos Python.
    Leitura bruta: inclui a peca ativa, se houver.
    """
    height, width = rgb.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("O recorte do tabuleiro esta vazio; confira --layout e a escala da tela.")
    linhas = min(linhas, BOARD_ROWS)
    ys, xs, limiar_cor, limiar_lixo = _indices_amostra_tabuleiro(height, width, BOARD_SAMPLES)
    ys, limiar_cor, limiar_lixo = ys[:linhas], limiar_cor[:linhas], limiar_lixo[:linhas]

    px = rgb[ys[:, None, :, None], xs[None, :, None, :]].astype(np.int16)  # (L, C, S, S, 3)
    mx = px.max(axis=4)
    mn = px.min(axis=4)
    colorido = ((mx - mn) >= SAT_MIN) & (mx >= BRILHO_MIN)
    lixo = ((mx - mn) <= LIXO_SAT_MAX) & (mx >= LIXO_MIN) & (mx <= LIXO_MAX)

    n_col = colorido.sum(axis=(2, 3))
    n_lixo = lixo.sum(axis=(2, 3))
    media = (px * colorido[..., None]).sum(axis=(2, 3)) / np.maximum(n_col[..., None], 1)
    dist2 = ((media[:, :, None, :] - CORES_REF[None, None, :, :]) ** 2).sum(axis=3)
    peca = (n_col >= limiar_cor) & (dist2.min(axis=2) <= MAX_DIST_CELULA ** 2)

    board = np.full((linhas, BOARD_COLS), ".", dtype="<U1")
    board[n_lixo >= limiar_lixo] = "G"
    board[peca] = "#"
    return board.tolist()


def ler_tabuleiro(frame):
    """Le o tabuleiro bruto de um frame. Retorna tupla de 20 strings de 10 caracteres."""
    return tuple("".join(l) for l in classify_board(recortar(frame, CAIXA_TABULEIRO)))


def validar_spawn(tab):
    """
    Confere se a peca ativa esta inteira nas linhas de spawn (4 celulas coloridas ali
    e nada colorido acima). Se sim, apaga essas linhas e devolve (tabuleiro_limpo, n).
    Se nao (a peca ja desceu), devolve (None, n): leitura inutil.
    """
    a, b = SPAWN_LINHAS
    if not 0 <= a <= b < len(tab):
        raise ValueError(
            f"SPAWN_LINHAS invalido: {a}-{b} para um tabuleiro com {len(tab)} linhas."
        )
    n = sum(tab[r].count("#") for r in range(a, b + 1))
    acima = sum(tab[r].count("#") for r in range(0, a))
    if n != 4 or acima:
        return None, n
    limpo = list(tab)
    for r in range(a, b + 1):
        limpo[r] = "." * BOARD_COLS
    return tuple(limpo), n


def ler_peca_spawn(rgb):
    """Letra da peca ativa (I,T,Z,L,S,J,O) lida pela cor nas linhas de spawn, ou None."""
    a, b = SPAWN_LINHAS
    height = rgb.shape[0]
    y0 = round(a * height / BOARD_ROWS)
    y1 = round((b + 1) * height / BOARD_ROWS)
    peca, _, _ = classify(rgb[y0:y1])
    return peca if peca in CORES else None


def ler_tabuleiro_limpo(frame, stats=None):
    """
    Le so a pilha (sem a peca ativa) ou devolve None se a peca nao esta no spawn.
    Quando valido devolve (pilha, letra_da_peca_ativa); a letra pode ser None se a cor nao bateu.
    Primeiro olha so as linhas de cima (barato); so le o tabuleiro inteiro se a peca
    estiver la, entao a maioria dos frames custa quase nada.
    stats (dict opcional): conta por que cada frame foi aceito ou descartado.
    """
    a, b = SPAWN_LINHAS
    rgb = recortar(frame, CAIXA_TABULEIRO)
    topo = classify_board(rgb, b + 1)
    n = sum(l.count("#") for l in topo[a:])
    acima = sum(l.count("#") for l in topo[:a])
    if n != 4 or acima:
        if stats is not None:
            if acima:
                chave = "celulas_acima_do_spawn"
            elif n == 0:
                chave = "spawn_vazio"
            elif n < 4:
                chave = "spawn_com_1_a_3"
            else:
                chave = "spawn_com_5_ou_mais"
            stats[chave] = stats.get(chave, 0) + 1
        return None
    if stats is not None:
        stats["validos"] = stats.get("validos", 0) + 1
    pilha = validar_spawn(tuple("".join(r) for r in classify_board(rgb)))[0]
    if pilha is None:
        return None
    return pilha, ler_peca_spawn(rgb)


def altura_pilha(tab):
    """Altura da pilha em linhas (0 = tabuleiro vazio)."""
    for i, linha in enumerate(tab):
        if any(c != "." for c in linha):
            return BOARD_ROWS - i
    return 0


def histograma_cinza(rgb, n=5):
    """Os n niveis de cinza mais comuns no tabuleiro (para calibrar o lixo)."""
    px = rgb.reshape(-1, 3).astype(np.int16)
    mx = px.max(axis=1)
    mn = px.min(axis=1)
    sel = ((mx - mn) <= LIXO_SAT_MAX) & (mx >= 50)
    if not sel.any():
        return []
    cont = np.bincount(mx[sel], minlength=256)
    topo = np.argsort(cont)[::-1][:n]
    return [(int(v), int(cont[v])) for v in topo if cont[v] > 0]


def draw_board_grid(rgb, board):
    """Marca limites das celulas e os centros detectados para a imagem de debug."""
    height, width = rgb.shape[:2]
    overlay = np.array(rgb)
    for row in range(BOARD_ROWS + 1):
        y = min(height - 1, int(round(row * height / BOARD_ROWS)))
        overlay[max(0, y - 1):y + 1, :, :] = (255, 80, 80)
    for col in range(BOARD_COLS + 1):
        x = min(width - 1, int(round(col * width / BOARD_COLS)))
        overlay[:, max(0, x - 1):x + 1, :] = (255, 80, 80)
    for row in range(BOARD_ROWS):
        for col in range(BOARD_COLS):
            center_y = int((row + 0.5) * height / BOARD_ROWS)
            center_x = int((col + 0.5) * width / BOARD_COLS)
            if board[row][col] == "#":
                color = (0, 255, 0)
            elif board[row][col] == "G":
                color = (255, 165, 0)
            else:
                color = (120, 120, 120)
            overlay[max(0, center_y - 2):center_y + 3, max(0, center_x - 2):center_x + 3] = color
    return overlay


# ---------- Utilidades de debug ----------
def salvar_png(mss_tools, rgb, caminho):
    rgb = np.ascontiguousarray(rgb)
    mss_tools.to_png(rgb.tobytes(), (rgb.shape[1], rgb.shape[0]), output=caminho)


def desenhar_caixa(img, caixa, cor):
    left, top, right, bottom = caixa
    left, right = left - REG_LEFT, right - REG_LEFT
    top, bottom = top - REG_TOP, bottom - REG_TOP
    img[top, left:right] = cor
    img[bottom - 1, left:right] = cor
    img[top:bottom, left] = cor
    img[top:bottom, right - 1] = cor


def espera(segundos):
    if segundos <= 0:
        return
    print(f"Va para a janela do jogo. Capturando em {segundos}s...")
    for i in range(segundos, 0, -1):
        print(f"  {i}...", flush=True)
        time.sleep(1)


def modo_debug(sct, mss_tools, atraso):
    espera(atraso)
    frame = capturar(sct)
    for nome in NOMES:
        arr = recortar(frame, CAIXAS[nome])
        salvar_png(mss_tools, arr, f"debug_{nome}.png")
        cx = (CAIXAS[nome][0] + CAIXAS[nome][2]) // 2
        cy = (CAIXAS[nome][1] + CAIXAS[nome][3]) // 2
        peca, med, dist = classify(arr)
        if nome == "hold" and peca is None:
            gray_pixels = count_hold_gray_pixels(arr)
            if gray_pixels >= MIN_GRAY_PIXELS:
                print(f"{nome:<6} (x={cx}, y={cy}): hold cinza detectado ({gray_pixels} pixels)")
                continue
        if peca is None:
            print(f"{nome:<6} (x={cx}, y={cy}): nenhuma peca colorida encontrada")
        else:
            r, g, b = (int(v) for v in med)
            print(f"{nome:<6} (x={cx}, y={cy}): {peca}  cor=({r},{g},{b})  dist={dist:.1f}")

    quadro = np.array(frame)
    for nome in NOMES:
        desenhar_caixa(quadro, CAIXAS[nome], (0, 255, 0))
    desenhar_caixa(quadro, CAIXA_TABULEIRO, (255, 80, 80))
    salvar_png(mss_tools, quadro, "debug_frame.png")
    print("\nPNGs salvos na pasta atual: debug_hold.png, debug_slot1.png, ... e debug_frame.png")
    print("(debug_frame.png mostra a area capturada com as caixas desenhadas)")


def modo_debug_board(sct, mss_tools, atraso):
    espera(atraso)
    frame = capturar(sct)
    board_rgb = recortar(frame, CAIXA_TABULEIRO)
    salvar_png(mss_tools, frame, "debug_frame_bruto.png")
    print(f"Captura bruta salva em debug_frame_bruto.png ({frame.shape[1]}x{frame.shape[0]} px).")
    if board_rgb.shape[0] > 0 and board_rgb.shape[1] > 0:
        salvar_png(mss_tools, board_rgb, "debug_board_bruto.png")
        print(f"Recorte bruto do tabuleiro salvo em debug_board_bruto.png "
              f"({board_rgb.shape[1]}x{board_rgb.shape[0]} px).", flush=True)
    else:
        print(f"Recorte do tabuleiro vazio: shape={board_rgb.shape}; "
              "a captura bruta foi salva para diagnostico.", flush=True)
    board = classify_board(board_rgb)
    imagem = draw_board_grid(board_rgb, board)
    arquivo = time.strftime("debug_board_%Y%m%d_%H%M%S.png")
    salvar_png(mss_tools, imagem, arquivo)
    salvar_png(mss_tools, imagem, "debug_board.png")
    print("Tabuleiro bruto (# = colorida, G = lixo cinza, . = vazia):")
    for row in board:
        print("".join(row))
    tab = tuple("".join(r) for r in board)
    print(f"\nAltura da pilha (bruta): {altura_pilha(tab)} linhas | "
          f"celulas coloridas: {sum(l.count('#') for l in tab)} | "
          f"celulas de lixo: {sum(l.count('G') for l in tab)}")
    limpo, n_spawn = validar_spawn(tab)
    a, b = SPAWN_LINHAS
    if limpo is not None:
        print(f"Spawn (linhas {a}-{b}): peca ativa inteira aqui ({n_spawn} celulas) -> "
              f"leitura valida, essas linhas sao apagadas na pilha")
        print(f"Peca ativa lida pela cor: {ler_peca_spawn(board_rgb) or 'nao reconhecida'}")
    else:
        print(f"Spawn (linhas {a}-{b}): {n_spawn} celulas coloridas, esperado 4 -> "
              f"a peca nao esta no spawn (ja desceu ou nao ha peca). Leitura seria descartada")
    hist = histograma_cinza(board_rgb)
    if hist:
        print("Cinzas mais comuns no tabuleiro (brilho: pixels): "
              + ", ".join(f"{v}: {c}" for v, c in hist))
        print(f"Faixa atual do lixo: {LIXO_MIN} a {LIXO_MAX}")
    print(f"Imagem com grade salva em {arquivo} (e debug_board.png)")


def modo_debug_sequencia(sct, mss_tools, atraso):
    """Salva 30 capturas sem overlay para analisar movimento entre frames."""
    quantidade, intervalo, colunas = 30, 0.2, 5
    espera(atraso)

    imagens, registros = [], []
    inicio = time.monotonic()
    for indice in range(quantidade):
        falta = inicio + indice * intervalo - time.monotonic()
        if falta > 0:
            time.sleep(falta)
        frame = capturar(sct)
        board_rgb = np.array(recortar(frame, CAIXA_TABULEIRO))
        board = tuple("".join(l) for l in classify_board(board_rgb))
        imagens.append(board_rgb)
        ocupadas = sum(c != "." for l in board for c in l)
        registros.append((time.monotonic() - inicio, board, ocupadas))

    altura, largura = imagens[0].shape[:2]
    esp = 2
    linhas = (quantidade + colunas - 1) // colunas
    mosaico = np.zeros((linhas * (altura + esp) + esp, colunas * (largura + esp) + esp, 3), dtype=np.uint8)
    for indice, imagem in enumerate(imagens):
        linha, coluna = divmod(indice, colunas)
        y = esp + linha * (altura + esp)
        x = esp + coluna * (largura + esp)
        mosaico[y:y + altura, x:x + largura] = imagem

    base = time.strftime("debug_sequence_%Y%m%d_%H%M%S")
    imagem_path, relatorio_path = base + ".png", base + ".txt"
    sufixo = 1
    while os.path.exists(imagem_path) or os.path.exists(relatorio_path):
        imagem_path, relatorio_path = f"{base}_{sufixo}.png", f"{base}_{sufixo}.txt"
        sufixo += 1

    salvar_png(mss_tools, mosaico, imagem_path)
    with open(relatorio_path, "w", encoding="utf-8") as rel:
        rel.write("Captura de diagnostico; nao e uma leitura validada da pilha.\n")
        rel.write(f"Capturas: {quantidade}; intervalo alvo: {intervalo:.1f} s.\n")
        rel.write("Mosaico em ordem: esquerda para direita, de cima para baixo.\n")
        for indice, (tempo, board, ocupadas) in enumerate(registros):
            rel.write(f"\nFrame {indice:02d} em {tempo:.3f}s; celulas ocupadas: {ocupadas}\n")
            rel.write("\n".join(board) + "\n")

    print(f"Capturadas {quantidade} imagens em {registros[-1][0]:.1f}s; sem enviar comandos ao jogo.")
    print(f"Mosaico do tabuleiro (sem overlay): {imagem_path}")
    print(f"Classificacao por frame: {relatorio_path}")


# ---------- Rastreador da pilha (modelo + conferencia a cada frame) ----------
def celulas_ocupadas(board):
    """Conjunto de (linha, coluna) com bloco ('#' ou 'G') numa leitura bruta do tabuleiro."""
    return frozenset((r, c) for r, linha in enumerate(board)
                     for c, ch in enumerate(linha) if ch != ".")


def componentes(cels):
    """Divide um conjunto de celulas em grupos conectados (vizinhos acima/abaixo/lados)."""
    pendentes = set(cels)
    grupos = []
    while pendentes:
        ini = pendentes.pop()
        grupo, pilha = {ini}, [ini]
        while pilha:
            r, c = pilha.pop()
            for v in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if v in pendentes:
                    pendentes.remove(v)
                    grupo.add(v)
                    pilha.append(v)
        grupos.append(frozenset(grupo))
    return grupos


def e_peca(cels):
    """True se as celulas formam um unico grupo conectado de 4 blocos (um tetromino)."""
    return len(cels) == 4 and len(componentes(cels)) == 1


def limpar_linhas(cels):
    """Remove linhas completas e desce o que estava acima. Retorna (novas_celulas, n_linhas)."""
    contagem = {}
    for r, _ in cels:
        contagem[r] = contagem.get(r, 0) + 1
    cheias = {r for r, n in contagem.items() if n == BOARD_COLS}
    if not cheias:
        return frozenset(cels), 0
    novas = frozenset((r + sum(1 for f in cheias if f > r), c)
                      for r, c in cels if r not in cheias)
    return novas, len(cheias)


def separar_duas_pecas(n):
    """8 blocos = peca que acabou de travar + peca nova. Retorna (travada, nova) ou None."""
    if len(n) != 8:
        return None
    grupos = componentes(n)
    if len(grupos) == 2 and all(len(g) == 4 for g in grupos):
        a, b = grupos
        if sum(r for r, _ in a) < sum(r for r, _ in b):
            return b, a
        return a, b
    if len(grupos) == 1:  # coladas: a nova e a que esta mais em cima
        ordenadas = sorted(n)
        nova, travada = frozenset(ordenadas[:4]), frozenset(ordenadas[4:])
        if e_peca(nova) and e_peca(travada):
            return travada, nova
    return None


EVENTOS_PILHA_MUDOU = ("trava", "trava_limpa", "sync", "sync_vazio", "sync_quieto", "sync_topo")


class RastreadorPilha:
    """
    Mantem a pilha (blocos ja travados) e a peca em voo, frame a frame.

    Ideia central (v2): a peca nova nasce ACIMA do recorte do tabuleiro, entao quando uma
    peca trava nem sempre ha outra peca visivel para separar. Por isso, em vez de adivinhar
    pela FORMA, o rastreador LEMBRA a ultima peca de 4 blocos vista em voo:
      - leitura = pilha + 4 blocos conectados      -> peca em voo (guardada em self.voo);
      - a peca guardada continua no mesmo lugar e apareceram blocos extras
        -> ela travou: entra na pilha (o resto e a peca nova);
      - linhas somem -> conferimos pela pilha + peca guardada com as linhas completas removidas;
      - nada se encaixa por alguns frames -> sincroniza de novo.
    Ressincronizar tambem funciona sem peca visivel: se a leitura ficou IGUAL por
    QUIETO_FRAMES frames e nao ha nada nas linhas de cima, ela inteira e a pilha.
    """
    LIMITE_INCOERENTE = 3  # frames seguidos sem sentido antes de sincronizar de novo
    QUIETO_FRAMES = 6      # leituras iguais seguidas para aceitar a pilha sem ver a peca
    LINHAS_TOPO = 3        # linhas de cima que precisam estar vazias nesse caso

    def __init__(self):
        self.pilha = None   # frozenset de (linha, coluna), ou None enquanto nao sincronizou
        self.voo = None     # ultima peca em voo (4 celulas) ainda nao confirmada na pilha
        self.incoerentes = 0
        self.contagem = {}
        self._ultima = None
        self._iguais = 0

    def _ev(self, nome):
        self.contagem[nome] = self.contagem.get(nome, 0) + 1
        return nome

    def atualizar(self, ocupadas):
        """Processa uma leitura bruta (conjunto de celulas ocupadas). Retorna o nome do evento."""
        if ocupadas == self._ultima:
            self._iguais += 1
        else:
            self._ultima, self._iguais = ocupadas, 1
        if self.pilha is None:
            return self._sincronizar(ocupadas)
        P = self.pilha
        if not P <= ocupadas:
            return self._trava_com_limpeza(ocupadas) or self._incoerente(ocupadas)

        novas = ocupadas - P
        if len(novas) < 4:  # peca escondida (acima do recorte) ou so parte dela visivel
            self.voo, self.incoerentes = None, 0
            return self._ev("parcial")
        if len(novas) == 4 and e_peca(novas):
            self.voo, self.incoerentes = frozenset(novas), 0
            return self._ev("voo")
        # mais de 4 blocos novos: alguma peca travou
        if self.voo is not None and self.voo <= novas:
            return self._commit(P, self.voo, novas - self.voo)
        dividido = self._dividir(novas)
        if dividido:
            return self._commit(P, dividido[0], dividido[1])
        return self._incoerente(ocupadas)

    def _commit(self, P, travada, resto):
        """Coloca a peca travada na pilha; 'resto' sao os blocos da peca nova (ou nada)."""
        if len(resto) > 4:
            dividido = self._dividir(resto)  # duas pecas travaram entre dois frames
            if not dividido:
                return self._incoerente(P | travada | resto)
            travada, resto = travada | dividido[0], dividido[1]
        self.pilha = frozenset(P | travada)
        self.voo = frozenset(resto) if len(resto) == 4 and e_peca(resto) else None
        self.incoerentes = 0
        return self._ev("trava")

    @staticmethod
    def _dividir(novas):
        """Separa blocos novos em (peca_travada, resto) sem saber qual era a peca em voo, ou None."""
        sep = separar_duas_pecas(novas)
        if sep:
            return sep
        grupos = componentes(novas)
        quatro = [g for g in grupos if len(g) == 4 and e_peca(g)]
        if len(grupos) == 2 and len(quatro) >= 1:
            a, b = sorted(grupos, key=lambda g: -sum(r for r, _ in g) / len(g))  # a = a de baixo
            if len(a) == 4 and len(b) <= 4:
                return a, b
        return None

    def _trava_com_limpeza(self, R):
        """Linha(s) completada(s): a leitura nao contem mais a pilha antiga inteira."""
        P = self.pilha
        # 1) a peca guardada travou e completou linhas: simula e confere
        if self.voo is not None:
            Q, k = limpar_linhas(P | self.voo)
            if k and Q <= R and len(R - Q) <= 4:
                resto = R - Q
                self.pilha, self.incoerentes = frozenset(Q), 0
                self.voo = frozenset(resto) if len(resto) == 4 and e_peca(resto) else None
                return self._ev("trava_limpa")
        # 2) nao vimos a peca (travou entre dois frames): confere pela contagem de blocos
        topo_nova = self._peca_do_topo(R)
        opcoes = []
        if topo_nova is not None:
            opcoes.append((R - topo_nova, topo_nova))
        opcoes.append((R, None))
        base = len(P) + 4
        for candidata, nova in opcoes:
            if any(len(candidata) == base - 10 * k for k in range(1, 5)):
                self.pilha, self.voo, self.incoerentes = frozenset(candidata), nova, 0
                return self._ev("trava_limpa")
        return None

    @staticmethod
    def _peca_do_spawn(R):
        """Grupo unico de 4 blocos dentro da faixa de spawn, mesmo com outras cores na tela."""
        candidatos = [g for g in componentes(R)
                      if len(g) == 4 and e_peca(g)
                      and min(r for r, _ in g) <= SPAWN_LINHAS[1]]
        return candidatos[0] if len(candidatos) == 1 else None

    @staticmethod
    def _peca_do_topo(R):
        """O grupo de 4 blocos isolado que contem o bloco mais alto, ou None."""
        if not R:
            return None
        topo = min(r for r, _ in R)
        grupos = [g for g in componentes(R) if min(r for r, _ in g) == topo]
        if len(grupos) == 1 and len(grupos[0]) == 4:
            return grupos[0]
        return None

    def _incoerente(self, R):
        self.incoerentes += 1
        if self.incoerentes >= self.LIMITE_INCOERENTE:
            self.pilha = None
            self.voo = None
            return self._sincronizar(R)
        return self._ev("incoerente")

    def _sincronizar(self, R):
        if not R:
            self.pilha, self.voo, self.incoerentes = frozenset(), None, 0
            return self._ev("sync_vazio")
        # leitura parada e topo vazio: nao ha peca em voo visivel, entao tudo e pilha
        if self._iguais >= self.QUIETO_FRAMES and all(r >= self.LINHAS_TOPO for r, _ in R):
            self.pilha, self.voo, self.incoerentes = frozenset(R), None, 0
            return self._ev("sync_quieto")
        peca = self._peca_do_topo(R)
        if peca is None:
            # Inicio de rodada: a peca de spawn convive com sobras coloridas da tela (contagem,
            # anuncio de rodada, particulas), entao o grupo do topo pode nao ser um grupo de 4.
            peca = self._peca_do_spawn(R)
        if peca is None:
            # Nenhum grupo em forma de peca e nada abaixo da faixa de spawn: pilha vazia.
            if not any(r > SPAWN_LINHAS[1] for r, _ in R):
                self.pilha, self.voo, self.incoerentes = frozenset(), None, 0
                return self._ev("sync_topo")
            return self._ev("sem_sync")
        resto = R - peca
        if resto and len(resto) < 4 and all(r < SPAWN_LINHAS[0] for r, _ in resto):
            resto = frozenset()   # cor acima da faixa de spawn e ruido de tela, nao pilha
        self.pilha, self.voo, self.incoerentes = frozenset(resto), peca, 0
        return self._ev("sync")


def pilha_para_texto(pilha):
    """Pilha (conjunto de celulas) -> tupla de 20 strings de 10 caracteres."""
    return tuple("".join("#" if (r, c) in pilha else "." for c in range(BOARD_COLS))
                 for r in range(BOARD_ROWS))


def mapa_diferencas(pilha, ocupadas):
    """Texto para o log: # = na pilha e na leitura, o = so na leitura, x = so na pilha."""
    pilha = pilha or frozenset()
    linhas = []
    for r in range(BOARD_ROWS):
        linhas.append("      " + "".join(
            "#" if (r, c) in pilha and (r, c) in ocupadas else
            "o" if (r, c) in ocupadas else
            "x" if (r, c) in pilha else "."
            for c in range(BOARD_COLS)))
    return "\n".join(linhas)


def letra_das_celulas(rgb, celulas):
    """Letra da peca pela cor do centro das celulas dadas (voto), ou None se nao bater."""
    h, w = rgb.shape[:2]
    nomes = list(CORES)
    votos = {}
    for r, c in celulas:
        y, x = int((r + 0.5) * h / BOARD_ROWS), int((c + 0.5) * w / BOARD_COLS)
        trecho = rgb[max(0, y - 4):y + 5, max(0, x - 4):x + 5].reshape(-1, 3)
        med = np.median(trecho, axis=0)
        d = np.linalg.norm(CORES_REF - med, axis=1)
        i = int(d.argmin())
        if d[i] <= MAX_DIST_CELULA:
            votos[nomes[i]] = votos.get(nomes[i], 0) + 1
    if not votos:
        return None
    letra, n = max(votos.items(), key=lambda kv: kv[1])
    return letra if n >= 3 else None


def mostrar_tabuleiro(tab):
    print()
    for linha in tab:
        print("  " + linha)
    print(f"  pilha: {altura_pilha(tab)} linhas")


def modo_ao_vivo(sct, frames, frames_tab=FRAMES_TAB, imprimir_tab=False, mss_tools=None):
    est = Estabilizador(frames)
    rastr = Rastreador()
    pilha_tr = RastreadorPilha()
    tabuleiro = None  # ultima pilha confirmada (texto)
    stats = {}
    total_frames = 0
    inicio = time.monotonic()
    log = open(ARQUIVO_LOG, "w", encoding="utf-8")

    def registrar(msg):
        """Anota um evento no arquivo de log (nao depende do terminal atualizar)."""
        log.write(f"[{time.monotonic() - inicio:7.2f}s] {msg}\n")
        log.flush()

    print(f"Lendo a tela. Ctrl+C para sair. (os eventos tambem vao para {ARQUIVO_LOG})\n")
    ultimo = ""
    ultimo_estado = None
    falhas_vistas = 0   # quantas vezes a pilha falhou (incoerente / sem_sync)
    falhas_salvas = 0
    diverge_seguidos = 0
    n, t0, fps = 0, time.monotonic(), 0.0
    try:
        while True:
            frame = capturar(sct)
            n += 1
            total_frames += 1
            leitura = ler_hold_e_fila(frame)
            if leitura is not None and est.atualizar(leitura):
                hold_obs, fila = est.estavel
                rastr.atualizar(fila, hold_obs)
                registrar(f"fila/hold mudou -> hold_lido={hold_obs} fila={' '.join(fila)}")

            # Pilha: acompanhada frame a frame (peca em voo, trava, linha limpa).
            rgb_tab = recortar(frame, CAIXA_TABULEIRO)
            ocupadas = celulas_ocupadas(classify_board(rgb_tab))
            ev = pilha_tr.atualizar(ocupadas)
            if ev in EVENTOS_PILHA_MUDOU:
                tabuleiro = pilha_para_texto(pilha_tr.pilha)
                registrar(f"{ev}: pilha={altura_pilha(tabuleiro)} linhas, {len(pilha_tr.pilha)} blocos")
                if imprimir_tab:
                    mostrar_tabuleiro(tabuleiro)
                    ultimo = ""
            elif ev in ("incoerente", "sem_sync"):
                falhas_vistas += 1
                # guarda as 3 primeiras e depois 1 a cada 40 (ate 8 no total)
                if falhas_salvas < 8 and (falhas_vistas <= 3 or falhas_vistas % 40 == 0):
                    falhas_salvas += 1
                    registrar(f"{ev} (falha {falhas_vistas}): leitura com {len(ocupadas)} blocos. Mapa "
                              "(# pilha e leitura, o so leitura, x so pilha):\n"
                              + mapa_diferencas(pilha_tr.pilha, ocupadas))
                    if mss_tools is not None:
                        nome_png = f"debug_falha_{falhas_salvas}.png"
                        salvar_png(mss_tools, draw_board_grid(rgb_tab, classify_board(rgb_tab)), nome_png)
                        registrar(f"imagem salva: {nome_png}")

            # Conferencia: a cor da peca em voo bate com a mao deduzida pela fila?
            if ev == "voo" and rastr.mao is not None:
                lida = letra_das_celulas(rgb_tab, pilha_tr.voo)
                if lida is None:
                    stats["voo_cor_nao_reconhecida"] = stats.get("voo_cor_nao_reconhecida", 0) + 1
                elif lida == rastr.mao:
                    stats["mao_confere"] = stats.get("mao_confere", 0) + 1
                    diverge_seguidos = 0
                else:
                    stats["mao_diverge"] = stats.get("mao_diverge", 0) + 1
                    diverge_seguidos += 1
                    if diverge_seguidos == 6:  # persistente (nao e so atraso de 1-2 frames)
                        registrar(f"MAO DIVERGE: fila/hold dizem {rastr.mao}, cor lida {lida}")

            agora = time.monotonic()
            if agora - t0 >= 1.0:
                fps, n, t0 = n / (agora - t0), 0, agora

            estado = (rastr.texto_hold(), rastr.mao, rastr.fila)
            if estado != ultimo_estado and rastr.fila is not None:
                registrar(f"ESTADO hold={rastr.texto_hold()} mao={rastr.mao or '?'} "
                          f"proximas={' '.join(rastr.fila)}")
                ultimo_estado = estado

            if rastr.fila is None:
                texto = "Aguardando leitura estavel..."
            else:
                texto = (f"Hold: {rastr.texto_hold()}  |  Mao: {rastr.mao or '?'}  |  "
                         f"Proximas: {' '.join(rastr.fila)}  |  ")
                if pilha_tr.pilha is None or tabuleiro is None:
                    texto += "Pilha: sincronizando  |  "
                elif pilha_tr.incoerentes:
                    texto += f"Pilha: {altura_pilha(tabuleiro)}? (conferindo)  |  "
                else:
                    texto += f"Pilha: {altura_pilha(tabuleiro)}  |  "
                texto += f"{fps:.0f} fps"
            if texto != ultimo:
                padding = " " * max(0, len(ultimo) - len(texto))
                print(f"\r{texto}{padding}", end="", flush=True)
                ultimo = texto
    except KeyboardInterrupt:
        print("\nFim.")
        duracao = time.monotonic() - inicio
        resumo = [f"Resumo: {total_frames} frames em {duracao:.1f}s "
                  f"({total_frames / max(duracao, 0.001):.0f} fps medio)",
                  "Eventos da pilha:"]
        resumo += [f"  {k}: {v}" for k, v in sorted(pilha_tr.contagem.items(), key=lambda kv: -kv[1])]
        resumo += ["Conferencia da mao pela cor:"]
        resumo += [f"  {k}: {v}" for k, v in sorted(stats.items(), key=lambda kv: -kv[1])]
        for linha in resumo:
            print(linha)
            log.write(linha + "\n")
        if tabuleiro is not None:
            mostrar_tabuleiro(tabuleiro)
            log.write("Ultima pilha confirmada:\n" + "\n".join("  " + l for l in tabuleiro) + "\n")
        log.close()
        print(f"\nLog salvo em {ARQUIVO_LOG} (pasta onde voce rodou o comando).")


def desativar_selecao_console():
    """Windows: clicar na janela do terminal pausa o programa (modo de selecao). Desliga isso."""
    try:
        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-10)  # entrada padrao
        modo = ctypes.c_uint32()
        if k32.GetConsoleMode(handle, ctypes.byref(modo)):
            # 0x40 = selecao rapida; 0x80 = flags estendidas (precisa ligar para valer)
            k32.SetConsoleMode(handle, (modo.value & ~0x40) | 0x80)
    except Exception:
        pass


def modo_perfil(sct, quadros=100):
    """Mede o tempo medio de cada etapa (captura, hold/fila, tabuleiro) em ms."""
    t_cap = t_hf = t_tab = 0.0
    for _ in range(quadros):
        a = time.perf_counter()
        frame = capturar(sct)
        b = time.perf_counter()
        ler_hold_e_fila(frame)
        c = time.perf_counter()
        ler_tabuleiro_limpo(frame)
        d = time.perf_counter()
        t_cap, t_hf, t_tab = t_cap + (b - a), t_hf + (c - b), t_tab + (d - c)
    total = (t_cap + t_hf + t_tab) / quadros * 1000
    print(f"Media por frame em {quadros} frames:")
    print(f"  captura da tela:  {t_cap / quadros * 1000:6.2f} ms")
    print(f"  hold e fila:      {t_hf / quadros * 1000:6.2f} ms")
    print(f"  tabuleiro:        {t_tab / quadros * 1000:6.2f} ms")
    print(f"  total:            {total:6.2f} ms  (~{1000 / total:.0f} fps)")


def ajustar_janela_console():
    """Tenta reduzir o console para 100x10 (o terminal pode ignorar)."""
    resultado = subprocess.run("mode con: cols=100 lines=10", shell=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if resultado.returncode != 0:
        print("Aviso: nao foi possivel redimensionar o console automaticamente.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", choices=tuple(LAYOUTS), default="centro",
                    help="layout de captura: centro (padrao) ou esquerda (versus)")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--debug-board", action="store_true", help="captura e imprime a grade do tabuleiro")
    ap.add_argument("--debug-sequence", action="store_true",
                    help="captura 30 frames para diagnosticar movimento no tabuleiro")
    ap.add_argument("--atraso", type=int, default=3, help="segundos de espera antes da captura de debug")
    ap.add_argument("--frames", type=int, default=FRAMES_ESTAVEIS)
    ap.add_argument("--frames-tab", type=int, default=FRAMES_TAB,
                    help="frames seguidos iguais para aceitar o tabuleiro (ao vivo)")
    ap.add_argument("--tabuleiro", action="store_true",
                    help="no modo ao vivo, imprime a pilha toda vez que ela muda")
    ap.add_argument("--compacto", action="store_true", help="redimensiona o console para 100x10")
    ap.add_argument("--perfil", action="store_true", help="mede quanto tempo cada etapa leva por frame")
    args = ap.parse_args()

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    desativar_selecao_console()

    modo = ("perfil" if args.perfil else "debug_sequence" if args.debug_sequence else
            "debug_board" if args.debug_board else "debug" if args.debug else None)
    if modo:  # nestes modos o texto tambem vai para um arquivo (a janela pode fechar sozinha)
        arquivo_rel = open(f"relatorio_{modo}.txt", "w", encoding="utf-8")
        sys.stdout = Duplicar(sys.stdout, arquivo_rel)
        print(f"(este texto tambem esta salvo em relatorio_{modo}.txt)")

    import mss
    import mss.tools

    # mss novo usa mss.MSS(); versoes antigas so tem mss.mss()
    abrir_mss = getattr(mss, "MSS", None) or mss.mss
    with abrir_mss() as sct:
        configurar_layout(args.layout, sct.monitors[1])
        if args.perfil:
            modo_perfil(sct)
        elif args.debug_sequence:
            modo_debug_sequencia(sct, mss.tools, args.atraso)
        elif args.debug_board:
            modo_debug_board(sct, mss.tools, args.atraso)
        elif args.debug:
            modo_debug(sct, mss.tools, args.atraso)
        else:
            if args.compacto:
                ajustar_janela_console()
            modo_ao_vivo(sct, args.frames, args.frames_tab, args.tabuleiro, mss.tools)


class Duplicar:
    """Escreve na tela e num arquivo ao mesmo tempo."""

    def __init__(self, tela, arquivo):
        self.tela, self.arquivo = tela, arquivo

    def write(self, texto):
        self.tela.write(texto)
        self.arquivo.write(texto)
        self.arquivo.flush()

    def flush(self):
        self.tela.flush()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()  # mostra o erro em vez de a janela fechar sozinha
    finally:
        try:
            input("\nPressione Enter para fechar...")
        except Exception:
            pass
