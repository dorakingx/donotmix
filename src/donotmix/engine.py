"""Scan orchestration: snapshot -> space -> analysis -> plan -> execute -> minimize -> explain -> report."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, generate
from .analysis import RiskModel, analyze
from .config import ConfigError, ProjectConfig, load_incidents
from .flagsmith import FlagsmithCLI, segment_scoped_flags, take_snapshot, load_snapshot
from .minimize import FAIL, Minimizer, nearest, status_for
from .runner import Atom, CommandRunner, Execution, Persona, PythonRunner, Runner
from .space import Constraint, Param, Space, constraint_from_spec

EXIT_CLEAN, EXIT_LATENT, EXIT_LIVE = 0, 10, 11
EXIT_CONFIG, EXIT_FLAGSMITH, EXIT_RUNNER, EXIT_APPROVAL = 3, 4, 5, 6


class RunnerError(RuntimeError):
    exit_code = EXIT_RUNNER


@dataclass
class ScanOptions:
    environment: str | None = None
    snapshot_path: str | None = None
    strategy: str | None = None
    strength: int | None = None
    budget: int | None = None
    seed: int | None = None
    jobs: int | None = None
    ai: str | None = None
    dry_run: bool = False
    minimize: bool = True
    out_dir: str | None = None
    progress: object = None  # callable(str)


def _log(opts: ScanOptions, msg: str) -> None:
    if callable(opts.progress):
        opts.progress(msg)


def git_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except OSError:
        return None


# --------------------------------------------------------------------------------------


def get_snapshot(cfg: ProjectConfig, opts: ScanOptions) -> dict:
    if opts.snapshot_path:
        snap = load_snapshot(opts.snapshot_path)
        snap.setdefault("source", "file")
        snap["mode"] = "offline-snapshot"
        return snap
    env = opts.environment or cfg.environment
    if not env:
        raise ConfigError("no environment: pass --environment or set `environment` in donotmix.yaml")
    cli = FlagsmithCLI(project=cfg.project, api_url=cfg.api_url)
    snap = take_snapshot(cli, env)
    snap["mode"] = "live"
    return snap


def build_space(cfg: ProjectConfig, snap: dict) -> tuple[Space, list[str]]:
    notes = []
    available = [f["name"] for f in snap["flags"]]
    names = cfg.flags or available
    missing = [n for n in names if n not in available]
    if missing:
        raise ConfigError(f"flags not found in environment {snap['environment']['name']}: {missing}")
    names = [n for n in names if n not in set(cfg.ignore_flags)]
    baseline = {f["name"]: bool(f["enabled"]) for f in snap["flags"]}
    constraints: list[Constraint] = [constraint_from_spec(c, i) for i, c in enumerate(cfg.constraints)]
    if cfg.derive_segment_constraints:
        for flag, segs in segment_scoped_flags(snap).items():
            if flag in names:
                constraints.append(Constraint(id=f"segment-scoped:{flag}", rule=f"!{flag}", reason=f"enabled only through segment override(s) {segs}", source="derived:flagsmith-segments"))
                notes.append(f"derived constraint: {flag} stays off at environment level (segment-scoped: {segs})")
    try:
        space = Space([Param(n) for n in names], constraints, tuple(baseline[n] for n in names))
    except ValueError as e:
        raise ConfigError(str(e)) from e
    return space, notes


def make_runner(cfg: ProjectConfig, snap: dict, run_dir: Path) -> Runner:
    rc = cfg.runner
    if rc.type == "command":
        if not rc.command:
            raise ConfigError("runner.command is empty")
        cwd = str((cfg.root / rc.cwd).resolve()) if rc.cwd else str(cfg.root)
        return CommandRunner(rc.command, snap["document"], run_dir / "exec", cwd=cwd, timeout=rc.timeout)
    if rc.type == "python":
        import importlib

        mod, _, fn = rc.target.partition(":")
        return PythonRunner(getattr(importlib.import_module(mod), fn))
    raise ConfigError(f"unknown runner type {rc.type!r}")


def make_plan(space: Space, strategy: str, strength: int, budget: int | None, seed: int, risk: RiskModel | None) -> generate.Plan:
    if strategy == "exhaustive":
        return generate.exhaustive(space, cap=budget or 4096)
    if strategy == "random":
        if not budget:
            raise ConfigError("random strategy needs --budget")
        return generate.random_suite(space, budget, seed=seed)
    if strategy in ("tway", "pairwise", "t-way"):
        t = 2 if strategy == "pairwise" else strength
        return generate.tway(space, t=t, budget=budget, seed=seed)
    if strategy == "risk":
        if risk is None:
            raise ConfigError("risk strategy needs analysis")
        return generate.risk_based(space, risk.tuple_weight, risk.hyperedges_idx(), t=strength, budget=budget, seed=seed)
    raise ConfigError(f"unknown strategy {strategy!r} (exhaustive|random|pairwise|tway|risk)")


# --------------------------------------------------------------------------------------


def _atom_status(ex: Execution, atom: Atom) -> str:
    return status_for(ex, atom)


def find_hazards(space: Space, runner: Runner, personas: list[Persona], plan_execs: list[Execution], cfg: ProjectConfig, jobs: int, opts: ScanOptions) -> tuple[list, Minimizer]:
    pmap = {p.id: p for p in personas}
    mcfg = cfg.minimize
    mini = Minimizer(space, runner, jobs=jobs, global_limit=mcfg.global_limit, exhaustive_limit=mcfg.exhaustive_limit, sample=mcfg.sample, seed=cfg.strategy.seed, max_tests=mcfg.max_tests)
    atoms: dict[Atom, list[tuple]] = {}
    for ex in plan_execs:
        for a in ex.atoms:
            atoms.setdefault(a, []).append(ex.config)
    by_persona: dict[str, list[Execution]] = {}
    for ex in plan_execs:
        by_persona.setdefault(ex.persona, []).append(ex)
    results = []
    order = sorted(atoms, key=lambda a: (-len(atoms[a]), a.persona, a.test))
    for atom in order:
        persona = pmap[atom.persona]
        failing = list(dict.fromkeys(atoms[atom]))
        explained: set = set()
        for _ in range(mcfg.max_hazards_per_atom):
            todo = [c for c in failing if c not in explained]
            if not todo:
                break
            # reference: the live state if it passes for this atom, else the nearest passing config
            base_ex = runner.run(space.names, space.baseline, persona)
            if _atom_status(base_ex, atom) == "PASS":
                ref, ref_kind = space.baseline, "live-environment"
            else:
                passing = [e.config for e in by_persona.get(atom.persona, []) if _atom_status(e, atom) == "PASS"]
                ref = nearest(passing, space.baseline)
                ref_kind = "nearest-passing-config"
                if ref is None:
                    off = tuple(p.domain[0] for p in space.params)
                    if space.feasible(off) and _atom_status(runner.run(space.names, off, persona), atom) == "PASS":
                        ref, ref_kind = off, "all-defaults"
                    else:
                        _log(opts, f"  ! no passing reference for {atom.test} ({atom.persona}); cannot minimize")
                        results.append(("unminimized", atom, persona, todo[0]))
                        break
            fail = nearest(todo, ref)
            _log(opts, f"  minimizing {atom.test} [{atom.kind}] for persona {atom.persona} ...")
            m = mini.minimize(ref, ref_kind, fail, atom, persona)
            results.append(("ok", m))
            for c in todo:
                if all(c[i] == v for i, v in m.core.items()):
                    explained.add(c)
            if not any(all(c[i] == v for i, v in m.core.items()) for c in todo):
                explained.add(fail)
    return results, mini


def _signature(kind: str, message: str) -> str:
    import re

    msg = re.sub(r"\d+", "#", message or "")[:80]
    return f"{kind}: {msg}" if kind != "AssertionError" else f"{kind}: {msg.split(' != ')[0]}"


def _hazard_id(core_named: dict, tests: list[str], personas: list[str]) -> str:
    h = hashlib.sha256(json.dumps([sorted(core_named.items()), sorted(tests), sorted(personas)], default=str).encode()).hexdigest()
    return h[:6]


def merge_hazards(space: Space, results: list, plan_execs: list[Execution], runner: Runner) -> list[dict]:
    """Group minimized atoms that share the same core (same root condition)."""
    groups: dict[tuple, dict] = {}
    unmin = []
    for r in results:
        if r[0] != "ok":
            unmin.append(r)
            continue
        m = r[1]
        key = (m.persona.id, tuple(sorted(m.core.items())))
        g = groups.setdefault(key, {"m": m, "atoms": [], "tests_used": 0})
        g["atoms"].append(m.atom)
        g["tests_used"] += m.tests_used
    # merge identical cores across personas
    by_core: dict[tuple, list] = {}
    for (pid, core), g in groups.items():
        by_core.setdefault(core, []).append(g)
    hazards = []
    for core, gs in by_core.items():
        m = gs[0]["m"]
        atoms = [a for g in gs for a in g["atoms"]]
        personas = sorted({a.persona for a in atoms})
        core_named = space.fmt_literals(m.core)
        tests = sorted({a.test for a in atoms})
        live = any(
            (space.baseline, a.persona) in runner.cache and a in runner.cache[(space.baseline, a.persona)].atoms for a in atoms
        )
        msgs = {}
        for g in gs:
            ex = runner.cache.get((g["m"].repro, g["m"].persona.id))
            if ex:
                msgs.update({k: v for k, v in ex.messages.items()})
        kinds = sorted({a.kind for a in atoms})
        hazards.append({
            "fingerprint": _hazard_id(core_named, tests, personas),
            "status": "live" if live else "latent",
            "severity": "critical" if live else ("high" if any(k != "AssertionError" for k in kinds) else "medium"),
            "failure_class": "crash" if any(k not in ("AssertionError", "Failure") for k in kinds) else "silent-wrong-result",
            "personas": personas,
            "atoms": [{"persona": a.persona, "test": a.test, "kind": a.kind, "message": msgs.get(a.key(), "")} for a in atoms],
            "core": core_named,
            "order": len(core_named),
            "pinned": {space.names[i]: {"value": m.repro[i], "by": cs} for i, cs in sorted(m.pinned.items())},
            "masked": [space.names[i] for i in m.masked_literals],
            "trigger": space.fmt_literals(m.trigger),
            "trigger_claim": m.trigger_claim,
            "reference": m.reference_kind,
            "reference_diff": {space.names[i]: m.reference[i] for i in space.diff(space.baseline, m.reference)},
            "generality": m.generality.as_dict(),
            "repro_config": space.to_dict(m.repro),
            "repro_diff_from_live": {space.names[i]: m.repro[i] for i in space.diff(space.baseline, m.repro)},
            "minimization_tests": sum(g["tests_used"] for g in gs),
        })
    for h in hazards:
        h["signatures"] = sorted({_signature(a["kind"], a.get("message", "")) for a in h["atoms"]})
    hazards.sort(key=lambda h: ({"critical": 0, "high": 1, "medium": 2}[h["severity"]], -len(h["personas"]), h["order"], sorted(h["core"])))
    for i, h in enumerate(hazards, 1):
        h["id"] = f"HZ-{i}"
        lits = " + ".join(f"{k}={'on' if v is True else 'off' if v is False else v}" for k, v in h["core"].items())
        h["title"] = f"{lits} -> {h['atoms'][0]['test'].split('::')[-1]} ({h['atoms'][0]['kind']})"
    # relations: same failure signature, or a core that strictly extends another hazard's core
    for h in hazards:
        rel = []
        for o in hazards:
            if o is h:
                continue
            if set(h["signatures"]) & set(o["signatures"]):
                rel.append({"id": o["id"], "relation": "same-failure-signature"})
            elif set(o["core"].items()) < set(h["core"].items()) and set(o["personas"]) & set(h["personas"]):
                rel.append({"id": o["id"], "relation": "extends"})
        h["related"] = rel
    for r in unmin:
        _, atom, persona, cfgx = r
        hazards.append({"id": f"HZ-U{len(hazards) + 1}", "status": "unminimized", "severity": "high", "personas": [atom.persona],
                        "atoms": [{"persona": atom.persona, "test": atom.test, "kind": atom.kind, "message": ""}],
                        "core": {}, "repro_config": space.to_dict(cfgx), "title": f"{atom.test} fails; no passing reference to minimize against"})
    return hazards


# --------------------------------------------------------------------------------------


def matrix(space: Space, executed: list[tuple], hazards: list[dict]) -> dict:
    n = len(space)
    pairs = []
    hazard_pairs: dict[tuple, list[str]] = {}
    for h in hazards:
        fl = sorted(h.get("core", {}))
        for a, b in itertools.combinations(fl, 2):
            hazard_pairs.setdefault((a, b), []).append(h["id"])
        if len(fl) == 1:
            hazard_pairs.setdefault((fl[0], fl[0]), []).append(h["id"])
    seen2 = set()
    for cfg in executed:
        for i, j in itertools.combinations(range(n), 2):
            seen2.add((i, cfg[i], j, cfg[j]))
    for i, j in itertools.combinations(range(n), 2):
        feas = [(vi, vj) for vi in space.params[i].domain for vj in space.params[j].domain if space.extendable({i: vi, j: vj})]
        cov = sum(1 for vi, vj in feas if (i, vi, j, vj) in seen2)
        a, b = space.names[i], space.names[j]
        hz = hazard_pairs.get((min(a, b), max(a, b)), [])
        pairs.append({"a": a, "b": b, "feasible": len(feas), "covered": cov, "infeasible": 4 - len(feas) if space.params[i].is_bool and space.params[j].is_bool else None,
                      "status": "hazard" if hz else ("covered" if cov == len(feas) else ("partial" if cov else "untested")), "hazards": hz})
    triples = []
    if n <= 16:
        seen3 = set()
        for cfg in executed:
            for c in itertools.combinations(range(n), 3):
                seen3.add(tuple((k, cfg[k]) for k in c))
        for c in itertools.combinations(range(n), 3):
            feas = [vals for vals in itertools.product(*(space.params[k].domain for k in c)) if space.extendable(dict(zip(c, vals)))]
            cov = sum(1 for vals in feas if tuple(zip(c, vals)) in seen3)
            names = [space.names[k] for k in c]
            hz = [h["id"] for h in hazards if len(h.get("core", {})) >= 3 and set(names) <= set(h["core"])]
            triples.append({"flags": names, "feasible": len(feas), "covered": cov, "hazards": hz})
    singles = {h["id"]: sorted(h["core"]) for h in hazards if h.get("core")}
    return {"flags": space.names, "pairs": pairs, "triples": triples, "hazard_cores": singles}


def scan(cfg: ProjectConfig, opts: ScanOptions) -> dict:
    t_start = time.perf_counter()
    root = cfg.root
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(opts.out_dir) if opts.out_dir else root / ".donotmix" / "runs"
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _log(opts, "snapshot: reading Flagsmith environment (read-only)")
    snap = get_snapshot(cfg, opts)
    (run_dir / "snapshot.json").write_text(json.dumps(snap, indent=1))
    space, notes = build_space(cfg, snap)

    constraints_info = {
        "declared": [{"id": c.id, "rule": c.rule, "reason": c.reason, "source": c.source} for c in space.constraints],
        "consistent": space.is_consistent(),
        "conflict": space.minimal_conflict(),
        "baseline_violations": space.violated(space.baseline),
        "feasible_configs": None,
        "total_configs": space.total_configs(),
        "notes": notes,
    }
    if not constraints_info["consistent"]:
        raise ConfigError(f"constraints are contradictory; minimal conflicting set: {constraints_info['conflict']}")
    constraints_info["feasible_configs"] = space.count_feasible()

    personas = [Persona(id=p.id, identifier=p.identifier, traits=tuple(sorted(p.traits.items()))) for p in cfg.personas] or [Persona("environment")]

    _log(opts, "analysis: scanning source for flag interactions")
    analysis = analyze([root / p for p in cfg.analysis.paths], space.names, load_incidents(cfg), root=root)
    risk = RiskModel(analysis, space.names, space.baseline)

    strategy = opts.strategy or cfg.strategy.name
    strength = opts.strength or cfg.strategy.strength
    budget = opts.budget if opts.budget is not None else cfg.strategy.budget
    seed = opts.seed if opts.seed is not None else cfg.strategy.seed
    jobs = opts.jobs or cfg.runner.jobs
    _log(opts, f"plan: strategy={strategy} t={strength} budget={budget} seed={seed}")
    plan = make_plan(space, strategy, strength, budget, seed, risk)
    tests = [space.baseline] + [c for c in plan.tests if c != space.baseline] if space.feasible(space.baseline) else list(plan.tests)

    report: dict = {
        "schema": "donotmix.report/v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "donotmix_version": __version__,
        "git_commit": git_commit(root),
        "mode": {"flagsmith": snap.get("mode", "live"), "ai": None, "dry_run": opts.dry_run},
        "environment": {k: snap.get(k) for k in ("source", "cli_version", "api_url", "project", "environment", "fetched_at", "state_hash")},
        "flags": [{"name": n, "live": space.baseline[i], "segment_scoped": segment_scoped_flags(snap).get(n, []),
                   "degree": analysis.degree().get(n, 0), "recency": analysis.recency.get(n, 0.0)} for i, n in enumerate(space.names)],
        "constraints": constraints_info,
        "personas": [p.as_dict() for p in personas],
        "plan": {"strategy": plan.strategy, "strength": strength, "budget": budget, "seed": seed, "configs": len(tests), "notes": plan.notes,
                 "includes_live_state": space.feasible(space.baseline)},
        "analysis": {"files_scanned": analysis.files_scanned, "hyperedges": analysis.to_dict()["hyperedges"][:25]},
    }
    for k in (2, 3):
        if k <= len(space):
            report.setdefault("coverage_planned", {})[str(k)] = generate.coverage(space, tests, k).as_dict()
    if opts.dry_run:
        report["verdict"] = {"status": "DRY_RUN", "exit_code": 0, "summary": f"would execute {len(tests)} configurations x {len(personas)} personas"}
        report["planned_tests"] = [{"index": i, "diff_from_live": {space.names[j]: c[j] for j in space.diff(space.baseline, c)}} for i, c in enumerate(tests)]
        _write(run_dir, report, root)
        return report

    runner = make_runner(cfg, snap, run_dir)
    _log(opts, f"execute: {len(tests)} configurations x {len(personas)} personas, {jobs} jobs")
    t_exec = time.perf_counter()
    work = [(c, p) for c in tests for p in personas]
    execs = runner.run_many(space.names, work, jobs=jobs)
    exec_time = time.perf_counter() - t_exec
    errors = [e for e in execs if e.error]
    base_execs = [e for e in execs if e.config == space.baseline]
    if errors and len(errors) == len(execs):
        raise RunnerError(f"test command failed to run for every configuration: {errors[0].error}\n{errors[0].log_tail[-800:]}")
    first_fail_idx = next((i for i, c in enumerate(tests) if any(not e.passed for e in execs if e.config == c)), None)

    hazards: list[dict] = []
    mini_tests = 0
    if opts.minimize and any(not e.passed for e in execs):
        _log(opts, "minimize: delta debugging + sensitivity + generality checks")
        results, mini = find_hazards(space, runner, personas, execs, cfg, jobs, opts)
        mini_tests = mini.used
        hazards = merge_hazards(space, results, execs, runner)

    # reproduction artifacts
    repro_dir = run_dir / "repro"
    repro_dir.mkdir(exist_ok=True)
    from .flagsmith import apply_config_to_document

    for h in hazards:
        doc = apply_config_to_document(snap["document"], h["repro_config"])
        p = repro_dir / f"{h['id']}.environment.json"
        p.write_text(json.dumps(doc, indent=1))
        persona = next((pp for pp in personas if pp.id == h["personas"][0]), personas[0])
        envs = [f"DONOTMIX_ENV_DOCUMENT={_rel(p, root)}"]
        if persona.identifier:
            envs.append(f"DONOTMIX_IDENTITY={persona.identifier}")
        if persona.traits:
            envs.append(f"DONOTMIX_TRAITS='{json.dumps(dict(persona.traits))}'")
        cmd = cfg.runner.command.replace(" --junitxml={junit}", "").replace("{junit}", "/dev/null") if cfg.runner.type == "command" else f"python -c 'import {cfg.runner.target}'"
        h["effective_flags"] = effective_flags(p, persona)
        if h["effective_flags"]:
            h["overrides_applied"] = {k: v for k, v in h["effective_flags"].items() if k in h["repro_config"] and v != h["repro_config"][k]}
        h["repro"] = {"document": _rel(p, root), "persona": persona.id, "command": " ".join(envs) + " " + cmd,
                      "donotmix": f"donotmix reproduce {h['id']} --run {run_id}"}

    from .ai import make_analyst
    from .evidence import attach_evidence

    attach_evidence(hazards, analysis, root)
    analyst = make_analyst(cfg.ai, opts.ai)
    _log(opts, f"explain: {analyst.mode}")
    for h in hazards:
        h["explanation"] = analyst.explain(h, report)
    report["ai"] = analyst.usage()
    report["mode"]["ai"] = analyst.mode

    live = [h for h in hazards if h.get("status") == "live"]
    latent = [h for h in hazards if h.get("status") == "latent"]
    base_failed = any(not e.passed for e in base_execs)
    if base_failed or live:
        status, code = "LIVE_HAZARD", EXIT_LIVE
        summary = f"The current {snap['environment']['name']} state fails ({len(live)} live hazard(s)); {len(latent)} latent hazard(s) more."
    elif hazards or any(not e.passed for e in execs):
        status, code = "LATENT_HAZARDS", EXIT_LATENT
        summary = f"The current state passes, but {len(latent) or 'some'} flag combination(s) reachable from it fail."
    else:
        status, code = "NO_HAZARDS_FOUND", EXIT_CLEAN
        summary = f"No failing combination found in {len(tests)} configurations."
    report["verdict"] = {
        "status": status, "exit_code": code, "summary": summary,
        "caveat": "Finite testing: a clean result means no failure was observed under the executed configurations, tests and personas. It is not a proof of safety.",
    }
    report["execution"] = {
        "runner": runner.name, "configs": len(tests), "personas": len(personas), "executions_plan": len(execs),
        "executions_minimize": mini_tests, "executions_total": runner.executions, "cache_hits": runner.cache_hits,
        "failed_executions": sum(1 for e in execs if not e.passed), "runner_errors": len(errors),
        "first_failure_config_index": first_fail_idx, "exec_seconds": round(exec_time, 2),
        "total_seconds": round(time.perf_counter() - t_start, 2),
    }
    executed = list(dict.fromkeys(e.config for e in execs))
    report["coverage"] = {str(k): generate.coverage(space, executed, k).as_dict() for k in (2, 3) if k <= len(space)}
    report["hazards"] = hazards
    report["matrix"] = matrix(space, executed, hazards)
    report["tests"] = [
        {"index": i, "diff_from_live": {space.names[j]: c[j] for j in space.diff(space.baseline, c)},
         "results": {e.persona: ("pass" if e.passed else "error" if e.error else "fail") for e in execs if e.config == c},
         "failed": sorted({a.test.split("::")[-1] for e in execs if e.config == c for a in e.atoms})}
        for i, c in enumerate(tests)
    ]
    _write(run_dir, report, root)
    return report


def effective_flags(document_path: Path, persona: Persona) -> dict | None:
    """Evaluate a document for a persona with the official Flagsmith SDK (offline mode), if installed."""
    try:
        from flagsmith import Flagsmith
        from flagsmith.offline_handlers import LocalFileHandler
    except ImportError:
        return None
    try:
        fs = Flagsmith(offline_mode=True, offline_handler=LocalFileHandler(str(document_path)))
        flags = fs.get_identity_flags(persona.identifier, traits=dict(persona.traits)) if persona.identifier else fs.get_environment_flags()
        return {f.feature_name: bool(f.enabled) for f in flags.all_flags()}
    except Exception:
        return None


def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(p)


def _write(run_dir: Path, report: dict, root: Path) -> None:
    from .report import render_html, render_markdown

    (run_dir / "report.json").write_text(json.dumps(report, indent=1, default=str))
    (run_dir / "report.md").write_text(render_markdown(report))
    (run_dir / "report.html").write_text(render_html(report))
    latest = run_dir.parent.parent / "latest.json"
    latest.write_text(json.dumps({"run_id": report["run_id"], "path": _rel(run_dir, root)}))


def latest_run(root: Path, run: str | None = None) -> Path:
    base = root / ".donotmix" / "runs"
    if run and run != "latest":
        p = base / run
    else:
        ptr = root / ".donotmix" / "latest.json"
        if not ptr.exists():
            raise ConfigError("no DoNotMix run found. Run `donotmix scan` first.")
        p = root / json.loads(ptr.read_text())["path"]
    if not (p / "report.json").exists():
        raise ConfigError(f"run {p} has no report.json")
    return p
