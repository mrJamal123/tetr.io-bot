import random
import sys
import types
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import decisor as d
import jogar as j
import probability
import tetrio_projeto as tp


def _bonus_setup(rows, visiveis, b2b=0, combo=0, cache=None):
    """Valor total de setup de T-spin: progresso do slot + clear full alcancavel."""
    cache = {} if cache is None else cache
    return (d._bonus_tspin_setup_estagio(rows, visiveis)
            + d._bonus_tspin_clear(rows, visiveis, b2b, combo, cache))


class InitialReadDiagnosticTests(unittest.TestCase):
    def test_failed_initial_queue_read_saves_current_capture(self):
        frame = np.zeros((480, 514, 3), dtype=np.uint8)
        fake_mss = types.ModuleType("mss")
        fake_mss.tools = object()
        with patch.dict(sys.modules, {"mss": fake_mss}), \
                patch.object(tp, "capturar", return_value=frame), \
                patch.object(tp, "ler_hold_e_fila", return_value=None), \
                patch.object(tp, "recortar", return_value=np.zeros((1, 1, 3), dtype=np.uint8)), \
                patch.object(tp, "classify", return_value=(None, None, None)), \
                patch.object(tp, "count_hold_gray_pixels", return_value=0), \
                patch.object(tp, "classify_board", return_value=tuple("." * 10 for _ in range(20))), \
                patch.object(tp, "celulas_ocupadas", return_value=frozenset()), \
                patch.object(tp, "validar_spawn", return_value=(None, 0)), \
                patch.object(tp, "componentes", return_value=[]), \
                patch.object(tp, "desenhar_caixa"), \
                patch.object(tp, "salvar_png") as salvar:
            j.diagnosticar_leitura_inicial(None)

        self.assertEqual(
            ["debug_frame_bruto.png", "debug_board_bruto.png", "debug_frame.png"],
            [chamada.args[2] for chamada in salvar.call_args_list],
        )


class SpawnValidationTests(unittest.TestCase):
    def test_spawn_range_cleans_piece_in_configured_rows(self):
        tab = ["." * tp.BOARD_COLS for _ in range(tp.BOARD_ROWS)]
        tab[2] = "....#....."
        tab[3] = "...###...."

        limpo, quantidade = tp.validar_spawn(tuple(tab))

        self.assertEqual(4, quantidade)
        self.assertEqual("." * tp.BOARD_COLS, limpo[2])
        self.assertEqual("." * tp.BOARD_COLS, limpo[3])

    def test_spawn_range_outside_board_raises_clear_error(self):
        tab = tuple("." * tp.BOARD_COLS for _ in range(tp.BOARD_ROWS))

        with patch.object(tp, "SPAWN_LINHAS", (2, 311)):
            with self.assertRaisesRegex(ValueError, "SPAWN_LINHAS invalido"):
                tp.validar_spawn(tab)


class ReachabilityTests(unittest.TestCase):
    def test_tspin_geometry_prefilter_matches_full_scan(self):
        rng = random.Random(17)

        def full_scan(rows):
            for k, cells in enumerate(d.ROT["T"]):
                masks = [0, 0, 0]
                for r, c in cells:
                    masks[r] |= 1 << c
                for bc in range(d.COLUNAS - 2):
                    for br in range(d.LINHAS - 1):
                        if (not d._livre(rows, cells, br, bc)
                                or d._livre(rows, cells, br + 1, bc)
                                or not d._tipo_spin(rows, k, br, bc, 1)):
                            continue
                        if any(mask and (rows[br + r] | (mask << bc)) == d.CHEIA
                               for r, mask in enumerate(masks)):
                            return True
            return False

        for _ in range(12):
            rows = [rng.getrandbits(d.COLUNAS) if r >= 12 else 0
                    for r in range(d.LINHAS)]
            rows = [row & (d.CHEIA - 1) if row == d.CHEIA else row for row in rows]
            self.assertEqual(full_scan(rows), d._tem_tspin_clear_geometrico(rows))

    def test_bitboard_collision_check_matches_row_masks(self):
        boards = [
            [0] * d.LINHAS,
            [0] * 17 + [d.CHEIA ^ (1 << 6), d.CHEIA ^ (1 << 2), d.CHEIA ^ (1 << 4)],
            [sum(1 << c for c in range(d.COLUNAS) if (r + c) % 3)
             for r in range(d.LINHAS)],
        ]
        for rows in boards:
            bits = d._bits_tabuleiro(rows)
            for shapes in d.ROT.values():
                for cells in shapes:
                    for br in range(-3, d.LINHAS + 1):
                        for bc in range(-2, d.COLUNAS + 1):
                            with self.subTest(br=br, bc=bc, cells=cells):
                                self.assertEqual(
                                    d._livre(rows, cells, br, bc),
                                    d._livre_bits(bits, cells, br, bc),
                                )

    def _assert_replay(self, rows, letter):
        placements = d.colocacoes_alcancaveis(rows, letter)
        self.assertTrue(placements, "BFS deve encontrar ao menos uma posicao legal")
        for k, col, y, masks, cells, spin, actions in placements:
            teclas = d._acoes_para_teclas(rows, actions, letter)
            teclas.append(("toque", "drop"))
            simulated, simulated_spin = d.executar_teclas(
                [(r, c) for r, mask in enumerate(rows) for c in range(d.COLUNAS) if mask & (1 << c)],
                letter,
                teclas,
            )
            self.assertEqual(frozenset(cells), simulated, (letter, k, col, actions, teclas))
            self.assertEqual(spin, simulated_spin, (letter, k, col, actions, teclas))

    def test_I_vertical_wall_placement_rotates_before_horizontal_move(self):
        rows = [0] * d.LINHAS
        placement = next(p for p in d.colocacoes_alcancaveis(rows, "I")
                         if p[0] == 1 and min(c for _, c in p[4]) == 0)
        actions = placement[6]
        self.assertIn(actions[0], ("cw", "ccw"))
        self.assertLess(actions.index(actions[0]), next(i for i, action in enumerate(actions)
                                                        if action in ("L", "R")))
        keys = d._acoes_para_teclas(rows, actions, "I") + [("toque", "drop")]
        actual, _ = d.executar_teclas([], "I", keys)
        self.assertEqual(frozenset(placement[4]), actual)

    def test_every_piece_replays_on_empty_and_obstructed_boards(self):
        empty = [0] * d.LINHAS
        skyline = [0] * d.LINHAS
        skyline[17] = d.CHEIA ^ (1 << 6)
        skyline[18] = d.CHEIA ^ (1 << 2)
        skyline[19] = d.CHEIA ^ (1 << 4)
        heights = [1, 3, 5, 2, 4, 1, 5, 3, 2, 4]
        stack = [0] * d.LINHAS
        for col, height in enumerate(heights):
            for depth in range(height):
                stack[d.LINHAS - 1 - depth] |= 1 << col
        high_stack = [0] * d.LINHAS
        for row in range(8, d.LINHAS):
            high_stack[row] = sum(1 << col for col in (0, 1, 2, 7, 8, 9))

        for rows in (empty, skyline, stack, high_stack):
            for letter in "IOTSZJL":
                with self.subTest(board=rows, piece=letter):
                    self._assert_replay(rows, letter)

    def test_lookahead_uses_fast_placements_for_non_T_pieces_only(self):
        rows = [0] * d.LINHAS
        with patch.object(d, "_colocacoes_rapidas",
                          wraps=d._colocacoes_rapidas) as fast, \
                patch.object(d, "_get_colocacoes", wraps=d._get_colocacoes) as exact:
            d._expandir(rows, 0, 0, "I", {}, False)
            d._expandir(rows, 0, 0, "T", {}, False)
            d._expandir(rows, 0, 0, "T", {}, False, aproximado_t=True)

        self.assertEqual(2, fast.call_count)
        self.assertEqual(1, exact.call_count)

    def test_root_uses_geometric_placements_when_no_spin_clear_is_possible(self):
        rows = [0] * d.LINHAS
        with patch.object(d, "_colocacoes_rapidas",
                          wraps=d._colocacoes_rapidas) as fast, \
                patch.object(d, "_get_colocacoes", wraps=d._get_colocacoes) as exact:
            placements = d._expandir(rows, 0, 0, "J", {}, True)

        self.assertTrue(placements)
        self.assertEqual(1, fast.call_count)
        self.assertEqual(0, exact.call_count)

    def test_root_keeps_exact_reachability_when_non_T_clear_is_possible(self):
        rows = [0] * d.LINHAS
        rows[-1] = d.CHEIA ^ (15 << 3)
        with patch.object(d, "_get_colocacoes", wraps=d._get_colocacoes) as exact:
            placements = d._expandir(rows, 0, 0, "I", {}, True)

        self.assertTrue(any(placement[9] for placement in placements))
        self.assertEqual(1, exact.call_count)

    def test_soft_drop_route_retains_distance_before_later_actions(self):
        rows = [0] * d.LINHAS
        actions = ["D", "L"]
        teclas = d._acoes_para_teclas(rows, actions, "T")
        self.assertIn(("sd", d.LINHAS - d.LINHA_SPAWN - 2), teclas)
        teclas.append(("toque", "drop"))
        simulated, _ = d.executar_teclas([], "T", teclas)
        self.assertEqual(frozenset(((18, 3), (19, 2), (19, 3), (19, 4))), simulated)

    def test_live_soft_drop_duration_scales_with_descent_distance(self):
        move = SimpleNamespace(teclas=[("sd", 16)], usar_hold=False)
        plano = j.plano_teclas(move)
        self.assertEqual(0.021, plano[0][2])
        self.assertEqual(0.021, j.SD_S)

        legacy_move = SimpleNamespace(teclas=[("sd",)], usar_hold=False)
        self.assertEqual(j.SD_S, j.plano_teclas(legacy_move)[0][2])

    def test_game_settings_use_das_nine_frames_and_arr_zero_wall_shift(self):
        self.assertAlmostEqual(9 / 60, j.DAS_S)
        self.assertEqual(j.DAS_S + 1 / 60, j.SEGURAR_S)
        self.assertEqual(6, d.DAS_MIN)

    def test_legacy_placement_api_only_returns_reachable_moves(self):
        pile = frozenset((r, c) for r in range(16, 20) for c in range(d.COLUNAS)
                         if c != (r % d.COLUNAS))
        for letter in "IOTSZJL":
            expected = {(k, col, frozenset(cells))
                        for k, col, _, _, cells, _, _ in d.colocacoes_alcancaveis(d.para_linhas(pile), letter)}
            self.assertEqual(expected, set(d.colocacoes(pile, letter)))

    def test_I_uses_its_own_kick_offsets(self):
        rows = [0] * d.LINHAS
        rows[2] = 1 << 5
        rotated = d._tentar_rotacao(rows, "I", 0, d.LINHA_SPAWN, d.COL_SPAWN["I"], "cw")
        self.assertEqual((1, d.LINHA_SPAWN, d.COL_SPAWN["I"] - 2, 1), rotated)
        self.assertNotEqual(d.KICKS[(0, 1)], d.KICKS_I[(0, 1)])

    def test_180_rotation_does_not_apply_unverified_kicks(self):
        rows = [0] * d.LINHAS
        rows[d.LINHA_SPAWN + 1] = 1 << (d.COL_SPAWN["T"] + 1)
        self.assertIsNone(d._tentar_rotacao(rows, "T", 0, d.LINHA_SPAWN, d.COL_SPAWN["T"], "180"))

    def test_full_spin_route_wins_when_landing_is_also_reachable_as_mini(self):
        rows = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 352, 124, 688, 425, 1008,
                542, 568, 444, 437, 937]
        classify = lambda _rows, _k, _r, _c, kick: {1: "mini", 2: "full"}.get(kick, "")
        with patch.object(d, "_tipo_spin", side_effect=classify):
            same_landing = [p for p in d.colocacoes_alcancaveis(rows, "T")
                            if (p[0], p[2], p[1]) == (1, 15, -1)]
        self.assertEqual(1, len(same_landing))
        self.assertEqual("full", same_landing[0][5])

    def test_T_spin_corner_and_fifth_kick_classification(self):
        rows = [0] * d.LINHAS
        rows[10] |= (1 << 3) | (1 << 5)
        rows[12] |= 1 << 3
        self.assertEqual("full", d._tipo_spin(rows, 0, 10, 3, 1))

        rows = [0] * d.LINHAS
        rows[10] |= 1 << 3
        rows[12] |= (1 << 3) | (1 << 5)
        self.assertEqual("mini", d._tipo_spin(rows, 0, 10, 3, 1))
        self.assertEqual("full", d._tipo_spin(rows, 0, 10, 3, 2))

    def test_all_mini_spin_requires_a_rotation_and_an_immobile_landing(self):
        rows = [0] * d.LINHAS
        with patch.object(d, "_livre", side_effect=[False, False, False, False]):
            self.assertEqual("mini", d._spin_all_mini(rows, "J", 1, 18, 4, 1))
        with patch.object(d, "_livre", side_effect=[False, True, False, False]):
            self.assertEqual("", d._spin_all_mini(rows, "J", 1, 18, 4, 1))
        # pode subir (sem overhang): girar de ultimo segundo NAO e spin no TETR.IO
        with patch.object(d, "_livre", side_effect=[False, False, False, True]):
            self.assertEqual("", d._spin_all_mini(rows, "J", 1, 18, 4, 1))
        self.assertEqual("", d._spin_all_mini(rows, "J", 1, 18, 4, 0))
        self.assertEqual("", d._spin_all_mini(rows, "T", 1, 18, 4, 1))
        self.assertEqual("", d._spin_all_mini(rows, "O", 1, 18, 4, 1))


