"""Real-browser end-to-end test against the bundled buggy shop. Skipped when no Chromium is available."""
import functools, http.server, os, threading
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright")
ROOT = Path(__file__).resolve().parents[1]


def _chromium_ok():
    if os.getenv("OMNISIGHT_CHROMIUM_PATH"):
        return True
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_ok(), reason="no Chromium available")
def test_pipeline_fixes_all_deliberate_bugs(tmp_path):
    from omnisight.config import Settings, parse_viewports
    from omnisight.pipeline import run_job
    from omnisight.store import JobStore
    from omnisight.vlm.clients import HeuristicClient
    from omnisight.vlm.engine import VLMEngine

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "mock_shop"))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = Settings(runs_dir=tmp_path, github_token="", viewports=parse_viewports("phone:375x812:m,desktop:1280x800"))
        store = JobStore(tmp_path)
        job = store.create("demo/shop", "a" * 40, f"http://127.0.0.1:{srv.server_port}?bugs=clip,hidden,contrast,overlap")
        rep = run_job(job["id"], s, store, engine=VLMEngine(HeuristicClient()))
        assert rep is not None
        types = {h.finding.type for st in rep.states for h in st.findings if h.status == "fixed"}
        assert {"clipping", "hidden_element", "contrast", "overlap"} <= types
        assert rep.summary["unresolved"] == 0
        assert store.get(job["id"])["status"] == "awaiting_review"
    finally:
        srv.shutdown()
