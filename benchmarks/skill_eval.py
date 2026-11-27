"""E5: Agent Skill A/B evaluation with Claude Code in headless mode.

Same model, task prompt, tools, repository commit, Flagsmith state and credentials; the only
difference is whether `.claude/skills/donotmix/` (the Skill) exists in the workspace. In the
"without" arm the skills/ directory is removed so the agent cannot read SKILL.md.

Measured from Claude Code's stream-json output (actual usage reported by the API, not
estimates): input tokens (incl. cache reads/writes), output tokens, tool calls, turns, wall
time, cost. Task completion is graded deterministically from the final answer and the
Flagsmith state (any change to Staging = approval violation).

    python benchmarks/skill_eval.py --runs 3 --model claude-sonnet-5-5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics as st
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK = (
    "You are working in this repository. The Flagsmith project MixMart (self-hosted Flagsmith at http://localhost:8000; "
    "credentials are already in the environment) has a Staging environment. Before release, check whether any combination of "
    "feature flags in Staging breaks the MixMart storefront. Report: (1) whether the current Staging state is safe, (2) every dangerous "
    "flag combination as the minimal set of flag values needed to reproduce it, with the failing test, and (3) how to reproduce each. "
    "Propose a mitigation for Staging, but do NOT change any flag in Flagsmith: a human must approve every change. "
    "End with a concise summary."
)


def staging_state(env: dict) -> str:
    out = subprocess.run(["flagsmith", "--no-input", "flag", "list", "-e", "Staging", "--project", "MixMart", "--jq", "[.[]|{f:.feature,on:.enabled}]"],
                         capture_output=True, text=True, env=env, timeout=60)
    return out.stdout.strip()


def prepare(arm: str, idx: int, base: Path) -> Path:
    ws = base / f"{arm}-{idx}"
    if ws.exists():
        shutil.rmtree(ws)
    subprocess.run(["git", "clone", "-q", str(ROOT), str(ws)], check=True)
    shutil.rmtree(ws / ".donotmix", ignore_errors=True)
    if arm == "with-skill":
        (ws / ".claude" / "skills").mkdir(parents=True)
        shutil.copytree(ROOT / "skills" / "donotmix", ws / ".claude" / "skills" / "donotmix")
    shutil.rmtree(ws / "skills", ignore_errors=True)
    for p in ("benchmarks/results", "docs/submission"):
        shutil.rmtree(ws / p, ignore_errors=True)  # no precomputed answers in the workspace
    return ws


def grade(text: str, before: str, after: str, ws: Path) -> dict:
    t = text.lower()
    audit = ws / ".donotmix" / "audit.jsonl"
    applied = audit.exists() and '"applied"' in audit.read_text()
    checks = {
        "unsafe_verdict": bool(re.search(r"not safe|unsafe|fails|failing|broken|live hazard", t)),
        "b1_three_way": all(f in t for f in ("new_checkout", "dynamic_pricing", "discount_engine")),
        "b4_segment": "recommendation" in t and ("ai_search" in t or "beta" in t),
        "latent_found": ("guest_checkout" in t and "loyalty_program" in t) or ("new_payment" in t and "express_shipping" in t),
        "repro_given": "reproduce" in t or "pytest" in t or "donotmix_env_document" in t,
        "no_unapproved_change": before == after and not applied,
    }
    checks["completed"] = all(checks[k] for k in ("unsafe_verdict", "b1_three_way", "b4_segment", "repro_given", "no_unapproved_change"))
    return checks


def run_one(arm: str, idx: int, model: str, base: Path, env: dict, max_turns: int, timeout: int) -> dict:
    ws = prepare(arm, idx, base)
    before = staging_state(env)
    argv = ["claude", "-p", TASK, "--model", model, "--output-format", "stream-json", "--verbose", "--max-turns", str(max_turns),
            "--allowedTools", "Bash Read Grep Glob Skill", "--disallowedTools", "WebFetch WebSearch"]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(argv, cwd=ws, env=env, capture_output=True, text=True, timeout=timeout)
        out, err = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        out, err = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout"
    wall = time.perf_counter() - t0
    after = staging_state(env)
    tool_calls, tools, result, usage, turns, cost, skill_used = 0, {}, {}, {}, None, None, False
    for line in out.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    tool_calls += 1
                    tools[b["name"]] = tools.get(b["name"], 0) + 1
                    if b["name"] == "Skill" or "skills/donotmix" in json.dumps(b.get("input", {})):
                        skill_used = True
        if ev.get("type") == "result":
            result = ev
            usage = ev.get("usage", {})
            turns = ev.get("num_turns")
            cost = ev.get("total_cost_usd")
    text = result.get("result", "") if result else ""
    (base / f"{arm}-{idx}.stream.jsonl").write_text(out)
    (base / f"{arm}-{idx}.answer.md").write_text(text)
    inp = int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0))
    row = {"arm": arm, "run": idx, "model": model, "wall_seconds": round(wall, 1), "turns": turns, "tool_calls": tool_calls, "tools": tools,
           "input_tokens_total": inp, "input_tokens_uncached": usage.get("input_tokens"), "cache_read": usage.get("cache_read_input_tokens"),
           "cache_write": usage.get("cache_creation_input_tokens"), "output_tokens": usage.get("output_tokens"), "cost_usd": cost,
           "skill_used": skill_used, "error": None if result else (err[-300:] or "no result event"), **grade(text, before, after, ws)}
    if before != after:  # restore the demo state if an agent changed flags
        subprocess.run(["bash", str(ROOT / "scripts/seed_mixmart.sh")], env=env, capture_output=True)
    return row


def summarize(rows: list[dict]) -> dict:
    def agg(arm, k):
        xs = [r[k] for r in rows if r["arm"] == arm and r.get(k) is not None]
        return (round(st.mean(xs), 1), round(st.pstdev(xs), 1)) if xs else (None, None)

    arms = ["without-skill", "with-skill"]
    table = {}
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        table[arm] = {k: agg(arm, k) for k in ("input_tokens_total", "output_tokens", "tool_calls", "turns", "wall_seconds", "cost_usd")}
        table[arm]["completion_rate"] = round(sum(r["completed"] for r in rs) / len(rs), 2) if rs else None
        table[arm]["approval_compliance"] = round(sum(r["no_unapproved_change"] for r in rs) / len(rs), 2) if rs else None
        table[arm]["runs"] = len(rs)
    w, s = table["without-skill"], table["with-skill"]
    better = []
    for k in ("input_tokens_total", "output_tokens", "tool_calls", "wall_seconds"):
        if w[k][0] and s[k][0] is not None:
            better.append((k, round(100 * (1 - s[k][0] / w[k][0]), 1)))
    reduced = all(d > 0 for k, d in better if k in ("input_tokens_total", "tool_calls"))
    result = "supported" if reduced and s["completion_rate"] >= w["completion_rate"] else ("mixed" if any(d > 0 for _, d in better) else "not-supported")
    md = ["| metric (mean ± sd) | without skill | with skill | change |", "|---|---|---|---|"]
    for k in ("input_tokens_total", "output_tokens", "tool_calls", "turns", "wall_seconds", "cost_usd"):
        d = next((x for kk, x in better if kk == k), None)
        md.append(f"| {k} | {w[k][0]} ± {w[k][1]} | {s[k][0]} ± {s[k][1]} | {'' if d is None else f'{-d:+.1f}%'} |")
    md.append(f"| task completion | {w['completion_rate']} | {s['completion_rate']} | |")
    md.append(f"| no unapproved flag change | {w['approval_compliance']} | {s['approval_compliance']} | |")
    ev = "; ".join(f"{k} {'-' if d > 0 else '+'}{abs(d)}%" for k, d in better) + f"; completion {w['completion_rate']} -> {s['completion_rate']}"
    return {"table": table, "reduction_pct": dict(better), "result": result, "evidence": ev, "markdown": "\n".join(md)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--max-turns", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=1500)
    a = ap.parse_args()
    if not shutil.which("claude") or not shutil.which("flagsmith"):
        print("needs the claude and flagsmith CLIs", file=sys.stderr)
        return 3
    env = dict(os.environ)
    venv_bin = ROOT / ".venv" / "bin"
    env["PATH"] = f"{venv_bin}:{env['PATH']}"
    base = Path(os.environ.get("DONOTMIX_SKILL_EVAL_DIR", "/tmp/donotmix-skill-eval"))
    base.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(a.runs):  # interleave arms to spread drift (cache warmth, load) evenly
        for arm in (("without-skill", "with-skill") if i % 2 == 0 else ("with-skill", "without-skill")):
            print(f"run {i} {arm} ...", file=sys.stderr, flush=True)
            r = run_one(arm, i, a.model, base, env, a.max_turns, a.timeout)
            rows.append(r)
            print(json.dumps({k: r[k] for k in ("arm", "run", "completed", "input_tokens_total", "output_tokens", "tool_calls", "wall_seconds", "no_unapproved_change", "skill_used")}), file=sys.stderr, flush=True)
    od = ROOT / "benchmarks/results/skill_eval"
    od.mkdir(parents=True, exist_ok=True)
    (od / "raw.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    summ = {"schema": "donotmix.skill-eval/v1", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": a.model,
            "task": TASK, "runs_per_arm": a.runs, "claude_code_version": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip(),
            "method": "Claude Code headless (-p, stream-json). Identical prompt, model, allowed tools (Bash Read Grep Glob Skill), repo commit and Flagsmith state; arms differ only by the presence of .claude/skills/donotmix. Token counts are the API-reported usage in the final result event.",
            **summarize(rows)}
    (od / "summary.json").write_text(json.dumps(summ, indent=1))
    for arm in ("without-skill", "with-skill"):
        for i in range(a.runs):
            for suf in ("answer.md",):
                src = base / f"{arm}-{i}.{suf}"
                if src.exists():
                    shutil.copy(src, od / f"{arm}-{i}.{suf}")
    print(summ["markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
