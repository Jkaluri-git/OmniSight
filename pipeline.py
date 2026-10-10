"""End-to-end job: staging URL -> capture -> VLM -> self-heal -> PR."""
from __future__ import annotations

import logging
import traceback
from pathlib import Path

from .config import Settings
from .github_bot.pr import PRBot
from .models import JobReport
from .navigator.navigator import Navigator, load_flow
from .orchestrator.loop import SelfHealer
from .store import JobStore
from .vlm.clients import make_client
from .vlm.engine import VLMEngine

log = logging.getLogger("omnisight.pipeline")
DEFAULT_FLOW = Path(__file__).resolve().parent.parent / "flows" / "mock_shop.json"


def summarize(report: JobReport) -> dict:
    hs = [h for s in report.states for h in s.findings]
    return {"states": len(report.states), "findings": len(hs), "fixed": sum(h.status == "fixed" for h in hs),
            "unresolved": sum(h.status == "unresolved" for h in hs), "max_layout_shift": max([s.layout_shift for s in report.states] or [0])}


def run_job(job_id: str, settings: Settings, store: JobStore, flow_path: str | None = None, crawl: bool = False, engine: VLMEngine | None = None) -> JobReport | None:
    job = store.get(job_id)
    run_dir = settings.runs_dir / job_id
    run_dir.mkdir(parents=True, exist_ok=True)
    store.update(job_id, status="running")
    try:
        engine = engine or VLMEngine(make_client(settings))
        healer = SelfHealer(engine, run_dir, settings.max_attempts)
        nav = Navigator(job["staging_url"], settings.viewports, run_dir)
        cb = healer.heal_state
        if crawl or not (flow_path or job.get("flow")):
            states = nav.crawl(cb) if crawl else nav.run_flow(load_flow(DEFAULT_FLOW), cb)
        else:
            states = nav.run_flow(load_flow(flow_path or job["flow"]), cb)
        report = JobReport(states=states, vlm=engine.name)
        report.summary = summarize(report)
        (run_dir / "report.json").write_text(report.model_dump_json(indent=2))
        store.update(job_id, report=report.model_dump_json(), summary=report.summary)

        if report.accepted_fixes():
            bot = PRBot(settings.github_token, settings.css_path, settings.runs_dir, settings.auto_merge)
            pr = bot.open_pr(job, report)
            store.update(job_id, status="awaiting_review", pr_url=pr.url, pr_number=pr.number)
        else:
            store.update(job_id, status="clean" if not report.summary["findings"] else "needs_human", decision="n/a")
        return report
    except Exception as e:  # noqa: BLE001
        log.error("job %s failed: %s", job_id, traceback.format_exc())
        store.update(job_id, status="failed", error=str(e)[:500])
        return None
