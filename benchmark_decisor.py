"""Compara beam widths em seeds fixos; sem dependencias externas.

Exemplo: py -3.9 benchmark_decisor.py --pecas 40 --seeds 0 1
"""
import argparse
import statistics
import time

import decisor as d


BEAMS = ((1, 1), (2, 1), (3, 1), (4, 2), (6, 3), (8, 4), (10, 6), (20, 10))
MODES = ("blitz", "ataque")
MAX_METRICS = {"max_b2b", "max_combo", "max_height", "max_holes"}


def benchmark(pecas, seeds):
    modo_original, largura_original = d.MODO, d.LARGURA
    try:
        print("modo beam pecas atk/pc linhas singles doubles triples quads spins TSS TSD TST "
              "b2b+ b2b- b2bmax comb+ combmax altura buracos+ buracosmax opener abandonados "
              "vivos p50_ms p95_ms total_s")
        for modo in MODES:
            for largura in BEAMS:
                d.MODO, d.LARGURA = modo, largura
                d._CACHE_AVALIAR.clear()
                d._colocacoes_cacheadas.cache_clear()
                d._colocacoes_rapidas.cache_clear()
                d.openers._aderencia_cached.cache_clear()
                chaves = ("pecas", "linhas", "valor", "singles", "doubles", "triples",
                          "quads", "valid_spins", "tss", "tsd", "tst",
                          "b2b_continuations", "b2b_breaks", "max_b2b",
                          "combo_continuations", "max_combo", "max_height",
                          "holes_created", "max_holes", "opener_attempts",
                          "opener_abandoned", "garbage")
                totais = {chave: 0 for chave in chaves}
                totais["vivos"] = 0
                latencias = []
                duracao_total = 0.0
                for seed in seeds:
                    def timed_decidir(*args):
                        inicio = time.perf_counter()
                        resultado = d.decidir(*args)
                        latencias.append((time.perf_counter() - inicio) * 1000)
                        return resultado

                    inicio_simulacao = time.perf_counter()
                    partida = d.simular(pecas, seed, decidir_fn=timed_decidir,
                                        verificar=True, pressao=0.1)
                    duracao_total += time.perf_counter() - inicio_simulacao
                    for chave in chaves:
                        if chave in MAX_METRICS:
                            totais[chave] = max(totais[chave], partida[chave])
                        else:
                            totais[chave] += partida[chave]
                    totais["vivos"] += int(partida["vivo"])
                n = max(totais["pecas"], 1)
                latencias.sort()
                p50 = statistics.median(latencias) if latencias else 0.0
                p95 = latencias[min(len(latencias) - 1, int(0.95 * len(latencias)))] if latencias else 0.0
                print(f"{modo:6} {str(largura):9} {totais['pecas']:5} "
                      f"{totais['valor'] / n:6.3f} {totais['linhas']:6} "
                      f"{totais['singles']:7} {totais['doubles']:7} {totais['triples']:7} "
                      f"{totais['quads']:5} {totais['valid_spins']:5} "
                      f"{totais['tss']:3} {totais['tsd']:3} {totais['tst']:3} "
                      f"{totais['b2b_continuations']:4} {totais['b2b_breaks']:4} "
                      f"{totais['max_b2b']:7} {totais['combo_continuations']:5} "
                      f"{totais['max_combo']:7} {totais['max_height']:6} "
                      f"{totais['holes_created']:9} {totais['max_holes']:10} "
                      f"{totais['opener_attempts']:6} {totais['opener_abandoned']:11} "
                      f"{totais['vivos']:5}/{len(seeds):<2} {p50:7.1f} {p95:7.1f} "
                      f"{duracao_total:7.2f}")
    finally:
        d.MODO, d.LARGURA = modo_original, largura_original


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark de qualidade e latencia do decisor")
    parser.add_argument("--pecas", type=int, default=30, help="pecas simuladas por seed/configuracao")
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1], help="seeds fixos da simulacao")
    args = parser.parse_args()
    benchmark(args.pecas, args.seeds)
