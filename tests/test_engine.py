import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from donotmix.cli import app
from donotmix.config import ProjectConfig, load_config
from donotmix.engine import EXIT_CLEAN, EXIT_LATENT, EXIT_LIVE, ScanOptions, scan

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DOC = ROOT / "demo/mixmart/fixtures/environment.all_off.json"
FLAGS = ["new_checkout", "dynamic_pricing", "discount_engine", "guest_checkout", "express_shipping",
         "loyalty_program", "new_payment", "recommendation", "ai_search", "dark_mode"]


def snapshot(tmp_path, on=()):
    doc = json.loads(FIXTURE_DOC.read_text())
    for fs in doc["feature_states"]:
        fs["enabled"] = fs["feature"]["name"] in on
    snap = {"schema": "donotmix.snapshot/v1", "source": "test", "project": "T", "api_url": "offline",
            "environment": {"name": "Staging", "key": "k"}, "fetched_at": "now", "state_hash": "x",
            "flags": [{"name": f, "enabled": f in on} for f in FLAGS], "segments": [], "document": doc}
    p = tmp_path / "snap.json"
    p.write_text(json.dumps(snap))
    return p


def project(tmp_path, extra=""):
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "donotmix.yaml").write_text(f"""
project: T
environment: Staging
runner: {{type: python, target: "tests.fake_app:run"}}
analysis: {{paths: [src]}}
strategy: {{name: risk, strength: 3}}
constraints:
  - requires: [express_shipping, new_checkout]
{extra}
""")
    return load_config(tmp_path / "donotmix.yaml")


def test_latent_hazards(tmp_path):
    cfg = project(tmp_path)
    r = scan(cfg, ScanOptions(snapshot_path=str(snapshot(tmp_path))))
    assert r["verdict"]["exit_code"] == EXIT_LATENT
    cores = [h["core"] for h in r["hazards"]]
    assert {"new_checkout": True, "dynamic_pricing": True, "discount_engine": True} in cores
    assert {"guest_checkout": True, "loyalty_program": True} in cores
    assert r["coverage"]["3"]["ratio"] == 1.0
    assert r["mode"]["flagsmith"] == "offline-snapshot"
    assert r["mode"]["ai"] == "rule-based"
    assert (tmp_path / ".donotmix" / "runs" / r["run_id"] / "report.md").exists()


def test_live_hazard(tmp_path):
    cfg = project(tmp_path)
    r = scan(cfg, ScanOptions(snapshot_path=str(snapshot(tmp_path, on=("new_checkout", "dynamic_pricing", "discount_engine")))))
    assert r["verdict"]["status"] == "LIVE_HAZARD" and r["verdict"]["exit_code"] == EXIT_LIVE
    live = [h for h in r["hazards"] if h["status"] == "live"]
    assert live and live[0]["core"] == {"new_checkout": True, "dynamic_pricing": True, "discount_engine": True}


def test_clean_when_constraints_forbid(tmp_path):
    cfg = project(tmp_path, extra="  - excludes: [guest_checkout, loyalty_program]\n  - rule: \"!(new_checkout & dynamic_pricing & discount_engine)\"")
    r = scan(cfg, ScanOptions(snapshot_path=str(snapshot(tmp_path))))
    assert r["verdict"]["exit_code"] == EXIT_CLEAN
    assert "not a proof" in r["verdict"]["caveat"]


def test_dry_run_executes_nothing(tmp_path):
    cfg = project(tmp_path)
    r = scan(cfg, ScanOptions(snapshot_path=str(snapshot(tmp_path)), dry_run=True))
    assert r["verdict"]["status"] == "DRY_RUN" and "execution" not in r


def test_contradictory_constraints_exit_3(tmp_path, monkeypatch):
    project(tmp_path, extra="  - fixed: {new_checkout: false}\n  - fixed: {express_shipping: true}")
    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(app, ["scan", "--snapshot", str(snapshot(tmp_path))])
    assert res.exit_code == 3 and "contradictory" in res.output


def test_cli_exit_codes(tmp_path, monkeypatch):
    project(tmp_path)
    monkeypatch.chdir(tmp_path)
    snap = snapshot(tmp_path)
    res = CliRunner().invoke(app, ["scan", "--snapshot", str(snap), "--brief"])
    assert res.exit_code == 10, res.output
    brief = json.loads(res.stdout.strip().splitlines()[-1])
    assert brief["status"] == "LATENT_HAZARDS" and brief["hazards"]
    res = CliRunner().invoke(app, ["reproduce", brief["hazards"][0]["id"], "--json"])
    assert res.exit_code == 10 and json.loads(res.stdout)["reproduced"] is True


def test_missing_config_exit_3(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(app, ["scan"])
    assert res.exit_code == 3


def test_invalid_strategy_exit_3(tmp_path, monkeypatch):
    project(tmp_path)
    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(app, ["scan", "--snapshot", str(snapshot(tmp_path)), "--strategy", "bogus"])
    assert res.exit_code == 3


def test_flagsmith_unreachable_exit_4(tmp_path, monkeypatch):
    project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DONOTMIX_FLAGSMITH_BIN", "definitely-not-installed-flagsmith")
    res = CliRunner().invoke(app, ["scan"])
    assert res.exit_code == 4
