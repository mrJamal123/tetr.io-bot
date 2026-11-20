"""Testa a decisao especulada do Bot (sem tela e sem teclado)."""
import random
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import decisor
import jogar


class Rastr:
    def __init__(self, fila, mao):
        self.fila, self.mao = list(fila), mao
        self.hold_estado, self.hold_peca, self.avancos = "vazio", None, 0

    def texto_hold(self):
        return self.hold_peca or "-"


class Pil:
    voo = None

    def __init__(self):
        self.pilha = frozenset()


class Tec:
    def __init__(self):
        self.planos = []

    def executar(self, plano):
        self.planos.append(plano)


class TesteEspeculacao(unittest.TestCase):
    def jogar_partida(self, especular, pecas=25, seed=1):
        rnd = random.Random(seed)

        def saco():
            b = list("IOTSZJL")
            rnd.shuffle(b)
            return b

        reserva = saco() + saco() + saco()
        r = Rastr(reserva[1:6], reserva[0])
        reserva = reserva[6:]
        pil, tec = Pil(), Tec()
        bot = jogar.Bot(tec, lambda m: None, sem_foco=True)
        bot.especular = especular
        bot.rodada_confirmada = True
        t, ok = 0.0, 0
        with patch.object(jogar.decisor, "OPENERS", False):
            for _ in range(pecas):
                t += 1.0
                if len(reserva) < 4:
                    reserva += saco()
                if not bot.passo(r, pil, t):
                    break
                ok += 1
                usou_hold = tec.planos[-1][0] == ("toque", "hold")
                consumidas = 1
                if usou_hold and r.hold_peca is None:      # hold vazio: guarda a mao e joga a fila[0]
                    r.hold_peca, consumidas = r.mao, 2
                    r.mao = r.fila[1]
                elif usou_hold:                            # troca: joga o hold, guarda a mao
                    r.hold_peca = r.mao
                    r.mao = r.fila[0]
                else:
                    r.mao = r.fila[0]
                r.fila = r.fila[consumidas:] + [reserva.pop(0) for _ in range(consumidas)]
                r.avancos += consumidas
                r.hold_estado = "pronto" if r.hold_peca else "vazio"
                pil.pilha = bot.modelo
        return bot, ok

    def test_especulacao_acerta_e_gera_as_mesmas_jogadas_validas(self):
        bot, ok = self.jogar_partida(True)
        self.assertGreater(ok, 5)
        self.assertGreater(bot.acertos_espec, 0)

    def test_sem_especulacao_continua_funcionando(self):
        bot, ok = self.jogar_partida(False)
        self.assertGreater(ok, 5)
        self.assertEqual(bot.acertos_espec, 0)

    def test_bag_prevista_nao_descarta_a_decisao_especulada(self):
        """Na primeira bag a setima peca e dedutivel: a jogada seguinte tem de vir pronta."""
        plans = []
        bot = jogar.Bot(SimpleNamespace(executar=plans.append), lambda _m: None, sem_foco=True)
        bot.rodada_confirmada = True
        tracker = SimpleNamespace(
            fila=list("ILJSZ"), mao="T", hold_estado="vazio", hold_peca=None, avancos=0,
            texto_hold=lambda: "-", proxima_peca_da_bag=lambda fila: "O")
        board = SimpleNamespace(pilha=frozenset(), voo=None, incoerentes=0)
        move = decisor.Jogada(False, "T", 0, 3, frozenset({(19, 3), (19, 4), (19, 5), (18, 4)}),
                             [("toque", "drop")], "", 0, 0, 0, 0.0)
        with patch.object(jogar.decisor, "OPENERS", False), \
                patch.object(jogar.decisor, "decidir", return_value=move):
            self.assertTrue(bot.passo(tracker, board, 1.0))
            # a fila avancou: "I" virou a peca ativa e a bag previu a setima
            tracker.fila = list("LJSZO")
            tracker.mao, tracker.avancos = "I", 1
            board.pilha = bot.modelo
            self.assertTrue(bot.passo(tracker, board, 1.2))
        self.assertEqual((1, 0), (bot.acertos_espec, bot.erros_espec))


if __name__ == "__main__":
    unittest.main()
