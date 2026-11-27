"""Synthetic benchmark cases with hidden ground truth.

Each case is a black-box "application": a set of boolean flags, random constraints, and
planted interaction faults (2-, 3- and 4-way, including faults that need a flag to be OFF).
Faults surface through a small set of tests; when several faults hit the same test only the
first (hidden order) is observable, so faults can mask each other as in real suites.

The engine only sees: flag names, constraints, a live baseline, generated *source code*
(for the static analyzer) and a runner callable. The fault list stays on the benchmark side.

`colocate` controls how often a fault's flags are read in one shared function in the
generated code (the signal the risk analysis can exploit). Decoy functions read random flag
combinations that are harmless, so co-occurrence alone is never a perfect predictor.
"""

from __future__ import annotations

import itertools
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

TESTS = ["test_checkout", "test_cart", "test_account", "test_search", "test_render"]
KINDS = ["AssertionError", "KeyError", "ValueError", "TypeError"]


@dataclass
class Fault:
    id: str
    literals: dict  # flag -> bool
    test: str
    kind: str

    @property
    def order(self) -> int:
        return len(self.literals)

    def fires(self, states: dict) -> bool:
        return all(states[k] == v for k, v in self.literals.items())


@dataclass
class Case:
    name: str
    n: int
    seed: int
    colocate: float
    flags: list[str]
    constraints: list[dict]
    baseline: dict
    faults: list[Fault]
    code_dir: str = ""
    notes: list[str] = field(default_factory=list)

    def oracle(self, states: dict) -> tuple[list[tuple[str, str, str]], list[str]]:
        """Return observable failures and the ids of faults that produced them."""
        seen_tests, out, fired = set(), [], []
        for f in self.faults:  # list order = hidden evaluation order
            if f.test in seen_tests:
                continue
            if f.fires(states):
                seen_tests.add(f.test)
                out.append((f.test, f.kind, f"{f.kind} in {f.test}"))
                fired.append(f.id)
        return out, fired

    def runner_fn(self):
        def fn(states, persona):
            return self.oracle(states)[0]

        return fn

    def to_json(self) -> dict:
        d = asdict(self)
        return d


def _feasible(states: dict, constraints: list[dict]) -> bool:
    for c in constraints:
        if "requires" in c:
            a, b = c["requires"]
            if states[a] and not states[b]:
                return False
        if "excludes" in c:
            a, b = c["excludes"]
            if states[a] and states[b]:
                return False
    return True


def _partial_feasible(lits: dict, constraints: list[dict], flags: list[str], rng: random.Random, tries: int = 200) -> bool:
    for _ in range(tries):
        st = {f: (lits[f] if f in lits else rng.random() < 0.5) for f in flags}
        if _feasible(st, constraints):
            return True
    return False


def make_case(n: int, seed: int, colocate: float = 0.7, n_faults: int | None = None, workdir: Path | None = None) -> Case:
    rng = random.Random(seed * 7919 + n)
    flags = [f"f{i:02d}" for i in range(n)]
    # constraints: a few requires / excludes
    constraints = []
    for k in range(max(1, n // 8)):
        a, b = rng.sample(flags, 2)
        constraints.append({"id": f"c{k}", "requires": [a, b]} if rng.random() < 0.6 else {"id": f"c{k}", "excludes": [a, b]})
    n_faults = n_faults or max(3, n // 4)
    orders = rng.choices([2, 3, 4], weights=[0.5, 0.35, 0.15], k=n_faults)
    faults: list[Fault] = []
    attempts = 0
    while len(faults) < n_faults and attempts < 5000:
        attempts += 1
        k = orders[len(faults)]
        vars_ = rng.sample(flags, k)
        lits = {v: (rng.random() < 0.75) for v in vars_}
        if not _partial_feasible(lits, constraints, flags, rng):
            continue
        items = set(lits.items())
        if any(set(f.literals.items()) <= items or items <= set(f.literals.items()) for f in faults):
            continue
        faults.append(Fault(f"F{len(faults) + 1}", lits, rng.choice(TESTS), rng.choice(KINDS)))
    rng.shuffle(faults)  # hidden masking order
    # baseline: a feasible state that triggers no fault (the "live" environment)
    baseline = None
    for _ in range(5000):
        st = {f: rng.random() < 0.35 for f in flags}
        if _feasible(st, constraints) and not any(f.fires(st) for f in faults):
            baseline = st
            break
    case = Case(f"synth-n{n}-s{seed}-c{int(colocate * 100)}", n, seed, colocate, flags, constraints, baseline or {f: False for f in flags}, faults)
    if baseline is None:
        case.notes.append("no fault-free baseline found; using all-off")
    if workdir is not None:
        case.code_dir = str(write_code(case, Path(workdir), rng))
    return case


def write_code(case: Case, root: Path, rng: random.Random) -> Path:
    d = root / case.name
    d.mkdir(parents=True, exist_ok=True)
    lines = ["# generated synthetic application code (benchmark only)", "", "def _on(flags, name):", "    return flags.get(name, False)", ""]
    for f in case.flags:
        lines += [f"def feature_{f}(flags, order):", f"    if _on(flags, \"{f}\"):", f"        order.log.append(\"{f}\")", ""]
    k = 0
    for fault in case.faults:
        if rng.random() < case.colocate:
            names = list(fault.literals)
            cond = " and ".join(f"_on(flags, \"{x}\")" for x in names)
            lines += [f"def interaction_{k}(flags, order):", f"    if {cond}:", f"        order.r_{k} = order.total * 2", ""]
            k += 1
    for _ in range(2 * len(case.faults)):
        names = rng.sample(case.flags, rng.choice([2, 2, 3]))
        cond = " or ".join(f"_on(flags, \"{x}\")" for x in names)
        lines += [f"def helper_{k}(flags, order):", f"    if {cond}:", f"        order.r_{k} = True", ""]
        k += 1
    (d / "app.py").write_text("\n".join(lines))
    return d
