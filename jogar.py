"""
Passo 4 do bot: JOGA no TETR.IO ao vivo. Junta a leitura da tela (tetrio_projeto.py),
a decisao (decisor.py) e o envio de teclas (SendInput do Windows, sem instalar nada).

Uso:
  py player.py --seco               -> NAO aperta nada: mostra o que jogaria.
  py player.py                      -> joga com estrategia de ataque; TETR.IO precisa estar em foco.
  py player.py --modo blitz         -> usa pontuacao de Blitz para jogo solo.
  py player.py --atraso 5           -> aguarda ate 5s antes de esperar o foco do jogo (padrao 3).
  py player.py --sd-ms-por-linha 20 -> calibra o tempo do soft drop por linha descida.
  py player.py --sem-dt             -> desliga o DT Cannon (ativado por padrao).
  py player.py --sem-especular      -> nao calcula a proxima jogada durante o envio das teclas.

Teclas de controle (funcionam com o jogo em foco): F7 pausa/retoma, F9 encerra.
Teclas do jogo usadas (as suas): NUMPAD1 esquerda, NUMPAD3 direita, NUMPAD5 girar horario,
Z girar anti-horario, A girar 180, C hold, SPACE hard drop, NUMPAD2 soft drop em rotas com tuck.

Como decide e se corrige:
  - o bot mantem a PROPRIA pilha (sabe onde colocou cada peca) e confere com a leitura da tela;
    leituras instaveis nao substituem o modelo; divergencias sincronizadas ficam no jogar_log.txt;
  - primeira peca desconhecida: aperta hold e joga a proxima da fila (a peca guardada vira
    conhecida depois, porque o hold mostra a cor quando nao esta cinza).
Hipoteses a conferir no jogo (se algo sair torto, o jogar_log.txt mostra onde):
  peca nasce nas colunas 3-5 (I: 3-6, O: 4-5), rotacao horaria padrao SRS.
T-spins usam kicks SRS e classificacao por cantos; --sem-spin desliga esse planejamento.
Soft-drop intermediario tambem pode ser usado para tucks de outras pecas.
"""
import argparse
import collections
import ctypes
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
import traceback
import tetrio_projeto as tp
import decisor
import openers

ARQUIVO_LOG = "jogar_log.txt"
SEM_LEITURA_S = 1.5        # sem fila legivel por este tempo = nao ha jogo na tela (menu, resultado, tela de espera)
GAP_NOVA_PARTIDA_S = 3.0   # a tela de jogo voltou depois disto: o estado antigo e descartado e espera-se uma partida nova
MEIO_JOGO_S = 2.5          # voltou com pilha nao vazia por este tempo: era a mesma partida, retoma pela leitura da tela

# scancodes (teclado fisico; o jogo le a tecla fisica, entao NumLock nao atrapalha)
SCAN = {"esq": 0x4F, "dir": 0x51, "baixo": 0x50, "drop": 0x39,
        "ccw": 0x2C, "cw": 0x4C, "180": 0x1E, "hold": 0x2E}

TOQUE_S = 0.021      # tempo com a tecla apertada em cada toque (18 ms perdia entradas no navegador; 21 ms > 1 frame a 60 Hz)
PAUSA_S = 0.004      # pausa curta entre toques
TOQUE_DROP_S = 0.035
ESPERA_APOS_HOLD_S = 0.06
DAS_S = 9 / 60      # DAS configurado no TETR.IO: 9 frames a 60 fps (150 ms)
SEGURAR_S = DAS_S + 1 / 60  # uma margem de frame para o DAS disparar com ARR=0
SD_S = 0.021         # SDF infinito: um pulso curto ja leva a peca ao fundo
SD_S_POR_LINHA = 0.0
COL_SPAWN = {"I": 3, "O": 4, "T": 3, "S": 3, "Z": 3, "J": 3, "L": 3}  # coluna da esquerda da caixa

# ---------- envio de teclas (Windows) ----------
KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x0002, 0x0008
_PUL = ctypes.POINTER(ctypes.c_ulong)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _PUL)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", _PUL)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _UNIAO(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNIAO)]


class Teclado:
    """Aperta e solta teclas pelo scancode (SendInput)."""

    def __init__(self, toque=TOQUE_S, pausa=PAUSA_S):
        self.toque, self.pausa = toque, pausa

    def _evento(self, nome, solto):
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if solto else 0)
        entrada = _INPUT(type=1, u=_UNIAO(ki=_KEYBDINPUT(0, SCAN[nome], flags, 0, None)))
        enviados = ctypes.windll.user32.SendInput(1, ctypes.byref(entrada), ctypes.sizeof(entrada))
        if enviados != 1:
            estado = "soltar" if solto else "pressionar"
            raise OSError(f"SendInput nao enviou a tecla {nome!r} ({estado}); retorno={enviados}.")

    def _tocar(self, nome, duracao):
        self._evento(nome, False)
        time.sleep(duracao)
        self._evento(nome, True)
        time.sleep(self.pausa)

    def executar(self, plano):
        """plano: lista de ('toque', nome) ou ('segurar', nome, segundos)."""
        for acao in plano:
            if acao[0] == "toque":
                nome = acao[1]
                duracao = max(self.toque, TOQUE_DROP_S) if nome == "drop" else self.toque
                self._tocar(nome, duracao)
                if nome == "hold":
                    time.sleep(ESPERA_APOS_HOLD_S)
            else:
                self._tocar(acao[1], acao[2])


def plano_teclas(j):
    """Jogada -> lista de acoes do Teclado. Usa o plano do decisor (com giros com kick, soft drop,
    DAS); se a jogada nao trouxer plano (decisor antigo), monta o simples: giros, deslocamento, drop."""
    plano = []
    if getattr(j, "teclas", None):
        for t in j.teclas:
            if t[0] == "das":
                plano.append(("segurar", t[1], SEGURAR_S))
            elif t[0] == "sd":
                distancia = t[1] if len(t) > 1 else 1
                plano.append(("segurar", "baixo", max(SD_S, SD_S_POR_LINHA * distancia)))
            else:
                plano.append(t)
        if j.usar_hold and plano[0] != ("toque", "hold"):  # mao desconhecida: o Bot forca o hold
            plano.insert(0, ("toque", "hold"))
        return plano
    if j.usar_hold:
        plano.append(("toque", "hold"))
    plano += {0: [], 1: [("toque", "cw")], 2: [("toque", "180")], 3: [("toque", "ccw")]}[j.giros]
    d = j.coluna_caixa - COL_SPAWN[j.letra]
    if d:
        nome = "dir" if d > 0 else "esq"
        cols = [c for _, c in j.celulas]
        na_parede = (d < 0 and min(cols) == 0) or (d > 0 and max(cols) == tp.BOARD_COLS - 1)
        if na_parede and abs(d) >= 5:
            plano.append(("segurar", nome, SEGURAR_S))
        else:
            plano += [("toque", nome)] * abs(d)
    plano.append(("toque", "drop"))
    return plano


def texto_plano(plano):
    return " ".join(a[1] if a[0] == "toque" else f"{a[1]}(segura)" for a in plano)


