"""E4: DoNotMix on the real MixMart app, real pytest executions, live Flagsmith snapshot.

Exhaustive execution over all feasible states x personas gives the reference; each strategy
is then scored on which planted bugs (benchmarks/ground_truth/mixmart.yaml) its suite exposes
at budgets B2/B3, and the full DoNotMix scan's minimized hazards are matched to ground truth.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from donotmix import generate
from donotmix.analysis import RiskModel, analyze
from donotmix.config import load_config
from donotmix.engine import ScanOptions, build_space, get_snapshot, make_runner, scan
from donotmix.runner import Persona

ROOT = Path(__file__).resolve().parent.parent


def exposed(bug: dict, cfg_states: dict, persona: str, atoms) -> bool:
    cond = (bug.get("persona_conditions") or {}).get(persona, bug["condition"])
    if not all(cfg_states[k] == v for k, v in cond.items()):
        return False
    return any(a.test.split("::")[-1] in bug["tests"] and a.kind in bug["kinds"] for a in atoms)


def main(out: str = "benchmarks/results", jobs: int = 8) -> int:
    t0 = time.perf_counter()
    cfg = load_config(ROOT / "donotmix.yaml")
    gt = yaml.safe_load((ROOT / "benchmarks/ground_truth/mixmart.yaml").read_text())["bugs"]
    snap = get_snapshot(cfg, ScanOptions())
    space, _ = build_space(cfg, snap)
    personas = [Persona(p.id, p.identifier, tuple(sorted(p.traits.items()))) for p in cfg.personas]
    od = ROOT / out / "mixmart"
    od.mkdir(parents=True, exist_ok=True)
    runner = make_runner(cfg, snap, od / "exec")
    ex = generate.exhaustive(space).tests
    t1 = time.perf_counter()
    execs = runner.run_many(space.names, [(c, p) for c in ex for p in personas], jobs=jobs)
    exh_secs = time.perf_counter() - t1
    res = {(e.config, e.persona): e for e in execs}

    def detected(tests):
        found = {}
        first = None
        for pos, c in enumerate(tests, 1):
            st = dict(zip(space.names, c))
            for p in personas:
                e = res.get((c, p.id))
                if e is None:
                    e = runner.run(space.names, c, p)
                if not e.passed and first is None:
                    first = pos
                for b in gt:
                    if b["id"] not in found and exposed(b, st, p.id, e.atoms):
                        found[b["id"]] = pos
        return found, first

    truth, _ = detected(ex)
    a = analyze([cfg.root / p for p in cfg.analysis.paths], space.names, root=cfg.root)
    risk = RiskModel(a, space.names, space.baseline)
    pw = generate.tway(space, 2).tests
    t3 = generate.tway(space, 3).tests
    rk = generate.risk_based(space, risk.tuple_weight, risk.hyperedges_idx(), t=3).tests
    rows = []
    for label, B in (("B2", len(pw)), ("B3", len(t3))):
        for name, tests in (("pairwise", pw), ("3-way", t3), ("risk", rk)):
            f, first = detected(tests[:B])
            rows.append({"budget_label": label, "budget": B, "strategy": name, "bugs_found": len(f), "bugs": sorted(f), "first_failure": first})
        fs = []
        for s in range(10):
            f, first = detected(generate.random_suite(space, B, seed=s).tests)
            fs.append((len(f), first))
        rows.append({"budget_label": label, "budget": B, "strategy": "random", "bugs_found_mean": sum(x for x, _ in fs) / len(fs),
                     "first_failure_mean": sum((y or B + 1) for _, y in fs) / len(fs), "seeds": 10})
    # full DoNotMix scan (risk, t=3) on the live environment; match hazards to ground truth
    rep = scan(cfg, ScanOptions(out_dir=str(od / "runs"), jobs=jobs))
    matches = []
    for h in rep["hazards"]:
        core = h.get("core", {})
        hit, how = None, None
        full = {**core, **{k: v["value"] for k, v in (h.get("pinned") or {}).items()}}
        for b in gt:
            for p in h["personas"]:
                cond = (b.get("persona_conditions") or {}).get(p, b["condition"])
                if core == cond or (set(core.items()) <= set(cond.items()) <= set(full.items())):
                    hit, how = b["id"], "exact"
                elif hit is None and set(cond.items()) <= set(full.items()):
                    hit, how = b["id"], "implies (extra literals needed for this test to observe it)"
        matches.append({"hazard": h["id"], "status": h["status"], "core": core, "personas": h["personas"], "tests": sorted({x["test"].split("::")[-1] for x in h["atoms"]}),
                        "generality": h["generality"]["verdict"], "matches": hit, "match": how})
    summary = {
        "schema": "donotmix.mixmart-benchmark/v1", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "flagsmith": {"environment": snap["environment"]["name"], "api_url": snap.get("api_url"), "state_hash": snap["state_hash"], "source": snap["source"]},
        "flags": len(space), "personas": [p.id for p in personas], "feasible_configs": len(ex), "total_configs": space.total_configs(),
        "exhaustive_executions": len(execs), "exhaustive_seconds": round(exh_secs, 1), "planted_bugs": len(gt),
        "bugs_exposed_by_exhaustive": sorted(truth), "strategies": rows,
        "scan": {"run_id": rep["run_id"], "status": rep["verdict"]["status"], "configs": rep["plan"]["configs"], "executions_total": rep["execution"]["executions_total"],
                 "seconds": rep["execution"]["total_seconds"], "coverage": rep["coverage"], "hazards": matches,
                 "bugs_matched": sorted({m["matches"] for m in matches if m["matches"]}),
                 "unmatched_hazards": [m["hazard"] for m in matches if not m["matches"]]},
        "seconds": round(time.perf_counter() - t0, 1),
    }
    (od / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: summary[k] for k in ("feasible_configs", "bugs_exposed_by_exhaustive", "strategies")}, indent=1))
    print(json.dumps(summary["scan"]["bugs_matched"]))
    return 0
