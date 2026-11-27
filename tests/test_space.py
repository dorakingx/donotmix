import itertools

import pytest

from donotmix.space import ConstraintError, Param, Space, constraint_from_spec


def mk(n, specs=()):
    return Space([Param(f"f{i}") for i in range(n)], [constraint_from_spec(s, i) for i, s in enumerate(specs)])


@pytest.mark.parametrize("rule,env,expected", [
    ("a -> b", {"a": True, "b": False}, False),
    ("a -> b", {"a": False, "b": False}, True),
    ("!(a & b)", {"a": True, "b": True}, False),
    ("a | !b", {"a": False, "b": True}, False),
    ("a <-> b", {"a": True, "b": True}, True),
    ("(a & b) -> !c", {"a": True, "b": True, "c": True}, False),
])
def test_expression_semantics(rule, env, expected):
    c = constraint_from_spec(rule, 0)
    assert c.holds(env) is expected


def test_unknown_flag_rejected():
    with pytest.raises(ConstraintError):
        mk(2, [{"requires": ["f0", "nope"]}])


def test_bad_syntax_rejected():
    with pytest.raises(ConstraintError):
        constraint_from_spec("a -> -> b", 0).ast()


def test_count_feasible_matches_bruteforce():
    sp = mk(6, [{"requires": ["f0", "f1"]}, {"excludes": ["f2", "f3"]}, {"rule": "f4 -> !f0"}])
    brute = sum(1 for cfg in itertools.product((False, True), repeat=6) if sp.feasible(cfg))
    assert sp.count_feasible() == brute == len(list(sp.iter_feasible()))


def test_extendable_and_complete():
    sp = mk(4, [{"requires": ["f0", "f1"]}, {"excludes": ["f1", "f2"]}])
    assert not sp.extendable({0: True, 2: True})  # f0 -> f1, but f1 excludes f2
    assert sp.extendable({0: True, 3: True})
    cfg = sp.complete({0: True})
    assert cfg is not None and sp.feasible(cfg) and cfg[1] is True


def test_contradiction_minimal_conflict():
    sp = mk(3, [{"fixed": {"f0": True}}, {"requires": ["f0", "f1"]}, {"fixed": {"f1": False}}, {"excludes": ["f2", "f0"]}])
    assert not sp.is_consistent()
    assert sorted(sp.minimal_conflict()) == ["C1", "C2", "C3"]


def test_violated_reports_constraint_ids():
    sp = mk(2, [{"id": "dep", "requires": ["f0", "f1"]}])
    assert sp.violated((True, False)) == ["dep"]
