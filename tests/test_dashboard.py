import json

from fastapi.testclient import TestClient

from donotmix.approval import save_proposal
from donotmix.config import ProjectConfig
from donotmix.dashboard import create_app


def client(tmp_path):
    cfg = ProjectConfig(project="P", environment="Staging")
    cfg.root = tmp_path
    return TestClient(create_app(cfg))


def test_pages_and_empty_states(tmp_path):
    c = client(tmp_path)
    assert c.get("/").status_code == 200
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/api/report").status_code == 404
    assert c.get("/api/benchmark").json()["status"] == "not-run"
    assert c.get("/api/audit").json()["intact"] is True


def test_approval_requires_csrf_typed_env_and_approver(tmp_path):
    save_proposal(tmp_path, {"id": "P-1", "project": "P", "api_url": "x", "environment": {"name": "Staging", "key": "k"},
                             "changes": [{"flag": "a", "from": True, "to": False}], "preconditions": {"a": True}})
    c = client(tmp_path)
    csrf = c.get("/api/session").json()["csrf"]
    assert c.post("/api/proposals/P-1/approve", json={"typed": "Staging", "approver": "me"}).status_code == 403
    assert c.post("/api/proposals/P-1/approve", json={"csrf": csrf, "typed": "staging", "approver": "me"}).status_code == 400
    assert c.post("/api/proposals/P-1/approve", json={"csrf": csrf, "typed": "Staging", "approver": ""}).status_code == 400
    assert c.post("/api/proposals/P-1/approve", json={"csrf": csrf, "typed": "Staging", "approver": "me"},
                  headers={"origin": "http://evil.example"}).status_code == 403
    r = c.post("/api/proposals/P-1/approve", json={"csrf": csrf, "typed": "Staging", "approver": "me"})
    assert r.status_code == 200
    props = c.get("/api/proposals").json()
    assert props[0]["approval"]["approved"] and props[0]["approval"]["by"] == "me (dashboard)"