# ---------- o bot ----------
class Bot:
    ESPERA_MAX = 2.5   # segundos esperando a fila avancar depois de jogar
    ESPERA_REPETIR = 1.5  # apos "fila sem avancar", espera isto antes de tentar de novo
    ESPERA_REPETIR_LONGA = 8.0  # apos 3 falhas seguidas (jogo parado ou sem receber teclas)

    def __init__(self, teclado, registrar, seco=False, sem_foco=False, foco=None,
                 enviar_em_thread=False):
        self.teclado, self.log, self.seco = teclado, registrar, seco
        self.sem_foco, self.foco = sem_foco, foco or (lambda: True)
        # Envio de teclas fora do laco: enquanto as teclas saem, a leitura da tela continua
        # rodando, entao a confirmacao da fila chega junto com o fim do envio (era isto que
        # custava 1-2 frames por peca e derrubava o PPS).
        self.enviar_em_thread = enviar_em_thread
        self._envio_exec = ThreadPoolExecutor(max_workers=1) if enviar_em_thread else None
        self._envio_futuro = None
        self.marcas_jogadas = collections.deque(maxlen=64)
        self.ataque_total = 0.0      # ataque somado nesta partida (para medir VS/ataque por peca)
        self.modelo = None       # pilha que o bot ESPERA ter na tela
        self.modelo_tentativo = False  # True: previsto apos enviar teclas, ainda nao confirmado na tela
        self.alvo = None         # avancos da fila que indicam que a peca travou e outra nasceu
        self.limite = 0.0
        self.pausa_ate = 0.0
        self.desacordos = 0
        self.aviso_alvo_pendente = False
        self.jogadas = 0
        self.ultima_jogada = None
        self.ultimo_evento_pilha = None
        self.b2b = 0
        self.combo = 0
        self.usando_modelo = False
        self.sem_foco_aviso = 0.0
        self.mao_desconhecida_aviso = 0.0
        self.aviso_topo = 0.0
        self.tabuleiro_vazio_desde = None
        self.inicio_rodada_detectado = False
        self.ultimo_envio = 0.0        # ultimo instante em que teclas foram enviadas (separa fim de jogo de meio de jogo)
        self.aguardando_go = False     # nova partida detectada; esperando a primeira peca nascer (GO)
        self.voo_desde = None          # desde quando ha uma peca ativa visivel no spawn
        self.rodada_confirmada = False
        self.pecas_abertura = 0
        self.bloqueado = False
        self.falhas_decisao = 0
        self.drop_emergencia_avancos = None
        self.ultima_limpas = 0
        self.linhas_lixo = 0
        self.especular = True          # calcula a proxima jogada enquanto as teclas da atual sao enviadas
        self.espec = None              # (chave, Future) da jogada especulada
        self.espera_avanco = None      # (avancos, instante): fila nao avancou, nao repetir a jogada
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.acertos_espec = self.erros_espec = 0
        self.sem_jogo = False            # a fila nao esta legivel: nao ha jogo na tela, nao mandar teclas
        self.sem_jogo_desde = 0.0
        self.exigir_nova_rodada = False  # a tela voltou depois de um intervalo longo: so joga apos detectar partida nova
        self.retomada_desde = 0.0
        self.falhas_fila = 0             # "fila sem avancar" seguidas (sem a fila andar entre elas)
        self.aviso_fila_parada = False
        self.opener_rota = None          # rota do opener para a bag atual: ((letra, celulas), ...)
        self.opener_nome = None
        self.opener_fase = None          # ex.: suporte, dt_tsd, fase inicial
        self.opener_tsd_celulas = None   # celulas do T do TSD (DT Cannon), quando separadas da rota
        self.opener_hold_troca = None    # peca esperada apos hold-troca (fila nao avanca)
        self.opener_hold_avancos = None  # avanco da fila no instante do hold-troca
        self.opener_hold_mao_anterior = None
        self.opener_acao_pendente = None # fase a confirmar quando a fila avancar apos a colocacao

    # --- jogo sumiu da tela / voltou ---
    def jogo_sumiu(self, agora):
        """Chamado pelo loop quando a fila fica ilegivel por SEM_LEITURA_S: nao ha jogo na tela
        (menu, tela de resultado, espera entre partidas). Para de mandar teclas e descarta o plano."""
        if self.sem_jogo:
            return
        self.sem_jogo = True
        self.sem_jogo_desde = agora - SEM_LEITURA_S
        self.alvo = None
        self.aviso_alvo_pendente = False
        self.espera_avanco = None
        self.espec = None
        self.modelo = None
        self.modelo_tentativo = False
        self.usando_modelo = False
        self.log(f"JOGO NAO VISIVEL: fila ilegivel ha {SEM_LEITURA_S:.0f}s (menu, resultado ou espera entre partidas); "
                 "parei de enviar teclas e descartei o plano/modelo ate a tela de jogo voltar.")

    def jogo_voltou(self, agora):
        """Chamado a cada leitura valida da fila. Retorna True se o jogo estava sumido."""
        if not self.sem_jogo:
            return False
        self.sem_jogo = False
        gap = agora - self.sem_jogo_desde
        if gap >= GAP_NOVA_PARTIDA_S:
            self.exigir_nova_rodada = True
            self.retomada_desde = agora
            self.falhas_fila = 0
            self.aviso_fila_parada = False
            self.log(f"TELA DE JOGO VOLTOU apos {gap:.0f}s: estado antigo descartado; so jogo depois de detectar "
                     "partida nova (tabuleiro vazio + hold livre) ou de ver que e a mesma partida em andamento.")
        else:
            self.log(f"TELA DE JOGO VOLTOU apos {gap:.1f}s: retomando pela leitura da tela.")
        return True

    # --- envio de teclas (sincrono ou em thread) ---
    def _envio_pronto(self):
        """True se nao ha teclas em transito. Repassa erro de SendInput para o laco."""
        futuro = self._envio_futuro
        if futuro is None:
            return True
        if not futuro.done():
            return False
        self._envio_futuro = None
        erro = futuro.exception()
        if erro is not None:
            raise erro
        self.ultimo_envio = time.monotonic()
        return True

    def _aguardar_envio(self, timeout=2.0):
        """Espera o envio anterior terminar (usado antes de teclas avulsas)."""
        futuro, self._envio_futuro = self._envio_futuro, None
        if futuro is None:
            return
        if not futuro.done():
            futuro.result(timeout=timeout)
        erro = futuro.exception()
        if erro is not None:
            raise erro
        self.ultimo_envio = time.monotonic()

    def _enviar(self, plano):
        """Envia um plano de teclas. Em thread, retorna na hora e o laco segue lendo a tela."""
        if not self.enviar_em_thread:
            self.teclado.executar(plano)
            self.ultimo_envio = time.monotonic()
            return
        self._aguardar_envio()
        self.ultimo_envio = time.monotonic()
        self._envio_futuro = self._envio_exec.submit(self.teclado.executar, plano)

    def pps(self, janela=15.0, agora=None):
        """Pecas por segundo nos ultimos 'janela' segundos (mede o alvo de 3 PPS)."""
        agora = time.monotonic() if agora is None else agora
        recentes = [t for t in self.marcas_jogadas if agora - t <= janela]
        if len(recentes) < 2:
            return 0.0
        intervalo = recentes[-1] - recentes[0]
        return (len(recentes) - 1) / intervalo if intervalo > 0 else 0.0

    def _espera_repetir(self):
        # depois de varias falhas seguidas o jogo provavelmente nao esta recebendo as teclas: espaca as tentativas
        return self.ESPERA_REPETIR if self.falhas_fila < 3 else self.ESPERA_REPETIR_LONGA

    def _liberar_retomada(self, pil, agora):
        """Com exigir_nova_rodada: True = ainda esperando (nao jogar). Se a pilha nao esta vazia por
        MEIO_JOGO_S, nao e partida nova (so a tela sumiu um instante): retoma pela leitura da tela."""
        if not self.exigir_nova_rodada:
            return False
        vazio = pil.pilha is not None and (not pil.pilha or self._inicio_visual(pil))
        if vazio or pil.pilha is None:
            self.retomada_desde = agora
            return True
        if agora - self.retomada_desde >= MEIO_JOGO_S:
            self.exigir_nova_rodada = False
            self.log("RETOMADA: a pilha nao esta vazia, e a mesma partida em andamento; "
                     "ressincronizando pela leitura da tela.")
            return False
        return True

    # --- opener: rota montada para a bag que esta na tela ---
    def _cancelar_opener(self, motivo):
        if (self.opener_rota or self.opener_fase or self.opener_hold_troca
                or self.opener_acao_pendente):
            self.log(f"OPENER {self.opener_nome or ''}: abandonado ({motivo}); seguindo com a decisao normal.".strip())
        self.opener_rota, self.opener_nome, self.opener_fase, self.opener_tsd_celulas, self.opener_hold_troca = None, None, None, None, None
        self.opener_hold_avancos = None
        self.opener_hold_mao_anterior = None
        self.opener_acao_pendente = None

    def _confirmar_opener_avanco(self, rastr=None, pil=None, agora=None):
        """Confirma suporte/TSD pela fila; um TST completo exige prova visual do clear."""
        pendente, self.opener_acao_pendente = self.opener_acao_pendente, None
        if pendente in ("dt_suporte", "tsd_suporte"):
            self.opener_fase = "dt_tsd" if pendente == "dt_suporte" else "tsd"
            self.log(f"OPENER {self.opener_nome}: suporte confirmado pela fila; executando TSD (fase 2).")
            print(f"\n>>> OPENER {self.opener_nome}: suporte pronto, indo para TSD", flush=True)
        elif pendente in ("dt_tsd", "tsd"):
            nome = self.opener_nome or "opener"
            self._cancelar_opener("continuidade TST automatica desativada; decisor assume")
            print(f"\n>>> OPENER {nome}: TSD confirmado; decisor normal assume (sem busca de TST)",
                  flush=True)
        elif pendente == "fase_inicial":
            self.log(f"OPENER {self.opener_nome}: fase inicial confirmada pela fila ({self.jogadas} pecas). "
                     "Continuacoes nao modeladas; seguindo com decisao normal.")
            print(f"\n>>> OPENER {self.opener_nome}: fase inicial concluida (sem continuacao completa)", flush=True)
            self.opener_rota = None
            self.opener_fase = None

    def _abrir_opener(self, rastr):
        """No comeco da rodada, com a bag conhecida, pede a rota do opener.

        A rota so existe se as pecas da vez conseguirem montar um T-spin conferido
        pelo decisor; sem rota o bot joga normal (nunca trava por causa de opener).
        """
        if (not decisor.OPENERS or self.opener_rota is not None or self.jogadas
                or self.pecas_abertura or self.modelo):
            return
        fila = list(rastr.fila or ())
        if rastr.mao is None or len(fila) < 4:
            return
        # Completa a bag: a 7a peca (nao visivel na fila) pode ser inferida no inicio.
        if len(fila) == 5:
            setima = None
            if hasattr(rastr, "proxima_peca_da_bag"):
                try:
                    setima = rastr.proxima_peca_da_bag()
                except Exception:
                    setima = None
            if setima is None:
                faltando = [p for p in "TIOSZJL" if p not in {rastr.mao, *fila}]
                if len(faltando) == 1:
                    setima = faltando[0]
            if setima and setima not in fila:
                fila = fila + [setima]
        # Tenta ordens viaveis usando o hold: segurar a mao inicial troca a sequencia
        # para [fila[0], mao, fila[1]...], o que pode viabilizar a rota.
        ordens = [([rastr.mao] + fila, " ".join([rastr.mao] + fila), False)]
        if rastr.hold_estado == "vazio" and rastr.hold_peca is None and len(fila) >= 2:
            ordens.append(([fila[0], rastr.mao] + fila[1:],
                           "hold(" + rastr.mao + ") " + " ".join(fila), True))
        nome = rota = None
        bag_log = ordens[0][1]
        for pecas, etiqueta, _segurou in ordens:
            try:
                nome, rota = openers.planejar(decisor.para_linhas(frozenset()), pecas)
            except Exception:
                self.log("OPENER: planejador falhou (" + traceback.format_exc().splitlines()[-1] + "); seguindo normal.")
                self.opener_rota, self.opener_nome, self.opener_fase, self.opener_tsd_celulas, self.opener_hold_troca = [], None, None, None, None
                return
            if rota:
                bag_log = etiqueta
                break
        if not rota:
            self.log(f"OPENER: nenhuma rota para a bag {' '.join([rastr.mao] + fila)} (hold considerado); jogando normal.")
            self.opener_rota, self.opener_nome, self.opener_fase, self.opener_tsd_celulas, self.opener_hold_troca = [], None, None, None, None
            return
        rota = list(rota)
        self.opener_nome = nome
        # A rota termina no T do TSD. Guarda-o separado para buscar uma continuacao TST
        # depois do clear, tanto no DT Cannon quanto em TKI-3 e no fallback por bag.
        if rota and rota[-1][0] == "T":
            self.opener_tsd_celulas = rota[-1][1]
            self.opener_rota = rota[:-1]
            self.opener_fase = "dt_suporte" if openers.eh_dt_cannon(nome) else "tsd_suporte"
            passos = [letra for letra, _ in rota]
            self.log(f"OPENER {nome} (TSD com continuacao TST): suporte+TSD para [{bag_log}] -> "
                     + " ".join(passos))
            print(f"\n>>> OPENER: {nome} - fase 1/suporte "
                  f"({' '.join(letra for letra, _ in self.opener_rota)}) + TSD", flush=True)
        elif openers.eh_dt_cannon(nome):
            # sem T explicito na rota (inesperado): usar deteccao por estado
            self.opener_tsd_celulas = None
            self.opener_rota = rota
            self.opener_fase = "dt_suporte"
            self.log(f"OPENER {nome} (completo, em fases): suporte inicial para [{bag_log}] -> "
                     + " ".join(letra for letra, _ in rota))
            print(f"\n>>> OPENER: {nome} completo - fase 1/suporte ({' '.join(letra for letra, _ in rota)})", flush=True)
        else:
            self.opener_tsd_celulas = None
            self.opener_rota = rota
            self.opener_fase = "suporte"
            self.log(f"OPENER {nome} (fase inicial): rota para [{bag_log}] -> "
                     + " ".join(letra for letra, _ in rota))
            print(f"\n>>> OPENER: {nome} fase inicial ({' '.join(letra for letra, _ in rota)})", flush=True)

    def _opener_base(self, pil):
        base = self.modelo if self.modelo is not None else pil.pilha
        if base is None:
            return None
        if (pil.pilha is not None and getattr(pil, "incoerentes", 0) == 0
                and len(pil.pilha ^ base) > 4):
            self._cancelar_opener("a tela nao bate mais com a rota")
            return None
        return base

    def _opener_executar_celulas(self, rastr, pil, mao, estado, agora, letra, celulas, rotulo):
        if mao != letra:
            fila = rastr.fila or ()
            hold_troca = estado == "pronto" and rastr.hold_peca == letra
            hold_vazio = estado == "vazio" and fila and fila[0] == letra
            if hold_troca or hold_vazio:
                if self.seco:
                    return False
                self._enviar([("toque", "hold")])
                self.ultimo_envio = agora
                self.ultima_jogada = "HOLD DO OPENER"
                if hold_vazio:
                    self.alvo = rastr.avancos + 1
                    self.opener_hold_troca = None
                else:
                    # troca: fila nao avanca; esperar mao virar 'letra' sem spam
                    self.alvo = None
                    self.opener_hold_troca = letra
                    self.opener_hold_avancos = rastr.avancos
                    self.opener_hold_mao_anterior = mao
                self.limite = agora + self.ESPERA_MAX
                self.log(f"OPENER {self.opener_nome} ({rotulo}): hold para trazer {letra} (mao era {mao}).")
                return True
            proxima = fila[0] if fila else None
            self._cancelar_opener(
                f"hold nao pode trazer {letra} (estado={estado}, hold={rastr.hold_peca!r}, "
                f"proxima={proxima!r}; mao era {mao}, {rotulo})"
            )
            return False
        base = self._opener_base(pil)
        if base is None:
            return False
        j = decisor.jogada_para(decisor.para_linhas(base), letra, celulas,
                                False, self.b2b, self.combo)
        if j is None:
            self._cancelar_opener(f"{letra} nao alcanca as celulas da rota ({rotulo})")
            return False
        if letra == "T":
            if j.spin != "full" or j.limpas != 2:
                self._cancelar_opener(
                    f"T da rota nao confirmou T-spin double full ({rotulo})"
                )
                return False
        pilha_prevista = frozenset(decisor._limpar(base | j.celulas)[0])
        if self.seco:
            self.modelo, self.modelo_tentativo = pilha_prevista, False
            self.jogadas += 1
            self.pecas_abertura += 1
            self.ultimo_envio = agora
            self.alvo, self.limite = rastr.avancos + 1, agora + 60
            self.b2b, self.combo = j.b2b, j.combo
            self.ultima_limpas = j.limpas
            self.ultima_jogada = f"OPENER {rotulo}: {letra}"
            self.log(f"OPENER {self.opener_nome} ({rotulo}): {letra} "
                     f"{'T-SPIN' if j.spin else ''} {j.limpas}L")
            return True
        self.jogadas += 1
        self.marcas_jogadas.append(time.monotonic())
        self.ataque_total += j.valor
        self.pecas_abertura += 1
        self.b2b, self.combo = j.b2b, j.combo
        self._enviar(plano_teclas(j))
        self.ultimo_envio = time.monotonic()
        self.ultima_limpas = j.limpas
        self.ultima_jogada = (f"OPENER {self.jogadas} ({rotulo}): {letra} rotacao={j.giros} "
                              f"celulas={sorted(j.celulas)} limpa={j.limpas}")
        self.modelo = pilha_prevista
        self.modelo_tentativo = True
        self.usando_modelo = False
        self.alvo = rastr.avancos + 1
        self.limite = time.monotonic() + self.ESPERA_MAX
        self.espec = None
        self.log(f"OPENER {self.opener_nome} ({rotulo}) passo {self.jogadas}: {letra} "
                 f"{'T-SPIN' if j.spin else ''} {j.limpas}L ataque={j.valor:g} | {texto_plano(plano_teclas(j))}")
        return True

    def _jogar_opener(self, rastr, pil, mao, estado, agora):
        """Trata a fase do opener; True tambem quando precisa impedir um fallback neste frame."""
        if not self.opener_fase and not self.opener_rota and not self.opener_hold_troca:
            return False
        # aguardando confirmacao de hold-troca (fila nao avanca): nao reenviar, nao spammar
        if self.opener_hold_troca:
            if rastr.avancos != self.opener_hold_avancos:
                self._cancelar_opener("fila avancou durante hold-troca")
                return False
            if mao == self.opener_hold_troca:
                self.opener_hold_troca = None
                self.opener_hold_avancos = None
                self.opener_hold_mao_anterior = None
                self.ultima_jogada = None
                # troca confirmada: segue para executar a peca desejada abaixo
            elif (rastr.hold_estado == "pronto"
                  and self.opener_hold_mao_anterior is not None
                  and rastr.hold_peca == self.opener_hold_mao_anterior):
                mao = self.opener_hold_troca
                rastr.definir_mao(mao)
                rastr.hold_estado = "usado"
                rastr.hold_peca = self.opener_hold_mao_anterior
                self.log(f"OPENER {self.opener_nome}: hold-troca confirmada pela peca anterior no Hold; "
                         f"mao ressincronizada para {mao}.")
                self.opener_hold_troca = None
                self.opener_hold_avancos = None
                self.opener_hold_mao_anterior = None
                self.ultima_jogada = None
            elif agora > getattr(self, "limite", 0.0):
                self._cancelar_opener("hold-troca do opener sem confirmacao")
                return False
            elif mao is None:
                # leitura de mao instavel; aguardar
                return True
            else:
                # mao mudou para outra peca inesperada ou ainda nao trocou
                # (ex.: leitura antiga) -> aguardar ate timeout
                return True
        base = self.modelo if self.modelo is not None else pil.pilha
        if base is None and self.opener_rota:
            # sem base para validar rota de suporte
            if pil.pilha is None:
                return False
        # --- rota de suporte em andamento (fase 1) ---
        if self.opener_rota:
            letra, celulas = self.opener_rota[0]
            if self._opener_executar_celulas(rastr, pil, mao, estado, agora, letra, celulas,
                                             "suporte" if self.opener_fase in
                                             ("dt_suporte", "tsd_suporte") else "fase inicial"):
                # so consome o passo se realmente colocou a peca (nao se fez hold)
                if self.ultima_jogada != "HOLD DO OPENER":
                    self.opener_rota = self.opener_rota[1:]
                if self.ultima_jogada != "HOLD DO OPENER" and not self.opener_rota:
                    if self.opener_fase in ("dt_suporte", "tsd_suporte"):
                        self.opener_acao_pendente = self.opener_fase
                    else:
                        self.opener_acao_pendente = "fase_inicial"
                return True
            return False

        # --- fases TSD -> TST sem rota precomputada ---
        base = self._opener_base(pil)
        if base is None:
            return False
        base_t = tuple(decisor.para_linhas(base))

        if self.opener_fase in ("dt_tsd", "tsd"):
            tsd = self.opener_tsd_celulas or openers._tsd_agora(base_t)
            if tsd is None:
                self.log(f"OPENER {self.opener_nome}: estado nao permite TSD agora; cancelando fase completa.")
                self._cancelar_opener("sem TSD disponivel apos suporte")
                return False
            rotulo = "dt_tsd" if self.opener_fase == "dt_tsd" else "tsd"
            ok = self._opener_executar_celulas(rastr, pil, mao, estado, agora, "T", tsd, rotulo)
            if ok and self.ultima_jogada != "HOLD DO OPENER":
                self.opener_acao_pendente = self.opener_fase
            return ok

        # fase inicial simples (nao-DT) concluiu
        if self.opener_fase == "suporte" and not self.opener_rota:
            self.opener_fase = None
        return False

    def nova_rodada(self, rastr, motivo):
        """Zera o estado do bot para uma partida nova (modelo vazio, hold livre, abertura do zero)."""
        rastr.iniciar_rodada(rastr.fila)
        self.modelo = frozenset()
        self.modelo_tentativo = False
        self.alvo = None
        self.aviso_alvo_pendente = False
        self.b2b = self.combo = 0
        self.usando_modelo = False
        self.rodada_confirmada = True
        self.jogadas = 0
        self.ataque_total = 0.0
        self.pecas_abertura = 0
        self.linhas_lixo = 0
        self.bloqueado = False
        self.espec = None          # descarta a jogada especulada da rodada anterior
        self.espera_avanco = None
        self.falhas_decisao = 0
        self.drop_emergencia_avancos = None
        self.ultima_limpas = 0
        self.ultima_jogada = None
        self.voo_desde = None
        self.aguardando_go = True
        self.inicio_rodada_detectado = True
        self.sem_jogo = False
        self.exigir_nova_rodada = False
        self.falhas_fila = 0
        self.aviso_fila_parada = False
        self.opener_rota = None
        self.opener_nome = None
        self.opener_fase = None
        self.opener_tsd_celulas = None
        self.opener_hold_troca = None
        self.opener_hold_avancos = None
        self.opener_hold_mao_anterior = None
        self.opener_acao_pendente = None
        self.log(f"NOVA RODADA ({motivo}): tabuleiro vazio, Hold livre e fila inicial reconhecidos; "
                 "modelo antigo descartado; aguardando o GO (peca ativa nascer) para comecar.")
        print("\n>>> NOVA PARTIDA detectada: 3... 2... 1... aguardando o GO", flush=True)

    def observar_inicio_rodada(self, rastr, pil, agora):
        fila_fresca = rastr.fila is not None and len(set(rastr.fila)) == len(rastr.fila)
        # A peca ativa nasce DENTRO do tabuleiro lido (SPAWN_LINHAS), entao exigir "nenhuma
        # peca em voo" fazia o comeco da partida nunca ser confirmado. Peca na faixa de spawn
        # com a pilha vazia tambem e inicio de rodada.
        voo_no_spawn = (pil.voo is not None and tp.e_peca(pil.voo)
                        and all(r <= tp.SPAWN_LINHAS[1] for r, _ in pil.voo))
        inicio = (pil.pilha == frozenset() and (not pil.voo or voo_no_spawn)
                  and rastr.hold_estado == "vazio" and fila_fresca)
        # ANTES: depois da 1a partida o bot exigia modelo vazio, mas o modelo da partida anterior
        # (pilha cheia) nunca esvaziava: a nova partida jamais era detectada e o bot jogava com a
        # pilha velha ate o usuario derrubar pecas na mao. AGORA: tabuleiro vazio + hold vazio +
        # fila nova durante 1,5 s SEM o bot ter enviado teclas nesse tempo ja e partida nova
        # (no meio da partida o bot manda teclas a cada ~0,15 s e o hold fica ocupado).
        if not inicio or (self.modelo and agora - self.ultimo_envio < 1.0):
            self.tabuleiro_vazio_desde = None
            self.inicio_rodada_detectado = False
            return False
        if self.tabuleiro_vazio_desde is None:
            self.tabuleiro_vazio_desde = agora
            return False
        if agora - self.tabuleiro_vazio_desde < 1.5 or self.inicio_rodada_detectado:
            return False
        self.nova_rodada(rastr, "automatica")
        return True

    def sincronizar_mao_visual(self, rastr, pil, rgb):
        voo = pil.voo
        if (not voo and pil.pilha and len(pil.pilha) == 4 and tp.e_peca(pil.pilha)
                and all(r <= tp.SPAWN_LINHAS[1] for r, _ in pil.pilha)):
            voo = pil.pilha
        ativa = bool(voo)
        if ativa:
            if self.voo_desde is None:
                self.voo_desde = time.monotonic()
        else:
            self.voo_desde = None
        if not voo:
            return False
        if (rastr.mao is not None
                and (not tp.e_peca(voo)
                     or any(r > tp.SPAWN_LINHAS[1] for r, _ in voo))):
            return False
        letra = tp.letra_das_celulas(rgb, voo)
        if letra is None or rastr.mao == letra:
            return False
        mao_anterior = rastr.mao
        rastr.definir_mao(letra)
        if mao_anterior is None and self.aguardando_go:
            self.aguardando_go = False
            print(f"\n>>> GO! Jogando (primeira peca: {letra}).", flush=True)
        if mao_anterior is None:
            self.log(f"MAO INICIAL/RESSINCRONIZADA: peca ativa identificada no tabuleiro como {letra}; "
                     f"fila inicial={''.join(rastr.fila or ())}.")
        else:
            self.log(f"MAO RESSINCRONIZADA: rastreador={mao_anterior}, tela={letra}; "
                     f"fila={''.join(rastr.fila or ())}.")
        return True

    # --- eventos da pilha lida na tela ---
    def evento(self, ev):
        self.ultimo_evento_pilha = ev
        if ev == "sync_vazio" and self.modelo and not self.modelo_tentativo:
            self.log("pilha da tela ficou vazia (reinicio ou fim de jogo): modelo zerado")
            self.modelo = None

    @staticmethod
    def _repousa(pil):
        """A peca 'voo' esta apoiada (nao pode descer)? Entao e a que acabou de travar."""
        if not pil.voo:
            return False
        return any(r + 1 >= tp.BOARD_ROWS or (r + 1, c) in pil.pilha for r, c in pil.voo)

    def _pilha(self, pil):
        instavel = getattr(pil, "incoerentes", 0) > 0
        if pil.pilha is None or instavel:
            if self.modelo is not None:
                if not self.usando_modelo:
                    if pil.pilha is None:
                        self.log("LEITURA DA PILHA INDISPONIVEL: continuando com o modelo previsto pelo bot.")
                    else:
                        self.log("LEITURA DA PILHA INCOERENTE: mantendo o modelo previsto pelo bot.")
                    self.usando_modelo = True
                return self.modelo
            return None
        v1 = pil.pilha
        v2 = v1 | (pil.voo or frozenset())
        vista = v2 if self._repousa(pil) else v1
        if self.modelo is None or self.seco:
            self.modelo, self.desacordos = vista, 0
            self.modelo_tentativo = False
            self.usando_modelo = False
            return vista
        if self.modelo in (v1, v2):
            self.desacordos = 0
            self.modelo_tentativo = False
            self.usando_modelo = False
            return self.modelo
        if self.modelo_tentativo:
            evento_confiavel = self.ultimo_evento_pilha in ("trava", "trava_limpa", "sync")
            if self.ultimo_evento_pilha == "voo" and self._repousa(pil):
                evento_confiavel = True
            if self.ultimo_evento_pilha == "sync_quieto" and vista:
                evento_confiavel = True
            if not evento_confiavel:
                if not self.usando_modelo:
                    self.log("LEITURA DA PILHA PENDENTE: aguardando confirmacao visual sincronizada.")
                self.usando_modelo = True
                return self.modelo
        # modelo previsto (pos-envio) mas ainda nao confirmado: compara apenas com leitura sincronizada
        if self.modelo_tentativo:
            ultima = self.ultima_jogada or "jogada nao identificada"
            self.log(f"PILHA DIVERGE apos {ultima}; reconciliando pelo tabuleiro visual. "
                     "Mapa (# nos dois, o so na tela, x so no bot):\n"
                     + tp.mapa_diferencas(self.modelo, vista))
            self.modelo, self.desacordos = vista, 0
            self.modelo_tentativo = False
            self.usando_modelo = False
            self.espec = None
            return vista
        self.desacordos += 1
        dif = min(len(self.modelo ^ v1), len(self.modelo ^ v2))
        if self.desacordos >= 2 or dif > 12:
            ultima = self.ultima_jogada or "jogada nao identificada"
            self.log(f"PILHA DIVERGE apos {ultima}; reconciliando pelo tabuleiro visual. "
                     "Mapa (# nos dois, o so na tela, x so no bot):\n"
                     + tp.mapa_diferencas(self.modelo, vista))
            self.modelo, self.desacordos = vista, 0
            self.modelo_tentativo = False
            self.usando_modelo = False
            return vista
        self.usando_modelo = True
        return self.modelo

    # --- decisao especulada (esconde a latencia do decisor atras do envio de teclas) ---
    _SEM = object()
    MAX_FALHAS_DECISAO = 4

    @staticmethod
    def _chave_decisao(pilha, mao, hold, fila, permitir_hold, b2b, combo, fase_abertura, bag,
                       linhas_lixo=0):
        return (frozenset(pilha), mao, hold, tuple(fila[:4]), bool(permitir_hold), b2b, combo,
                bool(fase_abertura), bag, linhas_lixo)

    def _obter_especulada(self, chave):
        esp, self.espec = self.espec, None
        if esp is None or esp[0] != chave:
            if esp is not None:
                self.erros_espec += 1
            return self._SEM
        try:
            j = esp[1].result()
        except Exception:
            self.erros_espec += 1
            return self._SEM
        self.acertos_espec += 1
        return j

    def _especular(self, j, pilha, mao, hold, fila, estado, fase_proxima, deduzir_bag,
                   linhas_lixo=0):
        """Prediz o estado depois de j e ja manda o decisor calcular a jogada seguinte numa thread.

        A bag prevista entra na chave: sem isso a especulacao nunca acertaria durante a primeira
        bag (quando a setima peca e dedutivel), que e justamente o comeco da partida.
        """
        if not self.especular or len(fila) < 4:
            return
        if j.usar_hold:
            if hold is None and estado != "pronto":   # hold vazio: a fila[0] e jogada, a proxima mao e fila[1]
                mao2, hold2, fila2 = fila[1], mao, fila[2:]
            else:                                       # troca: o hold e jogado, a proxima mao e fila[0]
                mao2, hold2, fila2 = fila[0], mao, fila[1:]
        else:
            mao2, hold2, fila2 = fila[0], hold, fila[1:]
        if len(fila2) < 4:
            return
        pilha2 = frozenset(decisor._limpar(pilha | j.celulas)[0])
        bag2 = deduzir_bag(fila2)
        chave = self._chave_decisao(pilha2, mao2, hold2, fila2, True, j.b2b, j.combo,
                                    fase_proxima, bag2, linhas_lixo)
        futuro = self.executor.submit(decisor.decidir, pilha2, mao2, hold2, list(fila2), True,
                                      j.b2b, j.combo, bag2, fase_proxima, linhas_lixo)
        self.espec = (chave, futuro)

    def _fase_abertura(self, pilha, pecas_abertura, jogadas):
        return (self.rodada_confirmada and pecas_abertura < decisor.JANELA_ABERTURA) or (
            not self.rodada_confirmada and jogadas == 0 and not pilha)

    @staticmethod
    def _inicio_visual(pil):
        """Tabuleiro vazio, ou so com a peca de spawn mal lida: da para guardar a peca ativa
        sem adivinhar nada, sem esperar confirmacao de rodada (o comeco travado era isto)."""
        if pil.pilha is None:
            return False
        if not pil.pilha:
            return True
        return (len(pil.pilha) <= 4
                and all(r <= tp.SPAWN_LINHAS[1] for r, _ in pil.pilha))

    def _estado_atual_confiavel(self, rastr, pil):
        if pil.pilha is None or getattr(pil, "incoerentes", 0) > 0:
            return False
        if self.modelo is not None and pil.pilha != self.modelo:
            voo = pil.voo or frozenset()
            leitura_coerente = getattr(pil, "_ultima", None) in (
                pil.pilha, pil.pilha | voo)
            if (getattr(pil, "_iguais", 0) < tp.RastreadorPilha.QUIETO_FRAMES
                    or not leitura_coerente):
                return False
        if (rastr.mao not in decisor.SPAWN or rastr.fila is None or len(rastr.fila) < 4
                or any(peca not in decisor.SPAWN for peca in rastr.fila)):
            return False
        if rastr.hold_estado == "pronto":
            return rastr.hold_peca in decisor.SPAWN
        return rastr.hold_estado in ("vazio", "usado") and rastr.hold_peca is None

    # --- um passo por frame ---
    def passo(self, rastr, pil, agora):
        """Retorna True se enviou uma jogada ou consumiu o frame para tratar um estado."""
        if (self.bloqueado or rastr.fila is None or agora < self.pausa_ate
                or (self.alvo is None and pil.pilha is None and self.modelo is None)):
            return False
        if not self._envio_pronto():
            return False   # teclas da jogada anterior ainda saindo; nao mandar outra em cima
        if self.sem_jogo or self._liberar_retomada(pil, agora):
            return False   # sem jogo na tela, ou esperando a partida nova depois de um intervalo longo
        if self.alvo is not None:
            if rastr.avancos == self.alvo:
                self.alvo = None
                self.aviso_alvo_pendente = False
                self.falhas_fila = 0
                self.aviso_fila_parada = False
                self._confirmar_opener_avanco(rastr, pil, agora)
            elif rastr.avancos > self.alvo:
                if self._estado_atual_confiavel(rastr, pil):
                    self.log("FILA avancou alem do alvo; mao/fila/hold validos e leitura visual estavel, "
                             "ressincronizando o modelo.")
                    self._cancelar_opener("fila avancou alem do passo esperado")
                    self.alvo = None
                    self.aviso_alvo_pendente = False
                    self.modelo_tentativo = False
                    self.usando_modelo = False
                    self.espec = None
                    self.modelo = pil.pilha
                else:
                    if not self.aviso_alvo_pendente:
                        self.log("ERRO: a fila avancou mais do que o esperado; bot pausado para evitar jogar com "
                                 "pecas/estado antigos. Confira o estado do jogo e reinicie o bot para ressincronizar.")
                        self.aviso_alvo_pendente = True
                    return False
            elif agora > self.limite:
                if self.ultima_jogada == "HOLD INICIAL":
                    self.alvo = None
                    self.aviso_alvo_pendente = False
                    self.ultima_jogada = None
                    self.log("HOLD INICIAL sem confirmacao da fila; nao vou repetir a tecla. "
                             "Aguardando a leitura visual da peca ativa.")
                    return False
                if self.ultima_jogada == "HOLD DO OPENER":
                    self.alvo = None
                    self.aviso_alvo_pendente = False
                    self.ultima_jogada = None
                    self.modelo_tentativo = False
                    self._cancelar_opener("hold do opener sem confirmacao da fila; retomando jogo normal")
                    return False
                if not self.aviso_alvo_pendente:
                    altura_visual = (tp.altura_pilha(tp.pilha_para_texto(pil.pilha))
                                     if pil.pilha is not None else "indisponivel")
                    altura_modelo = (tp.altura_pilha(tp.pilha_para_texto(self.modelo))
                                     if self.modelo is not None else "indisponivel")
                    self.log(f"ERRO: fila sem avancar apos {self.ultima_jogada or 'jogada nao identificada'}; "
                             f"avancos={rastr.avancos}, alvo={self.alvo}, mao={rastr.mao!r}, "
                             f"pilha_visual={altura_visual}, pilha_modelo={altura_modelo}; "
                             "soltando o alvo e ressincronizando pela tela (nao vou parar de jogar).")
                    self.aviso_alvo_pendente = True
                # antes o bot parava de jogar aqui ate reiniciar; agora solta o alvo e re-le a tela
                if self._estado_atual_confiavel(rastr, pil):
                    self.alvo = None
                    self.aviso_alvo_pendente = False
                    self.modelo = None
                    self.modelo_tentativo = False
                    self.usando_modelo = False
                    self._cancelar_opener("fila sem avancar apos passo do opener")
                    # Sem avanco da fila o plano falhou: nao reenviar as mesmas teclas em
                    # sequencia (o log mostrava a mesma jogada repetida 3-4 vezes).
                    self.espera_avanco = (rastr.avancos, agora)
                    self.pausa_ate = agora + 0.05
                    self.falhas_fila += 1
                    if self.falhas_fila >= 3 and not self.aviso_fila_parada:
                        self.log(f"FILA PARADA: {self.falhas_fila} jogadas seguidas sem a fila avancar; o jogo nao esta "
                                 f"respondendo as teclas. Espacando as tentativas ({self.ESPERA_REPETIR_LONGA:.0f}s).")
                        self.aviso_fila_parada = True
                else:
                    # sem leitura confiavel, ainda assim cancela opener e solta para nao travar
                    if self.opener_rota or self.opener_fase or self.opener_hold_troca:
                        self._cancelar_opener("timeout sem leitura confiavel")
                    self.alvo = None
                    self.aviso_alvo_pendente = False
                    self.modelo_tentativo = False
                return False
            else:
                return False
        if self.espera_avanco is not None:
            avancos_antes, desde = self.espera_avanco
            if rastr.avancos != avancos_antes:
                self.espera_avanco = None
            elif agora - desde < self._espera_repetir():
                return False
            else:
                self.log("AGUARDANDO: a fila nao avancou; reavaliando uma vez antes de repetir a jogada.")
                self.espera_avanco = None
        estado, mao = rastr.hold_estado, rastr.mao
        if not self.seco and not self.sem_foco and not self.foco():
            if agora - self.sem_foco_aviso > 5:
                titulo = titulo_janela_em_foco() or "sem titulo"
                self.log(f"AVISO: janela em foco nao e o TETR.IO (titulo: {titulo!r}); nao vou apertar nada.")
                self.sem_foco_aviso = agora
            return False
        if (mao is not None and self.opener_rota is None and self.opener_fase is None
                and not self.jogadas and not self.pecas_abertura and not self.modelo):
            self._abrir_opener(rastr)
        if mao is not None and self._jogar_opener(rastr, pil, mao, estado, agora):
            return True
        if mao is None:
            fila_inicial = rastr.fila or ()
            # comeco garantido: tabuleiro vazio (ou so com a peca no spawn) OU nenhuma peca
            # jogada ainda com a tela quase vazia (leitura inicial costuma pegar o fantasma)
            altura_visual = (tp.altura_pilha(tp.pilha_para_texto(pil.pilha))
                             if pil.pilha is not None else 99)
            comeco_certo = (self._inicio_visual(pil)
                            or (self.jogadas == 0 and self.pecas_abertura == 0
                                and self.modelo is None and altura_visual <= 5))
            # Durante a contagem 3-2-1 nao ha peca ativa: apertar hold ali nao faz nada (o log mostrava
            # hold sem a fila avancar e depois um segundo hold). So vale se a peca ativa ja esta
            # visivel ha um instante (GO passou) e mesmo assim a cor nao foi lida.
            jogo_comecou = self.voo_desde is not None and agora - self.voo_desde >= 0.35
            pode_revelar = (comeco_certo and jogo_comecou
                            and estado == "vazio" and rastr.hold_peca is None
                            and fila_inicial and len(set(fila_inicial)) == len(fila_inicial)
                            and all(peca in decisor.SPAWN for peca in fila_inicial))
            if pode_revelar and not self.seco:
                self._enviar([("toque", "hold")])
                self.ultimo_envio = agora
                self.pecas_abertura += 1
                self.alvo = rastr.avancos + 1
                self.limite = agora + self.ESPERA_MAX
                self.ultima_jogada = "HOLD INICIAL"
                aviso = "" if self.rodada_confirmada else " (inicio visto pela tela, sem aguardar confirmacao)"
                self.log(f"HOLD INICIAL{aviso}: mao ainda desconhecida; guardando a peca ativa e usando "
                         f"{fila_inicial[0]} da fila para comecar sem adivinhar a peca.")
                return True
            if agora - self.mao_desconhecida_aviso > 3:
                pilha_txt = "sem leitura" if pil.pilha is None else f"{len(pil.pilha)} blocos"
                self.log(f"AGUARDANDO: peca ativa nao identificada (pilha={pilha_txt}, "
                         f"voo={'sim' if pil.voo else 'nao'}, hold={estado}, "
                         f"fila={''.join(str(p) for p in fila_inicial)}, jogadas={self.jogadas}); "
                         "nao vou colocar uma peca que nao consigo confirmar.")
                self.mao_desconhecida_aviso = agora
            return False

        pilha = self._pilha(pil)
        if pilha is None:
            # _pilha devolve None quando a leitura esta incoerente e nao ha modelo; sem
            # esta guarda _chave_decisao estourava com TypeError: frozenset(None).
            if agora - self.mao_desconhecida_aviso > 3:
                self.log("AGUARDANDO: leitura da pilha instavel e sem modelo; nao decido com estado indefinido.")
                self.mao_desconhecida_aviso = agora
            return False
        # Depois de um clear confirmado o modelo pos-animacao ja e conhecido: nao adianta
        # esperar a tela terminar de recolher as linhas (era o atraso sentido a cada clear).
        if self.usando_modelo and not (self.ultima_limpas and self.modelo is not None):
            return False
        fila = list(rastr.fila)
        inicio_decisao = time.perf_counter()
        hold = rastr.hold_peca if estado == "pronto" else None
        deduzir_bag = getattr(rastr, "proxima_peca_da_bag", lambda _fila: None)
        bag_seguinte = deduzir_bag(fila)
        fase_abertura = self._fase_abertura(pilha, self.pecas_abertura, self.jogadas)
        permitir = estado != "usado"
        chave = self._chave_decisao(pilha, mao, hold, fila, permitir, self.b2b, self.combo,
                                    fase_abertura, bag_seguinte, self.linhas_lixo)
        j = self._obter_especulada(chave)
        especulada = j is not self._SEM
        if not especulada:
            j = decisor.decidir(pilha, mao, hold, fila, permitir_hold=permitir,
                                b2b=self.b2b, combo=self.combo, bag_seguinte=bag_seguinte,
                                abertura=fase_abertura, linhas_lixo=self.linhas_lixo)
        decisao_ms = (time.perf_counter() - inicio_decisao) * 1000
        if j is None:
            altura_visual = (tp.altura_pilha(tp.pilha_para_texto(pil.pilha))
                             if pil.pilha is not None else 0)
            drop_emergencia = (
                not self.seco
                and bool(pil.pilha)
                and mao in decisor.SPAWN
                and self._estado_atual_confiavel(rastr, pil)
                and altura_visual >= decisor.ALTURA_PERIGO
                and rastr.avancos != self.drop_emergencia_avancos
            )
            if drop_emergencia:
                self._enviar([("toque", "drop")])
                self.drop_emergencia_avancos = rastr.avancos
                self.falhas_decisao += 1
                self.espec = None
                self.modelo = None
                self.modelo_tentativo = False
                self.usando_modelo = False
                self.ultima_limpas = 0
                self.ultimo_envio = agora
                self.ultima_jogada = "DROP DE EMERGENCIA (sem rota alcancavel)"
                self.alvo = rastr.avancos + 1
                self.limite = agora + self.ESPERA_MAX
                self.log(f"SEM ROTA na altura visual {altura_visual}: hard drop da peca "
                         f"{mao} enviado uma vez; aguardando confirmacao da fila.")
                return True
            self.falhas_decisao += 1
            self.espec = None
            # Sem jogada NAO e fim de jogo: modelo e leitura erram no topo. Descarta o modelo,
            # volta para a leitura da tela e tenta de novo — o bot nunca pausa por "morreu".
            if self.falhas_decisao % 10 == 0 and agora - self.aviso_topo > 2:
                altura_visual = (tp.altura_pilha(tp.pilha_para_texto(pil.pilha))
                                 if pil.pilha is not None else "indisponivel")
                self.log(f"SEM JOGADA (tentativa {self.falhas_decisao}): nenhuma colocacao alcancavel "
                         f"com a leitura atual (pilha visual={altura_visual}); continuo tentando "
                         "pela leitura da tela.")
                self.aviso_topo = agora
            self.modelo = None
            self.modelo_tentativo = False
            self.usando_modelo = False
            self.pausa_ate = agora + 0.01
            return False
        self.falhas_decisao = 0

        plano = plano_teclas(j)
        avancos = 1 + (1 if j.usar_hold and estado == "vazio" else 0)
        self.jogadas += 1
        self.marcas_jogadas.append(time.monotonic())
        self.ataque_total += j.valor
        self.log(f"JOGADA {self.jogadas}: mao={mao or '?'} hold={rastr.texto_hold()} fila={''.join(fila)} -> "
                 f"{j.letra}{' via hold' if j.usar_hold else ''} "
                 f"{('T-SPIN ' + j.spin + ' ') if getattr(j, 'spin', '') else ''}"
                 f"{j.limpas}L ataque={j.valor:g} giros={j.giros} coluna={j.coluna_caixa} "
                 f"bag+={bag_seguinte or '-'} | {texto_plano(plano)} | decisor={decisao_ms:.1f}ms{' (especulado)' if especulada else ''} | "
                 f"B2B {self.b2b}->{j.b2b}, combo {self.combo}->{j.combo}")
        if self.seco:
            if fase_abertura:
                self.pecas_abertura += avancos
            self.modelo = frozenset(decisor._limpar(pilha | j.celulas)[0])
            self.modelo_tentativo = False
            self.ultimo_envio = agora
            self.alvo, self.limite = rastr.avancos + avancos, agora + 60
            return True
        if fase_abertura:
            self.pecas_abertura += avancos
        pilha_prevista = frozenset(decisor._limpar(pilha | j.celulas)[0])
        self.b2b, self.combo = j.b2b, j.combo
        self._especular(j, pilha, mao, hold, fila, estado,
                        self._fase_abertura(pilha_prevista, self.pecas_abertura, self.jogadas),
                        deduzir_bag, self.linhas_lixo)
        self._enviar(plano)
        self.ultima_limpas = j.limpas
        self.log(f"TECLAS enviadas; aguardando a tela confirmar (fila alvo {rastr.avancos + avancos}).")
        self.ultima_jogada = (f"JOGADA {self.jogadas}: {j.letra} rotacao={j.giros} "
                              f"coluna={j.coluna_caixa} celulas={sorted(j.celulas)} limpa={j.limpas}")
        # Mantem a pilha prevista ate a tela produzir uma leitura sincronizada.
        self.modelo = pilha_prevista
        self.modelo_tentativo = True
        self.usando_modelo = False
        self.alvo = rastr.avancos + avancos
        self.limite = time.monotonic() + self.ESPERA_MAX
        return True


