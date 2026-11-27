from donotmix.minimize import Minimizer
from donotmix.runner import Atom, Persona, PythonRunner
from donotmix.space import Param, Space, constraint_from_spec

P = Persona("p")


def setup(n, fault, specs=(), baseline=None):
    names = [f"f{i}" for i in range(n)]
    sp = Space([Param(x) for x in names], [constraint_from_spec(s, i) for i, s in enumerate(specs)], baseline)

    def fn(states, persona):
        return [("test_x", "AssertionError", "boom")] if fault(states) else []

    return sp, PythonRunner(fn)


def test_three_way_fault_exact_core_and_sufficient():
    sp, r = setup(10, lambda s: s["f1"] and s["f4"] and s["f7"])
    fail = tuple(True for _ in range(10))
    m = Minimizer(sp, r, exhaustive_limit=1024).minimize(sp.baseline, "live", fail, Atom("p", "test_x", "AssertionError"), P)
    assert sp.fmt_literals(m.core) == {"f1": True, "f4": True, "f7": True}
    assert m.trigger_claim == "globally-minimal"
    assert m.generality.verdict == "sufficient" and m.generality.exhaustive


def test_fault_needing_flag_off():
    sp, r = setup(8, lambda s: s["f2"] and not s["f5"], baseline=(False,) * 5 + (True,) + (False,) * 2)
    fail = tuple([False, False, True, False, False, False, True, True])
    m = Minimizer(sp, r).minimize(sp.baseline, "live", fail, Atom("p", "test_x", "AssertionError"), P)
    assert sp.fmt_literals(m.core) == {"f2": True, "f5": False}


def test_pinned_by_constraint_not_in_core():
    sp, r = setup(6, lambda s: s["f0"] and s["f1"], specs=[{"id": "dep", "requires": ["f0", "f1"]}])
    fail = (True, True, False, False, False, False)
    m = Minimizer(sp, r).minimize(sp.baseline, "live", fail, Atom("p", "test_x", "AssertionError"), P)
    assert sp.fmt_literals(m.core) == {"f0": True}
    assert 1 in m.pinned and m.pinned[1] == ["dep"]


def test_context_dependent_refined():
    # true condition f0 & f3 & !f6, but reference/failing config make f6 look irrelevant at first
    sp, r = setup(8, lambda s: s["f0"] and s["f3"] and not s["f6"])
    fail = (True, False, False, True, False, False, False, False)
    m = Minimizer(sp, r, exhaustive_limit=1024).minimize(sp.baseline, "live", fail, Atom("p", "test_x", "AssertionError"), P)
    assert sp.fmt_literals(m.core) == {"f0": True, "f3": True, "f6": False}
    assert m.generality.verdict == "sufficient"


def test_masking_is_unresolved_not_pass():
    names = [f"f{i}" for i in range(6)]
    sp = Space([Param(x) for x in names])

    def fn(states, persona):
        if states["f5"]:
            return [("test_x", "KeyError", "other bug")]  # masks
        if states["f0"] and states["f1"]:
            return [("test_x", "AssertionError", "target")]
        return []

    r = PythonRunner(fn)
    fail = (True, True, False, False, False, False)
    m = Minimizer(sp, r, exhaustive_limit=1024).minimize(sp.baseline, "live", fail, Atom("p", "test_x", "AssertionError"), P)
    assert sp.fmt_literals(m.core) == {"f0": True, "f1": True}
    assert m.generality.masked > 0 and m.generality.passed == 0
