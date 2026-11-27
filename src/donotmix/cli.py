"""DoNotMix command line.

Exit codes (stable, for CI):
  0   no failing combination observed
  10  latent hazards: the live state passes, some reachable combinations fail
  11  live hazard: the current environment state itself fails
  2   usage error            3  config / constraint error
  4   Flagsmith error (CLI missing, auth, connectivity)
  5   test runner error (the test command could not run)
  6   approval required / refused / drift detected
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .config import TEMPLATE, ConfigError, load_config
from .engine import EXIT_LIVE, ScanOptions, latest_run, scan as run_scan
from .flagsmith import FlagsmithCLI, FlagsmithError, take_snapshot
from .report import brief as make_brief
from .report import dumps, render_markdown

app = typer.Typer(add_completion=False, no_args_is_help=True, help="DoNotMix - Safe alone. Dangerous together. Combinatorial testing for Flagsmith feature flags.")

CONFIG_OPT = typer.Option("donotmix.yaml", "--config", "-c", help="Path to donotmix.yaml")


def _err(msg: str, code: int) -> None:
    typer.secho(f"donotmix: {msg}", fg="red", err=True)
    raise typer.Exit(code)


def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ConfigError as e:
        _err(str(e), 3)
    except FlagsmithError as e:
        _err(str(e), 4)
    except Exception as e:
        code = getattr(e, "exit_code", None)
        if code:
            _err(str(e), code)
        raise


def _progress(json_mode: bool):
    if json_mode:
        return lambda m: print(m, file=sys.stderr)
    return lambda m: typer.secho(f"> {m}", fg="yellow", err=True)


@app.command()
def version():
    """Print the DoNotMix version."""
    typer.echo(__version__)


@app.command()
def init(
    project: str = typer.Option(None, "--project", "-p", help="Flagsmith project name or id"),
    environment: str = typer.Option("Staging", "--environment", "-e"),
    force: bool = typer.Option(False, "--force"),
):
    """Create donotmix.yaml (and .donotmix/) in the current directory."""
    p = Path("donotmix.yaml")
    if p.exists() and not force:
        _err("donotmix.yaml already exists (use --force to overwrite)", 3)
    p.write_text(TEMPLATE.format(project=project or "YOUR_PROJECT", environment=environment))
    Path(".donotmix").mkdir(exist_ok=True)
    gi = Path(".gitignore")
    lines = gi.read_text().splitlines() if gi.exists() else []
    if ".donotmix/" not in lines:
        gi.write_text("\n".join(lines + [".donotmix/"]) + "\n")
    typer.echo("wrote donotmix.yaml; edit constraints, personas and runner.command, then run: donotmix doctor")


@app.command()
def doctor(config: str = CONFIG_OPT, json_out: bool = typer.Option(False, "--json")):
    """Check Flagsmith CLI, credentials, project/environment and runner configuration."""
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": ok, "detail": detail})

    try:
        cfg = load_config(config)
        add("config", True, str(Path(config).resolve()))
    except ConfigError as e:
        add("config", False, str(e))
        cfg = None
    cli = FlagsmithCLI(project=cfg.project if cfg else None, api_url=cfg.api_url if cfg else None)
    add("flagsmith-cli", cli.available(), cli.version() if cli.available() else "install: brew install Flagsmith/tap/flagsmith")
    if cli.available():
        try:
            st = cli.auth_status()
            add("flagsmith-auth", True, f"{st.get('kind')} credentials for {st.get('apiUrl')}")
        except FlagsmithError as e:
            add("flagsmith-auth", False, str(e)[:200])
        if cfg and cfg.environment:
            try:
                env = cli.environment(cfg.environment)
                add("environment", True, f"{env.get('name')} ({env.get('api_key')})")
            except FlagsmithError as e:
                add("environment", False, str(e)[:200])
    if cfg:
        add("runner", bool(cfg.runner.command or cfg.runner.target), cfg.runner.command or cfg.runner.target)
        add("protected-environments", True, ", ".join(cfg.protected_environments))
    ok = all(c["ok"] for c in checks)
    if json_out:
        typer.echo(json.dumps({"ok": ok, "checks": checks}))
    else:
        for c in checks:
            typer.echo(f"{'OK ' if c['ok'] else 'ERR'} {c['check']:<22} {c['detail']}")
    raise typer.Exit(0 if ok else 4)


@app.command()
def snapshot(
    environment: str = typer.Option(None, "--environment", "-e"),
    out: str = typer.Option(None, "--out", "-o", help="Write snapshot JSON here"),
    config: str = CONFIG_OPT,
    json_out: bool = typer.Option(False, "--json"),
):
    """Read-only snapshot of a Flagsmith environment (flags, segments, environment document)."""
    cfg = _guard(load_config, config)
    cli = FlagsmithCLI(project=cfg.project, api_url=cfg.api_url)
    snap = _guard(take_snapshot, cli, environment or cfg.environment)
    path = Path(out) if out else cfg.root / ".donotmix" / f"snapshot-{snap['environment']['name']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=1))
    summary = {"path": str(path), "environment": snap["environment"], "state_hash": snap["state_hash"],
               "flags": {f["name"]: f["enabled"] for f in snap["flags"]}, "segments": [s["name"] for s in snap["segments"]]}
    typer.echo(json.dumps(summary, indent=None if json_out else 1))


@app.command()
def analyze(config: str = CONFIG_OPT, top: int = typer.Option(10, "--top"), json_out: bool = typer.Option(False, "--json"),
            snapshot_path: str = typer.Option(None, "--snapshot")):
    """Static flag-dependency analysis: which flags meet in the same code (risk prior)."""
    from .analysis import analyze as run_analysis
    from .config import load_incidents
    from .engine import build_space, get_snapshot

    cfg = _guard(load_config, config)
    snap = _guard(get_snapshot, cfg, ScanOptions(snapshot_path=snapshot_path))
    space, _ = _guard(build_space, cfg, snap)
    a = run_analysis([cfg.root / p for p in cfg.analysis.paths], space.names, load_incidents(cfg), root=cfg.root)
    d = a.to_dict()
    d["hyperedges"] = d["hyperedges"][:top]
    if not json_out:
        typer.echo(f"{d['files_scanned']} files, {len(a.refs)} flag references")
        for e in d["hyperedges"]:
            typer.echo(f"  {e['weight']:>5}  {' + '.join(e['flags'])}   [{', '.join(sorted({x['kind'] for x in e['evidence']}))}]")
        return
    d.pop("references")
    typer.echo(json.dumps(d))


@app.command()
def scan(
    environment: str = typer.Option(None, "--environment", "-e", help="Flagsmith environment (name or key)"),
    strategy: str = typer.Option(None, "--strategy", "-s", help="exhaustive | random | pairwise | tway | risk"),
    strength: int = typer.Option(None, "--strength", "-t", help="interaction strength t (2..6)"),
    budget: int = typer.Option(None, "--budget", "-b", help="max configurations to execute"),
    seed: int = typer.Option(None, "--seed"),
    jobs: int = typer.Option(None, "--jobs", "-j"),
    ai: str = typer.Option(None, "--ai", help="rule | claude-cli | anthropic"),
    snapshot_path: str = typer.Option(None, "--snapshot", help="offline mode: use a saved snapshot instead of Flagsmith"),
    dry_run: bool = typer.Option(False, "--dry-run", help="plan only, execute nothing"),
    json_out: bool = typer.Option(False, "--json", help="print the full JSON report"),
    brief: bool = typer.Option(False, "--brief", help="print a compact JSON summary (for agents)"),
    config: str = CONFIG_OPT,
):
    """Snapshot -> plan -> execute real tests -> minimize -> report."""
    cfg = _guard(load_config, config)
    opts = ScanOptions(environment=environment, snapshot_path=snapshot_path, strategy=strategy, strength=strength, budget=budget,
                       seed=seed, jobs=jobs, ai=ai, dry_run=dry_run, progress=_progress(json_out or brief))
    report = _guard(run_scan, cfg, opts)
    if json_out:
        typer.echo(dumps(report))
    elif brief:
        typer.echo(json.dumps(make_brief(report), separators=(",", ":")))
    else:
        _print_human(report)
    raise typer.Exit(report["verdict"]["exit_code"])


def _print_human(r: dict) -> None:
    v = r["verdict"]
    color = {"LIVE_HAZARD": "red", "LATENT_HAZARDS": "yellow", "NO_HAZARDS_FOUND": "green"}.get(v["status"], "white")
    typer.secho(f"\n  [{v['status']}] {v['summary']}", fg=color, bold=True)
    cov = r.get("coverage") or {}
    e = r.get("execution") or {}
    if cov:
        typer.echo(f"  tested {r['plan']['configs']} configs x {len(r['personas'])} personas | 2-way {cov.get('2', {}).get('ratio', 0):.0%} | "
                   f"3-way {cov.get('3', {}).get('ratio', 0):.0%} | {e.get('executions_total')} executions | {e.get('total_seconds')}s | analysis: {r['mode']['ai']}")
    for h in r.get("hazards", []):
        g = h.get("generality") or {}
        typer.secho(f"\n  {h['id']} {h.get('status', '').upper():6} {h.get('title')}", fg="red" if h.get("status") == "live" else "yellow")
        typer.echo(f"      core: {', '.join(f'{k}={v}' for k, v in h.get('core', {}).items())}   generality: {g.get('verdict')} (fails {g.get('failed')}/{g.get('checked')}"
                   + (f", masked {g['masked']}" if g.get('masked') else "") + (", exhaustive)" if g.get('exhaustive') else ", sampled)"))
        if h.get("repro"):
            typer.echo(f"      repro: {h['repro']['donotmix']}")
    typer.echo(f"\n  report: .donotmix/runs/{r['run_id']}/report.md   (dashboard: donotmix dashboard)")
    typer.echo(f"  {v.get('caveat', '')}\n")


@app.command()
def reproduce(hazard: str, run: str = typer.Option("latest", "--run"), config: str = CONFIG_OPT, json_out: bool = typer.Option(False, "--json")):
    """Re-execute a hazard's minimal reproduction. Exit 10 if it still fails, 0 if it no longer reproduces."""
    from .engine import build_space, make_runner
    from .minimize import status_for
    from .runner import Atom, Persona

    cfg = _guard(load_config, config)
    rd = _guard(latest_run, cfg.root, run)
    report = json.loads((rd / "report.json").read_text())
    snap = json.loads((rd / "snapshot.json").read_text())
    h = next((x for x in report.get("hazards", []) if x["id"] == hazard or x.get("fingerprint") == hazard), None)
    if not h:
        _err(f"hazard {hazard} not in run {report['run_id']}", 3)
    space, _ = _guard(build_space, cfg, snap)
    runner = make_runner(cfg, snap, rd / "reproduce")
    pmap = {p.id: Persona(id=p.id, identifier=p.identifier, traits=tuple(sorted(p.traits.items()))) for p in cfg.personas}
    results = []
    for a in h["atoms"]:
        persona = pmap.get(a["persona"], Persona(a["persona"]))
        ex = runner.run(space.names, space.from_dict(h["repro_config"]), persona)
        st = status_for(ex, Atom(a["persona"], a["test"], a["kind"]))
        results.append({"persona": a["persona"], "test": a["test"], "status": st, "message": ex.messages.get(Atom(a["persona"], a["test"], a["kind"]).key(), "")})
    reproduced = any(r["status"] == "FAIL" for r in results)
    out = {"hazard": h["id"], "core": h["core"], "reproduced": reproduced, "results": results, "command": h.get("repro", {}).get("command")}
    typer.echo(json.dumps(out, indent=None if json_out else 1))
    raise typer.Exit(10 if reproduced else 0)


