"""Live integration tests against a real Flagsmith instance through the official CLI.

Run:  DONOTMIX_IT=1 FLAGSMITH_API_URL=http://localhost:8000 FLAGSMITH_API_KEY_localhost_8000=... pytest tests/integration -m integration
Uses the MixMart project's *Development* environment (seed with scripts/seed_mixmart.sh) and
restores every flag it touches.
"""

import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from donotmix.approval import apply, approve_interactive, save_proposal
from donotmix.config import load_config
from donotmix.engine import ScanOptions, scan
from donotmix.flagsmith import FlagsmithAuthError, FlagsmithCLI, take_snapshot

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.environ.get("DONOTMIX_IT") != "1" or not shutil.which("flagsmith"), reason="set DONOTMIX_IT=1 with a running Flagsmith")]

ROOT = Path(__file__).resolve().parents[2]
ENV = "Development"
FLAG = "dark_mode"


@pytest.fixture
def cli():
    return FlagsmithCLI(project="MixMart", api_url=os.environ.get("FLAGSMITH_API_URL", "http://localhost:8000"))


def _set(cli, flag, on):
    subprocess.run(["flagsmith", "--no-input", "flag", "enable" if on else "disable", flag, "-e", ENV, "--project", "MixMart", "--api-url", cli.api_url, "--yes"], check=True, capture_output=True)


def test_snapshot_reads_real_environment(cli):
    snap = take_snapshot(cli, ENV)
    assert snap["environment"]["name"] == ENV
    assert {f["name"] for f in snap["flags"]} >= {"new_checkout", "dark_mode"}
    assert snap["document"]["feature_states"]
    assert any(s["name"] == "beta_testers" for s in snap["segments"])


def test_server_and_offline_evaluation_agree(cli):
    """The SDK-in-offline-mode evaluation DoNotMix relies on matches Flagsmith's server evaluation."""
    from flagsmith import Flagsmith
    from flagsmith.offline_handlers import LocalFileHandler

    snap = take_snapshot(cli, "Staging")
    p = ROOT / ".donotmix" / "it-doc.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(snap["document"]))
    fs = Flagsmith(offline_mode=True, offline_handler=LocalFileHandler(str(p)))
    for ident, traits in (("it-shopper", {"beta": False}), ("it-beta", {"beta": True})):
        server = cli.evaluate(snap["environment"]["key"], ident, traits)["flags"]
        local = {f.feature_name: f.enabled for f in fs.get_identity_flags(ident, traits=traits).all_flags()}
        assert {k: v["enabled"] for k, v in server.items()} == local


def test_flag_change_is_reflected_in_snapshot_and_scan(cli, tmp_path):
    before = {f["name"]: f["enabled"] for f in take_snapshot(cli, ENV)["flags"]}
    try:
        _set(cli, FLAG, not before[FLAG])
        snap = take_snapshot(cli, ENV)
        assert {f["name"]: f["enabled"] for f in snap["flags"]}[FLAG] is (not before[FLAG])
        cfg = load_config(ROOT / "donotmix.yaml")
        r = scan(cfg, ScanOptions(environment=ENV, dry_run=True, out_dir=str(tmp_path)))
        live = {f["name"]: f["live"] for f in r["flags"]}
        assert live[FLAG] is (not before[FLAG])
        assert r["environment"]["state_hash"] == snap["state_hash"]
    finally:
        _set(cli, FLAG, before[FLAG])


def test_approved_change_applied_through_cli_and_rolled_back(cli, tmp_path):
    before = {f["name"]: f["enabled"] for f in take_snapshot(cli, ENV)["flags"]}
    envkey = cli.environment(ENV)["api_key"]
    p = {"id": "IT-1", "project": "MixMart", "api_url": cli.api_url, "environment": {"name": ENV, "key": envkey},
         "changes": [{"flag": FLAG, "from": before[FLAG], "to": not before[FLAG]}], "preconditions": {FLAG: before[FLAG]}}
    save_proposal(tmp_path, p)
    approve_interactive(tmp_path, "IT-1", ["Production"], stdin=io.StringIO(ENV + "\n"), stdout=io.StringIO(), require_tty=False)
    try:
        res = apply(tmp_path, "IT-1", ["Production"], cli)
        assert res["applied"][0]["read_back_ok"]
        after = {f["name"]: f["enabled"] for f in take_snapshot(cli, ENV)["flags"]}
        assert after[FLAG] is (not before[FLAG])
        assert {k: v for k, v in after.items() if k != FLAG} == {k: v for k, v in before.items() if k != FLAG}
    finally:
        _set(cli, FLAG, before[FLAG])


def test_bad_credentials_are_reported_as_auth_error(monkeypatch):
    host = os.environ.get("FLAGSMITH_API_URL", "http://localhost:8000").split("://")[1].replace(":", "_").replace(".", "_")
    monkeypatch.setenv(f"FLAGSMITH_API_KEY_{host}", "bogus")
    with pytest.raises(FlagsmithAuthError):
        FlagsmithCLI(project="MixMart", api_url=os.environ.get("FLAGSMITH_API_URL", "http://localhost:8000")).flag_list(ENV)
