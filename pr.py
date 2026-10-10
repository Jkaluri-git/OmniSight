"""CI/CD write-back: branch -> commit fixes + visual proof -> Pull Request (PyGithub). Dry-run writes a preview to disk."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..models import JobReport

log = logging.getLogger("omnisight.pr")
LABEL_AUTO, LABEL_OK, LABEL_NO = "omnisight", "omnisight:approved", "omnisight:rejected"


@dataclass
class PRResult:
    url: str
    number: int
    branch: str
    dry_run: bool
    patched_files: list[str]


def css_block(job_id: str, report: JobReport) -> str:
    lines = [f"/* ===== OmniSight job {job_id} — auto-generated, review before merge ===== */"]
    for fx in report.accepted_fixes():
        lines.append(f"/* {fx.rationale or fx.selector} */")
        lines.append(fx.to_css())
    return "\n".join(lines) + "\n"


def pr_body(job: dict, report: JobReport, proof_url) -> str:
    fixed = [(s, h) for s in report.states for h in s.findings if h.status == "fixed"]
    open_ = [(s, h) for s in report.states for h in s.findings if h.status == "unresolved"]
    out = [f"## 🔎 OmniSight visual QA\nBuild `{job['sha'][:7]}` on staging: {job['staging_url']}\n",
           f"**{len(fixed)} issue(s) auto-fixed**, {len(open_)} need a human. VLM: `{report.vlm}`.\n"]
    for s, h in fixed:
        f = h.finding
        out.append(f"### {f.type.replace('_', ' ').title()} — `{f.selector}` ({s.viewport} {s.width}px, step “{s.step}”)\n{f.description}\n")
        if h.before_crop and h.after_crop:
            out.append(f"| Before | After |\n|---|---|\n| ![before]({proof_url(h.before_crop)}) | ![after]({proof_url(h.after_crop)}) |\n")
        out.append("```css\n" + (h.fix.to_css() if h.fix else "") + "\n```")
        if h.fix and h.fix.tailwind and (h.fix.tailwind.add or h.fix.tailwind.remove):
            out.append(f"Tailwind equivalent: add `{' '.join(h.fix.tailwind.add)}`, remove `{' '.join(h.fix.tailwind.remove)}`")
        out.append(f"_Verified in {len(h.attempts)} attempt(s); layout shift outside target: {s.layout_shift:.1%}._\n")
    if open_:
        out.append("### ⚠️ Not auto-fixed\n" + "\n".join(f"- `{h.finding.selector}` ({s.viewport}): {h.finding.description}" for s, h in open_))
    out.append("\n---\nReview in the OmniSight dashboard to approve or reject.")
    return "\n".join(out)


class PRBot:
    def __init__(self, token: str, css_path: str, runs_dir: Path, auto_merge: bool = False):
        self.token, self.css_path, self.runs_dir, self.auto_merge = token, css_path, Path(runs_dir), auto_merge

    @property
    def dry_run(self) -> bool:
        return not self.token

    def open_pr(self, job: dict, report: JobReport) -> PRResult:
        run_dir = self.runs_dir / job["id"]
        title = f"fix(ui): OmniSight auto-fix for {len(report.accepted_fixes())} visual issue(s) @ {job['sha'][:7]}"
        branch = f"omnisight/fix-{job['id'][:8]}"
        evidence = sorted({p for s in report.states for h in s.findings if h.status == "fixed" for p in (h.before_crop, h.after_crop) if p})
        if self.dry_run:
            prev = run_dir / "pr_preview"
            prev.mkdir(parents=True, exist_ok=True)
            (prev / "omnisight-fixes.css").write_text(css_block(job["id"], report))
            (prev / "PR.md").write_text(f"# {title}\n\n" + pr_body(job, report, lambda p: f"../{p}"))
            return PRResult(url=f"file://{(prev / 'PR.md').resolve()}", number=0, branch=branch, dry_run=True, patched_files=[self.css_path])

        from github import Github, GithubException

        repo = Github(self.token).get_repo(job["repo"])
        base = job.get("branch") or repo.default_branch
        repo.create_git_ref(f"refs/heads/{branch}", repo.get_branch(base).commit.sha)
        patched: list[str] = []

        # 1) exact-match source patches proposed by the VLM (only when unambiguous)
        for fx in report.accepted_fixes():
            sp = fx.source_patch
            if not sp:
                continue
            try:
                cf = repo.get_contents(sp.file, ref=branch)
                text = cf.decoded_content.decode()
                if text.count(sp.find) == 1:
                    repo.update_file(sp.file, f"fix(ui): {fx.selector}", text.replace(sp.find, sp.replace), cf.sha, branch=branch)
                    patched.append(sp.file)
            except GithubException as e:
                log.warning("source patch skipped for %s: %s", sp.file, e)

        # 2) CSS override file (always applied; safe, reversible)
        block = css_block(job["id"], report)
        try:
            cf = repo.get_contents(self.css_path, ref=branch)
            repo.update_file(self.css_path, "chore(ui): OmniSight CSS overrides", cf.decoded_content.decode() + "\n" + block, cf.sha, branch=branch)
        except GithubException:
            repo.create_file(self.css_path, "chore(ui): OmniSight CSS overrides", block, branch=branch)
        patched.append(self.css_path)

        # 3) visual proof
        for rel in evidence:
            repo.create_file(f".omnisight/{job['id']}/{Path(rel).name}", "docs: OmniSight visual proof", (run_dir / rel).read_bytes(), branch=branch)
        proof = lambda rel: f"https://github.com/{job['repo']}/blob/{branch}/.omnisight/{job['id']}/{Path(rel).name}?raw=true"

        pr = repo.create_pull(title=title, body=pr_body(job, report, proof), head=branch, base=base)
        for lbl, color in ((LABEL_AUTO, "5319e7"),):
            try:
                repo.create_label(lbl, color)
            except GithubException:
                pass
        pr.add_to_labels(LABEL_AUTO)
        return PRResult(url=pr.html_url, number=pr.number, branch=branch, dry_run=False, patched_files=patched)

    def decide(self, job: dict, approve: bool, reviewer: str = "dashboard") -> str:
        """Approve -> label (+ optional merge). Reject -> comment, label, close PR."""
        if self.dry_run or not job.get("pr_number"):
            return "recorded locally (dry-run)"
        from github import Github, GithubException

        repo = Github(self.token).get_repo(job["repo"])
        pr = repo.get_pull(job["pr_number"])
        for lbl, color in ((LABEL_OK, "0e8a16"), (LABEL_NO, "b60205")):
            try:
                repo.create_label(lbl, color)
            except GithubException:
                pass
        if approve:
            pr.add_to_labels(LABEL_OK)
            pr.create_issue_comment(f"✅ Approved in OmniSight dashboard by {reviewer}.")
            if self.auto_merge:
                pr.merge(merge_method="squash")
                return "approved and merged"
            return "approved (labelled)"
        pr.add_to_labels(LABEL_NO)
        pr.create_issue_comment(f"❌ Rejected in OmniSight dashboard by {reviewer}. Closing.")
        pr.edit(state="closed")
        return "rejected and closed"