class SevenBagTests(unittest.TestCase):
    def test_probability_module_deduces_the_last_piece_of_a_seven_bag(self):
        self.assertEqual("S", probability.infer_missing_piece("IJOLTZ"))
        with self.assertRaises(ValueError):
            probability.infer_missing_piece("IIOLTZ")

    def test_tracker_only_predicts_when_six_current_bag_pieces_are_known(self):
        tracker = j.tp.Rastreador()
        tracker.iniciar_rodada(tuple("JOLTZ"))
        tracker.definir_mao("I")
        self.assertEqual("S", tracker.proxima_peca_da_bag())

        tracker.atualizar(tuple("OLTZS"), "-")
        self.assertIsNone(tracker.proxima_peca_da_bag())
        self.assertEqual("S", tracker.proxima_peca_da_bag(tuple("OLTZ")))

    def test_bag_inference_is_disabled_when_queue_history_breaks(self):
        tracker = j.tp.Rastreador()
        tracker.iniciar_rodada(tuple("JOLTZ"))
        tracker.definir_mao("I")
        tracker.atualizar(tuple("SSSSS"), "-")
        self.assertFalse(tracker.bag_confiavel)
        self.assertIsNone(tracker.proxima_peca_da_bag())


class LiveBotTests(unittest.TestCase):
    def test_keyboard_uses_reliable_hold_and_drop_timing(self):
        keyboard = j.Teclado()
        sleeps = []

        with patch.object(keyboard, "_evento"), \
                patch.object(j.time, "sleep", side_effect=sleeps.append):
            keyboard.executar([("toque", "hold"), ("toque", "drop")])

        self.assertEqual(
            [keyboard.toque, keyboard.pausa, j.ESPERA_APOS_HOLD_S,
             j.TOQUE_DROP_S, keyboard.pausa],
            sleeps,
        )

    def test_bot_does_not_pause_after_confirmed_line_clear(self):
        plans = []
        bot = j.Bot(SimpleNamespace(executar=plans.append), lambda _msg: None, sem_foco=True)
        bot.rodada_confirmada = True
        bot.alvo = 1
        bot.ultima_limpas = 2
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=1, mao="T",
                                  hold_estado="vazio", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset(), voo=None)
        move = d.Jogada(False, "T", 0, 3, frozenset({(19, 3), (19, 4), (19, 5), (18, 4)}),
                        [("toque", "drop")], "", 0, 0, 0, 0.0)
        with patch.object(j.decisor, "decidir", return_value=move):
            self.assertTrue(bot.passo(tracker, board, 10.0))

        self.assertEqual(2, bot.alvo)                       # alvo antigo consumido, novo criado
        self.assertEqual(0.0, bot.pausa_ate)                # sem espera depois do clear
        self.assertEqual([("toque", "drop")], plans[-1])  # jogou na hora

    def test_confirmed_clear_keeps_deciding_while_the_screen_animates(self):
        """A leitura fica instavel na animacao do clear: o modelo confirmado ja basta."""
        plans = []
        bot = j.Bot(SimpleNamespace(executar=plans.append), lambda _msg: None, sem_foco=True)
        bot.rodada_confirmada = True
        bot.ultima_limpas = 1
        bot.modelo = frozenset({(19, 0)})
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=5, mao="T",
                                  hold_estado="vazio", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset({(19, 0)}), voo=None, incoerentes=2)
        move = d.Jogada(False, "T", 0, 3, frozenset({(19, 3), (19, 4), (19, 5), (18, 4)}),
                        [("toque", "drop")], "", 0, 0, 0, 0.0)
        with patch.object(j.decisor, "decidir", return_value=move):
            self.assertTrue(bot.passo(tracker, board, 10.0))
        self.assertEqual([("toque", "drop")], plans[-1])

    def test_unstable_read_without_a_recent_clear_still_waits(self):
        plans = []
        bot = j.Bot(SimpleNamespace(executar=plans.append), lambda _msg: None, sem_foco=True)
        bot.rodada_confirmada = True
        bot.ultima_limpas = 0
        bot.modelo = frozenset({(19, 0)})
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=5, mao="T",
                                  hold_estado="vazio", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset({(19, 0)}), voo=None, incoerentes=2)
        self.assertFalse(bot.passo(tracker, board, 10.0))
        self.assertEqual([], plans)

    def test_incoherent_screen_reading_does_not_replace_the_predicted_stack(self):
        events = []
        bot = j.Bot(SimpleNamespace(), events.append)
        expected = frozenset({(19, 0), (19, 1), (18, 1), (17, 1)})
        bot.modelo = expected
        bot.modelo_tentativo = True

        bot.evento("incoerente")
        observed = bot._pilha(SimpleNamespace(
            pilha=frozenset(), voo=None, incoerentes=3))

        self.assertEqual(expected, observed)
        self.assertTrue(bot.usando_modelo)
        self.assertFalse(any("PILHA DIVERGE" in event for event in events))
        self.assertTrue(any("LEITURA DA PILHA INCOERENTE" in event for event in events))

    def test_transient_empty_sync_does_not_erase_a_pending_placement(self):
        events = []
        bot = j.Bot(SimpleNamespace(), events.append)
        expected = frozenset({(19, 0), (19, 1), (18, 1), (17, 1)})
        bot.modelo = expected
        bot.modelo_tentativo = True

        bot.evento("sync_vazio")
        observed = bot._pilha(SimpleNamespace(
            pilha=frozenset(), voo=None, incoerentes=0))

        self.assertEqual(expected, observed)
        self.assertEqual(expected, bot.modelo)
        self.assertTrue(bot.usando_modelo)
        self.assertFalse(any("PILHA DIVERGE" in event for event in events))

    def test_synchronized_lock_event_allows_reconciling_a_real_mismatch(self):
        events = []
        bot = j.Bot(SimpleNamespace(), events.append)
        bot.modelo = frozenset({(19, 0)})
        bot.modelo_tentativo = True
        actual = frozenset({(19, 9)})

        bot.evento("trava")
        observed = bot._pilha(SimpleNamespace(
            pilha=actual, voo=None, incoerentes=0))

        self.assertEqual(actual, observed)
        self.assertEqual(actual, bot.modelo)
        self.assertFalse(bot.modelo_tentativo)
        self.assertTrue(any("PILHA DIVERGE" in event for event in events))

    def test_board_divergence_log_identifies_the_last_planned_placement(self):
        events = []
        bot = j.Bot(SimpleNamespace(), events.append)
        bot.modelo = frozenset((19, col) for col in range(10)) | frozenset({(18, 0), (18, 1), (18, 2)})
        bot.ultima_jogada = "JOGADA 8: T rotacao=1 coluna=3"
        bot._pilha(SimpleNamespace(pilha=frozenset(), voo=None))
        self.assertTrue(any("apos JOGADA 8: T rotacao=1 coluna=3" in event for event in events))

    def test_queue_stall_log_includes_last_move_and_stack_heights(self):
        events = []
        bot = j.Bot(SimpleNamespace(), events.append, sem_foco=True)
        bot.ultima_jogada = "JOGADA 8: T rotacao=1 coluna=3"
        bot.alvo, bot.limite = 1, 0
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0,
                                  hold_estado="vazio", mao="I", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset((19, col) for col in range(10)), voo=None)

        self.assertFalse(bot.passo(tracker, board, 1))
        self.assertTrue(any("JOGADA 8" in event and "pilha_visual=1" in event for event in events))

    def test_bot_does_not_repeat_a_move_when_queue_did_not_advance(self):
        events = []
        keyboard = SimpleNamespace(executar=lambda _plan: self.fail("must not send stale inputs"))
        bot = j.Bot(keyboard, events.append, sem_foco=True)
        bot.alvo, bot.limite = 1, 0
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0,
                                  hold_estado="vazio", mao="I", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset(), voo=None)

        self.assertFalse(bot.passo(tracker, board, 1))
        self.assertIsNone(bot.alvo)      # solta o alvo e ressincroniza em vez de parar de jogar
        self.assertTrue(any("ressincronizando pela tela" in msg for msg in events))

    def test_opener_empty_hold_only_fetches_the_next_piece(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.opener_nome = "DT Cannon"
        bot.opener_fase = "dt_tsd"
        bot.opener_tsd_celulas = ((10, 3), (10, 4), (11, 3), (11, 4))
        bot.modelo = frozenset()
        board = SimpleNamespace(pilha=frozenset(), voo=None, incoerentes=0)
        tracker = SimpleNamespace(fila=tuple("TIOZ"), avancos=6, mao="S",
                                  hold_estado="vazio", hold_peca=None)

        self.assertTrue(bot._jogar_opener(tracker, board, "S", "vazio", 1.0))
        self.assertEqual([[("toque", "hold")]], plans)
        self.assertEqual(7, bot.alvo)

        # A fila avançou, mas a leitura da mão ainda está velha. Não pode gastar
        # outra peça para tentar buscar T de novo.
        tracker.fila = tuple("IOZL")
        tracker.avancos = 7
        self.assertFalse(bot._jogar_opener(tracker, board, "S", "vazio", 1.1))
        self.assertEqual([[("toque", "hold")]], plans)
        self.assertIsNone(bot.opener_fase)

    def test_opener_cancels_when_empty_hold_cannot_fetch_the_target(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.opener_nome = "TKI-3"
        bot.opener_fase = "suporte"
        bot.opener_rota = [("T", ((10, 3), (10, 4), (11, 3), (11, 4)))]
        tracker = SimpleNamespace(fila=tuple("OZLTI"), avancos=5, mao="O",
                                  hold_estado="vazio", hold_peca=None)
        board = SimpleNamespace(pilha=frozenset(), voo=None, incoerentes=0)

        self.assertFalse(bot._jogar_opener(tracker, board, "O", "vazio", 1.0))
        self.assertEqual([], plans)
        self.assertIsNone(bot.opener_fase)
        self.assertTrue(any("proxima='O'" in event for event in events))

    def test_opener_hold_swap_waits_without_spamming_and_requires_stable_queue(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.opener_nome = "DT Cannon"
        bot.opener_fase = "dt_tsd"
        bot.opener_tsd_celulas = ((10, 3), (10, 4), (11, 3), (11, 4))
        tracker = SimpleNamespace(fila=tuple("IOZL"), avancos=6, mao="S",
                                  hold_estado="pronto", hold_peca="T")
        board = SimpleNamespace(pilha=frozenset(), voo=None, incoerentes=0)

        self.assertTrue(bot._jogar_opener(tracker, board, "S", "pronto", 1.0))
        self.assertEqual([[("toque", "hold")]], plans)
        self.assertTrue(bot._jogar_opener(tracker, board, "S", "pronto", 1.1))
        self.assertEqual([[("toque", "hold")]], plans)

        tracker.avancos = 7
        self.assertFalse(bot._jogar_opener(tracker, board, "T", "pronto", 1.2))
        self.assertEqual([[("toque", "hold")]], plans)
        self.assertIsNone(bot.opener_fase)

    def test_timed_out_dt_tsd_cancels_remaining_complete_opener(self):
        events = []
        keyboard = SimpleNamespace(executar=lambda _plan: self.fail("timeout must not send inputs"))
        bot = j.Bot(keyboard, events.append, sem_foco=True)
        bot.opener_nome = "DT Cannon"
        bot.opener_fase = "dt_tsd"
        bot.alvo, bot.limite = 1, 0.5
        bot.ultima_jogada = "OPENER 7 (dt_tsd): T"
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0,
                                  hold_estado="vazio", mao="I", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset(), voo=None, incoerentes=0)

        with patch.object(bot, "_estado_atual_confiavel", return_value=True):
            self.assertFalse(bot.passo(tracker, board, 1.0))

        self.assertIsNone(bot.opener_fase)
        self.assertIsNotNone(bot.espera_avanco)
        self.assertTrue(any("fila sem avancar apos passo do opener" in event for event in events))

    def test_dt_stops_after_confirmed_tsd_without_running_tst_search(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.opener_nome = "DT Cannon"
        bot.opener_fase = "dt_tsd"
        bot.modelo = frozenset(
            (13 + r, c) for r, row in enumerate(j.openers.DT_MOLDE)
            for c, cell in enumerate(row) if cell == "X"
        )
        bot.opener_tsd_celulas = frozenset((13 + r, c) for r, c in j.openers.DT_TSD)
        tracker = SimpleNamespace(fila=tuple("TIOZJ"), avancos=0, mao="T",
                                  hold_estado="vazio", hold_peca=None)
        board = SimpleNamespace(pilha=bot.modelo, voo=None, incoerentes=0)

        with patch.object(j.decisor, "LINHA_SPAWN", 2):
            self.assertTrue(bot._jogar_opener(tracker, board, "T", "vazio", 1.0))
            self.assertEqual("dt_tsd", bot.opener_fase)
            self.assertEqual("dt_tsd", bot.opener_acao_pendente)
            self.assertFalse(any("COMPLETO" in event for event in events))
            tracker.avancos = 1
            with patch.object(j.openers, "buscar_rota_tst",
                              side_effect=AssertionError("TST search must stay disabled")):
                bot._confirmar_opener_avanco(tracker, board, 1.1)
                self.assertIsNone(bot.opener_fase)
                self.assertFalse(bot._jogar_opener(tracker, board, "T", "vazio", 1.2))

            self.assertIsNone(bot.opener_acao_pendente)
            self.assertEqual(1, len(plans))
            self.assertTrue(any("continuidade TST automatica desativada" in event
                                for event in events))
            self.assertFalse(any("COMPLETO" in event for event in events))

    def test_tki_initial_phase_waits_for_tsd_lock_confirmation(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.opener_nome = "TKI-3"
        bot.opener_fase = "suporte"
        bot.opener_rota = [("T", tuple((13 + r, c) for r, c in j.openers.DT_TSD))]
        bot.modelo = frozenset(
            (13 + r, c) for r, row in enumerate(j.openers.DT_MOLDE)
            for c, cell in enumerate(row) if cell == "X"
        )
        tracker = SimpleNamespace(fila=tuple("TIOZJ"), avancos=0, mao="T",
                                  hold_estado="vazio", hold_peca=None)
        board = SimpleNamespace(pilha=bot.modelo, voo=None, incoerentes=0)

        with patch.object(j.decisor, "LINHA_SPAWN", 2):
            self.assertTrue(bot._jogar_opener(tracker, board, "T", "vazio", 1.0))

        self.assertEqual("suporte", bot.opener_fase)
        self.assertEqual("fase_inicial", bot.opener_acao_pendente)
        self.assertFalse(any("fase inicial confirmada" in event for event in events))

        tracker.avancos = 1
        bot._confirmar_opener_avanco()
        self.assertIsNone(bot.opener_fase)
        self.assertTrue(any("fase inicial confirmada pela fila" in event for event in events))

    def test_planned_tki_tsd_returns_control_to_normal_decision(self):
        events = []
        bot = j.Bot(SimpleNamespace(executar=lambda _plan: None), events.append, sem_foco=True)
        tracker = SimpleNamespace(mao="I", fila=tuple("TJSOZ"),
                                  hold_estado="vazio", hold_peca=None)
        support = ("I", ((19, 0), (19, 1), (18, 0), (18, 1)))
        tsd = ("T", tuple((13 + r, c) for r, c in j.openers.DT_TSD))

        with patch.object(j.openers, "planejar", return_value=("TKI-3", (support, tsd))):
            bot._abrir_opener(tracker)

        self.assertEqual("tsd_suporte", bot.opener_fase)
        self.assertEqual([support], bot.opener_rota)
        self.assertEqual(tsd[1], bot.opener_tsd_celulas)
        bot.opener_acao_pendente = "tsd_suporte"
        bot._confirmar_opener_avanco()
        self.assertEqual("tsd", bot.opener_fase)
        bot.opener_acao_pendente = "tsd"
        bot._confirmar_opener_avanco()
        self.assertIsNone(bot.opener_fase)
        self.assertFalse(any("COMPLETO" in event for event in events))
        self.assertTrue(any("continuidade TST automatica desativada" in event
                            for event in events))

    def test_bot_waits_for_board_to_resynchronize_before_next_move(self):
        keyboard = SimpleNamespace(executar=lambda _plan: self.fail("must not use stale board"))
        bot = j.Bot(keyboard, lambda _msg: None, sem_foco=True)
        bot.modelo = frozenset({(19, 0)})
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0,
                                  hold_estado="vazio", mao="I", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset({(19, 9)}), voo=None)

        self.assertFalse(bot.passo(tracker, board, 1))
        self.assertTrue(bot.usando_modelo)

    def test_bot_stops_if_queue_advances_more_than_expected(self):
        events = []
        keyboard = SimpleNamespace(executar=lambda _plan: self.fail("must not send inputs after desync"))
        bot = j.Bot(keyboard, events.append, sem_foco=True)
        bot.modelo = frozenset({(19, 0)})
        bot.modelo_tentativo = True
        bot.alvo, bot.limite = 1, 10
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=2,
                                  hold_estado="vazio", mao="I", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset(), voo=None)

        self.assertFalse(bot.passo(tracker, board, 1))
        self.assertIsNotNone(bot.alvo)
        self.assertTrue(any("avancou mais do que o esperado" in msg for msg in events))

    def test_bot_recovers_from_queue_overshoot_only_when_visual_stack_matches(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.especular = False
        bot.modelo = frozenset({(19, 0)})
        bot.modelo_tentativo = True
        bot.alvo, bot.limite = 1, 10
        tracker = SimpleNamespace(fila=tuple("OTSZJ"), avancos=2,
                                  hold_estado="vazio", mao="I", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=bot.modelo, voo=None, incoerentes=0)
        move = d.Jogada(False, "I", 0, 3, frozenset(), [("toque", "drop")], "", 0, 0, 0, 0.0)

        with patch.object(j.decisor, "decidir", return_value=move):
            self.assertTrue(bot.passo(tracker, board, 1))

        self.assertEqual([[("toque", "drop")]], plans)
        self.assertEqual(3, bot.alvo)
        self.assertTrue(any("ressincronizando o modelo" in msg for msg in events))

    def test_bot_rebases_on_stable_visual_stack_after_queue_overshoot(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.especular = False
        bot.modelo = frozenset({(19, 0)})
        bot.alvo, bot.limite = 1, 10
        tracker = SimpleNamespace(fila=tuple("OTSZJ"), avancos=2,
                                  hold_estado="vazio", mao="I", hold_peca=None,
                                  texto_hold=lambda: "-")
        stack = frozenset({(19, 1)})
        board = SimpleNamespace(pilha=stack, voo=None, incoerentes=0,
                                _ultima=stack, _iguais=tp.RastreadorPilha.QUIETO_FRAMES)
        move = d.Jogada(False, "I", 0, 3, frozenset(), [("toque", "drop")], "", 0, 0, 0, 0.0)

        with patch.object(j.decisor, "decidir", return_value=move):
            self.assertTrue(bot.passo(tracker, board, 1))

        self.assertEqual(stack, bot.modelo)
        self.assertEqual([[("toque", "drop")]], plans)

    def test_bot_does_not_recover_queue_overshoot_with_unknown_hand(self):
        keyboard = SimpleNamespace(executar=lambda _plan: self.fail("must not use an unknown active piece"))
        bot = j.Bot(keyboard, lambda _msg: None, sem_foco=True)
        bot.modelo = frozenset({(19, 0)})
        bot.alvo, bot.limite = 1, 10
        tracker = SimpleNamespace(fila=tuple("OTSZJ"), avancos=2,
                                  hold_estado="vazio", mao=None, hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=bot.modelo, voo=None, incoerentes=0)

        self.assertFalse(bot.passo(tracker, board, 1))
        self.assertEqual(1, bot.alvo)

    def test_bot_never_stops_playing_when_no_placement_is_reachable(self):
        """Sem jogada o bot ressinconiza e segue tentando; ele nao se declara morto mais."""
        events = []
        keyboard = SimpleNamespace(executar=lambda _plan: self.fail("must not send inputs without a move"))
        bot = j.Bot(keyboard, events.append, sem_foco=True)
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0,
                                  hold_estado="vazio", mao="I", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset((r, 0) for r in range(5, 20)), voo=None)
        with patch.object(j.decisor, "decidir", return_value=None):
            for i in range(1, 40):
                self.assertFalse(bot.passo(tracker, board, i))
                self.assertFalse(bot.bloqueado)
        self.assertTrue(any("continuo tentando" in event for event in events))

    def test_high_stack_without_a_route_sends_one_emergency_drop(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.especular = False
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=3,
                                  hold_estado="vazio", mao="T", hold_peca=None,
                                  texto_hold=lambda: "-")
        stack = frozenset((r, 0) for r in range(3, 20))
        board = SimpleNamespace(pilha=stack, voo=None, incoerentes=0,
                                _ultima=stack, _iguais=tp.RastreadorPilha.QUIETO_FRAMES)

        with patch.object(j.decisor, "decidir", return_value=None):
            self.assertTrue(bot.passo(tracker, board, 1))
            self.assertEqual(4, bot.alvo)
            self.assertFalse(bot.passo(tracker, board, 1.1))
            timeout = bot.limite + 0.1
            self.assertFalse(bot.passo(tracker, board, timeout))
            self.assertFalse(bot.passo(
                tracker, board, timeout + bot.ESPERA_REPETIR + 0.1))

        self.assertEqual([[("toque", "drop")]], plans)
        self.assertIsNone(bot.alvo)
        self.assertEqual(3, bot.drop_emergencia_avancos)
        self.assertTrue(any("hard drop da peca T enviado uma vez" in event for event in events))

    def test_unknown_start_hand_waits_instead_of_spending_hold(self):
        events = []
        keyboard = SimpleNamespace(executar=lambda _plan: self.fail("must not guess the active piece"))
        bot = j.Bot(keyboard, events.append, sem_foco=True)
        bot.mao_desconhecida_aviso = -3
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0,
                                  hold_estado="vazio", mao=None, hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset((r, 3) for r in range(12, 20)), voo=None)  # ha pilha: nao e inicio

        self.assertFalse(bot.passo(tracker, board, 1))
        self.assertTrue(any("AGUARDANDO" in msg for msg in events))

    def test_confirmed_unknown_start_uses_hold_once_then_plays_known_queue_piece(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.rodada_confirmada = True
        bot.especular = False
        bot.voo_desde = 0.0   # GO ja passou: a peca ativa esta visivel ha mais de 0,35 s
        tracker = j.tp.Rastreador()
        tracker.iniciar_rodada(tuple("IOTSZ"))
        board = SimpleNamespace(pilha=frozenset(), voo=None)

        self.assertTrue(bot.passo(tracker, board, 1))
        self.assertEqual([[("toque", "hold")]], plans)
        self.assertEqual(1, bot.alvo)
        self.assertEqual(0, bot.jogadas)

        tracker.atualizar(tuple("OTSZL"), "GRAY")
        self.assertEqual("I", tracker.mao)
        self.assertEqual("usado", tracker.hold_estado)
        move = d.Jogada(False, "I", 0, 3, frozenset(), [("toque", "drop")],
                        "", 0, 0, 0, 0.0)
        with patch.object(j.decisor, "decidir", return_value=move) as decide:
            self.assertTrue(bot.passo(tracker, board, 2))
        self.assertEqual("I", decide.call_args.args[1])
        self.assertFalse(decide.call_args.kwargs["permitir_hold"])
        self.assertEqual(("toque", "drop"), plans[-1][-1])
        self.assertTrue(any("HOLD INICIAL" in msg for msg in events))

    def test_unconfirmed_but_empty_board_starts_with_hold(self):
        """O inicio nao pode esperar 1,5 s de confirmacao: tabuleiro lido vazio basta."""
        plans = []
        bot = j.Bot(SimpleNamespace(executar=plans.append), lambda _msg: None, sem_foco=True)
        bot.voo_desde = 0.0
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0,
                                  hold_estado="vazio", mao=None, hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset(), voo=None)
        self.assertTrue(bot.passo(tracker, board, 1))
        self.assertEqual([[("toque", "hold")]], plans)

    def test_countdown_does_not_press_hold_before_go(self):
        """Durante o 3-2-1 nao ha peca ativa: apertar hold ali nao faz nada e bagunca o inicio."""
        plans = []
        bot = j.Bot(SimpleNamespace(executar=plans.append), lambda _msg: None, sem_foco=True)
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0,
                                  hold_estado="vazio", mao=None, hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset(), voo=None)
        for i in range(1, 30):
            self.assertFalse(bot.passo(tracker, board, i * 0.1))
        self.assertEqual([], plans)

    def test_new_round_is_detected_even_with_the_old_round_model_still_on_the_stack(self):
        """Antes o modelo velho (pilha cheia) impedia detectar a partida nova depois da 1a."""
        events = []
        bot = j.Bot(SimpleNamespace(), events.append, sem_foco=True)
        bot.jogadas = 120
        bot.modelo = frozenset((r, c) for r in range(12, 20) for c in range(9))
        bot.ultimo_envio = 0.0
        tracker = j.tp.Rastreador()
        tracker.fila, tracker.hold_estado, tracker.hold_peca = tuple("SIJZT"), "vazio", None
        board = SimpleNamespace(pilha=frozenset(), voo=None)
        self.assertFalse(bot.observar_inicio_rodada(tracker, board, 100.0))
        self.assertTrue(bot.observar_inicio_rodada(tracker, board, 101.6))
        self.assertEqual(frozenset(), bot.modelo)
        self.assertTrue(bot.aguardando_go)
        self.assertEqual(0, bot.pecas_abertura)

    def test_empty_board_right_after_sending_keys_is_not_a_new_round(self):
        bot = j.Bot(SimpleNamespace(), lambda _msg: None, sem_foco=True)
        bot.jogadas = 40
        bot.modelo = frozenset({(19, 0)})
        tracker = j.tp.Rastreador()
        tracker.fila, tracker.hold_estado, tracker.hold_peca = tuple("SIJZT"), "vazio", None
        board = SimpleNamespace(pilha=frozenset(), voo=None)
        bot.ultimo_envio = 99.9
        self.assertFalse(bot.observar_inicio_rodada(tracker, board, 100.0))
        bot.ultimo_envio = 100.5
        self.assertFalse(bot.observar_inicio_rodada(tracker, board, 101.6))

    def test_no_move_with_a_low_visible_stack_does_not_declare_top_out(self):
        """Sem jogada pelo modelo com pilha baixa na tela = leitura errada, nao fim de jogo."""
        events = []
        bot = j.Bot(SimpleNamespace(), events.append, sem_foco=True)
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0, hold_estado="vazio",
                                  mao="I", hold_peca=None, texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset({(19, 0), (18, 0), (17, 0)}), voo=None)
        with patch.object(j.decisor, "decidir", return_value=None):
            for i in range(1, 25):
                self.assertFalse(bot.passo(tracker, board, i))
        self.assertFalse(bot.bloqueado)
        self.assertTrue(any("continuo tentando" in msg for msg in events))

    def test_start_holds_the_piece_even_after_a_previous_round(self):
        """Tabuleiro vazio basta para guardar a peca ativa; nao depende de rodada 1."""
        plans = []
        bot = j.Bot(SimpleNamespace(executar=plans.append), lambda _msg: None, sem_foco=True)
        bot.pecas_abertura = 5
        bot.voo_desde = 0.0
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=3, mao=None,
                                  hold_estado="vazio", hold_peca=None, texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset({(2, 4), (3, 4)}), voo=None)  # spawn mal lido
        self.assertTrue(bot.passo(tracker, board, 1.0))
        self.assertEqual([[("toque", "hold")]], plans)

    def test_unknown_start_with_stack_on_screen_does_not_press_hold(self):
        plans = []
        bot = j.Bot(SimpleNamespace(executar=plans.append), lambda _msg: None, sem_foco=True)
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0,
                                  hold_estado="vazio", mao=None, hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset((r, 0) for r in range(12, 20)), voo=None)
        self.assertFalse(bot.passo(tracker, board, 1))
        self.assertEqual([], plans)

    def test_unconfirmed_initial_hold_timeout_does_not_repeat_input(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.rodada_confirmada = True
        bot.voo_desde = 0.0
        tracker = SimpleNamespace(fila=tuple("IOTSZ"), avancos=0,
                                  hold_estado="vazio", mao=None, hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset(), voo=None)
        self.assertTrue(bot.passo(tracker, board, 1))
        self.assertFalse(bot.passo(tracker, board, 1 + bot.ESPERA_MAX + 0.1))
        self.assertEqual([[("toque", "hold")]], plans)
        self.assertIsNone(bot.alvo)
        self.assertTrue(any("HOLD INICIAL sem confirmacao" in msg for msg in events))

    def test_initial_hand_is_read_from_active_piece_color(self):
        events = []
        bot = j.Bot(SimpleNamespace(), events.append)
        tracker = SimpleNamespace(mao=None, fila=tuple("ITZSO"), definir_mao=lambda p: setattr(tracker, "mao", p))
        flying = frozenset({(4, 4), (4, 5), (4, 6), (5, 5)})
        board = SimpleNamespace(voo=flying)
        with patch.object(j.tp, "letra_das_celulas", return_value="T"):
            self.assertTrue(bot.sincronizar_mao_visual(tracker, board, object()))
        self.assertEqual("T", tracker.mao)
        self.assertTrue(any("MAO INICIAL" in msg for msg in events))

    def test_spawn_color_corrects_a_stale_known_hand(self):
        events = []
        bot = j.Bot(SimpleNamespace(), events.append)
        tracker = SimpleNamespace(mao="S", fila=tuple("TIOZJ"),
                                  definir_mao=lambda p: setattr(tracker, "mao", p))
        flying = frozenset({(2, 4), (2, 5), (2, 6), (3, 5)})

        with patch.object(j.tp, "letra_das_celulas", return_value="T"):
            self.assertTrue(bot.sincronizar_mao_visual(
                tracker, SimpleNamespace(voo=flying, pilha=frozenset()), object()))

        self.assertEqual("T", tracker.mao)
        self.assertTrue(any("MAO RESSINCRONIZADA: rastreador=S, tela=T" in msg for msg in events))

    def test_hold_content_change_confirms_opener_swap_even_without_gray_read(self):
        events, plans = [], []
        bot = j.Bot(SimpleNamespace(executar=plans.append), events.append, sem_foco=True)
        bot.opener_nome = "DT Cannon"
        bot.opener_fase = "dt_tsd"
        bot.opener_tsd_celulas = frozenset(
            (13 + r, c) for r, c in j.openers.DT_TSD
        )
        bot.modelo = frozenset(
            (13 + r, c) for r, row in enumerate(j.openers.DT_MOLDE)
            for c, cell in enumerate(row) if cell == "X"
        )
        tracker = j.tp.Rastreador()
        tracker.fila = tuple("IOZL")
        tracker.mao = "S"
        tracker.hold_estado, tracker.hold_peca = "pronto", "T"
        tracker.avancos = 6
        board = SimpleNamespace(pilha=bot.modelo, voo=None, incoerentes=0)

        with patch.object(j.decisor, "LINHA_SPAWN", 2):
            self.assertTrue(bot._jogar_opener(tracker, board, "S", "pronto", 1.0))
            self.assertEqual([[("toque", "hold")]], plans)

            # A leitura da cor do Hold mudou para a mao anterior, mas nao detectou cinza.
            tracker.atualizar(tuple("IOZL"), "S")
            self.assertEqual(("S", "pronto"), (tracker.mao, tracker.hold_estado))
            self.assertTrue(bot._jogar_opener(
                tracker, board, tracker.mao, tracker.hold_estado, 1.2))

        self.assertEqual("dt_tsd", bot.opener_acao_pendente)
        self.assertEqual(2, len(plans))
        self.assertEqual(("T", "usado", "S"),
                         (tracker.mao, tracker.hold_estado, tracker.hold_peca))
        self.assertTrue(any("hold-troca confirmada pela peca anterior no Hold" in msg
                            for msg in events))

    def test_new_round_resets_hold_b2b_and_combo_after_empty_countdown(self):
        events = []
        bot = j.Bot(SimpleNamespace(), events.append)
        bot.modelo, bot.b2b, bot.combo = frozenset({(19, 0)}), 4, 3
        tracker = j.tp.Rastreador()
        tracker.fila, tracker.mao, tracker.avancos = tuple("TIOZJ"), "S", 8
        board = SimpleNamespace(pilha=frozenset(), voo=None)

        self.assertFalse(bot.observar_inicio_rodada(tracker, board, 1.0))
        self.assertTrue(bot.observar_inicio_rodada(tracker, board, 2.6))
        self.assertEqual((None, 0, "vazio", 0),
                         (tracker.mao, tracker.avancos, tracker.hold_estado, bot.b2b))
        self.assertEqual(0, bot.combo)
        self.assertEqual(frozenset(), bot.modelo)
        self.assertFalse(bot.observar_inicio_rodada(tracker, board, 2.7))
        self.assertTrue(any("NOVA RODADA" in msg for msg in events))

    def test_empty_board_after_first_move_is_not_mistaken_for_a_new_round(self):
        events = []
        bot = j.Bot(SimpleNamespace(), events.append)
        bot.jogadas = 1
        bot.rodada_confirmada = True
        bot.modelo = frozenset({(19, 4)})   # o bot empilhou: leitura vazia aqui e divergencia
        bot.b2b, bot.combo = 3, 2
        bot.ultimo_envio = 2.5   # o bot acabou de enviar teclas: esta jogando
        tracker = j.tp.Rastreador()
        tracker.fila, tracker.mao, tracker.avancos = tuple("TIOZJ"), "S", 4
        board = SimpleNamespace(pilha=frozenset(), voo=None)

        self.assertFalse(bot.observar_inicio_rodada(tracker, board, 1.0))
        self.assertFalse(bot.observar_inicio_rodada(tracker, board, 3.0))
        self.assertEqual((4, "S", 3, 2),
                         (tracker.avancos, tracker.mao, bot.b2b, bot.combo))
        self.assertTrue(bot.rodada_confirmada)
        self.assertFalse(any("NOVA RODADA" in event for event in events))

    def test_repeated_queue_does_not_trigger_new_round(self):
        bot = j.Bot(SimpleNamespace(), lambda _msg: None)
        tracker = j.tp.Rastreador()
        tracker.fila = tuple("TITIO")
        board = SimpleNamespace(pilha=frozenset(), voo=None)
        self.assertFalse(bot.observar_inicio_rodada(tracker, board, 1.0))
        self.assertFalse(bot.observar_inicio_rodada(tracker, board, 3.0))
        self.assertEqual(0, tracker.avancos)
        self.assertFalse(bot.rodada_confirmada)

    def test_start_of_round_syncs_stack_with_extra_screen_colors(self):
        """Sobras coloridas da tela (contagem/anuncio) nao podem travar a pilha no inicio."""
        pilha = j.tp.RastreadorPilha()
        peca = {(2, 4), (3, 3), (3, 4), (3, 5)}
        evento = pilha.atualizar(frozenset(peca) | {(0, 0), (1, 1)})
        self.assertEqual("sync", evento)
        self.assertEqual(frozenset(), pilha.pilha)
        self.assertEqual(frozenset(peca), pilha.voo)

    def test_partially_read_spawn_piece_is_an_empty_stack_not_a_stuck_read(self):
        pilha = j.tp.RastreadorPilha()
        self.assertEqual("sync_topo", pilha.atualizar(frozenset({(2, 4), (3, 4)})))
        self.assertEqual(frozenset(), pilha.pilha)
        self.assertIsNone(pilha.voo)

    def test_stack_below_the_spawn_band_is_not_treated_as_empty(self):
        pilha = j.tp.RastreadorPilha()
        self.assertEqual("sem_sync", pilha.atualizar(frozenset({(2, 4), (5, 0), (19, 1)})))
        self.assertIsNone(pilha.pilha)

    def test_next_round_is_detected_when_the_board_is_cleared(self):
        """Rodada 2 em diante: modelo zerado + tabuleiro vazio + peca no spawn = reinicio."""
        bot = j.Bot(SimpleNamespace(), lambda _msg: None)
        bot.jogadas = 30
        bot.rodada_confirmada = True
        bot.modelo = None              # a tela avisou sync_vazio
        bot.b2b, bot.combo, bot.pecas_abertura = 5, 4, 14
        tracker = j.tp.Rastreador()
        tracker.fila, tracker.mao, tracker.avancos = tuple("TIOSZ"), "L", 30
        spawn = frozenset({(2, 4), (3, 3), (3, 4), (3, 5)})   # T nas linhas de spawn
        board = SimpleNamespace(pilha=frozenset(), voo=spawn)

        self.assertFalse(bot.observar_inicio_rodada(tracker, board, 10.0))
        self.assertTrue(bot.observar_inicio_rodada(tracker, board, 11.6))
        self.assertEqual((0, 0, 0), (bot.b2b, bot.combo, bot.pecas_abertura))
        self.assertEqual(frozenset(), bot.modelo)

    def test_confirmed_round_keeps_opener_window_after_first_stack_placement(self):
        bot = j.Bot(SimpleNamespace(), lambda _msg: None)
        tracker = j.tp.Rastreador()
        tracker.fila = tuple("IJLOS")
        tracker.hold_estado = "vazio"
        empty = SimpleNamespace(pilha=frozenset(), voo=None)
        self.assertFalse(bot.observar_inicio_rodada(tracker, empty, 1.0))
        bot.bloqueado = True
        self.assertTrue(bot.observar_inicio_rodada(tracker, empty, 3.0))
        self.assertTrue(bot.rodada_confirmada)
        self.assertFalse(bot.bloqueado)
        self.assertEqual(0, bot.pecas_abertura)
        self.assertTrue(bot._fase_abertura(empty.pilha, 0, 0))
        self.assertTrue(bot._fase_abertura(empty.pilha, d.JANELA_ABERTURA - 1, 0))
        self.assertFalse(bot._fase_abertura(empty.pilha, d.JANELA_ABERTURA, 0))

        bot.pecas_abertura = 1
        active = SimpleNamespace(pilha=frozenset({(19, 0)}), voo=None)
        self.assertFalse(bot.observar_inicio_rodada(tracker, active, 3.2))
        self.assertTrue(bot.rodada_confirmada)
        self.assertEqual(1, bot.pecas_abertura)


class SpinDetectionTests(unittest.TestCase):
    def test_non_t_spin_needs_all_four_directions_blocked(self):
        """TETR.IO (All-Mini+): so e spin se a peca fica imovel — inclusive sem subir."""
        alvo = (3, 4)
        with patch.object(d, "MODO", "ataque"):
            def so_sobe(_k, r, c):          # pode subir => nao e spin
                return (r, c) == (alvo[0] - 1, alvo[1])
            self.assertEqual("", d._spin_all_mini([0] * d.LINHAS, "S", 0, *alvo, 1,
                                                  verificar=so_sobe))

            def travada(_k, r, c):          # nao anda para lado nenhum => mini
                return False
            self.assertEqual("mini", d._spin_all_mini([0] * d.LINHAS, "S", 0, *alvo, 1,
                                                      verificar=travada))

    def test_combo_bonus_only_counts_in_defense(self):
        baixo = [0] * d.LINHAS
        baixo[-1] = d.CHEIA ^ (1 << 4)
        with patch.object(d, "MODO", "ataque"):
            self.assertEqual(d._avaliar(baixo, 0, 0, (False, False)),
                             d._avaliar(baixo, 0, 4, (False, False)))


class WellTests(unittest.TestCase):
    def test_multiple_one_wide_wells_cost_more_than_a_single_one(self):
        self.assertEqual(0.0, d._penalidade_pocos_um([0] * 10))
        um = d._penalidade_pocos_um([8, 0, 8, 0, 0, 0, 0, 0, 0, 0])
        tres = d._penalidade_pocos_um([8, 0, 8, 0, 8, 0, 8, 0, 0, 0])
        self.assertGreater(um, 0.0)          # fundo demais para um unico I
        self.assertGreater(tres, um)         # cada poco extra so aceita I

    def test_extra_one_wide_well_charges_poco_extra_even_when_shallow(self):
        um = [6, 4, 6, 6, 6, 6, 6, 6, 6, 6]        # um poco de profundidade 2
        dois = [6, 4, 6, 4, 6, 6, 6, 6, 6, 6]      # dois pocos rasos
        self.assertEqual(0.0, d._penalidade_pocos_um(um))
        self.assertGreater(d._penalidade_pocos_um(dois), 0.0)
        self.assertEqual(2, d._contar_pocos_um(um) + d._contar_pocos_um(dois) - 1)

    def test_filter_allows_one_well_and_drops_a_second(self):
        flat = [d.CHEIA] * d.LINHAS
        um = [0] * (d.LINHAS - 2) + [d.CHEIA ^ (1 << 4)] * 2
        dois = [0] * (d.LINHAS - 2) + [d.CHEIA ^ (1 << 3) ^ (1 << 6)] * 2

        def colocacao(board):
            return (0.0, board, 0, 0, 0, 3, frozenset(), "", [], 0, 1)

        filtradas = d._filtrar_pocos_novos(flat, [colocacao(um), colocacao(dois)])
        self.assertEqual([um], [c[1] for c in filtradas])
        # com um poco ja na pilha o filtro nao age
        self.assertEqual(2, len(d._filtrar_pocos_novos(um, [colocacao(um), colocacao(dois)])))

    def test_weak_clears_are_filtered_only_while_the_stack_is_low(self):
        safe_rows = [0] * d.LINHAS
        safe_rows[-8:] = [d.CHEIA ^ (1 << 5)] * 8
        weak = (0.0, safe_rows, 0, 0, 0, 3, frozenset(), "", [], 1, 1)
        safe = (0.0, safe_rows, 0, 0, 0, 3, frozenset(), "", [], 0, 1)
        self.assertEqual([safe], d._filtrar_clear_fraco([weak, safe], safe_rows))

        low_rows = [0] * d.LINHAS
        low_rows[-d.ALTURA_ALVO + 1:] = [d.CHEIA] * (d.ALTURA_ALVO - 1)
        with patch.object(d, "MODO", "ataque"):
            self.assertGreater(d._custo_clear_pequeno(1, "", 0, low_rows), 0)
            # com a pilha em ALTURA_DOWNSTACK limpar deixa de ser desperdicio: baixa a pilha
            self.assertEqual(0, d._custo_clear_pequeno(1, "", 0, safe_rows))
        self.assertGreater(d._custo_quebra_b2b(1, "", 4, 12), 0)
        self.assertEqual(0, d._custo_quebra_b2b(1, "", 4, d.ALTURA_PERIGO))

    def test_simulator_counts_only_rows_with_received_garbage(self):
        garbage = {(r, c) for r in range(4) for c in range(d.COLUNAS) if c != 5}
        self.assertEqual(4, d._contar_linhas_lixo(garbage))
        self.assertEqual(0, d._contar_linhas_lixo({(r, c) for r, c in garbage if c < 4}))

    def test_reserve_play_keeps_the_bot_playing_when_no_move_is_evaluated(self):
        vazio = [0] * d.LINHAS
        reserva = d._jogada_reserva(vazio, "T")
        self.assertIsNotNone(reserva)
        self.assertEqual("T", reserva.letra)
        self.assertFalse(reserva.usar_hold)
        self.assertTrue(any(t[0] == "toque" and t[1] == "drop" for t in reserva.teclas))
        # tabuleiro cheio: nao existe colocacao legal, entao nao ha reserva
        self.assertIsNone(d._jogada_reserva([d.CHEIA] * d.LINHAS, "T"))


class TransitionTests(unittest.TestCase):
    def test_opener_phase_matches_the_fourteen_piece_game_window(self):
        self.assertEqual(14, d.JANELA_ABERTURA)

    def test_search_uses_bag_prediction_only_for_a_confirmed_opening(self):
        with patch.object(d, "_busca", wraps=d._busca) as search:
            d.decidir(frozenset(), "I", None, list("JOLTZ"), permitir_hold=True,
                      bag_seguinte="S")
        searched = [call.args[3] for call in search.call_args_list]
        self.assertIn(list("IJOLTZ"), searched)
        self.assertIn(list("JOLTZS"), searched)
        self.assertTrue(all(call.args[6] == d.LARGURA_PCO + (5,)
                            for call in search.call_args_list))

    def test_decision_search_keeps_the_unplayed_hold_piece_available(self):
        with patch.object(d, "_busca", wraps=d._busca) as search:
            d.decidir(frozenset(), "I", "T", list("JO"), permitir_hold=True,
                      abertura=False)
        reservas = [call.kwargs["reservas"] for call in search.call_args_list]
        self.assertIn(("T",), reservas)
        self.assertIn(("I",), reservas)

    def test_search_horizon_recognizes_opening_and_midgame_stacks(self):
        opening = [0] * d.LINHAS
        midgame = [0] * d.LINHAS
        midgame[15:] = [d.CHEIA ^ (1 << 9)] * 5
        self.assertEqual(3, d._horizonte_abertura(opening))
        self.assertEqual(3, d._horizonte_abertura(midgame))

        with patch.object(d, "_busca", wraps=d._busca) as search:
            d.decidir(frozenset(), "I", None, list("OTSZ"), permitir_hold=False)
        self.assertEqual(d.HORIZONTE_ABERTURA, len(search.call_args[0][3]))
        self.assertEqual(d.LARGURA_PCO, search.call_args[0][6])
        midgame_pile = frozenset((r, c) for r, mask in enumerate(midgame)
                                 for c in range(d.COLUNAS) if mask & (1 << c))
        with patch.object(d, "_busca", wraps=d._busca) as search:
            d.decidir(midgame_pile, "I", None, list("OJSZ"), permitir_hold=False)
        self.assertEqual(3, len(search.call_args[0][3]))

        with patch.object(d, "_busca", wraps=d._busca) as search:
            d.decidir(midgame_pile, "I", None, list("OTSZJ"), permitir_hold=False,
                      abertura=True)
        self.assertEqual(d.HORIZONTE_ABERTURA, len(search.call_args[0][3]))
        self.assertTrue(search.call_args[0][8])

    def test_dt_mold_requires_a_visible_t_and_an_opening_window(self):
        pile = frozenset((r + 13, c)
                         for r, row in enumerate(d.openers.DT_MOLDE)
                         for c, cell in enumerate(row) if cell == "X")
        placement = (0.0, [0] * d.LINHAS, 0, 0, 0, 3,
                     frozenset(), "", [], 0, 1)

        with patch.object(d, "OPENERS", True), \
                patch.object(d.openers, "ativo", return_value=True), \
                patch.object(d, "_busca", return_value=(0.0, placement)) as search:
            d.decidir(pile, "S", None, ["I"], permitir_hold=False, abertura=True)
            self.assertFalse(search.call_args.args[4][2])
            self.assertTrue(search.call_args.args[8])

            d.decidir(pile, "S", None, ["T"], permitir_hold=False, abertura=False)
            self.assertFalse(search.call_args.args[4][2])
            self.assertFalse(search.call_args.args[8])

            d.decidir(pile, "S", None, ["T"], permitir_hold=False, abertura=True)
            self.assertTrue(search.call_args.args[4][2])

    def test_search_only_rewards_i_when_it_remains_available_soon(self):
        empty = [0] * d.LINHAS
        placement = (0.0, empty, 0, 0, 0, 3, frozenset(), "", [], 0, 1)
        contexts = []

        with patch.dict(d._CACHE_AVALIAR, {}, clear=True), \
                patch.object(d, "_expandir", side_effect=[[placement], [placement]]), \
                patch.object(d, "_avaliar_rapido",
                             side_effect=lambda _board, _b2b, _combo, ctx:
                             contexts.append(ctx) or 0.0):
            d._busca(empty, 0, 0, ["I", "O"], (False, True, False), {},
                     larguras=(1,), abertura=False)
        self.assertTrue(contexts)
        self.assertFalse(any(ctx[1] for ctx in contexts))

        contexts.clear()
        with patch.dict(d._CACHE_AVALIAR, {}, clear=True), \
                patch.object(d, "_expandir", return_value=[placement]), \
                patch.object(d, "_avaliar",
                             side_effect=lambda _board, _b2b, _combo, ctx:
                             contexts.append(ctx) or 0.0):
            d._busca(empty, 0, 0, ["O"], (False, True, False), {},
                     abertura=False, reservas=("I",))
        self.assertTrue(contexts[0][1])

    def test_midgame_search_looks_one_piece_deeper_when_a_t_is_visible_and_safe(self):
        rows = [0] * d.LINHAS
        for row in range(d.LINHAS - 5, d.LINHAS):
            rows[row] = d.CHEIA ^ (1 << 9)
        pile = frozenset((r, c) for r, mask in enumerate(rows) for c in range(d.COLUNAS)
                         if mask & (1 << c))
        with patch.object(d, "_busca", wraps=d._busca) as search:
            d.decidir(pile, "I", None, list("TJO"), permitir_hold=False)
        self.assertEqual(4, len(search.call_args[0][3]))

        high = [0] * d.LINHAS
        for row in range(d.LINHAS - d.ALTURA_SEGURA, d.LINHAS):
            high[row] = d.CHEIA ^ (1 << 9)
        high_pile = frozenset((r, c) for r, mask in enumerate(high) for c in range(d.COLUNAS)
                              if mask & (1 << c))
        with patch.object(d, "_busca", wraps=d._busca) as search:
            d.decidir(high_pile, "I", None, list("TJO"), permitir_hold=False)
        self.assertEqual(3, len(search.call_args[0][3]))

    def test_line_clear_uses_tetrio_rounding_and_b2b_surge_rules(self):
        modo = d.MODO
        try:
            d.MODO = "ataque"
            self.assertEqual((0, 1, 0.0), d.transicao(0, 0, "", 1))
            self.assertEqual((0, 2, 0.0), d.transicao(0, 1, "", 1))
            self.assertEqual((0, 3, 1.0), d.transicao(0, 2, "", 1))
            self.assertEqual((0, 2, 1.0), d.transicao(0, 1, "", 2))
            self.assertEqual((0, 1, 2.0), d.transicao(0, 0, "", 3))
            self.assertEqual((1, 1, 4.0), d.transicao(0, 0, "", 4))
            self.assertEqual((2, 1, 5.0), d.transicao(1, 0, "", 4))
            self.assertEqual((0, 1, 4.0), d.transicao(4, 0, "", 1))
        finally:
            d.MODO = modo

    def test_all_spin_mini_is_difficult_and_perfect_clear_keeps_b2b(self):
        with patch.object(d, "MODO", "ataque"):
            self.assertEqual((2, 1, 1.0), d.transicao(1, 0, "mini", 1))
            self.assertEqual((4, 1, 1.0), d.transicao(3, 0, "", 1, perfect_clear=True))

    def test_planner_rewards_every_attack_that_the_game_sends(self):
        for value in (0.0, 1.0, 2.0, 4.0):
            self.assertEqual(value, d._valor_planejamento(2, "", value))
        self.assertEqual(12.0, d._valor_planejamento(2, "full", 12.0))

    def test_small_normal_clears_are_penalized_only_when_safe(self):
        board = [0] * d.LINHAS
        with patch.object(d, "MODO", "ataque"):
            self.assertGreater(d._custo_clear_pequeno(1, "", 0, board),
                               d._custo_clear_pequeno(2, "", 1, board))
            self.assertEqual(0.0, d._custo_clear_pequeno(1, "", 4, board))
            self.assertEqual(0.0, d._custo_clear_pequeno(1, "", 0, [d.CHEIA] * 11 + [0] * 9))
            self.assertEqual(0.0, d._custo_clear_pequeno(2, "full", 1, board))

    def test_zero_attack_mini_single_does_not_beat_a_nonclear_on_tie(self):
        empty = [0] * d.LINHAS
        mini = (0.0, empty, 1, 1, 0, 3, frozenset(), "mini", [], 1, 1)
        nonclear = (0.0, empty, 0, 0, 0, 3, frozenset(), "", [], 0, 1)
        with patch.object(d, "_expandir", return_value=[mini, nonclear]), \
                patch.object(d, "_avaliar", return_value=0.0), \
                patch.object(d, "MODO", "ataque"):
            result = d._busca(empty, 0, 0, ["T"], (True, False), {})
        self.assertEqual(0, result[1][9])

    def test_low_attack_combo_clear_does_not_beat_a_nonclear_on_tie(self):
        empty = [0] * d.LINHAS
        single = (0.0, empty, 0, 2, 0, 3, frozenset(), "", [], 1, 1)
        nonclear = (0.0, empty, 0, 0, 0, 3, frozenset(), "", [], 0, 1)
        with patch.object(d, "_expandir", return_value=[single, nonclear]), \
             patch.object(d, "_avaliar", return_value=0.0), \
             patch.object(d, "MODO", "ataque"):
            result = d._busca(empty, 0, 1, ["I"], (False, False), {})
        self.assertEqual(0, result[1][9])

    def test_perfect_clear_adds_attack_to_planner_move(self):
        rows = [0] * d.LINHAS
        rows[-1] = d.CHEIA ^ 15
        pile = frozenset((r, c) for r, mask in enumerate(rows) for c in range(d.COLUNAS)
                         if mask & (1 << c))
        with patch.object(d, "MODO", "ataque"):
            move = d.decidir(pile, "I", None, [], permitir_hold=False)
        self.assertEqual(1, move.limpas)
        self.assertFalse(d._limpar(pile | move.celulas)[0])
        self.assertEqual(d.BONUS_PERFECT_CLEAR, move.valor)
        self.assertEqual(5.0, d.BONUS_PERFECT_CLEAR)

    def test_pco_lookahead_finds_a_reachable_two_line_perfect_clear(self):
        rows = [0] * d.LINHAS
        rows[-2] = d.CHEIA ^ (3 << 2)
        rows[-1] = d.CHEIA ^ (3 << 1)
        cache = {}
        self.assertTrue(d._tem_perfect_clear_no_proximo(rows, "S", cache))
        self.assertFalse(d._tem_perfect_clear_no_proximo(rows, "I", cache))

    def test_perfect_clear_lookahead_recognizes_four_line_pc_outside_the_opener(self):
        rows = [0] * d.LINHAS
        rows[-4:] = [d.CHEIA ^ (1 << 9)] * 4

        self.assertTrue(d._tem_perfect_clear_no_proximo(rows, "I", {}))
        self.assertFalse(d._tem_perfect_clear_no_proximo(rows, "O", {}))

    def test_four_line_perfect_clear_receives_its_attack_bonus(self):
        rows = [0] * d.LINHAS
        rows[-4:] = [d.CHEIA ^ (1 << 9)] * 4

        with patch.object(d, "MODO", "ataque"):
            candidates = d._expandir(rows, 0, 0, "I", {}, True)

        self.assertTrue(any(c[9] == 4 and not any(c[1])
                            and c[0] == d.ATAQUE_BASE[4] + d.BONUS_PERFECT_CLEAR
                            for c in candidates))

    def test_midgame_beam_rewards_a_perfect_clear_available_from_visible_pieces(self):
        board = [0] * d.LINHAS
        board[-1] = 1
        placement = (0.0, board, 0, 0, 0, 3, frozenset(), "", [], 0, 1)
        scores = []

        for pc_reachable in (False, True):
            with patch.dict(d._CACHE_AVALIAR, {}, clear=True), \
                    patch.object(d, "_expandir", return_value=[placement]), \
                    patch.object(d, "_avaliar_rapido", return_value=0.0), \
                    patch.object(d, "_avaliar", return_value=0.0), \
                    patch.object(d, "_tem_perfect_clear_no_proximo",
                                 return_value=pc_reachable):
                result = d._busca(board, 0, 0, ["O", "I"], (False, False, False), {},
                                 larguras=(1,), abertura=False)
            scores.append(result[0])

        self.assertAlmostEqual(d.BONUS_PCO_POTENCIAL, scores[1] - scores[0])

    def test_tspin_setup_bonus_requires_a_reachable_full_spin_clear_and_visible_t(self):
        rows = [0] * d.LINHAS
        rows[-1] = d.CHEIA ^ 1
        full_spin = (0, 0, 19, [1], (), "full", [])
        mini_spin = (0, 0, 19, [1], (), "mini", [])

        with patch.object(d, "_tem_tspin_clear_geometrico", return_value=True), \
                patch.object(d, "colocacoes_alcancaveis", return_value=[full_spin]):
            bonus = _bonus_setup(rows, ("T",), 0, 0, {})
        self.assertGreater(bonus, d.BONUS_TSPIN_SETUP[1])
        self.assertEqual(0.0, _bonus_setup(rows, ("I",), 0, 0, {}))

        with patch.object(d, "_tem_tspin_clear_geometrico", return_value=True), \
                patch.object(d, "colocacoes_alcancaveis", return_value=[mini_spin]):
            self.assertEqual(0.0, _bonus_setup(rows, ("T",), 0, 0, {}))

        with patch.object(d, "_tem_tspin_clear_geometrico", return_value=False), \
                patch.object(d, "colocacoes_alcancaveis") as placements:
            self.assertEqual(0.0, _bonus_setup(rows, ("T",), 0, 0, {}))
        placements.assert_not_called()

    def test_tspin_setup_finds_reachable_clear_before_the_tsd_slot_is_complete(self):
        rows = [0] * d.LINHAS
        rows[17] = 1 << 3
        rows[18] = (d.CHEIA ^ (7 << 3)) & ~(1 << 0)
        rows[19] = d.CHEIA ^ (1 << 4)
        self.assertEqual(0, d._slots_detalhes(rows)[0])
        placements = d.colocacoes_alcancaveis(rows, "T")
        self.assertTrue(any(
            spin == "full" and d._aplicar(rows, y, masks)[1] > 0
            for _, _, y, masks, _, spin, _ in placements
        ))
        quick_placements = d.colocacoes_alcancaveis(
            rows, "T", parar_no_primeiro_tspin_clear=True)
        self.assertEqual(1, len(quick_placements))
        self.assertEqual("full", quick_placements[0][5])

        bonus = _bonus_setup(rows, ("T",), 0, 0, {})

        self.assertGreater(bonus, d.BONUS_TSPIN_SETUP_STAGE[1])

    def test_tspin_setup_bonus_scales_with_visible_setup_stage(self):
        rows = [0] * d.LINHAS
        with patch.object(d, "_slots_detalhes", return_value=(0, 1, 1)):
            stage_one = _bonus_setup(rows, ("T",), 0, 0, {})
        with patch.object(d, "_slots_detalhes", return_value=(0, 2, 1)):
            stage_two = _bonus_setup(rows, ("T",), 0, 0, {})

        self.assertEqual(d.BONUS_TSPIN_SETUP_STAGE[1], stage_one)
        self.assertEqual(d.BONUS_TSPIN_SETUP_STAGE[2], stage_two)
        self.assertGreater(stage_two, stage_one)

    def test_tspin_setup_detection_uses_reachable_srs_full_double(self):
        rows = [0] * d.LINHAS
        rows[17] = 1 << 3
        rows[18] = d.CHEIA ^ (7 << 3)
        rows[19] = d.CHEIA ^ (1 << 4)
        self.assertEqual(1, d._slots_detalhes(rows)[0])

        bonus = _bonus_setup(rows, ("T",), 0, 0, {})

        self.assertGreater(bonus, 0.0)

    def test_beam_rewards_an_srs_reachable_tspin_setup_with_a_visible_t(self):
        rows = [0] * d.LINHAS
        rows[17] = 1 << 3
        rows[18] = d.CHEIA ^ (7 << 3)
        rows[19] = d.CHEIA ^ (1 << 4)
        placement = (0.0, rows, 0, 0, 0, 3, frozenset(), "", [], 0, 1)

        def score(next_piece):
            with patch.dict(d._CACHE_AVALIAR, {}, clear=True), \
                    patch.object(d, "_expandir", return_value=[placement]), \
                    patch.object(d, "_avaliar_rapido", return_value=0.0), \
                    patch.object(d, "_avaliar", return_value=0.0):
                return d._busca(rows, 0, 0, ["O", next_piece],
                                (True, False, False), {}, larguras=(1,),
                                abertura=False)[0]

        setup_bonus = _bonus_setup(rows, ("T",), 0, 0, {})

        self.assertGreater(setup_bonus, 0.0)
        self.assertGreater(score("T"), score("S"))

    def test_beam_finds_reachable_tspin_clear_outside_the_tsd_slot_pattern(self):
        rows = [0] * d.LINHAS
        rows[15:20] = [32, 967, 109, 908, 696]
        self.assertEqual(0, d._slots_detalhes(rows)[0])
        placement = (0.0, rows, 0, 0, 0, 3, frozenset(), "", [], 0, 1)

        def score(next_piece):
            with patch.dict(d._CACHE_AVALIAR, {}, clear=True), \
                    patch.object(d, "_expandir", return_value=[placement]), \
                    patch.object(d, "_avaliar_rapido", return_value=0.0), \
                    patch.object(d, "_avaliar", return_value=0.0):
                return d._busca(rows, 0, 0, ["O", next_piece],
                                (True, False, False), {}, larguras=(1,),
                                abertura=False)[0]

        setup_bonus = _bonus_setup(rows, ("T",), 0, 0, {})

        self.assertGreater(setup_bonus, 0.0)
        self.assertAlmostEqual(setup_bonus, score("T") - score("S"))

    def test_beam_limits_generic_tspin_checks_to_top_candidates(self):
        empty = [0] * d.LINHAS
        placement = (0.0, empty, 0, 0, 0, 3, frozenset(), "", [], 0, 1)
        placements = [placement] * 5

        with patch.dict(d._CACHE_AVALIAR, {}, clear=True), \
                patch.object(d, "_expandir", return_value=placements), \
                patch.object(d, "_avaliar_rapido", return_value=0.0), \
                patch.object(d, "_avaliar", return_value=0.0), \
                patch.object(d, "_tem_tspin_clear_geometrico", return_value=True), \
                patch.object(d, "_bonus_tspin_clear", return_value=0.0) as tspin_clear:
            d._busca(empty, 0, 0, ["O", "S", "T"], (True, False, False), {},
                     larguras=(1, 1), abertura=False)

        self.assertEqual(d.TSPIN_SETUP_CANDIDATES, tspin_clear.call_count)

    def test_beam_limits_geometric_tspin_prefilter_candidates(self):
        empty = [0] * d.LINHAS
        placements = []
        for col in range(5):
            board = empty.copy()
            board[-1] = 1 << col
            placements.append((0.0, board, 0, 0, 0, 3, frozenset(), "", [], 0, 1))

        with patch.dict(d._CACHE_AVALIAR, {}, clear=True), \
                patch.object(d, "_expandir", return_value=placements), \
                patch.object(d, "_avaliar_rapido", return_value=0.0), \
                patch.object(d, "_avaliar", return_value=0.0), \
                patch.object(d, "_tem_tspin_clear_geometrico", return_value=False) as geometry, \
                patch.object(d, "_bonus_tspin_clear") as tspin_clear:
            d._busca(empty, 0, 0, ["O", "T"], (True, False, False), {},
                     larguras=(1,), abertura=False)

        self.assertEqual(d.TSPIN_SETUP_GEOMETRY_CANDIDATES, geometry.call_count)
        tspin_clear.assert_not_called()

    def test_simulation_counts_perfect_clear_attack_bonus(self):
        move = d.Jogada(False, "I", 0, 0, frozenset(), [], "", 1, 0, 1, 0.0)
        with patch.object(d, "MODO", "ataque"):
            with patch.object(d, "decidir", return_value=move):
                with patch.object(d, "_limpar", return_value=(frozenset(), 1)):
                    result = d.simular(1, verificar=False)
        self.assertEqual(d.BONUS_PERFECT_CLEAR, result["valor"])

    def test_simulation_propagates_type_errors_from_decision_callback(self):
        def faulty_decider(*args):
            if len(args) >= 7:
                raise TypeError("decision implementation failed")
            return None

        with self.assertRaisesRegex(TypeError, "decision implementation failed"):
            d.simular(1, decidir_fn=faulty_decider, verificar=False)

    def test_simulation_keeps_legacy_four_argument_decision_callback(self):
        calls = []

        def legacy_decider(pilha, mao, hold, proximas):
            calls.append((pilha, mao, hold, proximas))
            return None

        result = d.simular(1, decidir_fn=legacy_decider, verificar=False)

        self.assertEqual(1, len(calls))
        self.assertFalse(result["vivo"])

    def test_simulation_reports_stack_attack_and_abandoned_opener_metrics(self):
        def no_clear(*args):
            return d.Jogada(False, args[1], 0, 0, frozenset(), [], "", 0, 0, 0, 0.0)

        with patch.object(d, "MODO", "ataque"), patch.object(d, "OPENERS", True), \
                patch.object(d.openers, "ativo", side_effect=[True] + [False] * 6):
            result = d.simular(6, decidir_fn=no_clear, verificar=False)

        cleared_lines = (result["singles"] + 2 * result["doubles"]
                         + 3 * result["triples"] + 4 * result["quads"])
        self.assertEqual(result["linhas"], cleared_lines)
        self.assertEqual(1, result["opener_attempts"])
        self.assertEqual(1, result["opener_abandoned"])
        self.assertEqual(0, result["max_height"])
        self.assertEqual(0, result["max_holes"])
        self.assertEqual(0, result["valid_spins"])

    def test_simulation_reports_offline_equivalent_attack_and_quality_metrics(self):
        move = d.Jogada(False, "I", 0, 0, frozenset(), [], "", 0, 0, 0, 0.0)
        with patch.object(d, "decidir", return_value=move):
            result = d.simular(1, verificar=False)

        self.assertGreater(result["tempo_s"], 0.0)
        self.assertGreater(result["pps_equivalente"], 0.0)
        self.assertEqual(0.0, result["apm_equivalente"])
        self.assertEqual(0.0, result["app"])
        self.assertEqual(0.0, result["buracos_medios"])
        self.assertEqual(0.0, result["b2b_mantido_pct"])

    def test_simulation_does_not_count_tst_after_opener_left_its_mold(self):
        moves = [
            d.Jogada(False, "T", 0, 0, frozenset(), [], "full", 2, 1, 1, 0.0),
            d.Jogada(False, "T", 0, 0, frozenset(), [], "full", 3, 2, 2, 0.0),
        ]

        def decider(*_args):
            return moves.pop(0)

        with patch.object(d, "MODO", "ataque"), patch.object(d, "OPENERS", True), \
                patch.object(d, "_limpar", side_effect=[(frozenset(), 2), (frozenset(), 3)]), \
                patch.object(d.openers, "ativo", side_effect=[True, False, True]):
            result = d.simular(2, decidir_fn=decider, verificar=False)

        self.assertEqual(1, result["opener_abandoned"])

    def test_i_piece_is_saved_when_safe_and_prefers_wall_or_supported_flat_landing(self):
        empty = [0] * d.LINHAS
        vertical_center = (0.0, empty, 0, 0, 1, 4, frozenset((r, 4) for r in range(10, 14)), "", [], 0, 1)
        vertical_wall = (0.0, empty, 0, 0, 1, 0, frozenset((r, 0) for r in range(10, 14)), "", [], 0, 1)
        self.assertLess(d._ajuste_i("I", vertical_center, empty), 0)
        self.assertGreater(d._ajuste_i("I", vertical_wall, empty),
                           d._ajuste_i("I", vertical_center, empty))

        supported = [0] * d.LINHAS
        supported[-1] = 15 << 3
        flat = (0.0, supported, 0, 0, 0, 3,
                frozenset((18, c) for c in range(3, 7)), "", [], 0, 1)
        self.assertGreater(d._ajuste_i("I", flat, supported),
                           d._ajuste_i("I", vertical_center, empty))

    def test_tsd_setup_hole_is_not_penalized_as_an_accidental_buried_hole(self):
        slot = [0] * d.LINHAS
        slot[-1] = d.CHEIA ^ (1 << 4)
        slot[-2] = d.CHEIA ^ (7 << 3)
        self.assertEqual(1, d._slots_detalhes(slot)[2])
        self.assertEqual((0, 2), d._slots_detalhes(slot)[:2])
        self.assertFalse(hasattr(d, "BONUS_HOLD_T"))   # nao premia mais segurar o T

    def test_accidental_buried_hole_is_not_forgiven_by_an_unrelated_tsd_slot(self):
        open_slot = [0] * d.LINHAS
        open_slot[-1] = d.CHEIA ^ (1 << 4)
        open_slot[-2] = d.CHEIA ^ (7 << 3)

        buried_hole = open_slot[:]
        buried_hole[-2] &= ~1
        buried_hole[-3] |= 1

        clean_score = d._avaliar(open_slot, 0, 0, (True, False))
        buried_score = d._avaliar(buried_hole, 0, 0, (True, False))
        self.assertLess(buried_score, clean_score)
        self.assertLess(buried_score, clean_score - d.PESO_VALOR * d.ATAQUE_BASE[4])

    def test_new_hole_moves_are_filtered_when_a_clean_placement_exists(self):
        empty = [0] * d.LINHAS
        creates_hole = empty[:]
        creates_hole[10] = 1
        clean = empty[:]
        clean[-1] = 1
        candidates = [(0.0, creates_hole), (0.0, clean)]

        self.assertEqual([candidates[1]], d._filtrar_buracos_novos(empty, candidates))

    def test_holes_and_overhang_blocks_are_counted_separately(self):
        board = [0] * d.LINHAS
        board[10] = 1

        self.assertEqual(9, d._buracos(board))
        self.assertEqual(1, d._sobreposicoes(board))

    def test_dependency_counts_piece_types_with_clean_fits(self):
        self.assertEqual(5, d._pecas_que_encaixam_sem_buraco(tuple([0] * d.LINHAS)))

    def test_wide_wells_are_penalized_but_one_wide_wells_are_not(self):
        one_wide = [0, 0, 4, 1, 4, 0, 0, 0, 0, 0]
        two_wide = [0, 0, 4, 1, 1, 4, 0, 0, 0, 0]

        self.assertEqual(0.0, d._penalidade_pocos_largos(one_wide))
        self.assertGreater(d._penalidade_pocos_largos(two_wide), 0.0)

    def test_search_beam_keeps_a_bounded_set_of_alternatives(self):
        self.assertEqual(3, len(d.LARGURA))
        self.assertEqual(5, len(d.LARGURA_PCO))
        self.assertTrue(all(1 <= width <= 10 for width in d.LARGURA))
        self.assertTrue(all(1 <= width <= 10 for width in d.LARGURA_PCO))

    def test_beam_counts_piece_dependency_only_at_leaf_states(self):
        board = [0] * d.LINHAS
        placement = (0.0, board, 0, 0, 0, 3, frozenset(), "", [], 0, 1)

        with patch.dict(d._CACHE_AVALIAR, {}, clear=True), \
                patch.object(d, "_expandir", return_value=[placement]), \
                patch.object(d, "_pecas_que_encaixam_sem_buraco",
                             wraps=d._pecas_que_encaixam_sem_buraco) as dependency:
            d._busca(board, 0, 0, ["I", "O"], (True, False, False), {},
                     larguras=(1,), abertura=False)

        self.assertEqual(1, dependency.call_count)

    def test_beam_exactly_checks_dependency_for_only_top_leaf_candidates_per_root(self):
        empty = [0] * d.LINHAS
        root = (0.0, empty, 0, 0, 0, 3, frozenset(), "", [], 0, 1)
        leaves = []
        for col in range(8):
            board = empty.copy()
            board[-1] = 1 << col
            leaves.append((0.0, board, 0, 0, 0, 3, None, "", None, 0, 1))

        with patch.dict(d._CACHE_AVALIAR, {}, clear=True), \
                patch.object(d, "_expandir", side_effect=[[root], leaves]), \
                patch.object(d, "_avaliar_rapido", return_value=0.0), \
                patch.object(d, "_penalidade_dependencia", return_value=0.0) as dependency:
            d._busca(empty, 0, 0, ["O", "I"], (False, False), {},
                     larguras=(1,), abertura=False)

        self.assertEqual(d.FINAL_EXACT_CANDIDATES_PER_ROOT, dependency.call_count)

    def test_opening_prefers_flat_s_and_z_placements_on_empty_board(self):
        for letter, queue in (("S", "OTILO"), ("Z", "TISLJ")):
            with self.subTest(letter=letter):
                move = d.decidir(frozenset(), letter, None, list(queue),
                                 permitir_hold=False, abertura=True)
                self.assertEqual(0, move.giros % 2, (letter, move.giros, move.coluna_caixa))

    def test_tsd_slot_keeps_value_without_a_visible_t(self):
        slot = [0] * d.LINHAS
        slot[-1] = d.CHEIA ^ (1 << 4)
        slot[-2] = d.CHEIA ^ (7 << 3)
        self.assertGreater(d._avaliar(slot, 0, 0, (False, False)),
                           d._avaliar([0] * d.LINHAS, 0, 0, (False, False)))

    def test_board_evaluation_punishes_tetris_wells_and_rewards_tsd_slots(self):
        well = [0] * d.LINHAS
        for row in range(d.LINHAS - 4, d.LINHAS):
            well[row] = d.CHEIA ^ (1 << (d.COLUNAS - 1))
        empty = [0] * d.LINHAS
        # poco de 1 coluna = dependencia de I: agora custa, nao rende
        self.assertLess(d._avaliar(well, 0, 0, (False, True)),
                        d._avaliar(empty, 0, 0, (False, True)))

        slot = [0] * d.LINHAS
        slot[-1] = d.CHEIA ^ (1 << 4)
        slot[-2] = d.CHEIA ^ (7 << 3)
        self.assertGreater(d._avaliar(slot, 0, 0, (True, False)),
                           d._avaliar(empty, 0, 0, (True, False)))

    def test_attack_evaluation_punishes_i_dependencies(self):
        four_wide = [0] * d.LINHAS
        three_wide = [0] * d.LINHAS
        com_base = [0] * d.LINHAS
        four_open = sum(1 << col for col in (3, 4, 5, 6))
        three_open = sum(1 << col for col in (3, 4, 5))
        lane = 15 << 3
        for row in range(d.LINHAS - 10, d.LINHAS):
            four_wide[row] = d.CHEIA ^ four_open                 # canal vazio, sem base
            com_base[row] = (d.CHEIA ^ lane) | (lane & ~(1 << 6))   # tres residuais por camada
        for row in range(d.LINHAS - 3, d.LINHAS):
            three_wide[row] = d.CHEIA ^ three_open
        modo = d.MODO
        try:
            d.MODO = "ataque"
            vazio = d._avaliar([0] * d.LINHAS, 0, 0, (False, False))
            quatro = d._avaliar(four_wide, 0, 0, (False, False))
            base = d._avaliar(com_base, 0, 0, (False, False))
            tres = d._avaliar(three_wide, 0, 0, (False, False))
            # dependencia de I nao e mais premiada: canal 4-wide e poco custam
            self.assertLess(base, vazio)
            self.assertLess(quatro, vazio)
            self.assertLess(tres, vazio)

            narrow = [0] * d.LINHAS
            for row in range(d.LINHAS - 10, d.LINHAS):
                narrow[row] = d.CHEIA ^ (1 << 4)
            self.assertGreater(d._penalidade_pocos_um(d._altura_colunas(narrow)), 0.0)
        finally:
            d.MODO = modo

    def test_decision_uses_the_best_search_result_instead_of_a_forced_clear(self):
        lane = 15 << 3
        sides = d.CHEIA ^ lane
        rows = [0] * d.LINHAS
        for row in range(d.LINHAS - 10, d.LINHAS):
            rows[row] = sides | (lane & ~(1 << 6))
        pile = frozenset((r, c) for r, mask in enumerate(rows) for c in range(d.COLUNAS)
                         if mask & (1 << c))
        board_after = rows[:]
        single = (0.0, board_after, 0, 1, 0, 3,
                  frozenset({(19, 6), (10, 0), (10, 1), (10, 2)}), "", [], 1, 1)
        tetris = (0.0, board_after, 0, 0, 0, 3,
                  frozenset((r, 6) for r in range(16, 20)), "", [], 4, 1)
        with patch.object(d, "_busca", side_effect=[(10.0, tetris), (0.0, single)]):
            move = d.decidir(pile, "T", "I", ["J"], combo=0, abertura=False)
        # vale a melhor busca (o tetris), nao um clear forcado
        self.assertFalse(move.usar_hold)
        self.assertEqual(4, move.limpas)

    def test_early_tki_bonus_only_rewards_a_full_t_spin_double(self):
        self.assertEqual(d.BONUS_TKI_CLEAR, d._bonus_clear_abertura("full", 2))
        self.assertEqual(0.0, d._bonus_clear_abertura("mini", 2))
        self.assertEqual(0.0, d._bonus_clear_abertura("full", 1))

    def test_dt_cannon_bonus_requires_opening_tsd_then_reachable_full_tst(self):
        self.assertEqual(d.BONUS_DT_CANNON, d._bonus_dt_cannon(True, True, "T", "full", 3))
        self.assertEqual(0.0, d._bonus_dt_cannon(False, True, "T", "full", 3))
        self.assertEqual(0.0, d._bonus_dt_cannon(True, False, "T", "full", 3))
        self.assertEqual(0.0, d._bonus_dt_cannon(True, True, "T", "mini", 3))
        self.assertEqual(0.0, d._bonus_dt_cannon(True, True, "T", "full", 2))
        self.assertEqual(0.0, d._bonus_dt_cannon(True, True, "I", "full", 3))

    def test_opening_search_carries_tsd_history_to_the_later_tst(self):
        empty = [0] * d.LINHAS
        tsd = (0.0, empty, 0, 1, 0, 3, frozenset(), "full", [], 2, 1)
        tst = (0.0, empty, 0, 1, 0, 3, None, "full", [], 3, 1)
        ordinary = (0.0, empty, 0, 0, 0, 3, None, "", [], 0, 1)
        with patch.object(d, "LARGURA", (1, 1, 1)):
            with patch.object(d, "_expandir", side_effect=[[tsd], [ordinary], [tst], [ordinary]]):
                with patch.object(d, "_avaliar", return_value=0.0):
                    with patch.object(d, "_bonus_dt_cannon", wraps=d._bonus_dt_cannon) as bonus:
                        d._busca(empty, 0, 0, ["T", "I", "T", "O"],
                                 (True, True), {}, abertura=True)
        self.assertIn((True, True, "T", "full", 3),
                      [call.args for call in bonus.call_args_list])

    def test_opening_search_prefers_an_early_full_t_spin_double(self):
        empty = [0] * d.LINHAS
        tsd = (0.0, empty, 0, 0, 0, 3, frozenset(), "full", [], 2, 1)
        ordinary = (0.0, empty, 0, 0, 0, 3, frozenset(), "", [], 0, 1)
        continuation = (0.0, empty, 0, 0, 0, 3, None, "", [], 0, 1)
        with patch.object(d, "LARGURA", (1, 1, 1)):
            with patch.object(d, "_expandir", side_effect=[[ordinary, tsd], [continuation],
                                                           [continuation], [continuation]]):
                with patch.object(d, "_avaliar", return_value=0.0):
                    _, chosen = d._busca(empty, 0, 0, ["T", "I", "O", "S"],
                                         (True, True), {}, abertura=True)
        self.assertIs(tsd, chosen)

    def test_tki_and_dt_opening_bonuses_need_tspin_pieces_and_slots(self):
        board = [0] * d.LINHAS
        with patch.object(d, "_slots_detalhes", return_value=(0, 2, 1)):
            self.assertGreater(d._bonus_abertura_tsd(board, (True, True)), 0)
            sem_t = d._bonus_abertura_tsd(board, (False, True))
            self.assertGreater(sem_t, 0.0)
            self.assertLess(sem_t, d._bonus_abertura_tsd(board, (True, True)))

        with patch.object(d, "_slots_detalhes", return_value=(0, 2, 2)):
            self.assertGreater(d._bonus_abertura_tsd(board, (True, True)),
                               d._bonus_abertura_tsd(board, (True, False)))

    def test_empty_hold_counts_both_spawned_pieces_toward_the_opener_window(self):
        bot = j.Bot(SimpleNamespace(), lambda _msg: None, seco=True)
        bot.rodada_confirmada = True
        tracker = SimpleNamespace(fila=tuple("IJOTS"), avancos=0,
                                  hold_estado="vazio", mao="S", hold_peca=None,
                                  texto_hold=lambda: "-")
        board = SimpleNamespace(pilha=frozenset(), voo=None)
        move = d.Jogada(True, "J", 0, 3, frozenset(),
                        [("toque", "hold"), ("toque", "drop")], "", 0, 0, 0, 0.0)
        with patch.object(j.decisor, "decidir", return_value=move):
            self.assertTrue(bot.passo(tracker, board, 1))
        self.assertEqual(2, bot.pecas_abertura)
        self.assertEqual(2, bot.alvo)

    def test_opening_saves_t_until_a_tspin_clear_is_available(self):
        empty = [0] * d.LINHAS
        ordinary_move = (0.0, empty, 0, 0, 0, 3, frozenset(), "", [], 0, 1)
        tspin_clear = (0.0, empty, 0, 0, 0, 3, frozenset(), "full", [], 2, 1)

        with patch.object(d, "_busca", side_effect=[(10.0, ordinary_move),
                                                     (20.0, ordinary_move)]):
            move = d.decidir(frozenset(), "S", "T", ["J"], abertura=True)
        self.assertFalse(move.usar_hold)
        self.assertEqual("S", move.letra)

        with patch.object(d, "_busca", side_effect=[(10.0, ordinary_move),
                                                     (10.0, ordinary_move)]):
            move = d.decidir(frozenset(), "T", None, ["S"], abertura=True)
        self.assertTrue(move.usar_hold)
        self.assertEqual("S", move.letra)

        with patch.object(d, "_busca", side_effect=[(10.0, ordinary_move),
                                                     (20.0, tspin_clear)]):
            move = d.decidir(frozenset(), "S", "T", ["J"], abertura=True)
        self.assertTrue(move.usar_hold)
        self.assertEqual("T", move.letra)

    def test_combo_does_not_force_clears_even_if_their_board_score_is_lower(self):
        nonclear = (0.0, [1] + [0] * (d.LINHAS - 1), 0, 0, 0, 3,
                    frozenset(), "", [], 0, 1)
        clear = (0.0, [2] + [0] * (d.LINHAS - 1), 0, 1, 0, 3,
                 frozenset(), "", [], 1, 1)
        defesa = [0] * (d.LINHAS - 12) + [0b1000] * 12   # combo so força em defesa
        with patch.object(d, "_expandir", return_value=[nonclear, clear]), \
             patch.object(d, "_avaliar", side_effect=lambda board, *_args: 1000 if board[0] == 1 else 0):
            result = d._busca(defesa, 0, 3, ["I"], (False, False), {},
                              abertura=False)
        self.assertEqual(0, result[1][9])

    def test_combo_does_not_force_hold_clear_over_better_current_move(self):
        nonclear = (0.0, [0] * d.LINHAS, 0, 0, 0, 3, frozenset(), "", [], 0, 1)
        clear = (0.0, [0] * d.LINHAS, 0, 1, 0, 3, frozenset(), "", [], 1, 1)
        defesa = frozenset((r, c) for r in range(8, d.LINHAS) for c in range(d.COLUNAS))
        with patch.object(d, "_busca", side_effect=[(100.0, nonclear), (0.0, clear)]):
            move = d.decidir(defesa, "S", "I", ["J"], combo=3, abertura=False)
        self.assertFalse(move.usar_hold)
        self.assertEqual(0, move.limpas)

    def test_safe_b2b_does_not_break_for_a_clear_that_sends_no_attack(self):
        empty = [0] * d.LINHAS
        cleared = empty[:]
        cleared[-1] = 1
        stacked = empty[:]
        stacked[-1] = 3
        single = (0.0, cleared, 0, 1, 0, 3, frozenset(), "", [], 1, 1)
        nonclear = (0.0, stacked, 2, 0, 0, 3, frozenset(), "", [], 0, 1)
        with patch.object(d, "_expandir", return_value=[single, nonclear]), \
             patch.object(d, "_avaliar",
                          side_effect=lambda board, *_args: 100.0 if board[-1] == 1 else 0.0):
            result = d._busca(empty, 2, 0, ["I"], (False, False), {})
        self.assertEqual(0, result[1][9])
        self.assertEqual(2, result[1][2])

    def test_b2b_may_break_when_the_stack_is_dangerously_high(self):
        board = [0] * d.LINHAS
        board[d.LINHAS - d.ALTURA_PERIGO] = 1
        candidates = [
            (0.0, board, 0, 1, 0, 3, frozenset(), "", [], 1, 1),
            (0.0, board, 2, 0, 0, 3, frozenset(), "", [], 0, 1),
        ]
        self.assertEqual(candidates, d._filtrar_quebra_b2b(candidates, 2, board))

    def test_height_18_is_not_a_game_over_threshold(self):
        self.assertEqual(d.LINHAS - 3, d.ALTURA_PERIGO)

        weak_clear = (0.0, [1] + [0] * (d.LINHAS - 1), 0, 1, 0, 3,
                      frozenset(), "", [], 1, 1)
        nonclear = (0.0, [2] + [0] * (d.LINHAS - 1), 0, 0, 0, 3,
                    frozenset(), "", [], 0, 1)
        below_emergency = [0] * d.LINHAS
        below_emergency[d.LINHAS - (d.ALTURA_PERIGO - 1)] = 1
        in_emergency = [0] * d.LINHAS
        in_emergency[d.LINHAS - d.ALTURA_PERIGO] = 1

        self.assertEqual([nonclear], d._filtrar_clear_fraco(
            [weak_clear, nonclear], below_emergency))
        self.assertEqual([weak_clear, nonclear], d._filtrar_clear_fraco(
            [weak_clear, nonclear], in_emergency))

        rows = [0] * d.LINHAS
        for row in range(2, d.LINHAS):
            rows[row] = d.CHEIA ^ (1 << 9)
        pile = frozenset((r, c) for r, mask in enumerate(rows)
                         for c in range(d.COLUNAS) if mask & (1 << c))
        move = d.decidir(pile, "I", None, [], permitir_hold=False, abertura=False)
        self.assertEqual(18, d._altura_max(rows))
        self.assertIsNotNone(move)
        self.assertEqual(4, move.limpas)

    def test_planner_takes_tetris_and_tsd_when_they_are_ready(self):
        with patch.object(d, "MODO", "ataque"):
            well = [0] * d.LINHAS
            for row in range(d.LINHAS - 4, d.LINHAS):
                well[row] = d.CHEIA ^ (1 << (d.COLUNAS - 1))
            well_pile = frozenset((r, c) for r, mask in enumerate(well) for c in range(d.COLUNAS)
                                  if mask & (1 << c))
            tetris = d.decidir(well_pile, "I", None, [], permitir_hold=False)
            self.assertEqual(4, tetris.limpas)

            slot = [0] * d.LINHAS
            slot[-1] = d.CHEIA ^ (1 << 4)
            slot[-2] = d.CHEIA ^ (7 << 3)
            slot[-3] = 1 << 3
            self.assertEqual((1, 3), d._slots_detalhes(slot)[:2])
            slot_pile = frozenset((r, c) for r, mask in enumerate(slot) for c in range(d.COLUNAS)
                                  if mask & (1 << c))
            tspin = d.decidir(slot_pile, "T", None, [], permitir_hold=False)
            self.assertEqual(("full", 2), (tspin.spin, tspin.limpas))


class BoardLayoutTests(unittest.TestCase):
    @staticmethod
    def _monitor():
        return {"left": 0, "top": 0, "width": tp.SCREEN_REF_W, "height": tp.SCREEN_REF_H}

    def test_debug_board_saves_raw_capture_before_classification(self):
        previous_layout = tp.LAYOUT_NAME
        tp.configurar_layout("esquerda", self._monitor())
        frame = np.zeros((480, 514, 3), dtype=np.uint8)
        saved = []

        with patch.object(tp, "espera"), \
                patch.object(tp, "capturar", return_value=frame), \
                patch.object(tp, "salvar_png", side_effect=lambda tools, image, path: saved.append(path)), \
                patch.object(tp, "classify_board", side_effect=ValueError("test failure")):
            try:
                with self.assertRaisesRegex(ValueError, "test failure"):
                    tp.modo_debug_board(None, None, 0)
            finally:
                tp.configurar_layout("centro", self._monitor())
                tp.LAYOUT_NAME = previous_layout

        self.assertEqual(["debug_frame_bruto.png", "debug_board_bruto.png"], saved)

    def test_center_layout_remains_the_default(self):
        previous_layout = tp.LAYOUT_NAME
        tp.configurar_layout("centro", self._monitor())
        self.assertEqual((562, 135, 802, 617), tp.CAIXA_TABULEIRO)
        self.assertEqual((495, 196), tp.HOLD)
        self.assertEqual(877, tp.SLOT_X)
        tp.LAYOUT_NAME = previous_layout

    def test_left_layout_selects_the_versus_board_and_its_panels(self):
        previous_layout = tp.LAYOUT_NAME
        tp.configurar_layout("esquerda", self._monitor())
        self.assertEqual((303, 135, 544, 615), tp.CAIXA_TABULEIRO)
        self.assertEqual((225, 196), tp.HOLD)
        self.assertEqual(609, tp.SLOT_X)
        tp.LAYOUT_NAME = previous_layout

    def test_first_capture_without_an_option_uses_center_coordinates(self):
        monitor = self._monitor()
        previous_layout = tp.LAYOUT_NAME
        regions = []

        class FakeCapture:
            monitors = [None, monitor]

            def grab(self, region):
                regions.append(region)
                return np.zeros((region["height"], region["width"], 4), dtype=np.uint8)

        try:
            tp.LAYOUT_NAME = None
            frame = tp.capturar(FakeCapture())
            self.assertEqual("centro", tp.LAYOUT_NAME)
            self.assertEqual((482, 512, 3), frame.shape)
            self.assertEqual({
                "left": 430, "top": 135, "width": 512, "height": 482,
            }, regions[0])
        finally:
            tp.configurar_layout("centro", monitor)
            tp.LAYOUT_NAME = previous_layout

    def test_left_option_captures_a_nonempty_region(self):
        monitor = self._monitor()
        previous_layout = tp.LAYOUT_NAME
        regions = []

        class FakeCapture:
            monitors = [None, monitor]

            def grab(self, region):
                regions.append(region)
                return np.zeros((region["height"], region["width"], 4), dtype=np.uint8)

        try:
            tp.configurar_layout("esquerda", monitor)
            frame = tp.capturar(FakeCapture())
            self.assertEqual({
                "left": 160, "top": 135, "width": 514, "height": 480,
            }, regions[0])
            self.assertEqual((480, 514, 3), frame.shape)
            board = tp.recortar(frame, tp.CAIXA_TABULEIRO)
            self.assertEqual((480, 241, 3), board.shape)
            self.assertFalse(tp.celulas_ocupadas(tp.classify_board(board)))
        finally:
            tp.configurar_layout("centro", monitor)
            tp.LAYOUT_NAME = previous_layout

    def test_empty_board_crop_reports_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "recorte do tabuleiro esta vazio"):
            tp.classify_board(np.zeros((0, 0, 3), dtype=np.uint8))

    def test_board_classifier_recognizes_colors_from_debug_capture(self):
        palette = {
            "I": (45, 193, 255),
            "T": (255, 34, 195),
            "Z": (255, 45, 89),
            "L": (255, 128, 44),
            "S": (129, 255, 6),
            "J": (46, 85, 255),
            "O": (255, 193, 44),
        }
        image = np.zeros((480, 240, 3), dtype=np.uint8)
        row = 10
        for column, color in enumerate(palette.values()):
            image[row * 24:(row + 1) * 24, column * 24:(column + 1) * 24] = color

        board = tp.classify_board(image)
        self.assertEqual("#######...", "".join(board[row]))

        for piece, color in palette.items():
            recognized, _, _ = tp.classify(np.full((20, 20, 3), color, dtype=np.uint8))
            self.assertEqual(piece, recognized)

    def test_board_classifier_ignores_ghost_gray_but_keeps_garbage_gray(self):
        image = np.zeros((480, 240, 3), dtype=np.uint8)
        row = 10
        image[row * 24:(row + 1) * 24, 0:24] = (110, 110, 110)
        image[row * 24:(row + 1) * 24, 24:48] = (128, 128, 128)

        board = tp.classify_board(image)
        self.assertEqual(".G........", "".join(board[row]))



