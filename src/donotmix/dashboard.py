"""Local dashboard: FastAPI JSON API + static single-page UI (no build step).

Binds to localhost only (enforced by the CLI). Approvals need the CSRF token served to the
page and the environment name typed by the human, mirroring the CLI's TTY confirmation.
"""

from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .approval import AuditLog, ApprovalError, approval_record, approve_from_dashboard, apply as do_apply, load_proposal, proposals_dir
from .config import ProjectConfig
from .engine import ScanOptions, latest_run, scan
import os

from .flagsmith import FlagsmithCLI, FlagsmithError
from .report import brief

STATIC = Path(__file__).parent / "dashboard_static"
ASSETS = Path(__file__).parent / "brand"


def create_app(cfg: ProjectConfig) -> FastAPI:
    app = FastAPI(title="DoNotMix", docs_url=None, redoc_url=None)
    csrf = secrets.token_urlsafe(24)
    jobs: dict = {"scan": {"state": "idle"}}
    root = cfg.root

    def _local(request: Request) -> None:
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        host = request.client.host if request.client else ""
        # inside Docker the browser reaches us through a port mapping (client = bridge gateway);
        # DONOTMIX_DASHBOARD_ALLOW_REMOTE relaxes only the client-address check, never CSRF/Origin/typed confirmation
        if host not in ("127.0.0.1", "::1", "localhost", "testclient") and not os.environ.get("DONOTMIX_DASHBOARD_ALLOW_REMOTE"):
            raise HTTPException(403, "dashboard actions are only accepted from localhost")
        if origin and not any(origin.startswith(p) for p in ("http://127.0.0.1", "http://localhost", "http://[::1]", "http://testserver")):
            raise HTTPException(403, "cross-origin request refused")

    @app.get("/api/session")
    def session():
        return {"csrf": csrf, "protected_environments": cfg.protected_environments, "project": cfg.project, "environment": cfg.environment}

    @app.get("/api/runs")
    def runs():
        base = root / ".donotmix" / "runs"
        out = []
        for d in sorted(base.glob("*/report.json"), reverse=True)[:50]:
            try:
                r = json.loads(d.read_text())
                out.append({"run_id": r["run_id"], "status": r["verdict"]["status"], "created_at": r["created_at"],
                            "environment": (r.get("environment") or {}).get("environment", {}).get("name"), "hazards": len(r.get("hazards", []))})
            except (json.JSONDecodeError, KeyError):
                continue
        return out

    @app.get("/api/report")
    def report(run: str = "latest"):
        try:
            rd = latest_run(root, run)
        except Exception:
            return JSONResponse({"status": "no-run", "hint": "run `donotmix scan` first"}, status_code=404)
        return json.loads((rd / "report.json").read_text())

    @app.get("/api/brief")
    def report_brief(run: str = "latest"):
        rd = latest_run(root, run)
        return brief(json.loads((rd / "report.json").read_text()))

    @app.get("/api/benchmark")
    def benchmark():
        for p in (root / "benchmarks" / "results" / "latest" / "summary.json", root / "benchmarks" / "results" / "summary.json"):
            if p.exists():
                return json.loads(p.read_text())
        return {"status": "not-run", "hint": "run `donotmix benchmark --suite quick`"}

    @app.get("/api/proposals")
    def proposals():
        out = []
        for f in sorted(proposals_dir(root).glob("*.json"), reverse=True):
            p = json.loads(f.read_text())
            rec = approval_record(root, p["id"])
            applied = any(e.get("proposal") == p["id"] and e["event"] == "applied" for e in AuditLog(root).entries())
            p["approval"] = {"approved": bool(rec), "by": rec.get("actor") if rec else None, "at": rec.get("ts") if rec else None, "applied": applied}
            out.append(p)
        return out

    @app.get("/api/audit")
    def audit():
        log = AuditLog(root)
        ok, detail = log.verify()
        return {"intact": ok, "detail": detail, "entries": log.entries()[-200:]}

    @app.post("/api/proposals/{pid}/approve")
    async def approve(pid: str, request: Request):
        _local(request)
        body = await request.json()
        if body.get("csrf") != csrf:
            raise HTTPException(403, "bad csrf token")
        approver = (body.get("approver") or "").strip()
        if not approver:
            raise HTTPException(400, "approver name required")
        try:
            e = approve_from_dashboard(root, pid, body.get("typed", ""), cfg.protected_environments, actor=f"{approver} (dashboard)")
        except ApprovalError as ex:
            raise HTTPException(400, str(ex))
        return {"approved": pid, "audit": e["hash"][:12]}

    @app.post("/api/proposals/{pid}/apply")
    async def apply(pid: str, request: Request):
        _local(request)
        body = await request.json()
        if body.get("csrf") != csrf:
            raise HTTPException(403, "bad csrf token")
        p = load_proposal(root, pid)
        cli = FlagsmithCLI(project=p.get("project") or cfg.project, api_url=cfg.api_url)
        try:
            res = do_apply(root, pid, cfg.protected_environments, cli, dry_run=bool(body.get("dry_run")))
        except ApprovalError as ex:
            raise HTTPException(400, str(ex))
        except FlagsmithError as ex:
            raise HTTPException(502, f"Flagsmith: {ex}")
        return res

    @app.post("/api/scan")
    async def start_scan(request: Request):
        _local(request)
        body = await request.json()
        if body.get("csrf") != csrf:
            raise HTTPException(403, "bad csrf token")
        if jobs["scan"].get("state") == "running":
            return jobs["scan"]
        log: list[str] = []
        jobs["scan"] = {"state": "running", "log": log, "reason": body.get("reason", "manual")}

        def work():
            try:
                rep = scan(cfg, ScanOptions(environment=body.get("environment"), progress=log.append))
                jobs["scan"] = {"state": "done", "run_id": rep["run_id"], "status": rep["verdict"]["status"], "log": log}
                if body.get("proposal"):
                    AuditLog(root).append("reverified", proposal=body["proposal"], run=rep["run_id"], status=rep["verdict"]["status"],
                                          live_hazards=[h["id"] for h in rep.get("hazards", []) if h.get("status") == "live"], actor="dashboard")
            except Exception as e:  # surfaced to the UI
                jobs["scan"] = {"state": "error", "error": f"{type(e).__name__}: {e}", "log": log}

        threading.Thread(target=work, daemon=True).start()
        return jobs["scan"]

    @app.get("/api/scan")
    def scan_status():
        return jobs["scan"]

    if ASSETS.exists():
        app.mount("/brand", StaticFiles(directory=ASSETS), name="brand")
    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        idx = STATIC / "index.html"
        if not idx.exists():
            return JSONResponse({"error": "dashboard assets missing"}, status_code=500)
        return FileResponse(idx)

    return app