# ---------- janela do jogo e atalhos ----------
def titulo_janela_em_foco():
    """Titulo da janela ativa, ou vazio se nao puder ser consultado."""
    try:
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        n = u.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, buf, n + 1)
        return buf.value
    except Exception:
        return ""


def janela_do_jogo():
    """True se a janela em foco tem 'tetr.io' no titulo."""
    return "tetr.io" in titulo_janela_em_foco().lower()


def tecla_apertada(vk):
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def aguardar_foco_jogo(atraso):
    """Aguarda foco no TETR.IO; retorna False se o usuario encerrar com F9."""
    print(f"Abra o TETR.IO e deixe a janela do jogo em foco; inicio em {atraso}s.")
    for restante in range(atraso, 0, -1):
        if janela_do_jogo():
            break
        if tecla_apertada(0x78):
            return False
        print(f"  Aguardando foco do jogo... {restante}s | janela atual: {titulo_janela_em_foco() or 'sem titulo'}",
              flush=True)
        time.sleep(1)
    avisou_foco = False
    while not janela_do_jogo():
        if tecla_apertada(0x78):
            return False
        if not avisou_foco:
            print("Ainda sem foco no TETR.IO. Alterne para a janela do jogo; o bot iniciara assim que ela estiver ativa.",
                  flush=True)
            avisou_foco = True
        time.sleep(0.25)
    print("TETR.IO detectado em primeiro plano. Iniciando leitura e jogadas.", flush=True)
    return True