class TspinSlotOverhangTests(unittest.TestCase):
    """O overhang de um slot de TSD pronto cobre uma casa vazia de proposito: nao e buraco."""

    @staticmethod
    def _slot():
        rows = [0] * d.LINHAS
        rows[17] = 1 << 3
        rows[18] = d.CHEIA ^ (7 << 3)
        rows[19] = d.CHEIA ^ (1 << 4)
        return rows

    def test_slot_overhang_is_not_counted_as_a_hole(self):
        rows = self._slot()
        self.assertEqual(1, d._slots_detalhes(rows)[0])
        self.assertEqual(1, d._buracos(rows))
        self.assertEqual(0, d._buracos_efetivos(rows))

    def test_real_holes_next_to_a_slot_still_count(self):
        rows = self._slot()
        rows[16] = 1 << 8       # bloco flutuando sobre casa vazia longe do slot
        self.assertEqual(1, d._buracos_efetivos(rows))

    def test_without_spins_the_overhang_remains_a_hole(self):
        rows = self._slot()
        antigo = d.USAR_SPINS
        d.USAR_SPINS = False
        try:
            d._slots_detalhes_cache.cache_clear()
            self.assertEqual(1, d._buracos_efetivos(rows))
        finally:
            d.USAR_SPINS = antigo
            d._slots_detalhes_cache.cache_clear()

    def test_ready_slot_scores_better_than_the_stage_two_board(self):
        pronto = self._slot()
        etapa2 = list(pronto)
        etapa2[17] = 0
        self.assertEqual(2, d._slots_detalhes(etapa2)[1])
        ctx = (True, False, False)
        self.assertGreater(d._avaliar(pronto, 0, 0, ctx), d._avaliar(etapa2, 0, 0, ctx))

    def test_placement_that_builds_the_overhang_is_not_filtered_out(self):
        etapa2 = self._slot()
        etapa2[17] = 0
        pronto = self._slot()
        ruim = list(etapa2)
        ruim[17] = 1 << 8       # buraco de verdade
        ruim[18] = d.CHEIA ^ (7 << 3)
        colocacoes = [(0.0, pronto), (0.0, ruim)]
        mantidas = d._filtrar_buracos_novos(etapa2, colocacoes)
        self.assertIn(colocacoes[0], mantidas)

    def test_simulation_now_completes_full_tspin_doubles(self):
        tsd = 0
        for seed in range(4):
            tsd += d.simular(200, seed, verificar=True)["tsd"]
        self.assertGreaterEqual(tsd, 4)


if __name__ == "__main__":
    unittest.main()
