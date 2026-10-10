"""CI/CD gateway (FastAPI): webhook receivers for GitHub Actions / Jenkins, job API, dashboard + artifact hosting."""
from __future__ import annotations

import hashlib
import hmac
import logging
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..github_bot.pr import PRBot
from ..pipeline import run_job
from ..store import JobStore

log = logging.getLogger("omnisight.gateway")
ROOT = Path(__file__).resolve().parents[2]


class CIEvent(BaseModel):
    repo: str                      # "owner/name"
    sha: str
    staging_url: str
    branch: str = ""
    flow: str = ""                 # optional path to a flow JSON
    crawl: bool = False


def create_app(settings: Settings | None = None, runner=run_job) -> FastAPI:
    s = settings or get_settings()
    store = JobStore(s.runs_dir)
    bot = PRBot(s.github_token, s.css_path, s.runs_dir, s.auto_merge)
    app = FastAPI(title="OmniSight", version="1.0")
    app.state.store, app.state.settings = store, s

    def auth(x_api_key: str = Header(default=""), authorization: str = Header(default="")):
        if s.api_key and s.api_key not in (x_api_key, authorization.removeprefix("Bearer ").strip()):
            raise HTTPException(401, "invalid API key")

    def enqueue(ev: CIEvent, bg: BackgroundTasks) -> dict:
        job = store.create(ev.repo, ev.sha, ev.staging_url, ev.branch, ev.flow)
        bg.add_task(runner, job["id"], s, store, ev.flow or None, ev.crawl)
        return {"job_id": job["id"], "status": "queued"}

    @app.get("/health")
    def health():
        return {"ok": True, "dry_run": s.dry_run, "vlm": s.vlm}

    # ---- GitHub webhook (deployment_status success => staging is live) ----
    @app.post("/webhook/github")
    async def github_webhook(request: Request, bg: BackgroundTasks, x_github_event: str = Header(default=""), x_hub_signature_256: str = Header(default="")):
        raw = await request.body()
        if s.webhook_secret:
            mac = "sha256=" + hmac.new(s.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(mac, x_hub_signature_256):
                raise HTTPException(401, "bad signature")
        p = await request.json()
        if x_github_event == "ping":
            return {"pong": True}
        if x_github_event == "deployment_status" and p.get("deployment_status", {}).get("state") == "success":
            url = p["deployment_status"].get("environment_url") or p["deployment_status"].get("target_url")
            if url:
                dep = p.get("deployment", {})
                return enqueue(CIEvent(repo=p["repository"]["full_name"], sha=dep.get("sha", ""), branch=dep.get("ref", ""), staging_url=url), bg)
        return {"ignored": x_github_event}

    # ---- Generic CI webhook (Jenkins, GitLab, curl from Actions) ----
    @app.post("/webhook/ci", dependencies=[Depends(auth)])
    def ci_webhook(ev: CIEvent, bg: BackgroundTasks):
        return enqueue(ev, bg)

    @app.post("/webhook/simulate", dependencies=[Depends(auth)])
    def simulate(bg: BackgroundTasks, bugs: str = "clip,hidden,contrast,overlap"):
        """Demo: scan the bundled mock shop with deliberate bugs."""
        return enqueue(CIEvent(repo="demo/mock-shop", sha="0" * 40, branch="main", staging_url=f"{s.public_url}/mock?bugs={bugs}"), bg)

    # ---- dashboard API ----
    @app.get("/api/jobs", dependencies=[Depends(auth)])
    def jobs():
        return store.list()

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(auth)])
    def job(job_id: str):
        j = store.get(job_id, full=True)
        if not j:
            raise HTTPException(404)
        return j

    def _decide(job_id: str, approve: bool):
        j = store.get(job_id)
        if not j:
            raise HTTPException(404)
        if j["decision"] not in ("pending",):
            raise HTTPException(409, f"already {j['decision']}")
        msg = bot.decide(j, approve)
        store.update(job_id, decision="approved" if approve else "rejected", status="approved" if approve else "rejected")
        return {"decision": "approved" if approve else "rejected", "detail": msg}

    @app.post("/api/jobs/{job_id}/approve", dependencies=[Depends(auth)])
    def approve(job_id: str):
        return _decide(job_id, True)

    @app.post("/api/jobs/{job_id}/reject", dependencies=[Depends(auth)])
    def reject(job_id: str):
        return _decide(job_id, False)

    # ---- static: artifacts, dashboard, demo shop ----
    s.runs_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/artifacts", StaticFiles(directory=s.runs_dir), name="artifacts")
    app.mount("/mock", StaticFiles(directory=ROOT / "mock_shop", html=True), name="mock")

    @app.get("/")
    @app.get("/dashboard")
    def dashboard():
        return FileResponse(ROOT / "dashboard" / "index.html")

    return app


app = create_app()