def diagnosticar_leitura_inicial(sct):
    """Mostra o que o leitor reconhece no perfil de tela selecionado."""
    frame = tp.capturar(sct)
    print(f"Layout configurado: {tp.LAYOUT_NAME}.")
    print(f"Captura: regiao x={tp.REG_LEFT}..{tp.REG_RIGHT}, y={tp.REG_TOP}..{tp.REG_BOTTOM}; "
          f"imagem={frame.shape[1]}x{frame.shape[0]} px")
    leitura = tp.ler_hold_e_fila(frame)
    print(f"Fila/hold reconhecidos: {leitura if leitura is not None else 'NAO; confira posicao, escala e calibracao'}")
    if leitura is None:
        from mss import tools as mss_tools

        board_rgb = tp.recortar(frame, tp.CAIXA_TABULEIRO)
        quadro = frame.copy()
        for nome in tp.NOMES:
            tp.desenhar_caixa(quadro, tp.CAIXAS[nome], (0, 255, 0))
        tp.desenhar_caixa(quadro, tp.CAIXA_TABULEIRO, (255, 80, 80))
        tp.salvar_png(mss_tools, frame, "debug_frame_bruto.png")
        tp.salvar_png(mss_tools, board_rgb, "debug_board_bruto.png")
        tp.salvar_png(mss_tools, quadro, "debug_frame.png")
        print("Leitura incompleta: capturas atuais salvas como debug_frame_bruto.png, "
              "debug_board_bruto.png e debug_frame.png.")
    for nome in tp.NOMES:
        caixa = tp.CAIXAS[nome]
        peca, _, distancia = tp.classify(tp.recortar(frame, caixa), tp.PASSO_FILA)
        if nome == "hold" and peca is None:
            cinza = tp.count_hold_gray_pixels(tp.recortar(frame, caixa), tp.PASSO_FILA)
            resultado = f"vazio/cinza ({cinza} amostras cinza)"
        else:
            resultado = f"{peca or 'nada'} (distancia={distancia:.1f})" if distancia is not None else "nada detectado"
        print(f"  {nome}: {resultado}")
    board = tp.classify_board(tp.recortar(frame, tp.CAIXA_TABULEIRO))
    ocupadas = tp.celulas_ocupadas(board)
    tabuleiro = tuple("".join(linha) for linha in board)
    pilha, n_spawn = tp.validar_spawn(tabuleiro)
    print(f"Tabuleiro: {len(ocupadas)} celulas ocupadas; "
          f"peca no spawn={'sim' if pilha is not None else 'nao'} ({n_spawn} blocos coloridos nas linhas de spawn); "
          f"componentes={sorted(len(grupo) for grupo in tp.componentes(ocupadas))}")
    for linha in tabuleiro:
        print("  " + linha)
    print("Se fila/hold der 'NAO' ou tabuleiro ficar vazio com o jogo visivel, rode --debug e --debug-board "
          "para conferir o layout selecionado e a escala.", flush=True)
    return leitura, len(ocupadas), pilha is not None


