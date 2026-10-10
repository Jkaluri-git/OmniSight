"""Tiny SQLite job store (stdlib only)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


class JobStore:
    def __init__(self, runs_dir: Path):
        runs_dir = Path(runs_dir)
        runs_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(runs_dir / "jobs.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, created REAL, status TEXT, repo TEXT, sha TEXT, branch TEXT,
            staging_url TEXT, flow TEXT, decision TEXT, pr_url TEXT, pr_number INTEGER, error TEXT, summary TEXT, report TEXT)""")
        self.db.commit()

    def create(self, repo: str, sha: str, staging_url: str, branch: str = "", flow: str = "") -> dict:
        jid = uuid.uuid4().hex[:12]
        with self.lock:
            self.db.execute("INSERT INTO jobs(id,created,status,repo,sha,branch,staging_url,flow,decision) VALUES(?,?,?,?,?,?,?,?,?)",
                            (jid, time.time(), "queued", repo, sha, branch, staging_url, flow, "pending"))
            self.db.commit()
        return self.get(jid)

    def update(self, jid: str, **kw):
        for k in ("summary", "report"):
            if k in kw and not isinstance(kw[k], str):
                kw[k] = json.dumps(kw[k])
        cols = ",".join(f"{k}=?" for k in kw)
        with self.lock:
            self.db.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*kw.values(), jid))
            self.db.commit()

    def get(self, jid: str, full: bool = False) -> dict | None:
        r = self.db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        return self._row(r, full) if r else None

    def list(self) -> list[dict]:
        return [self._row(r, False) for r in self.db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100")]

    @staticmethod
    def _row(r, full):
        d = dict(r)
        d["summary"] = json.loads(d["summary"]) if d.get("summary") else {}
        rep = d.pop("report")
        d["report"] = json.loads(rep) if (full and rep) else None
        if not full:
            d.pop("report")
        return d
