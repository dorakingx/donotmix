"""DoNotMix benchmark harness.

    python -m benchmarks.run --suite quick|full|mixmart [--seeds K]

Experiments
  E1 fixed budget     same number of tests for every strategy (B2 = |pairwise suite|,
                      B3 = |3-way suite|); faults detected, tests to first failure, NAPFD
  E2 coverage cost    tests needed to reach 100% t-way coverage: greedy vs random vs exhaustive
  E3 minimization     DoNotMix pipeline (risk plan + ddmin + sensitivity + generality) vs the
                      hidden ground truth: exact / over- / under-specified / false positive
  E4 MixMart          the real demo app, real pytest runs, live Flagsmith snapshot

Seeds 0..4 are the development set (used while building the risk heuristics); the reported
evaluation set is seeds 100+ and was not used for tuning. Everything (config, seeds, git
commit, machine, raw rows) is written next to the summary.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics as st
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from donotmix import generate  # noqa: E402
from donotmix.analysis import RiskModel, analyze  # noqa: E402
from donotmix.config import ProjectConfig  # noqa: E402
from donotmix.engine import ScanOptions, find_hazards, merge_hazards  # noqa: E402
from donotmix.runner import Persona, PythonRunner  # noqa: E402
from donotmix.space import Param, Space, constraint_from_spec  # noqa: E402

from benchmarks.synth import Case, make_case  # noqa: E402

RANDOM_SEEDS = 10


def build(case: Case):
    space = Space([Param(f) for f in case.flags], [constraint_from_spec(c, i) for i, c in enumerate(case.constraints)],
                  tuple(case.baseline[f] for f in case.flags))
    a = analyze([case.code_dir], case.flags, root=Path(case.code_dir))
    return space, RiskModel(a, space.names, space.baseline)


def score(case: Case, space: Space, tests: list[tuple]) -> dict:
    """Detection metrics for an ordered test sequence (ground truth used here only)."""
    first = {}
    first_fail = None
    for pos, cfg in enumerate(tests, 1):
        states = dict(zip(space.names, cfg))
        fails, fired = case.oracle(states)
        if fails and first_fail is None:
            first_fail = pos
        for fid in fired:
            first.setdefault(fid, pos)
    m, n = len(case.faults), max(1, len(tests))
    p = len(first) / m if m else 1.0
    napfd = p - sum(first.values()) / (n * m) + p / (2 * n) if m else 1.0
    by_order = {}
    for k in (2, 3, 4):
        fs = [f for f in case.faults if f.order == k]
        if fs:
            by_order[str(k)] = sum(1 for f in fs if f.id in first) / len(fs)
    return {"detected": len(first), "faults": m, "first_failure": first_fail, "napfd": round(napfd, 4), "by_order": by_order, "tests": len(tests)}


def run_case(args) -> list[dict]:
    n, seed, colocate, split, tmp = args
    case = make_case(n, seed, colocate=colocate, workdir=Path(tmp))
    space, risk = build(case)
    rows = []
    t0 = time.perf_counter(); pw = generate.tway(space, 2, seed=0).tests; t_pw = time.perf_counter() - t0
    t0 = time.perf_counter(); t3 = generate.tway(space, 3, seed=0).tests; t_t3 = time.perf_counter() - t0
    t0 = time.perf_counter(); rk = generate.risk_based(space, risk.tuple_weight, risk.hyperedges_idx(), t=3, seed=0).tests; t_rk = time.perf_counter() - t0
    # ablations: which prior carries the gain?
    a = risk.a
    rc = RiskModel(a, space.names, space.baseline, use_change=False)
    rch = RiskModel(a, space.names, space.baseline, use_code=False)
    t0 = time.perf_counter(); rk_code = generate.risk_based(space, rc.tuple_weight, rc.hyperedges_idx(), t=3, seed=0).tests; t_rc = time.perf_counter() - t0
    t0 = time.perf_counter(); rk_chg = generate.risk_based(space, rch.tuple_weight, rch.hyperedges_idx(), t=3, seed=0).tests; t_rch = time.perf_counter() - t0
    B2, B3 = len(pw), len(t3)
    base = {"n": n, "seed": seed, "colocate": colocate, "split": split, "case": case.name, "faults": len(case.faults),
            "fault_orders": [f.order for f in case.faults], "feasible": space.count_feasible() if n <= 20 else None}
    suites = {"pairwise": (pw, t_pw), "3-way": (t3, t_t3), "risk": (rk, t_rk), "risk:code-only": (rk_code, t_rc), "risk:change-only": (rk_chg, t_rch)}
    if n <= 14:
        t0 = time.perf_counter(); ex = generate.exhaustive(space, cap=1 << 15).tests; t_ex = time.perf_counter() - t0
        suites["exhaustive"] = (ex, t_ex)
        sc = score(case, space, ex)
        base["exhaustive_detectable"] = sc["detected"]
    rand = []
    for rs in range(RANDOM_SEEDS):
        t0 = time.perf_counter(); r = generate.random_suite(space, max(B3, 1), seed=rs).tests; rand.append((r, time.perf_counter() - t0))
    for label, B in (("B2", B2), ("B3", B3)):
        for name, (tests, secs) in suites.items():
            if name == "exhaustive":
                continue
            sc = score(case, space, tests[:B])
            rows.append({**base, "exp": "fixed_budget", "budget_label": label, "budget": B, "strategy": name, "gen_seconds": round(secs, 3), **sc})
        for rs, (tests, secs) in enumerate(rand):
            sc = score(case, space, tests[:B])
            rows.append({**base, "exp": "fixed_budget", "budget_label": label, "budget": B, "strategy": "random", "random_seed": rs, "gen_seconds": round(secs, 3), **sc})
    # E2 coverage cost
    for t, suite in ((2, pw), (3, t3)):
        rows.append({**base, "exp": "coverage_cost", "t": t, "strategy": f"greedy-{t}way", "tests": len(suite)})
        uni = set(generate.feasible_tuples(space, t))
        for rs in range(5):
            covered, cnt = set(), 0
            r = generate.random_suite(space, min(20000, space.count_feasible() if n <= 20 else 20000), seed=1000 + rs).tests
            for cfg in r:
                cnt += 1
                covered.update(tp for tp in generate.tuples_of(cfg, t) if tp in uni)
                if len(covered) == len(uni):
                    break
            rows.append({**base, "exp": "coverage_cost", "t": t, "strategy": "random", "random_seed": rs, "tests": cnt, "reached": len(covered) == len(uni)})
    if n <= 20:
        rows.append({**base, "exp": "coverage_cost", "t": 0, "strategy": "exhaustive", "tests": base["feasible"]})
    # E3 minimization through the real pipeline
    runner = PythonRunner(case.runner_fn())
    cfg = ProjectConfig()
    cfg.minimize.exhaustive_limit = 128
    execs = runner.run_many(space.names, [(c, Persona("environment")) for c in rk])
    t0 = time.perf_counter()
    results, mini = find_hazards(space, runner, [Persona("environment")], execs, cfg, 1, ScanOptions())
    hazards = merge_hazards(space, results, execs, runner)
    first_fail_cfg = next((e.config for e in execs if not e.passed), None)
    raw_diff = len(space.diff(space.baseline, first_fail_cfg)) if first_fail_cfg else None
    for h in hazards:
        core = set(h.get("core", {}).items())
        full = core | {(k, v["value"]) for k, v in (h.get("pinned") or {}).items()}
        truth = [set(f.literals.items()) for f in case.faults]
        if core in truth:
            verdict = "exact"
        elif any(core < t <= full for t in truth):
            verdict = "exact-with-implied"
        elif any(t < core for t in truth):
            verdict = "overspecified"
        elif any(core < t for t in truth):
            verdict = "underspecified"
        else:
            verdict = "false_positive"
        rows.append({**base, "exp": "minimization", "hazard": h["id"], "core_size": len(core), "verdict": verdict,
                     "generality": (h.get("generality") or {}).get("verdict"), "extra_tests": h.get("minimization_tests"),
                     "true_size": min((len(t) for t in truth if t <= core or core <= t), default=None), "raw_counterexample_diff": raw_diff})
    detected_ids = {fid for e in execs for fid in case.oracle(dict(zip(space.names, e.config)))[1]}
    rows.append({**base, "exp": "minimization_summary", "detected_by_plan": len(detected_ids), "hazards": len(hazards),
                 "matched_faults": len({frozenset(f.literals.items()) for f in case.faults if frozenset(f.literals.items()) in {frozenset(h.get('core', {}).items()) for h in hazards}}),
                 "minimize_tests": mini.used, "seconds": round(time.perf_counter() - t0, 2)})
    return rows


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(st.mean(xs), 4) if xs else None


def _std(xs):
    xs = [x for x in xs if x is not None]
    return round(st.pstdev(xs), 4) if len(xs) > 1 else 0.0


def summarize(rows: list[dict], split: str = "eval") -> dict:
    R = [r for r in rows if r.get("split") == split]
    fixed = []
    keys = sorted({(r["n"], r["colocate"], r["budget_label"], r["strategy"]) for r in R if r["exp"] == "fixed_budget"})
    for n, co, bl, sname in keys:
        rs = [r for r in R if r["exp"] == "fixed_budget" and r["n"] == n and r["colocate"] == co and r["budget_label"] == bl and r["strategy"] == sname]
        orders = {}
        for k in ("2", "3", "4"):
            v = [r["by_order"].get(k) for r in rs if k in r["by_order"]]
            if v:
                orders[k] = _mean(v)
        fixed.append({"n": n, "colocate": co, "budget_label": bl, "budget": _mean([r["budget"] for r in rs]), "strategy": sname,
                      "detected_mean": _mean([r["detected"] / r["faults"] for r in rs]), "detected_std": _std([r["detected"] / r["faults"] for r in rs]),
                      "faults_total": sum(r["faults"] for r in rs) // max(1, len({(r['case']) for r in rs})) if rs else 0,
                      "detection_by_order": orders, "first_failure_mean": _mean([r["first_failure"] for r in rs]),
                      "apfd_mean": _mean([r["napfd"] for r in rs]), "tests_used_mean": _mean([r["tests"] for r in rs]),
                      "seconds_mean": _mean([r["gen_seconds"] for r in rs]), "runs": len(rs)})
    cov = []
    for n, t, sname in sorted({(r["n"], r["t"], r["strategy"]) for r in R if r["exp"] == "coverage_cost"}):
        rs = [r for r in R if r["exp"] == "coverage_cost" and r["n"] == n and r["t"] == t and r["strategy"] == sname and r["colocate"] == 0.7]
        if rs:
            cov.append({"n": n, "t": t, "strategy": sname, "tests_mean": _mean([r["tests"] for r in rs]), "tests_std": _std([r["tests"] for r in rs]), "runs": len(rs),
                        "all_reached": all(r.get("reached", True) for r in rs)})
    mini = []
    for n, co in sorted({(r["n"], r["colocate"]) for r in R if r["exp"] == "minimization"}):
        rs = [r for r in R if r["exp"] == "minimization" and r["n"] == n and r["colocate"] == co]
        cnt = len(rs)
        ms = [r for r in R if r["exp"] == "minimization_summary" and r["n"] == n and r["colocate"] == co]
        mini.append({"n": n, "colocate": co, "count": cnt,
                     "exact_rate": round(sum(r["verdict"] == "exact" for r in rs) / cnt, 4),
                     "exact_or_implied_rate": round(sum(r["verdict"] in ("exact", "exact-with-implied") for r in rs) / cnt, 4),
                     "overspecified_rate": round(sum(r["verdict"] == "overspecified" for r in rs) / cnt, 4),
                     "underspecified_rate": round(sum(r["verdict"] == "underspecified" for r in rs) / cnt, 4),
                     "false_positive_rate": round(sum(r["verdict"] == "false_positive" for r in rs) / cnt, 4),
                     "extra_tests_mean": _mean([r["extra_tests"] for r in rs]), "core_size_mean": _mean([r["core_size"] for r in rs]),
                     "true_size_mean": _mean([r["true_size"] for r in rs]), "raw_counterexample_diff_mean": _mean([r["raw_counterexample_diff"] for r in rs]),
                     "faults_matched": sum(r["matched_faults"] for r in ms), "faults_detected_by_plan": sum(r["detected_by_plan"] for r in ms),
                     "faults_total": sum(r["faults"] for r in ms)})
    return {"fixed_budget": fixed, "coverage_cost": cov, "minimization": mini}


def hypotheses(summary: dict, skill: dict | None) -> list[dict]:
    out = []
    fb = summary["fixed_budget"]
    # H1
    ev = []
    ok = True
    for row in [r for r in fb if r["strategy"] == "3-way" and r["budget_label"] == "B3" and r["colocate"] == 0.7]:
        low = [row["detection_by_order"].get(k) for k in ("2", "3") if row["detection_by_order"].get(k) is not None]
        exh = next((c for c in summary["coverage_cost"] if c["n"] == row["n"] and c["strategy"] == "exhaustive"), None)
        ev.append(f"n={row['n']}: {row['tests_used_mean']:.0f} tests detect {100 * min(low):.0f}% of <=3-way faults"
                  + (f" vs {exh['tests_mean']:.0f} feasible configs for exhaustive" if exh else " (exhaustive infeasible to run)"))
        ok &= min(low) >= 0.95
    out.append({"id": "H1", "statement": "Combinatorial (3-way) suites find low-order interaction faults with far fewer tests than exhaustive testing.",
                "result": "supported" if ok and ev else "not-supported", "evidence": "; ".join(ev)})
    # H2
    ev, wins, total = [], 0, 0
    for row in [r for r in fb if r["strategy"] == "risk" and r["budget_label"] == "B2"]:
        rnd = next(r for r in fb if r["strategy"] == "random" and r["n"] == row["n"] and r["colocate"] == row["colocate"] and r["budget_label"] == "B2")
        total += 1
        wins += row["detected_mean"] > rnd["detected_mean"] and row["apfd_mean"] >= rnd["apfd_mean"]
        ev.append(f"n={row['n']} colocate={row['colocate']}: risk {100 * row['detected_mean']:.0f}% / NAPFD {row['apfd_mean']:.2f} vs random {100 * rnd['detected_mean']:.0f}% / {rnd['apfd_mean']:.2f}")
    out.append({"id": "H2", "statement": "Risk-weighted prioritization finds more faults earlier than random testing at the same budget.",
                "result": "supported" if wins == total and total else ("mixed" if wins else "not-supported"), "evidence": "; ".join(ev)})
    # H3
    ev, ok = [], True
    for m in summary["minimization"]:
        ev.append(f"n={m['n']} colocate={m['colocate']}: {100 * m['exact_rate']:.0f}% exact ({100 * m['exact_or_implied_rate']:.0f}% incl. constraint-implied literals), 0 FP rate {m['false_positive_rate']}, core {m['core_size_mean']} literals vs raw counterexample diff {m['raw_counterexample_diff_mean']}, {m['extra_tests_mean']} extra tests")
        ok &= (m["core_size_mean"] or 99) < (m["raw_counterexample_diff_mean"] or 0) and m["exact_or_implied_rate"] >= 0.8
    out.append({"id": "H3", "statement": "Minimization yields much smaller, correct reproduction conditions.", "result": "supported" if ok and ev else "mixed", "evidence": "; ".join(ev)})
    if skill:
        out.append({"id": "H4", "statement": "The Agent Skill reduces context and tool calls for the same verification task.", "result": skill.get("result", "see skill eval"),
                    "evidence": skill.get("evidence", "")})
    else:
        out.append({"id": "H4", "statement": "The Agent Skill reduces context and tool calls for the same verification task.", "result": "not-run",
                    "evidence": "run benchmarks/skill_eval.py"})
    return out


def machine() -> dict:
    return {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(), "cpus": os.cpu_count()}


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip() or None
    except OSError:
        return None


SUITES = {
    "quick": {"sizes": [10, 15], "eval_seeds": 6, "dev_seeds": 2, "adversarial": [15]},
    "full": {"sizes": [10, 15, 20, 30], "eval_seeds": 20, "dev_seeds": 5, "adversarial": [20]},
}


def main(suite: str = "quick", out: str = "benchmarks/results", seeds: int | None = None, jobs: int = 8) -> int:
    if suite == "mixmart":
        from benchmarks.mixmart import main as mm

        return mm(out=out, jobs=jobs)
    spec = SUITES[suite]
    eval_seeds = seeds or spec["eval_seeds"]
    tmp = tempfile.mkdtemp(prefix="donotmix-bench-")
    work = []
    for n in spec["sizes"]:
        work += [(n, s, 0.7, "dev", tmp) for s in range(spec["dev_seeds"])]
        work += [(n, 100 + s, 0.7, "eval", tmp) for s in range(eval_seeds)]
    for n in spec["adversarial"]:
        work += [(n, 100 + s, 0.0, "eval", tmp) for s in range(eval_seeds)]
    t0 = time.perf_counter()
    rows = []
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for i, res in enumerate(pool.map(run_case, work), 1):
            rows.extend(res)
            print(f"[{i}/{len(work)}] {res[0]['case']} ({time.perf_counter() - t0:.0f}s)", file=sys.stderr)
    shutil.rmtree(tmp, ignore_errors=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    od = ROOT / out / f"{stamp}-{suite}"
    od.mkdir(parents=True, exist_ok=True)
    with (od / "raw.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    skill_path = ROOT / out / "skill_eval" / "summary.json"
    skill = json.loads(skill_path.read_text()) if skill_path.exists() else None
    summary = {"schema": "donotmix.benchmark/v1", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "suite": suite,
               "git_commit": git_commit(), "command": f"python -m benchmarks.run --suite {suite}" + (f" --seeds {seeds}" if seeds else ""),
               "machine": machine(), "config": {**spec, "eval_seeds_used": eval_seeds, "random_seeds": RANDOM_SEEDS, "split": "eval seeds 100+, dev seeds 0..k excluded"},
               "seconds": round(time.perf_counter() - t0, 1), **summarize(rows, "eval")}
    summary["dev_summary"] = summarize(rows, "dev")
    summary["hypotheses"] = hypotheses(summary, skill)
    mm_path = ROOT / out / "mixmart" / "summary.json"
    if mm_path.exists():
        summary["mixmart"] = json.loads(mm_path.read_text())
    (od / "summary.json").write_text(json.dumps(summary, indent=1))
    latest = ROOT / out / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "summary.json").write_text(json.dumps(summary, indent=1))
    (latest / "SOURCE").write_text(os.path.relpath(od, ROOT) + "\n")
    print(json.dumps({"out": os.path.relpath(od, ROOT), "hypotheses": [(h["id"], h["result"]) for h in summary["hypotheses"]]}, indent=1))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="quick", choices=["quick", "full", "mixmart"])
    ap.add_argument("--out", default="benchmarks/results")
    ap.add_argument("--seeds", type=int, default=None)
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()
    raise SystemExit(main(a.suite, a.out, a.seeds, a.jobs))
