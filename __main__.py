"""CLI:  python -m omnisight serve | demo | scan <url>"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

import uvicorn

from .config import get_settings


def main(argv=None):
    ap = argparse.ArgumentParser(prog="omnisight")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve"); sv.add_argument("--port", type=int, default=8000); sv.add_argument("--host", default="0.0.0.0")
    dm = sub.add_parser("demo"); dm.add_argument("--port", type=int, default=8000); dm.add_argument("--bugs", default="clip,hidden,contrast,overlap")
    sc = sub.add_parser("scan"); sc.add_argument("url"); sc.add_argument("--flow"); sc.add_argument("--crawl", action="store_true"); sc.add_argument("--repo", default="local/scan")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if a.cmd == "serve":
        uvicorn.run("omnisight.gateway.app:app", host=a.host, port=a.port)
    elif a.cmd == "demo":
        # start the gateway, fire a simulated CI event against the bundled buggy shop, open the dashboard
        import httpx

        s = get_settings()
        threading.Thread(target=lambda: uvicorn.run("omnisight.gateway.app:app", host="127.0.0.1", port=a.port, log_level="warning"), daemon=True).start()
        for _ in range(50):
            try:
                if httpx.get(f"http://127.0.0.1:{a.port}/health").status_code == 200:
                    break
            except Exception:
                time.sleep(0.2)
        h = {"X-API-Key": s.api_key} if s.api_key else {}
        r = httpx.post(f"http://127.0.0.1:{a.port}/webhook/simulate", params={"bugs": a.bugs}, headers=h).json()
        print(f"Job {r['job_id']} queued. Dashboard: http://127.0.0.1:{a.port}/dashboard   (Ctrl-C to quit)")
        while True:
            time.sleep(3600)
    elif a.cmd == "scan":
        from .pipeline import run_job
        from .store import JobStore

        s = get_settings()
        store = JobStore(s.runs_dir)
        job = store.create(a.repo, "0" * 40, a.url, flow=a.flow or "")
        rep = run_job(job["id"], s, store, a.flow, a.crawl)
        j = store.get(job["id"])
        print(j["status"], j["summary"], j.get("pr_url") or j.get("error") or "")
        sys.exit(0 if rep else 1)


if __name__ == "__main__":
    main()