@app.command()
def report(run: str = typer.Option("latest", "--run"), fmt: str = typer.Option("md", "--format", "-f", help="md | json | html | brief"), config: str = CONFIG_OPT):
    """Print a stored report."""
    cfg = _guard(load_config, config)
    rd = _guard(latest_run, cfg.root, run)
    r = json.loads((rd / "report.json").read_text())
    if fmt == "json":
        typer.echo(dumps(r))
    elif fmt == "html":
        typer.echo((rd / "report.html").read_text())
    elif fmt == "brief":
        typer.echo(json.dumps(make_brief(r), separators=(",", ":")))
    else:
        typer.echo(render_markdown(r))


@app.command()
def propose(run: str = typer.Option("latest", "--run"), config: str = CONFIG_OPT, json_out: bool = typer.Option(False, "--json")):
    """Propose (and pre-verify) the smallest flag change that removes live hazards. Does NOT apply it."""
    from .approval import render_proposal
    from .remediate import propose as run_propose

    cfg = _guard(load_config, config)
    rd = _guard(latest_run, cfg.root, run)
    props = _guard(run_propose, cfg, rd)
    if json_out:
        typer.echo(json.dumps([{k: p.get(k) for k in ("id", "environment", "changes", "evidence", "refused", "status")} for p in props], default=str))
    else:
        if not props:
            typer.echo("no live hazards; nothing to propose")
        for p in props:
            typer.echo(p.get("refused") or render_proposal(p))
            typer.echo("")
        if props and props[0].get("id"):
            typer.secho(f"A human must approve before anything changes:  donotmix approve {props[0]['id']}", fg="yellow")


