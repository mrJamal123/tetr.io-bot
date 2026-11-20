#!/usr/bin/env python3
"""Calcula a probabilidade de montar setups a partir de uma bag de Tetris.

Cada setup e uma lista de regras "X<Y", indicando que X deve ser colocada
antes de Y. Pode-se informar varios setups para calcular cada probabilidade,
a uniao e a intersecao.

Exemplos:
  python probability.py "O<S,T<L,T<Z"
  python probability.py "O<S,T<L,T<Z" "O<Z,T<L,T<S"
  python probability.py --hold 0 "O<S,T<L,T<Z"
"""
import argparse
from fractions import Fraction
from itertools import permutations

PIECES = "IOTSZLJ"


def reachable_orders(bag, use_hold):
    """Todas as ordens de colocacao possiveis para um bag, com 0 ou 1 hold."""
    if not use_hold:
        return {tuple(bag)}
    results = set()

    def rec(i, hold, placed):
        if i == len(bag):
            if hold is not None:
                placed = placed + (hold,)
            results.add(placed)
            return
        cur = bag[i]
        rec(i + 1, hold, placed + (cur,))  # coloca a peca atual
        if hold is None:
            rec(i + 1, cur, placed)  # guarda no hold, pega a proxima
        else:
            rec(i + 1, cur, placed + (hold,))  # troca: coloca o hold, guarda a atual

    rec(0, None, ())
    return results


def parse_setup(text):
    rules = []
    for part in text.split(","):
        pieces = part.upper().split("<")
        if len(pieces) != 2:
            raise ValueError(f"Regra invalida: '{part}'. Use o formato X<Y.")

        before, after = (piece.strip() for piece in pieces)
        if len(before) != 1 or len(after) != 1 or before not in PIECES or after not in PIECES:
            raise ValueError(f"Peca invalida em '{part}'. Pecas validas: {PIECES}")
        rules.append((before, after))

    return rules


def infer_missing_piece(seen):
    """Return the unique 7-bag piece not present in exactly six observed pieces."""
    seen = set(seen)
    if len(seen) != 6 or not seen <= set(PIECES):
        raise ValueError("A deducao exige seis pecas validas e distintas da bag.")
    return next(piece for piece in PIECES if piece not in seen)


def satisfies(order, rules):
    pos = {p: i for i, p in enumerate(order)}
    return all(pos[a] < pos[b] for a, b in rules)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("setups", nargs="+", help='ex: "O<S,T<L,T<Z"')
    ap.add_argument("--hold", type=int, default=1, choices=[0, 1])
    args = ap.parse_args()

    try:
        setups = [parse_setup(setup) for setup in args.setups]
    except ValueError as error:
        ap.error(str(error))
    n_setups = len(setups)

    bags = list(permutations(PIECES))
    total = len(bags)

    # para cada bag, o conjunto de setups que ele permite
    feasible_sets = []
    for bag in bags:
        orders = reachable_orders(bag, bool(args.hold))
        feasible_sets.append(frozenset(
            i for i, rules in enumerate(setups) if any(satisfies(o, rules) for o in orders)
        ))

    def show(label, count):
        f = Fraction(count, total)
        print(f"{label:<38} {count:>6}/{total}  = {f!s:>8}  ({float(f) * 100:6.2f}%)")

    print(f"Pecas: {PIECES} | hold: {'sim' if args.hold else 'nao'} | bags: {total}\n")

    for i, s in enumerate(args.setups):
        show(f"Setup {i + 1} ({s})", sum(1 for fs in feasible_sets if i in fs))

    print()
    if n_setups > 1:
        show("um dos dois vai", sum(1 for fs in feasible_sets if fs))
        show("vai os dois", sum(1 for fs in feasible_sets if len(fs) == n_setups))
        for i in range(n_setups):
            show(f"só vai 1 {i + 1}", sum(1 for fs in feasible_sets if fs == {i}))
    show("num da", sum(1 for fs in feasible_sets if not fs))


if __name__ == "__main__":
    main()
