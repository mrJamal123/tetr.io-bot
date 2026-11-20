import unittest
from unittest import mock

import decisor
import openers


def tabuleiro(molde_linhas, espelho=False):
    lin = [0] * 20
    base = 20 - len(molde_linhas)
    for i, texto in enumerate(molde_linhas):
        for c, ch in enumerate(texto):
            if ch == "X":
                lin[base + i] |= 1 << (9 - c if espelho else c)
    return lin


def spins(lin):
    saida = []
    for k, col, y, ms, cels, sp, acoes in decisor.colocacoes_alcancaveis(lin, "T"):
        if sp == "full":
            saida.append((decisor._aplicar(lin, y, ms)[1], sorted((r - 13, c) for r, c in cels), lin))
    return saida


class TesteOpeners(unittest.TestCase):
    def setUp(self):
        self.spawn = decisor.LINHA_SPAWN
        decisor.LINHA_SPAWN = 2

    def tearDown(self):
        decisor.LINHA_SPAWN = self.spawn

    def test_molde_dt_faz_tsd_e_depois_tst(self):
        lin = tabuleiro(openers.DT_MOLDE)
        tsd = [s for s in spins(lin) if s[0] == 2 and s[1] == sorted(openers.DT_TSD)]
        self.assertTrue(tsd, "o TSD do molde tem de ser alcancavel pelo BFS/SRS do decisor")
        for k, col, y, ms, cels, sp, acoes in decisor.colocacoes_alcancaveis(lin, "T"):
            if sp == "full" and sorted((r - 13, c) for r, c in cels) == sorted(openers.DT_TSD):
                depois, n = decisor._aplicar(lin, y, ms)
                tst = [s for s in spins(depois) if s[0] == 3]
                self.assertTrue(tst, "apos o TSD o molde restante tem de dar um TST")
                return
        self.fail("TSD nao encontrado")

    def test_busca_tst_reconhece_tabuleiro_pronto_mesmo_sem_suportes(self):
        lin = tabuleiro(openers.DT_MOLDE)
        for _k, _col, y, ms, _cels, sp, _acoes in decisor.colocacoes_T(lin):
            if sp == "full" and decisor._aplicar(lin, y, ms)[1] == 2:
                depois, _ = decisor._aplicar(lin, y, ms)
                break
        else:
            self.fail("TSD do molde nao encontrado")

        self.assertTrue(openers._tst_geometrico(tuple(depois)))
        nome, rota = openers.buscar_rota_tst(depois, [])
        self.assertEqual("TSD com a bag atual", nome)
        self.assertEqual((), rota)

    def test_busca_tst_considera_hold_antes_da_fila(self):
        opcoes = openers._opcoes_peca_tst("T", ("J", "Z"), "S", True)

        self.assertIn(("S", "J", ("Z",), "T"), opcoes)
        self.assertFalse(any(letra == "J" for letra, *_estado in opcoes))

    def test_busca_tst_deduplica_estados_comparando_limpezas(self):
        board = tuple([0] * 20)
        placements = [
            (0, 0, 19, [0, 0], ((19, 0),), "", None),
            (0, 0, 18, [0, 0], ((18, 0),), "", None),
        ]
        with mock.patch.object(decisor, "_colocacoes_rapidas", return_value=placements), \
                mock.patch.object(decisor, "_aplicar", side_effect=[(board, 1), (board, 0)]), \
                mock.patch.object(openers, "_tst_geometrico", return_value=False):
            self.assertEqual(
                (None, None),
                openers.buscar_rota_tst(board, ["O"], profundidade=1, tempo_max=2.0),
            )

    def test_busca_tst_verifica_mais_candidatos_no_estado_do_log(self):
        _, rota = openers.planejar([0] * 20, list("OJZILST"))
        self.assertIsNotNone(rota)
        board = [0] * 20
        for letra, celulas in rota:
            alvo = frozenset(celulas)
            jogada = next(((y, ms) for _, _, y, ms, cels, _, _ in
                           decisor.colocacoes_alcancaveis(board, letra)
                           if frozenset(cels) == alvo), None)
            self.assertIsNotNone(jogada, f"{letra} da rota nao e alcancavel")
            board, _ = decisor._aplicar(board, *jogada)

        with mock.patch.object(openers, "_tst_agora", return_value=None) as verificar_tst:
            self.assertEqual((None, None), openers.buscar_rota_tst(
                tuple(board), list("ZLTIJ"), profundidade=7, largura=24,
                altura_max=10, tempo_max=2.0))
        self.assertLessEqual(verificar_tst.call_count, 193)

    def test_busca_tst_aceita_clear_intermediario_e_usa_colocacoes_alcancaveis(self):
        board = [0] * 20
        board[19] = 0b1111111100
        placement = (0, 0, 18, [3, 3], ((18, 0), (18, 1), (19, 0), (19, 1)), "", None)
        after, cleared = decisor._aplicar(board, placement[2], placement[3])
        self.assertEqual(1, cleared)

        def tst_apenas_depois(linhas):
            return ((10, 4),) if tuple(linhas) == tuple(after) else None

        with mock.patch.object(decisor, "_colocacoes_rapidas", return_value=[placement]), \
                mock.patch.object(decisor, "jogada_para", return_value=object()) as reachable, \
                mock.patch.object(openers, "_tst_geometrico", side_effect=tst_apenas_depois), \
                mock.patch.object(openers, "_tst_agora", side_effect=tst_apenas_depois):
            _nome, rota = openers.buscar_rota_tst(
                tuple(board), ["O", "T"], profundidade=1, largura=8, tempo_max=2.0)

        self.assertEqual(("O", placement[4]), rota[0])
        reachable.assert_called_once_with(tuple(board), "O", placement[4])

        with mock.patch.object(decisor, "_colocacoes_rapidas", return_value=[placement]), \
                mock.patch.object(decisor, "jogada_para", return_value=None), \
                mock.patch.object(openers, "_tst_geometrico", side_effect=tst_apenas_depois), \
                mock.patch.object(openers, "_tst_agora", side_effect=tst_apenas_depois):
            self.assertEqual(
                (None, None),
                openers.buscar_rota_tst(
                    tuple(board), ["O", "T"], profundidade=1, largura=8, tempo_max=2.0),
            )

    def test_molde_espelhado_tambem_funciona(self):
        lin = tabuleiro(openers.DT_MOLDE, espelho=True)
        alvo = sorted((r, 9 - c) for r, c in openers.DT_TSD)
        self.assertTrue([s for s in spins(lin) if s[0] == 2 and s[1] == alvo])

    def test_aderencia_prefere_o_molde(self):
        molde = tabuleiro(openers.DT_MOLDE)
        bagunca = [0] * 12 + [0b0000100000] * 8   # torre no meio: ocupa o encaixe e sobe acima do molde
        self.assertTrue(openers.ativo(molde))
        self.assertFalse(openers.ativo(bagunca))
        self.assertGreater(openers.aderencia(molde), openers.aderencia(bagunca))

    def test_dt_opener_ativo_por_padrao(self):
        self.assertTrue(decisor.OPENERS)

    # --- biblioteca de rotas por peca (openers.planejar) ---
    def test_rota_de_opener_termina_num_tsd_verificado(self):
        """A rota tem de ser executavel pelo BFS e terminar em T-spin double de verdade."""
        lin = [0] * 20
        nome, rota = openers.planejar(lin, list("LSIZOJT"))
        self.assertIsNotNone(rota, "bag com L e S cedo tem de ter rota de opener")
        self.assertEqual("T", rota[-1][0], "o ultimo passo da rota e o T do TSD")
        board, final = list(lin), None
        for letra, celulas in rota:
            alvo = frozenset(celulas)
            achou = None
            for _k, _col, y, ms, cels, sp, _acoes in decisor.colocacoes_alcancaveis(board, letra):
                if frozenset(cels) == alvo:
                    achou = (y, ms, sp)
                    break
            self.assertIsNotNone(achou, f"{letra} da rota nao e alcancavel")
            board, n = decisor._aplicar(board, achou[0], achou[1])
            final = (n, achou[2])
        self.assertEqual((2, "full"), final)

    def test_planejador_nao_inventa_rota_com_pilha_alta(self):
        lin = [0] * 10 + [0b1111111111] * 10
        self.assertEqual((None, None), openers.planejar(lin, list("LSIZOJT")))

    def test_jogada_para_coloca_a_peca_nas_celulas_pedidas(self):
        lin = [0] * 20
        alvo = tuple(next(iter(decisor.colocacoes_alcancaveis(lin, "O")))[4])
        j = decisor.jogada_para(lin, "O", alvo)
        self.assertIsNotNone(j)
        self.assertEqual(frozenset(alvo), j.celulas)
        self.assertEqual(("toque", "drop"), j.teclas[-1])
        self.assertIsNone(decisor.jogada_para(lin, "O", ((0, 0), (0, 1), (1, 0), (1, 1))))


if __name__ == "__main__":
    unittest.main()