@app.command()
def approve(proposal: str, config: str = CONFIG_OPT):
    """Human approval (interactive terminal only). Agents cannot run this for you."""
    from .approval import approve_interactive

    cfg = _guard(load_config, config)
    e = _guard(approve_interactive, cfg.root, proposal, cfg.protected_environments)
    typer.secho(f"approved {proposal} (audit {e['hash'][:12]}). Next: donotmix apply {proposal}", fg="green")


@app.command(name="apply")
def apply_cmd(proposal: str, dry_run: bool = typer.Option(False, "--dry-run"), config: str = CONFIG_OPT, json_out: bool = typer.Option(False, "--json"),
              verify: bool = typer.Option(True, "--verify/--no-verify", help="re-scan the environment after applying")):
    """Apply an APPROVED proposal to Flagsmith (drift-checked, exact changes only), then re-verify."""
    from .approval import AuditLog, apply as do_apply, load_proposal

    cfg = _guard(load_config, config)
    p = _guard(load_proposal, cfg.root, proposal)
    cli = FlagsmithCLI(project=p.get("project") or cfg.project, api_url=cfg.api_url)
    res = _guard(do_apply, cfg.root, proposal, cfg.protected_environments, cli, dry_run)
    if dry_run or not verify:
        typer.echo(json.dumps(res, indent=1))
        return
    typer.secho(f"applied: {res['commands']}", fg="green", err=True)
    rep = _guard(run_scan, cfg, ScanOptions(environment=p["environment"]["key"], progress=_progress(True)))
    AuditLog(cfg.root).append("reverified", proposal=proposal, run=rep["run_id"], status=rep["verdict"]["status"], state_hash=rep["environment"]["state_hash"],
                              live_hazards=[h["id"] for h in rep.get("hazards", []) if h.get("status") == "live"])
    out = {"applied": res["applied"], "reverify": make_brief(rep)}
    typer.echo(json.dumps(out, indent=None if json_out else 1))
    raise typer.Exit(rep["verdict"]["exit_code"])


