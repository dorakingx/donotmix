"""Human-in-the-loop changes to Flagsmith.

Lifecycle:  propose (machine)  ->  approve (human, interactive TTY or dashboard with typed
confirmation)  ->  apply (machine, re-checks everything)  ->  re-verify  ->  rollback (human).

Guarantees enforced in code:
* Nothing writes to Flagsmith except `apply`, which requires an approval record whose hash
  matches the proposal content exactly (no edits after approval).
* Protected environments (default: Production) are refused outright.
* Drift check: before writing, the live flag states must still equal the proposal's
  preconditions; otherwise apply aborts and a new proposal is required.
* Only the approved flags are touched; each write is read back and verified.
* Every event is appended to .donotmix/audit.jsonl as a hash chain (tamper-evident).
* `approve` refuses to run without an interactive terminal, so a coding agent's shell
  (non-TTY) cannot approve on the human's behalf. This is a safeguard, not a security
  boundary against a determined local attacker.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .flagsmith import FlagsmithCLI


class ApprovalError(RuntimeError):
    exit_code = 6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


# ---- audit log -------------------------------------------------------------------------


class AuditLog:
    def __init__(self, root: Path):
        self.path = root / ".donotmix" / "audit.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def append(self, event: str, **data) -> dict:
        prev = self.entries()
        prev_hash = prev[-1]["hash"] if prev else "0" * 64
        entry = {"ts": _now(), "event": event, "actor": data.pop("actor", None) or _actor(), **data, "prev": prev_hash}
        entry["hash"] = _h({k: v for k, v in entry.items() if k != "hash"})
        with self.path.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def verify(self) -> tuple[bool, str]:
        prev = "0" * 64
        for i, e in enumerate(self.entries()):
            if e.get("prev") != prev:
                return False, f"entry {i} breaks the chain (prev mismatch)"
            if _h({k: v for k, v in e.items() if k != "hash"}) != e.get("hash"):
                return False, f"entry {i} was modified (hash mismatch)"
            prev = e["hash"]
        return True, f"{len(self.entries())} entries, chain intact"


def _actor() -> str:
    # DONOTMIX_ACTOR lets people record a handle instead of their OS login name.
    if os.environ.get("DONOTMIX_ACTOR"):
        return os.environ["DONOTMIX_ACTOR"]
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "unknown")


def redact_actors(entries: list[dict], names: list[str], label: str = "local-user") -> list[dict]:
    """Display-only copy with the given actor names replaced (the log on disk is untouched)."""
    hide = {n for n in names if n}
    return [{**e, "actor": label} if e.get("actor") in hide else e for e in entries]


def actors_to_redact() -> list[str]:
    """Names hidden by DONOTMIX_REDACT_ACTORS: '1' hides the OS login name; otherwise a comma-separated list."""
    v = os.environ.get("DONOTMIX_REDACT_ACTORS", "").strip()
    if not v:
        return []
    if v.lower() in ("1", "true", "yes"):
        try:
            return [getpass.getuser()]
        except Exception:
            return [os.environ.get("USER", "")]
    return [x.strip() for x in v.split(",")]


# ---- proposals --------------------------------------------------------------------------


def proposals_dir(root: Path) -> Path:
    p = root / ".donotmix" / "proposals"
    p.mkdir(parents=True, exist_ok=True)
    return p


def content_of(p: dict) -> dict:
    """The approval-relevant content of a proposal (what the hash covers)."""
    return {k: p[k] for k in ("id", "project", "api_url", "environment", "changes", "preconditions")}


def save_proposal(root: Path, p: dict) -> Path:
    p["content_hash"] = _h(content_of(p))
    path = proposals_dir(root) / f"{p['id']}.json"
    path.write_text(json.dumps(p, indent=1, default=str))
    return path


def load_proposal(root: Path, pid: str) -> dict:
    path = proposals_dir(root) / f"{pid}.json"
    if not path.exists():
        raise ApprovalError(f"proposal {pid} not found")
    p = json.loads(path.read_text())
    if _h(content_of(p)) != p.get("content_hash"):
        raise ApprovalError(f"proposal {pid} was modified after it was created (hash mismatch)")
    return p


def approval_record(root: Path, pid: str) -> dict | None:
    rec = None
    for e in AuditLog(root).entries():
        if e.get("proposal") == pid and e["event"] in ("approved", "approval_revoked"):
            rec = e if e["event"] == "approved" else None
    return rec


def is_protected(env_name: str, protected: list[str]) -> bool:
    low = env_name.lower()
    return any(low == x.lower() for x in protected) or low.startswith("prod")


@dataclass(frozen=True)
class ApprovalToken:
    proposal: str
    environment_key: str
    flags: frozenset

    def valid_for(self, flag: str, env: str) -> bool:
        return flag in self.flags and env == self.environment_key


def approve_interactive(root: Path, pid: str, protected: list[str], stdin=None, stdout=None, require_tty: bool = True) -> dict:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    p = load_proposal(root, pid)
    if require_tty and not (hasattr(stdin, "isatty") and stdin.isatty()):
        AuditLog(root).append("approval_refused", proposal=pid, reason="no interactive terminal")
        raise ApprovalError("approval requires a human at an interactive terminal (or the dashboard). Ask the user to run: donotmix approve " + pid)
    env = p["environment"]
    if is_protected(env["name"], protected):
        raise ApprovalError(f"{env['name']} is a protected environment; DoNotMix will not change it")
    stdout.write(render_proposal(p) + "\n")
    stdout.write(f"Type the environment name ({env['name']}) to approve, anything else to cancel: ")
    stdout.flush()
    answer = stdin.readline().strip()
    if answer != env["name"]:
        AuditLog(root).append("approval_declined", proposal=pid)
        raise ApprovalError("not approved")
    return AuditLog(root).append("approved", proposal=pid, content_hash=p["content_hash"], via="cli-tty")


def approve_from_dashboard(root: Path, pid: str, typed: str, protected: list[str], actor: str) -> dict:
    p = load_proposal(root, pid)
    if is_protected(p["environment"]["name"], protected):
        raise ApprovalError("protected environment")
    if typed != p["environment"]["name"]:
        raise ApprovalError("confirmation text does not match the environment name")
    return AuditLog(root).append("approved", proposal=pid, content_hash=p["content_hash"], via="dashboard", actor=actor)


def render_proposal(p: dict) -> str:
    env = p["environment"]
    lines = [f"Proposal {p['id']}  ({p.get('kind', 'change')})",
             f"  target   : {env['name']} ({env['key']}) in project {p.get('project')} at {p.get('api_url')}",
             "  changes  :"]
    for c in p["changes"]:
        lines.append(f"    {c['flag']}: {'on' if c['from'] else 'off'} -> {'on' if c['to'] else 'off'}")
    lines.append(f"  why      : {p.get('rationale', '')}")
    ev = p.get("evidence", {})
    if ev:
        lines.append(f"  evidence : resolves {', '.join(ev.get('resolves', []))}; verified state passes {ev.get('verified_passes')}/{ev.get('verified_runs')} test runs"
                     + (f"; still failing: {ev['still_failing']}" if ev.get("still_failing") else ""))
    lines.append(f"  impact   : {p.get('impact', '')}")
    return "\n".join(lines)


def apply(root: Path, pid: str, protected: list[str], cli: FlagsmithCLI, dry_run: bool = False) -> dict:
    p = load_proposal(root, pid)
    env = p["environment"]
    log = AuditLog(root)
    if is_protected(env["name"], protected):
        log.append("apply_refused", proposal=pid, reason="protected environment")
        raise ApprovalError(f"{env['name']} is protected; refusing to write")
    rec = approval_record(root, pid)
    if not rec:
        log.append("apply_refused", proposal=pid, reason="not approved")
        raise ApprovalError(f"proposal {pid} has no human approval. Ask the user to run: donotmix approve {pid}")
    if rec.get("content_hash") != p["content_hash"]:
        log.append("apply_refused", proposal=pid, reason="approval does not match proposal content")
        raise ApprovalError("approval does not match the proposal content")
    if any(e.get("proposal") == pid and e["event"] == "applied" for e in log.entries()):
        raise ApprovalError(f"proposal {pid} was already applied")
    # drift check against live state
    live = {f["feature"]: bool(f["enabled"]) for f in cli.flag_list(env["key"])}
    drift = {k: {"expected": v, "live": live.get(k)} for k, v in p["preconditions"].items() if live.get(k) != v}
    if drift:
        log.append("apply_refused", proposal=pid, reason="drift", drift=drift)
        raise ApprovalError(f"environment changed since the proposal was made: {drift}. Re-scan and propose again.")
    cmds = [f"flagsmith flag {'enable' if c['to'] else 'disable'} {c['flag']} -e {env['key']} --yes" for c in p["changes"]]
    if dry_run:
        return {"dry_run": True, "commands": cmds}
    token = ApprovalToken(pid, env["key"], frozenset(c["flag"] for c in p["changes"]))
    results = []
    for c in p["changes"]:
        after = cli._set_enabled(c["flag"], bool(c["to"]), env["key"], token)
        ok = bool(after.get("enabled")) == bool(c["to"]) if isinstance(after, dict) else None
        results.append({"flag": c["flag"], "to": c["to"], "read_back_ok": ok})
    entry = log.append("applied", proposal=pid, content_hash=p["content_hash"], commands=cmds, results=results)
    return {"applied": results, "audit": entry["hash"][:12], "commands": cmds}


def make_rollback(root: Path, pid: str) -> dict:
    p = load_proposal(root, pid)
    if not any(e.get("proposal") == pid and e["event"] == "applied" for e in AuditLog(root).entries()):
        raise ApprovalError(f"proposal {pid} was never applied")
    rb = {
        "id": f"{pid}-rollback",
        "kind": "rollback",
        "project": p["project"], "api_url": p["api_url"], "environment": p["environment"],
        "changes": [{"flag": c["flag"], "from": c["to"], "to": c["from"]} for c in p["changes"]],
        "preconditions": {c["flag"]: c["to"] for c in p["changes"]},
        "rationale": f"Roll back {pid}",
        "impact": "Restores the flag states recorded before " + pid,
        "created_at": _now(),
    }
    save_proposal(root, rb)
    AuditLog(root).append("proposed", proposal=rb["id"], kind="rollback")
    return rb
