"""Counterexample minimization.

Given a failing configuration F and a passing reference R (normally the live Flagsmith
state, or the nearest passing configuration when the live state itself fails):

1. trigger    ddmin (Zeller & Hildebrandt 2002) over the flag differences R -> F yields a
              1-minimal set of flag changes that still reproduces the *same* failure atom.
              Optionally an exhaustive check over smaller subsets upgrades the claim to
              "globally minimal with respect to R".
2. core       one-flip sensitivity analysis around R+trigger: a literal belongs to the core
              if flipping it (to any feasible alternative) makes the failure disappear.
              Literals whose flip is infeasible are reported as *pinned by constraints*.
3. generality the core is re-tested under other completions of the non-core flags:
              all feasible completions when few enough, otherwise a seeded sample. Only an
              exhaustive check with zero passing completions is reported as "sufficient".

Every step is backed by real executions; nothing here is inferred by an LLM.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from .runner import Atom, Execution, Persona, Runner
from .space import Config, Space

FAIL, PASS, UNRES = "FAIL", "PASS", "UNRESOLVED"


def status_for(ex: Execution, atom: Atom) -> str:
    if ex.error:
        return UNRES
    if atom in ex.atoms:
        return FAIL
    if any(a.test == atom.test for a in ex.atoms):
        return UNRES  # same test failed differently (masked by another failure)
    return PASS


@dataclass
class Generality:
    checked: int = 0
    failed: int = 0
    passed: int = 0
    masked: int = 0
    exhaustive: bool = False
    space_size: int = 0
    passing_examples: list[dict] = field(default_factory=list)
    passing_configs: list = field(default_factory=list, repr=False)
    failing_configs: list = field(default_factory=list, repr=False)

    @property
    def verdict(self) -> str:
        if self.checked == 0:
            return "unchecked"
        if self.passed == 0 and self.exhaustive:
            return "sufficient"  # fails (or is masked) in every feasible completion
        if self.passed == 0:
            return "held-in-sample"
        return "context-dependent"

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict, "checked": self.checked, "failed": self.failed, "passed": self.passed,
            "masked": self.masked, "exhaustive": self.exhaustive, "completion_space": self.space_size,
            "passing_examples": self.passing_examples[:3],
        }


@dataclass
class Minimized:
    atom: Atom
    persona: Persona
    reference: Config
    reference_kind: str
    failing: Config
    trigger: dict[int, object]
    trigger_claim: str
    core: dict[int, object]
    pinned: dict[int, list[str]]
    masked_literals: list[int]
    generality: Generality
    repro: Config
    tests_used: int


class Minimizer:
    def __init__(self, space: Space, runner: Runner, jobs: int = 1, global_limit: int = 12, exhaustive_limit: int = 256, sample: int = 48, seed: int = 0, max_tests: int = 4000):
        self.space = space
        self.runner = runner
        self.jobs = jobs
        self.global_limit = global_limit
        self.exhaustive_limit = exhaustive_limit
        self.sample = sample
        self.seed = seed
        self.max_tests = max_tests
        self.used = 0

    def _status(self, cfg: Config, atom: Atom, persona: Persona) -> str:
        if not self.space.feasible(cfg):
            return UNRES
        before = self.runner.executions
        ex = self.runner.run(self.space.names, cfg, persona)
        self.used += self.runner.executions - before
        return status_for(ex, atom)

    def _status_many(self, cfgs: list[Config], atom: Atom, persona: Persona) -> list[str]:
        feas = [c for c in cfgs if self.space.feasible(c)]
        before = self.runner.executions
        exs = {e.config: e for e in self.runner.run_many(self.space.names, [(c, persona) for c in feas], jobs=self.jobs)}
        self.used += self.runner.executions - before
        return [status_for(exs[c], atom) if c in exs else UNRES for c in cfgs]

    def _apply(self, ref: Config, fail: Config, subset) -> Config:
        cfg = list(ref)
        for i in subset:
            cfg[i] = fail[i]
        return tuple(cfg)

    # ---- ddmin -------------------------------------------------------------------
    def ddmin(self, ref: Config, fail: Config, atom: Atom, persona: Persona) -> list[int]:
        changes = self.space.diff(ref, fail)

        def test(sub):
            return self._status(self._apply(ref, fail, sub), atom, persona)

        n = 2
        while len(changes) >= 2 and self.used < self.max_tests:
            chunks = [changes[i * len(changes) // n:(i + 1) * len(changes) // n] for i in range(n)]
            chunks = [c for c in chunks if c]
            reduced = False
            for c in chunks:
                if test(c) == FAIL:
                    changes, n, reduced = c, 2, True
                    break
            if not reduced:
                for c in chunks:
                    comp = [x for x in changes if x not in c]
                    if comp and test(comp) == FAIL:
                        changes, n, reduced = comp, max(n - 1, 2), True
                        break
            if not reduced:
                if n >= len(changes):
                    break
                n = min(len(changes), 2 * n)
        # enforce 1-minimality explicitly (ddmin's guarantee weakens with unresolved outcomes)
        changed = True
        while changed and len(changes) > 1:
            changed = False
            for x in list(changes):
                rest = [y for y in changes if y != x]
                if test(rest) == FAIL:
                    changes, changed = rest, True
                    break
        return changes

    def minimize(self, ref: Config, ref_kind: str, fail: Config, atom: Atom, persona: Persona) -> Minimized:
        start = self.used
        sp = self.space
        trig = self.ddmin(ref, fail, atom, persona)
        claim = "1-minimal"
        diff = sp.diff(ref, fail)
        if len(trig) > 1 and len(diff) <= self.global_limit:
            found = None
            for k in range(1, len(trig)):
                for sub in itertools.combinations(diff, k):
                    if self._status(self._apply(ref, fail, sub), atom, persona) == FAIL:
                        found = list(sub)
                        break
                if found:
                    break
            if found:
                trig, claim = found, "globally-minimal"
            else:
                claim = "globally-minimal"
        elif len(trig) <= 1:
            claim = "globally-minimal"
        fail_min = self._apply(ref, fail, trig)

        # core via one-flip sensitivity
        core: dict[int, object] = {i: fail_min[i] for i in trig}
        pinned: dict[int, list[str]] = {}
        masked: list[int] = []
        flips, owners = [], []
        for i, p in enumerate(sp.params):
            if i in core:
                continue
            for v in p.domain:
                if v == fail_min[i]:
                    continue
                cfg = fail_min[:i] + (v,) + fail_min[i + 1:]
                if not sp.feasible(cfg):
                    pinned.setdefault(i, []).extend(sp.violated(cfg))
                    continue
                flips.append(cfg)
                owners.append(i)
        results = self._status_many(flips, atom, persona)
        seen_pass, seen_unres = set(), set()
        for i, r in zip(owners, results):
            if r == PASS:
                seen_pass.add(i)
            elif r == UNRES:
                seen_unres.add(i)
        for i in seen_pass:
            core[i] = fail_min[i]
            pinned.pop(i, None)
        masked = sorted(seen_unres - seen_pass)
        pinned = {i: sorted(set(v)) for i, v in pinned.items() if i not in core}

        gen = self.generality(core, atom, persona)
        refinements = 0
        while gen.verdict == "context-dependent" and gen.passing_configs and gen.failing_configs and refinements < 3 and self.used < self.max_tests:
            # counterexample-guided refinement: which extra literals separate a passing completion from a failing one?
            p_cfg = gen.passing_configs[0]
            f_cfg = nearest(gen.failing_configs, p_cfg)
            extra = self.ddmin(p_cfg, f_cfg, atom, persona)
            if not extra or self._status(self._apply(p_cfg, f_cfg, extra), atom, persona) != FAIL:
                break
            for i in extra:
                core[i] = f_cfg[i]
                pinned.pop(i, None)
            refinements += 1
            gen = self.generality(core, atom, persona)
        # literals forced by the other core literals through constraints are reported as pinned, not core
        for i in list(core):
            if len(core) <= 1:
                break
            rest = {j: v for j, v in core.items() if j != i}
            alts = [v for v in sp.params[i].domain if v != core[i]]
            if alts and not any(sp.extendable({**rest, i: v}) for v in alts):
                del core[i]
        # pinned = literals forced by the core through constraints (reported, not part of the core)
        pinned = {}
        for i in range(len(sp)):
            if i in core:
                continue
            ok_vals = [v for v in sp.params[i].domain if sp.extendable({**core, i: v})]
            if len(ok_vals) == 1:
                alt = next(v for v in sp.params[i].domain if v != ok_vals[0])
                pinned[i] = self._why_forced(core, i, alt)
        # reproduction: the feasible completion of the core closest to the live state, if it fails
        repro = fail_min
        cand = sp.complete(dict(core), prefer=sp.baseline)
        if cand is not None and cand != fail_min and self._status(cand, atom, persona) == FAIL:
            repro = cand
        masked = [i for i in masked if i not in core]
        return Minimized(atom, persona, ref, ref_kind, fail, {i: fail_min[i] for i in trig}, claim, dict(sorted(core.items())), pinned, masked, gen, repro, self.used - start)

    def _why_forced(self, rest: dict, i: int, v) -> list[str]:
        sp = self.space
        cfg = sp.complete(dict(rest)) or sp.baseline
        cfg = cfg[:i] + (v,) + cfg[i + 1:]
        return sp.violated(cfg) or ["constraints"]

    # ---- generality --------------------------------------------------------------
    def generality(self, core: dict[int, object], atom: Atom, persona: Persona) -> Generality:
        sp = self.space
        free = [i for i in range(len(sp)) if i not in core]
        size = 1
        for i in free:
            size *= len(sp.params[i].domain)
        g = Generality(space_size=size)
        if not sp.extendable(dict(core)):
            return g
        if size <= self.exhaustive_limit:
            cfgs = []
            for vals in itertools.product(*(sp.params[i].domain for i in free)):
                cfg = [None] * len(sp)
                for i, v in core.items():
                    cfg[i] = v
                for i, v in zip(free, vals):
                    cfg[i] = v
                cfg = tuple(cfg)
                if sp.feasible(cfg):
                    cfgs.append(cfg)
            g.exhaustive = True
        else:
            rng = random.Random(self.seed)
            seen, cfgs = set(), []
            for _ in range(self.sample * 4):
                c = sp.complete(dict(core), rng=rng)
                if c and c not in seen:
                    seen.add(c)
                    cfgs.append(c)
                if len(cfgs) >= self.sample:
                    break
        res = self._status_many(cfgs, atom, persona)
        for c, r in zip(cfgs, res):
            g.checked += 1
            if r == FAIL:
                g.failed += 1
                if len(g.failing_configs) < 16:
                    g.failing_configs.append(c)
            elif r == UNRES:
                g.masked += 1
            else:
                g.passed += 1
                if len(g.passing_configs) < 4:
                    g.passing_configs.append(c)
                if len(g.passing_examples) < 3:
                    g.passing_examples.append({sp.names[i]: c[i] for i in free if c[i] != sp.baseline[i]})
        return g


def nearest(cfgs: list[Config], target: Config) -> Config | None:
    best, bd = None, None
    for c in cfgs:
        d = sum(1 for a, b in zip(c, target) if a != b)
        if bd is None or d < bd:
            best, bd = c, d
    return best
