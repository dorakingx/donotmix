"""Test-suite generation strategies and interaction coverage.

Strategies
----------
exhaustive  all feasible configurations (capped)
random      uniform sampling of feasible configurations without replacement (seeded)
tway        greedy constrained covering array of strength t (AETG-style one-test-at-a-time)
risk        the same greedy, but maximizing *risk-weighted* coverage over a mixed universe:
            all t-way tuples plus every value combination of high-risk hyperedges from the
            dependency analysis (a variable-strength covering array, prioritized by weight)

The greedy construction is the classical density/AETG approach (Cohen et al. 1997) with
constraint handling by extendability checks, and the weighted variant follows prioritized
interaction testing (Bryce & Colbourn 2006). None of this is a new algorithm; DoNotMix's
contribution is wiring it to live Flagsmith state, real test execution and minimization.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Callable

from .space import Config, Space

Tuple_ = tuple  # ((param, value), ...) sorted by param index


def feasible_tuples(space: Space, t: int, params: list[int] | None = None) -> list[Tuple_]:
    idxs = params if params is not None else list(range(len(space)))
    out = []
    for combo in itertools.combinations(idxs, t):
        for vals in itertools.product(*(space.params[i].domain for i in combo)):
            partial = dict(zip(combo, vals))
            if space.extendable(partial):
                out.append(tuple(zip(combo, vals)))
    return out


def tuples_of(cfg: Config, t: int) -> list[Tuple_]:
    return [tuple((i, cfg[i]) for i in combo) for combo in itertools.combinations(range(len(cfg)), t)]


@dataclass
class CoverageReport:
    t: int
    covered: int
    total: int

    @property
    def ratio(self) -> float:
        return 1.0 if self.total == 0 else self.covered / self.total

    def as_dict(self) -> dict:
        return {"t": self.t, "covered": self.covered, "total": self.total, "ratio": round(self.ratio, 6)}


def coverage(space: Space, tests: list[Config], t: int, universe: list[Tuple_] | None = None) -> CoverageReport:
    uni = set(universe if universe is not None else feasible_tuples(space, t))
    covered = set()
    for cfg in tests:
        for tp in tuples_of(cfg, t):
            if tp in uni:
                covered.add(tp)
    return CoverageReport(t=t, covered=len(covered), total=len(uni))


def coverage_curve(space: Space, tests: list[Config], t: int, universe: list[Tuple_] | None = None) -> list[float]:
    uni = set(universe if universe is not None else feasible_tuples(space, t))
    covered, curve = set(), []
    for cfg in tests:
        for tp in tuples_of(cfg, t):
            if tp in uni:
                covered.add(tp)
        curve.append(len(covered) / len(uni) if uni else 1.0)
    return curve


# --------------------------------------------------------------------------------------


@dataclass
class Plan:
    strategy: str
    tests: list[Config]
    notes: list[str] = field(default_factory=list)
    truncated: bool = False
    duplicates_skipped: int = 0
    weights: dict | None = None  # tuple -> weight (risk strategy)


def exhaustive(space: Space, cap: int = 4096) -> Plan:
    tests, truncated = [], False
    for cfg in space.iter_feasible():
        if len(tests) >= cap:
            truncated = True
            break
        tests.append(cfg)
    notes = [f"enumerated {len(tests)} feasible configurations" + (f" (truncated at cap={cap})" if truncated else "")]
    return Plan("exhaustive", tests, notes, truncated=truncated)


def random_suite(space: Space, budget: int, seed: int = 0, max_attempts_factor: int = 200) -> Plan:
    """Uniform over the feasible set (rejection sampling), sampled without replacement.

    Duplicate draws are discarded and counted; the suite contains `budget` distinct configs
    unless the feasible space is smaller or the attempt limit is hit.
    """
    rng = random.Random(seed)
    seen, tests, dups, attempts = set(), [], 0, 0
    limit = max(1000, budget * max_attempts_factor)
    while len(tests) < budget and attempts < limit:
        attempts += 1
        cfg = tuple(rng.choice(p.domain) for p in space.params)
        if not space.feasible(cfg):
            continue
        if cfg in seen:
            dups += 1
            continue
        seen.add(cfg)
        tests.append(cfg)
    notes = [f"seed={seed}; {len(tests)} distinct feasible configs; {dups} duplicate draws discarded"]
    return Plan("random", tests, notes, duplicates_skipped=dups)


def greedy_cover(
    space: Space,
    universe: dict[Tuple_, float],
    budget: int | None = None,
    seed: int = 0,
    candidates: int = 8,
    seed_tests: list[Config] | None = None,
) -> list[Config]:
    """Weighted greedy covering-array construction under constraints.

    Builds one test at a time; each candidate starts from the heaviest uncovered tuple and
    fills the remaining parameters (random order) with the value that adds the most uncovered
    weight while staying extendable to a feasible configuration. The best of `candidates`
    candidates is kept. Stops when the universe is covered or `budget` tests exist.
    """
    rng = random.Random(seed)
    uncovered = dict(universe)
    by_param: dict[int, list[Tuple_]] = {}
    for tp in uncovered:
        for i, _ in tp:
            by_param.setdefault(i, []).append(tp)
    tests: list[Config] = []

    def cover(cfg: Config) -> None:
        for tp in [tp for tp in uncovered if all(cfg[j] == v for j, v in tp)]:
            del uncovered[tp]

    for cfg in seed_tests or []:
        tests.append(cfg)
        cover(cfg)

    n = len(space)
    min_len = min((len(tp) for tp in uncovered), default=0)
    while uncovered and (budget is None or len(tests) < budget):
        top_w = max(uncovered.values())
        heads = [tp for tp, w in uncovered.items() if w == top_w]
        best_cfg, best_gain = None, -1.0
        for _ in range(candidates):
            head = rng.choice(heads)
            partial = dict(head)
            if not space.extendable(partial):  # defensive; universe should be feasible
                del uncovered[head]
                break
            order = [i for i in range(n) if i not in partial]
            rng.shuffle(order)
            acc = 0.0
            for i in order:
                best_v, best_vg = None, -1.0
                vals = list(space.params[i].domain)
                rng.shuffle(vals)
                for v in vals:
                    partial[i] = v
                    if not space.extendable(partial):
                        del partial[i]
                        continue
                    g = 0.0
                    for tp in by_param.get(i, ()):
                        w = uncovered.get(tp)
                        if w is not None and all(j in partial and partial[j] == vv for j, vv in tp):
                            g += w
                    del partial[i]
                    if g > best_vg:
                        best_v, best_vg = v, g
                partial[i] = best_v
                acc += max(best_vg, 0.0)
            cfg = tuple(partial[i] for i in range(n))
            # each uncovered tuple is counted exactly once, when its last parameter is assigned;
            # tuples lying entirely inside the head are added here
            if len(head) > min_len:
                hs = set(head)
                gain = acc + sum(w for tp, w in uncovered.items() if len(tp) <= len(head) and set(tp) <= hs)
            else:
                gain = acc + uncovered.get(head, 0.0)
            if gain > best_gain:
                best_cfg, best_gain = cfg, gain
        if best_cfg is None:
            continue
        if best_gain <= 0:
            break
        tests.append(best_cfg)
        cover(best_cfg)
    return tests


def tway(space: Space, t: int = 2, budget: int | None = None, seed: int = 0, candidates: int = 8) -> Plan:
    uni = {tp: 1.0 for tp in feasible_tuples(space, t)}
    tests = greedy_cover(space, uni, budget=budget, seed=seed, candidates=candidates)
    notes = [f"greedy {t}-way covering array: {len(uni)} feasible {t}-tuples, {len(tests)} tests"]
    return Plan(f"{t}-way", tests, notes)


def risk_based(
    space: Space,
    tuple_weight: Callable[[Tuple_], float],
    hyperedges: list[tuple[list[int], float]],
    t: int = 3,
    budget: int | None = None,
    seed: int = 0,
    candidates: int = 8,
    max_edge: int = 6,
) -> Plan:
    """Prioritized variable-strength cover: t-way tuples + full combos of risky hyperedges."""
    uni: dict[Tuple_, float] = {}
    for tp in feasible_tuples(space, t):
        uni[tp] = tuple_weight(tp)
    extra = 0
    for members, w in hyperedges:
        members = sorted(set(members))
        if len(members) <= t or len(members) > max_edge or w <= 0:
            continue
        for vals in itertools.product(*(space.params[i].domain for i in members)):
            partial = dict(zip(members, vals))
            if space.extendable(partial):
                tp = tuple(zip(members, vals))
                uni[tp] = max(uni.get(tp, 0.0), tuple_weight(tp))
                extra += 1
    tests = greedy_cover(space, uni, budget=budget, seed=seed, candidates=candidates)
    notes = [
        f"risk-weighted variable-strength cover: {len(uni)} weighted tuples "
        f"({extra} from {sum(1 for m, _ in hyperedges if t < len(set(m)) <= max_edge)} high-order hyperedges), {len(tests)} tests"
    ]
    return Plan("risk", tests, notes, weights=uni)