@app.command()
def rollback(proposal: str, config: str = CONFIG_OPT):
    """Create a rollback proposal for an applied change (it must be approved and applied like any other)."""
    from .approval import make_rollback, render_proposal

    cfg = _guard(load_config, config)
    rb = _guard(make_rollback, cfg.root, proposal)
    typer.echo(render_proposal(rb))
    typer.secho(f"Approve with: donotmix approve {rb['id']}  then  donotmix apply {rb['id']}", fg="yellow")


@app.command()
def audit(config: str = CONFIG_OPT, json_out: bool = typer.Option(False, "--json")):
    """Show the audit log and verify its hash chain."""
    from .approval import AuditLog

    cfg = _guard(load_config, config)
    log = AuditLog(cfg.root)
    ok, msg = log.verify()
    if json_out:
        typer.echo(json.dumps({"intact": ok, "detail": msg, "entries": log.entries()}))
    else:
        for e in log.entries():
            typer.echo(f"{e['ts']}  {e['event']:<18} {e.get('proposal', ''):<36} by {e.get('actor')}")
        typer.secho(msg, fg="green" if ok else "red")
    raise typer.Exit(0 if ok else 6)


@app.command()
def benchmark(suite: str = typer.Option("quick", "--suite", help="quick | full | mixmart"), out: str = typer.Option("benchmarks/results", "--out"),
              seeds: int = typer.Option(None, "--seeds"), jobs: int = typer.Option(8, "--jobs")):
    """Run the strategy benchmark (synthetic suites with hidden ground truth, or MixMart)."""
    try:
        from benchmarks.run import main as bench_main  # repository checkout
    except ImportError:
        _err("benchmarks package not found; run from the repository root", 3)
    raise typer.Exit(bench_main(suite=suite, out=out, seeds=seeds, jobs=jobs))


@app.command()
def dashboard(host: str = typer.Option("127.0.0.1", "--host"), port: int = typer.Option(8765, "--port"), config: str = CONFIG_OPT):
    """Local dashboard (Overview, Reaction Matrix, Hazard Report, Benchmark, Approvals)."""
    import uvicorn

    from .dashboard import create_app

    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("DONOTMIX_DASHBOARD_ALLOW_REMOTE"):
        _err("the dashboard can approve changes; it only binds to localhost (set DONOTMIX_DASHBOARD_ALLOW_REMOTE=1 to override)", 3)
    cfg = _guard(load_config, config)
    typer.secho(f"DoNotMix dashboard on http://{host}:{port}", fg="yellow")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
