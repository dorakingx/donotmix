import itertools

import pytest

from donotmix import generate as g
from donotmix.space import Param, Space, constraint_from_spec


def mk(n, specs=()):
    return Space([Param(f"f{i}") for i in range(n)], [constraint_from_spec(s, i) for i, s in enumerate(specs)])


@pytest.mark.parametrize("n,t", [(5, 2), (8, 2), (8, 3), (12, 3)])
def test_tway_full_coverage_and_feasible(n, t):
    sp = mk(n, [{"requires": ["f0", "f1"]}, {"excludes": ["f2", "f3"]}])
    plan = g.tway(sp, t)
    assert all(sp.feasible(c) for c in plan.tests)
    cov = g.coverage(sp, plan.tests, t)
    assert cov.ratio == 1.0
    assert len(plan.tests) < sp.count_feasible()


def test_coverage_counts_against_bruteforce():
    sp = mk(5, [{"excludes": ["f0", "f1"]}])
    tests = [(False,) * 5, (True, False, True, False, True)]
    feas = set()
    for cfg in sp.iter_feasible():
        for c in itertools.combinations(range(5), 2):
            feas.add(tuple((i, cfg[i]) for i in c))
    cov = g.coverage(sp, tests, 2)
    assert cov.total == len(feas)
    covered = {tp for cfg in tests for tp in g.tuples_of(cfg, 2)}
    assert cov.covered == len(covered & feas)


def test_infeasible_tuples_excluded():
    sp = mk(3, [{"excludes": ["f0", "f1"]}])
    assert ((0, True), (1, True)) not in g.feasible_tuples(sp, 2)


def test_random_is_seeded_distinct_and_feasible():
    sp = mk(8, [{"requires": ["f0", "f1"]}])
    a = g.random_suite(sp, 20, seed=7)
    b = g.random_suite(sp, 20, seed=7)
    assert a.tests == b.tests
    assert len(set(a.tests)) == len(a.tests) == 20
    assert all(sp.feasible(c) for c in a.tests)


def test_exhaustive_cap():
    sp = mk(6)
    assert len(g.exhaustive(sp).tests) == 64
    p = g.exhaustive(sp, cap=10)
    assert len(p.tests) == 10 and p.truncated


def test_risk_weights_order_tests():
    sp = mk(8)
    heavy = {(0, True), (1, True), (2, True)}
    w = lambda tp: 50.0 if set(tp) <= heavy else 1.0
    plan = g.risk_based(sp, w, [], t=3)
    first = plan.tests[0]
    assert first[0] and first[1] and first[2]
    assert g.coverage(sp, plan.tests, 3).ratio == 1.0


def test_variable_strength_hyperedge_covered():
    sp = mk(7)
    plan = g.risk_based(sp, lambda tp: 1.0, [([0, 1, 2, 3], 5.0)], t=2)
    combos = {tuple(c[i] for i in range(4)) for c in plan.tests}
    assert len(combos) == 16  # all 2^4 value combinations of the 4-flag hyperedge
