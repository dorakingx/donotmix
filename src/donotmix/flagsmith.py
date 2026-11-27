"""Flagsmith adapter built on the official Flagsmith CLI (`flagsmith`, v2).

Read path (always safe):
  flagsmith auth status --json                      who am I, which instance
  flagsmith environment get <env> --json            resolve name -> SDK key
  flagsmith flag list -e <env> --json               flag states (environment defaults)
  flagsmith environment document <env> --json      full local-evaluation document
  flagsmith segment list --json                     segments
  flagsmith evaluate --identity .. --trait .. --json server-side evaluation (parity check)

Write path (only reachable through donotmix.approval with a verified human approval):
  flagsmith flag enable|disable <flag> -e <env> --yes

DoNotMix never toggles flags to run tests. Candidate configurations are evaluated by
rewriting a *copy* of the environment document and running the app's Flagsmith SDK in
offline/local-evaluation mode against it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class FlagsmithError(RuntimeError):
    exit_code = 4


class FlagsmithAuthError(FlagsmithError):
    pass


class FlagsmithUnavailable(FlagsmithError):
    pass


def _classify(stderr: str) -> type[FlagsmithError]:
    s = stderr.lower()
    if any(k in s for k in ("not logged in", "api key", "unauthor", "401", "403", "invalid token", "credentials", "permission")):
        return FlagsmithAuthError
    if any(k in s for k in ("connection refused", "no such host", "timeout", "dial tcp", "connect:")):
        return FlagsmithUnavailable
    return FlagsmithError


class FlagsmithCLI:
    def __init__(self, project: str | None = None, api_url: str | None = None, binary: str | None = None, timeout: float = 30.0, runner=None):
        self.project = project or os.environ.get("FLAGSMITH_PROJECT")
        self.api_url = api_url or os.environ.get("FLAGSMITH_API_URL")
        self.binary = binary or os.environ.get("DONOTMIX_FLAGSMITH_BIN") or "flagsmith"
        self.timeout = timeout
        self._run = runner or subprocess.run
        self.calls: list[list[str]] = []  # audit trail of CLI invocations (no secrets in argv)

    # ---- plumbing ----------------------------------------------------------------
    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _cmd(self, args: list[str], json_out: bool = True) -> Any:
        if not self.available() and self._run is subprocess.run:
            raise FlagsmithUnavailable(f"Flagsmith CLI '{self.binary}' not found. Install: brew install Flagsmith/tap/flagsmith")
        argv = [self.binary, "--no-input", *args]
        if self.project and "-p" not in args and "--project" not in args:
            argv += ["--project", self.project]
        if self.api_url and "--api-url" not in args:
            argv += ["--api-url", self.api_url]
        if json_out and "--json" not in args and "--jq" not in args:
            argv.append("--json")
        self.calls.append(argv)
        try:
            proc = self._run(argv, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as e:
            raise FlagsmithUnavailable(f"flagsmith CLI timed out after {self.timeout}s: {' '.join(args[:3])}") from e
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise _classify(err)(f"`flagsmith {' '.join(args)}` failed: {err[:400]}")
        if not json_out:
            return proc.stdout
        try:
            return json.loads(proc.stdout) if proc.stdout.strip() else None
        except json.JSONDecodeError as e:
            raise FlagsmithError(f"unexpected non-JSON output from `flagsmith {' '.join(args)}`: {proc.stdout[:200]}") from e

    # ---- read API ----------------------------------------------------------------
    def version(self) -> str:
        try:
            out = self._cmd(["--version"], json_out=False)
            return out.strip().split()[-1]
        except FlagsmithError:
            return "unknown"

    def auth_status(self) -> dict:
        return self._cmd(["auth", "status"])

    def environment(self, env: str) -> dict:
        return self._cmd(["environment", "get", env])

    def environments(self) -> list[dict]:
        return self._cmd(["environment", "list"])

    def flag_list(self, env: str) -> list[dict]:
        return self._cmd(["flag", "list", "-e", env])

    def flag_get(self, flag: str, env: str) -> dict:
        return self._cmd(["flag", "get", flag, "-e", env])

    def environment_document(self, env: str) -> dict:
        return self._cmd(["environment", "document", env])

    def segments(self) -> list[dict]:
        return self._cmd(["segment", "list"])

    def evaluate(self, env: str, identity: str | None = None, traits: dict | None = None) -> dict:
        args = ["evaluate", "-e", env]
        if identity:
            args += ["--identity", identity]
            for k, v in (traits or {}).items():
                args += ["--trait", f"{k}={_trait_str(v)}"]
        return self._cmd(args)

    # ---- write API (guarded) -----------------------------------------------------
    def _set_enabled(self, flag: str, enabled: bool, env: str, _approval_token: object) -> dict:
        from .approval import ApprovalToken  # local import to keep the dependency one-way

        if not isinstance(_approval_token, ApprovalToken) or not _approval_token.valid_for(flag, env):
            raise PermissionError("refusing to write to Flagsmith without a verified human approval")
        verb = "enable" if enabled else "disable"
        self._cmd(["flag", verb, flag, "-e", env, "--yes"], json_out=False)
        return self.flag_get(flag, env)


def _trait_str(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# --------------------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------------------


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def state_hash(flags: dict[str, bool]) -> str:
    return hashlib.sha256(_canonical(flags).encode()).hexdigest()[:16]


def take_snapshot(cli: FlagsmithCLI, env: str) -> dict:
    """Read-only snapshot of one environment through the official CLI."""
    envinfo = cli.environment(env)
    key = envinfo.get("api_key") or env
    flags = cli.flag_list(key)
    doc = cli.environment_document(key)
    try:
        segs = cli.segments()
    except FlagsmithError:
        segs = []
    states = {f["feature"]: bool(f["enabled"]) for f in flags}
    return {
        "schema": "donotmix.snapshot/v1",
        "source": "flagsmith-cli",
        "cli_version": cli.version(),
        "api_url": cli.api_url or "https://api.flagsmith.com",
        "project": cli.project,
        "environment": {"name": envinfo.get("name", env), "key": key, "id": envinfo.get("id")},
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "flags": [
            {"name": f["feature"], "enabled": bool(f["enabled"]), "value": f.get("value"), "type": f.get("type"),
             "segment_overrides": f.get("segment_overrides", 0), "identity_overrides": f.get("identity_overrides", 0)}
            for f in flags
        ],
        "segments": [{"id": s.get("id"), "name": s.get("name"), "description": s.get("description")} for s in segs],
        "state_hash": state_hash(states),
        "document": doc,
    }


def load_snapshot(path: str | Path) -> dict:
    snap = json.loads(Path(path).read_text())
    if snap.get("schema") != "donotmix.snapshot/v1":
        raise ValueError(f"{path} is not a DoNotMix snapshot")
    return snap


def segment_scoped_flags(snapshot: dict) -> dict[str, list[str]]:
    """Flags that are off by default but switched on for some segment (feature -> segment names)."""
    doc = snapshot["document"]
    default = {fs["feature"]["name"]: fs["enabled"] for fs in doc.get("feature_states", [])}
    out: dict[str, list[str]] = {}
    for seg in doc.get("project", {}).get("segments", []):
        for fs in seg.get("feature_states", []):
            name = fs["feature"]["name"]
            if fs.get("enabled") and not default.get(name, False):
                out.setdefault(name, []).append(seg["name"])
    return out


def apply_config_to_document(doc: dict, states: dict[str, Any]) -> dict:
    """Return a copy of the environment document with environment-default states replaced.

    Segment and identity overrides are kept verbatim, so the SDK still applies them.
    Boolean values set `enabled`; other values set the feature_state_value.
    """
    out = copy.deepcopy(doc)
    seen = set()
    for fs in out.get("feature_states", []):
        name = fs["feature"]["name"]
        if name in states:
            v = states[name]
            if isinstance(v, bool):
                fs["enabled"] = v
            else:
                fs["enabled"] = True
                fs["feature_state_value"] = v
                fs["multivariate_feature_state_values"] = []
            seen.add(name)
    missing = set(states) - seen
    if missing:
        raise KeyError(f"flags not present in environment document: {sorted(missing)}")
    return out
