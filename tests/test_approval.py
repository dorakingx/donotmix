import io
import json
import subprocess

import pytest

from donotmix.approval import ApprovalError, AuditLog, apply, approve_from_dashboard, approve_interactive, make_rollback, save_proposal
from donotmix.flagsmith import FlagsmithCLI


class FakeFS:
    """Fake flagsmith binary: stateful flag store, records every argv."""

    def __init__(self, states):
        self.states = dict(states)
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        a = argv[2:]
        if a[:2] == ["flag", "list"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps([{"feature": k, "enabled": v} for k, v in self.states.items()]), "")
        if a[:2] in (["flag", "enable"], ["flag", "disable"]):
            self.states[a[2]] = a[1] == "enable"
            return subprocess.CompletedProcess(argv, 0, "ok", "")
        if a[:2] == ["flag", "get"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"feature": a[2], "enabled": self.states[a[2]]}), "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")

    def writes(self):
        return [c for c in self.calls if c[2:4] in (["flag", "enable"], ["flag", "disable"])]


def proposal(tmp_path, env="Staging"):
    p = {"id": "P-1", "project": "MixMart", "api_url": "http://x", "environment": {"name": env, "key": "KEY"},
         "changes": [{"flag": "discount_engine", "from": True, "to": False}], "preconditions": {"discount_engine": True}}
    save_proposal(tmp_path, p)
    return p


def test_apply_without_approval_is_refused_and_writes_nothing(tmp_path):
    proposal(tmp_path)
    fs = FakeFS({"discount_engine": True, "new_checkout": True})
    with pytest.raises(ApprovalError):
        apply(tmp_path, "P-1", ["Production"], FlagsmithCLI(runner=fs))
    assert fs.writes() == []
    assert AuditLog(tmp_path).entries()[-1]["event"] == "apply_refused"


def test_approve_requires_tty(tmp_path):
    proposal(tmp_path)
    with pytest.raises(ApprovalError, match="interactive terminal"):
        approve_interactive(tmp_path, "P-1", ["Production"], stdin=io.StringIO("Staging\n"), stdout=io.StringIO())


def test_approve_tty_wrong_answer_declines(tmp_path):
    proposal(tmp_path)
    with pytest.raises(ApprovalError):
        approve_interactive(tmp_path, "P-1", ["Production"], stdin=io.StringIO("yes\n"), stdout=io.StringIO(), require_tty=False)


def test_approved_apply_changes_exactly_the_approved_flag(tmp_path):
    proposal(tmp_path)
    approve_interactive(tmp_path, "P-1", ["Production"], stdin=io.StringIO("Staging\n"), stdout=io.StringIO(), require_tty=False)
    fs = FakeFS({"discount_engine": True, "new_checkout": True})
    res = apply(tmp_path, "P-1", ["Production"], FlagsmithCLI(runner=fs))
    assert [w[2:5] for w in fs.writes()] == [["flag", "disable", "discount_engine"]]
    assert fs.states == {"discount_engine": False, "new_checkout": True}
    assert res["applied"][0]["read_back_ok"] is True
    with pytest.raises(ApprovalError, match="already applied"):
        apply(tmp_path, "P-1", ["Production"], FlagsmithCLI(runner=fs))
    rb = make_rollback(tmp_path, "P-1")
    assert rb["changes"] == [{"flag": "discount_engine", "from": False, "to": True}]


def test_drift_blocks_apply(tmp_path):
    proposal(tmp_path)
    approve_from_dashboard(tmp_path, "P-1", "Staging", ["Production"], actor="alice")
    fs = FakeFS({"discount_engine": False})  # someone already changed it
    with pytest.raises(ApprovalError, match="changed since"):
        apply(tmp_path, "P-1", ["Production"], FlagsmithCLI(runner=fs))
    assert fs.writes() == []


def test_protected_environment_refused(tmp_path):
    proposal(tmp_path, env="Production")
    with pytest.raises(ApprovalError):
        approve_from_dashboard(tmp_path, "P-1", "Production", ["Production"], actor="alice")
    fs = FakeFS({"discount_engine": True})
    with pytest.raises(ApprovalError):
        apply(tmp_path, "P-1", ["Production"], FlagsmithCLI(runner=fs))
    assert fs.writes() == []


def test_proposal_edit_after_approval_detected(tmp_path):
    proposal(tmp_path)
    approve_from_dashboard(tmp_path, "P-1", "Staging", ["Production"], actor="alice")
    path = tmp_path / ".donotmix" / "proposals" / "P-1.json"
    p = json.loads(path.read_text())
    p["changes"][0]["flag"] = "new_checkout"
    path.write_text(json.dumps(p))
    with pytest.raises(ApprovalError, match="modified"):
        apply(tmp_path, "P-1", ["Production"], FlagsmithCLI(runner=FakeFS({"new_checkout": True})))


def test_audit_chain_detects_tampering(tmp_path):
    log = AuditLog(tmp_path)
    log.append("proposed", proposal="P-1")
    log.append("approved", proposal="P-1")
    assert log.verify()[0]
    lines = log.path.read_text().splitlines()
    e = json.loads(lines[1])
    e["actor"] = "mallory"
    lines[1] = json.dumps(e, sort_keys=True)
    log.path.write_text("\n".join(lines) + "\n")
    assert not log.verify()[0]


def test_actor_env_and_display_redaction(tmp_path, monkeypatch):
    from donotmix.approval import AuditLog, actors_to_redact, redact_actors

    monkeypatch.setenv("DONOTMIX_ACTOR", "handle")
    e = AuditLog(tmp_path).append("proposed", proposal="P-1")
    assert e["actor"] == "handle"
    AuditLog(tmp_path).append("approved", proposal="P-1", actor="Real Name")
    monkeypatch.setenv("DONOTMIX_REDACT_ACTORS", "Real Name, other")
    assert actors_to_redact() == ["Real Name", "other"]
    shown = redact_actors(AuditLog(tmp_path).entries(), actors_to_redact())
    assert [x["actor"] for x in shown] == ["handle", "local-user"]
    # display redaction never touches the log on disk, so the hash chain stays intact
    assert AuditLog(tmp_path).entries()[1]["actor"] == "Real Name"
    assert AuditLog(tmp_path).verify()[0]
