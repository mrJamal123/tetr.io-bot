"""Mede ataque por peca do decisor em partidas simuladas (sem o jogo)."""
import sys, time
import decisor as d

def medir(pecas=300, seeds=range(4), pressao=0.0, rotulo=""):
    tot = dict(pecas=0, linhas=0, valor=0.0, quads=0, tsd=0, tst=0, tss=0, pc=0,
               vivos=0, ms=0.0)
    for s in seeds:
        t0 = time.time()
        e = d.simular(pecas, s, verificar=True, pressao=pressao)
        tot["ms"] += (time.time() - t0) * 1000
        for k in ("pecas", "linhas", "valor", "quads", "tsd", "tst", "tss"):
            tot[k] += e[k]
        tot["pc"] += e.get("perfect_clears", 0)
        tot["vivos"] += int(e["vivo"])
    n = max(tot["pecas"], 1)
    print(f"{rotulo:18} pecas={n:5} ataque/peca={tot['valor']/n:5.2f} linhas/peca={tot['linhas']/n:4.2f} "
          f"quad={tot['quads']:3} TSD={tot['tsd']:3} TST={tot['tst']:3} TSS={tot['tss']:3} PC={tot['pc']:2} "
          f"vivos={tot['vivos']}/{len(list(seeds))} {tot['ms']/n:5.1f}ms/peca")
    return tot

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    medir(n, rotulo="sem pressao")
    medir(n, pressao=0.12, rotulo="pressao 0.12")