class CapturaContinua:
    """Captura a tela em uma thread separada.

    O BitBlt custa ~30 ms nesta maquina; feito em serie com a classificacao (mais ~15 ms),
    o laco ficava em ~15 fps. Com a captura em paralelo, cada frame custa o maior dos dois
    (~30 ms) e a pilha/fila confirmam a jogada um frame antes.
    """

    def __init__(self, sct, capturar=None):
        self._sct = sct
        self._capturar = capturar or tp.capturar
        self._trava = threading.Lock()
        self._quadro = None
        self._pronto = threading.Event()
        self._parar = threading.Event()
        self._ativo = threading.Event()
        self._ativo.set()
        self._erro = None
        self._thread = threading.Thread(target=self._laco, name="captura", daemon=True)

    def iniciar(self):
        self._thread.start()
        return self

    def _laco(self):
        while not self._parar.is_set():
            if not self._ativo.wait(0.05):
                continue
            try:
                quadro = self._capturar(self._sct)
            except Exception as exc:   # captura nao pode matar o laco de leitura
                with self._trava:
                    self._erro = exc
                self._pronto.set()
                return
            with self._trava:
                self._quadro = quadro
            self._pronto.set()

    def pausar(self):
        self._ativo.clear()

    def retomar(self):
        self._ativo.set()

    def ultimo(self, timeout=0.5):
        """Frame mais recente (descarta frames velhos se o processamento atrasou), ou None."""
        if not self._pronto.wait(timeout):
            return None
        with self._trava:
            erro, self._erro = self._erro, None
            quadro = self._quadro
        self._pronto.clear()
        if erro is not None:
            self._reiniciar()
            raise erro
        return quadro

    def _reiniciar(self):
        self._parar.set()
        self._thread.join(timeout=1.0)
        self._parar.clear()
        self._ativo.set()
        self._thread = threading.Thread(target=self._laco, name="captura", daemon=True)
        self._thread.start()

    def parar(self):
        self._parar.set()
        self._thread.join(timeout=1.0)


