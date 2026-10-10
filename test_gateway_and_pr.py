import hashlib, hmac, json
from fastapi.testclient import TestClient
from omnisight.config import Settings
from omnisight.gateway.app import create_app
from omnisight.github_bot.pr import PRBot
from omnisight.models import BBox, Finding, Fix, HealedFinding, JobReport, StateReport
from omnisight.orchestrator import imaging
from PIL import Image
import io


def fake_runner(job_id, s, store, flow, crawl):
    store.update(job_id, status="awaiting_review", pr_url="file:///x", pr_number=0, summary={"fixed": 1})


def mk(tmp_path, **kw):
    s = Settings(runs_dir=tmp_path, **kw)
    return TestClient(create_app(s, runner=fake_runner))


def test_ci_webhook_creates_job_and_runs(tmp_path):
    c = mk(tmp_path)
    r = c.post("/webhook/ci", json={"repo": "o/r", "sha": "abc123", "staging_url": "http://s"}).json()
    j = c.get(f"/api/jobs/{r['job_id']}").json()
    assert j["status"] == "awaiting_review" and j["repo"] == "o/r"


def test_github_signature_enforced(tmp_path):
    c = mk(tmp_path, webhook_secret="s3cret")
    body = json.dumps({"repository": {"full_name": "o/r"}, "deployment": {"sha": "a", "ref": "main"},
                       "deployment_status": {"state": "success", "environment_url": "http://stg"}}).encode()
    hdr = {"X-GitHub-Event": "deployment_status", "Content-Type": "application/json"}
    assert c.post("/webhook/github", content=body, headers={**hdr, "X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    ok = c.post("/webhook/github", content=body, headers={**hdr, "X-Hub-Signature-256": sig})
    assert ok.status_code == 200 and "job_id" in ok.json()


def test_api_key_and_decision_flow(tmp_path):
    c = mk(tmp_path, api_key="k")
    assert c.get("/api/jobs").status_code == 401
    h = {"X-API-Key": "k"}
    jid = c.post("/webhook/ci", json={"repo": "o/r", "sha": "a", "staging_url": "http://s"}, headers=h).json()["job_id"]
    assert c.post(f"/api/jobs/{jid}/approve", headers=h).json()["decision"] == "approved"
    assert c.post(f"/api/jobs/{jid}/reject", headers=h).status_code == 409  # already decided


def test_dashboard_and_mock_served(tmp_path):
    c = mk(tmp_path)
    assert "OmniSight" in c.get("/dashboard").text and c.get("/mock/cart.html").status_code == 200


def test_pr_dry_run_writes_preview(tmp_path):
    fix = Fix(selector=".cta", css="margin-left:0", media="(max-width: 480px)", rationale="pull inside viewport")
    h = HealedFinding(finding=Finding(type="clipping", selector=".cta", description="clipped"), status="fixed", fix=fix, before_crop="shots/b.png", after_crop="shots/a.png")
    rep = JobReport(states=[StateReport(step="cart", viewport="iphone-14", width=375, height=812, url="u", before="x", findings=[h])], vlm="t")
    res = PRBot("", "src/o.css", tmp_path).open_pr({"id": "job1", "sha": "a" * 40, "staging_url": "http://s", "repo": "o/r"}, rep)
    assert res.dry_run and "@media (max-width: 480px)" in (tmp_path / "job1/pr_preview/omnisight-fixes.css").read_text()
    assert "![before]" in (tmp_path / "job1/pr_preview/PR.md").read_text()


def _png(color, size=(200, 200)):
    b = io.BytesIO(); Image.new("RGB", size, color).save(b, "PNG"); return b.getvalue()


def test_layout_shift_ignores_target_region():
    a = Image.new("RGB", (200, 200), "white"); b = a.copy(); b.paste("black", (10, 10, 60, 60))
    ba, bb = io.BytesIO(), io.BytesIO(); a.save(ba, "PNG"); b.save(bb, "PNG")
    assert imaging.layout_shift(ba.getvalue(), bb.getvalue()) > 0.05
    assert imaging.layout_shift(ba.getvalue(), bb.getvalue(), [(0, 0, 80, 80)]) == 0
