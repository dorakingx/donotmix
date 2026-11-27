"""Remediation proposals: find the smallest verified flag change that removes live hazards.

Candidates are single flips of core literals (then pairs, if no single flip works). Each
candidate state is executed against the full suite for every persona before it is proposed,
so a proposal carries evidence, not a guess. Turning features *off* is preferred over
turning them on, and fewer changes beat more.
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone
from pathlib import Path

from .approval import AuditLog, is_protected, save_proposal
from .config import ProjectConfig
from .engine import build_space, make_runner
from .runner import Persona


def propose(cfg: ProjectConfig, run_dir: Path, max_pairs: int = 30) -> list[dict]:
    report = json.loads((run_dir / "report.json").read_text())
    snap = json.loads((run_dir / "snapshot.json").read_text())
    env = snap["environment"]
    live = [h for h in report.get("hazards", []) if h.get("status") == "live"]
    if not live:
        return []
    if is_protected(env["name"], cfg.protected_environments):
        return [{"id": None, "refused": f"{env['name']} is protected; DoNotMix only reports, it will not propose changes there"}]
    space, _ = build_space(cfg, snap)
    runner = make_runner(cfg, snap, run_dir / "remediate")
    personas = [Persona(id=p.id, identifier=p.identifier, traits=tuple(sorted(p.traits.items()))) for p in cfg.personas] or [Persona("environment")]

    flippable = []
    for h in live:
        for k, v in h.get("core", {}).items():
            if k in space.index and isinstance(v, bool):
                flippable.append(k)
    flippable = list(dict.fromkeys(flippable))
    # prefer turning on-features off
    flippable.sort(key=lambda k: (not space.baseline[space.index[k]], k))

    def evaluate(changes: tuple[str, ...]):
        cfg_ = list(space.baseline)
        for k in changes:
            cfg_[space.index[k]] = not cfg_[space.index[k]]
        cfg_ = tuple(cfg_)
        if not space.feasible(cfg_):
            return None
        execs = runner.run_many(space.names, [(cfg_, p) for p in personas], jobs=cfg.runner.jobs)
        failing = sorted({f"{a.persona}:{a.test.split('::')[-1]}" for e in execs for a in e.atoms})
        return cfg_, execs, failing

    candidates = []
    for k in flippable:
        r = evaluate((k,))
        if r:
            candidates.append(((k,), *r))
    if not any(not c[3] for c in candidates):
        for pair in itertools.islice(itertools.combinations(flippable, 2), max_pairs):
            r = evaluate(pair)
            if r:
                candidates.append((pair, *r))
    candidates.sort(key=lambda c: (len(c[3]), len(c[0]), sum(space.baseline[space.index[k]] is False for k in c[0])))
    proposals = []
    log = AuditLog(cfg.root)
    for changes, cfg_, execs, failing in candidates[:3]:
        resolved = [h["id"] for h in live if not all(cfg_[space.index[k]] == v for k, v in h["core"].items() if k in space.index)]
        pid = "P-" + datetime.now(timezone.utc).strftime("%m%d%H%M%S") + "-" + "-".join(c[:10] for c in changes)
        p = {
            "id": pid,
            "kind": "mitigation",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": report["run_id"],
            "project": snap.get("project"),
            "api_url": snap.get("api_url"),
            "environment": {"name": env["name"], "key": env["key"]},
            "changes": [{"flag": k, "from": space.baseline[space.index[k]], "to": not space.baseline[space.index[k]]} for k in changes],
            "preconditions": {k: space.baseline[space.index[k]] for k in changes},
            "rationale": f"Breaks the hazardous combination(s) {', '.join(resolved)} by switching {', '.join(changes)} "
                         f"{'off' if all(space.baseline[space.index[k]] for k in changes) else 'on/off'} in {env['name']}.",
            "impact": f"Changes {len(changes)} flag(s) in {env['name']} only; users in this environment lose/gain: {', '.join(changes)}. No code changes.",
            "evidence": {"resolves": resolved, "verified_runs": len(execs), "verified_passes": sum(1 for e in execs if e.passed), "still_failing": failing,
                         "state_after": {k: cfg_[space.index[k]] for k in space.names}},
            "status": "awaiting-human-approval",
        }
        save_proposal(cfg.root, p)
        log.append("proposed", proposal=pid, kind="mitigation", changes=p["changes"], resolves=resolved, still_failing=failing)
        proposals.append(p)
    return proposals