def jogar(sct, args):
    log = open(ARQUIVO_LOG, "w", encoding="utf-8")
    inicio = time.monotonic()

    def registrar(msg):
        log.write(f"[{time.monotonic() - inicio:7.2f}s] {msg}\n")
        log.flush()

    est = tp.Estabilizador(args.frames)
    rastr, pil = tp.Rastreador(), tp.RastreadorPilha()
    teclado = Teclado(args.toque / 1000, args.pausa / 1000)
    bot = Bot(teclado, registrar, seco=args.seco, sem_foco=args.sem_foco, foco=janela_do_jogo,
              enviar_em_thread=not args.envio_sincrono)
    bot.especular = not args.sem_especular
    pausado, f7_antes, f8_antes, ultimo = False, False, False, ""
    aviso_foco = 0.0
    quadros = leituras_validas = 0
    ultimo_status = time.monotonic()
    avisou_pilha = False
    modo = "SECO (nao aperta nada)" if args.seco else "JOGANDO"
    motivo_fim = "F9"
    print(f"Modo {modo}. F7 pausa/retoma, F8 = nova partida (reinicia o estado), F9 encerra.")
    print("Layout de captura configurado para " + getattr(args, "layout", "centro") + ".")
    print("Controles esperados: NUM1 esquerda, NUM3 direita, NUM5 girar, Z anti-horario, A 180, C hold, "
          "SPACE drop, NUM2 soft drop.")
    if not args.seco and not args.sem_foco:
        if not aguardar_foco_jogo(args.atraso):
            print("Inicio cancelado pelo usuario.")
            log.close()
            return
    elif args.atraso > 0:
        print(f"Iniciando em {args.atraso}s...", flush=True)
        time.sleep(args.atraso)
    tp.configurar_layout(getattr(args, "layout", "centro"), sct.monitors[1])
    titulo = titulo_janela_em_foco()
    registrar(f"INICIO: foco={titulo!r}")
    registrar(f"CONTROLES ESPERADOS: DAS=9F (150ms), ARR=0, SDF=infinito; pulso de soft drop={SD_S * 1000:.0f}ms; "
              "rotas executadas apos o spawn (sem depender de IRS/IHS).")
    leitura_inicial, ocupadas_iniciais, spawn_inicial = diagnosticar_leitura_inicial(sct)
    registrar(f"LAYOUT: {tp.LAYOUT_NAME}; captura x={tp.REG_LEFT}..{tp.REG_RIGHT}, "
              f"y={tp.REG_TOP}..{tp.REG_BOTTOM}")
    registrar(f"DIAGNOSTICO: leitura={leitura_inicial!r}; celulas_tabuleiro={ocupadas_iniciais}; spawn={spawn_inicial}")
    captura = CapturaContinua(sct).iniciar()
    pilha_aguardando_desde = time.monotonic()
    ultima_leitura_valida = time.monotonic()
    try:
        while True:
            if tecla_apertada(0x78):  # F9
                break
            try:
                f7 = tecla_apertada(0x76)
                if f7 and not f7_antes:
                    pausado = not pausado
                    registrar("PAUSADO" if pausado else "RETOMADO")
                f7_antes = f7
                f8 = tecla_apertada(0x77)  # F8: avisa manualmente que uma partida nova vai comecar
                if f8 and not f8_antes and rastr.fila is not None:
                    bot.nova_rodada(rastr, "F8 manual")
                f8_antes = f8
                if not (args.seco or args.sem_foco or janela_do_jogo()):
                    # Com outra janela na frente o mss leria pixels alheios e o rastreador
                    # de pilha adotaria lixo (era a origem de sem_sync e das divergencias).
                    bot.alvo = None
                    bot.espera_avanco = None
                    if time.monotonic() - aviso_foco > 5:
                        titulo = titulo_janela_em_foco() or "sem titulo"
                        registrar(f"AVISO: janela em foco nao e o TETR.IO (titulo: {titulo!r}); "
                                  "captura pausada para nao ler pixels de outra janela.")
                        aviso_foco = time.monotonic()
                    ultima_leitura_valida = time.monotonic()   # tempo fora de foco nao conta como "sem jogo"
                    captura.pausar()
                    time.sleep(0.05)
                    continue
                captura.retomar()
                frame = captura.ultimo()
                if frame is None:
                    continue
                leitura = tp.ler_hold_e_fila(frame)
                quadros += 1
                agora_leitura = time.monotonic()
                if leitura is None and agora_leitura - ultima_leitura_valida >= SEM_LEITURA_S:
                    bot.jogo_sumiu(agora_leitura)
                if leitura is not None:
                    ultima_leitura_valida = agora_leitura
                    if bot.jogo_voltou(agora_leitura):
                        est.quebrar()
                    leituras_validas += 1
                    if est.atualizar(leitura):
                        rastr.atualizar(est.estavel[1], est.estavel[0])
                board_rgb = tp.recortar(frame, tp.CAIXA_TABULEIRO)
                tabuleiro_bruto = tp.classify_board(board_rgb)
                bot.linhas_lixo = sum(1 for linha in tabuleiro_bruto if linha.count("G") >= 5)
                ocupadas = tp.celulas_ocupadas(tabuleiro_bruto)
                evento_pilha = pil.atualizar(ocupadas)
                bot.evento(evento_pilha)
                bot.observar_inicio_rodada(rastr, pil, time.monotonic())
                bot.sincronizar_mao_visual(rastr, pil, board_rgb)
                if pil.pilha is None and bot.modelo is None and not avisou_pilha:
                    if time.monotonic() - pilha_aguardando_desde >= 2.0:
                        registrar("AGUARDANDO: sem pilha visual confirmada; o decisor permanece bloqueado ate a leitura estabilizar.")
                        print("Aguardando a primeira sincronizacao visual da pilha; ainda nao ha modelo seguro para decidir.",
                              flush=True)
                        avisou_pilha = True
                elif pil.pilha is not None or bot.modelo is not None:
                    if avisou_pilha:
                        registrar("Pilha/modelo disponivel; decisor liberado.")
                        print("Pilha/modelo disponivel; decisor liberado.", flush=True)
                    avisou_pilha = False
                    pilha_aguardando_desde = time.monotonic()
                if not pausado and bot.passo(rastr, pil, time.monotonic()):
                    est.quebrar()
                agora_status = time.monotonic()
                if agora_status - ultimo_status >= 3:
                    altura_visual = (tp.altura_pilha(tp.pilha_para_texto(pil.pilha))
                                     if pil.pilha is not None else "indisponivel")
                    altura_modelo = (tp.altura_pilha(tp.pilha_para_texto(bot.modelo))
                                     if bot.modelo is not None else "indisponivel")
                    registrar(f"STATUS: quadros={quadros}; leituras_fila_validas={leituras_validas}; "
                              f"fila={rastr.fila!r}; mao={rastr.mao!r}; "
                              f"altura_visual={altura_visual}; altura_modelo={altura_modelo}; "
                              f"pilha={'sincronizada' if pil.pilha is not None else 'aguardando'}; "
                              f"modelo={'disponivel' if bot.modelo is not None else 'indisponivel'}; usando_modelo={bot.usando_modelo}; "
                              f"pps={bot.pps(15.0):.2f}; "
                              f"atk_peca={bot.ataque_total / max(bot.jogadas, 1):.2f}; "
                              f"sem_jogo={bot.sem_jogo}; exigir_nova_rodada={bot.exigir_nova_rodada}; falhas_fila={bot.falhas_fila}; "
                              f"evento_pilha={evento_pilha!r}; contagem_pilha={pil.contagem!r}; foco={titulo_janela_em_foco()!r}")
                    ultimo_status = agora_status
                texto = (f"{'PAUSADO' if pausado else modo} | jogadas: {bot.jogadas} | "
                         f"mao={rastr.mao or '?'} hold={rastr.texto_hold()} "
                         f"fila={''.join(rastr.fila) if rastr.fila is not None else '?'} | "
                         f"pilha: {tp.altura_pilha(tp.pilha_para_texto(pil.pilha)) if pil.pilha is not None else '?'} | "
                         f"B2B={bot.b2b} combo={bot.combo}")
                if texto != ultimo:
                    print("\r" + texto + " " * max(0, len(ultimo) - len(texto)), end="", flush=True)
                    ultimo = texto
            except Exception:
                # um erro numa leitura/rodada nao derruba o bot: grava o traceback e segue na proxima captura
                registrar("ERRO NO LOOP (o bot continua):\n" + traceback.format_exc())
                bot.espec, bot.alvo, bot.modelo, bot.modelo_tentativo, bot.usando_modelo = None, None, None, False, False
                time.sleep(0.01)
    except KeyboardInterrupt:
        motivo_fim = "Ctrl+C"
    captura.parar()
    try:
        bot._aguardar_envio(timeout=1.0)   # nao deixar teclas da ultima jogada pendentes
    except Exception:
        pass
    duracao = max(1e-6, time.monotonic() - inicio)
    registrar(f"FIM: motivo={motivo_fim}; jogadas={bot.jogadas}; pps_medio={bot.jogadas / duracao:.2f}; "
              f"pps_15s={bot.pps(15.0):.2f}; ataque={bot.ataque_total:g}; "
              f"atk_peca={bot.ataque_total / max(bot.jogadas, 1):.2f}; "
              f"atk_segundo={bot.ataque_total / duracao:.2f}; "
              f"acertos_espec={bot.acertos_espec}; erros_espec={bot.erros_espec}.")
    print(f"\nFim. {bot.jogadas} jogadas em {duracao:.0f}s ({bot.jogadas / duracao:.2f} PPS; "
          f"{bot.pps(15.0):.2f} PPS nos ultimos 15s). Motivo: {motivo_fim}. Log em {ARQUIVO_LOG}.")
    log.close()


def main():
    global SD_S, SD_S_POR_LINHA
    ap = argparse.ArgumentParser()
    ap.add_argument("--seco", action="store_true", help="so mostra o que jogaria, sem apertar teclas")
    ap.add_argument("--sem-foco", action="store_true", help="nao exigir 'tetr.io' no titulo da janela em foco")
    ap.add_argument("--layout", choices=("centro", "esquerda"), default="centro",
                    help="captura o tabuleiro central (padrao) ou o da esquerda no versus")
    ap.add_argument("--atraso", type=int, default=3, help="segundos antes de comecar")
    ap.add_argument("--frames", type=int, default=tp.FRAMES_ESTAVEIS)
    ap.add_argument("--toque", type=float, default=TOQUE_S * 1000, help="ms com a tecla apertada em cada toque")
    ap.add_argument("--pausa", type=float, default=PAUSA_S * 1000, help="ms de pausa entre toques")
    ap.add_argument("--sem-spin", action="store_true", help="desliga os T-spins (joga so com colocacoes simples)")
    ap.add_argument("--modo", choices=("blitz", "ataque"), default="ataque",
                    help="objetivo do decisor: atacar em versus (padrao) ou pontuar no Blitz")
    grupo_dt = ap.add_mutually_exclusive_group()
    grupo_dt.add_argument("--dt", dest="openers", action="store_true",
                          help="ativa o DT Cannon (padrao)")
    grupo_dt.add_argument("--sem-dt", dest="openers", action="store_false",
                          help="desliga o setup de DT Cannon")
    ap.set_defaults(openers=True)
    ap.add_argument("--sem-especular", action="store_true",
                    help="desliga o calculo antecipado da proxima jogada durante o envio das teclas")
    ap.add_argument("--envio-sincrono", action="store_true",
                    help="envia as teclas travando o laco (comportamento antigo; mais lento)")
    ap.add_argument("--sd-ms", type=float, default=SD_S * 1000,
                    help="duracao minima do soft drop em ms")
    ap.add_argument("--sd-ms-por-linha", type=float, default=SD_S_POR_LINHA * 1000,
                    help="tempo adicional de soft drop por linha descida")
    args = ap.parse_args()
    SD_S = args.sd_ms / 1000
    SD_S_POR_LINHA = args.sd_ms_por_linha / 1000
    decisor.MODO = args.modo
    decisor.USAR_SPINS = not args.sem_spin
    decisor.OPENERS = args.openers
    decisor.LINHA_SPAWN = tp.SPAWN_LINHAS[0]
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        ctypes.windll.winmm.timeBeginPeriod(1)  # sleep() preciso (1 ms) no Windows
    except Exception:
        pass
    tp.desativar_selecao_console()
    import mss
    abrir_mss = getattr(mss, "MSS", None) or mss.mss
    with abrir_mss() as sct:
        jogar(sct, args)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        try:
            input("\nPressione Enter para fechar...")
        except Exception:
            pass
