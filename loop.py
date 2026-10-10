"""Agentic orchestrator: Plan -> Execute -> Evaluate, with retries until the fix is *visually and geometrically* verified."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models import BBox, FixAttempt, Fix, Finding, HealedFinding, StateReport
from ..navigator.navigator import CapturedState
from ..vlm.engine import VLMEngine
from . import imaging

log = logging.getLogger("omnisight.loop")
SHIFT_LIMIT = 0.10  # >10% of pixels moving outside the target region counts as a regression


class SelfHealer:
    def __init__(self, engine: VLMEngine, run_dir: Path, max_attempts: int = 3):
        self.engine, self.run_dir, self.max_attempts = engine, Path(run_dir), max_attempts

    def heal_state(self, driver, state: CapturedState) -> StateReport:
        vp = state.viewport
        report = StateReport(step=state.step, viewport=vp.name, width=vp.width, height=vp.height, url=state.url, before=state.path)
        if not state.signals and not state.meta.get("blocked_reason"):
            return self._clean_check(driver, state, report)

        # PLAN: the VLM looks at the downscaled screenshot + signals + DOM and proposes findings with fixes
        findings = self.engine.analyze(state, [imaging.downscale(state.screenshot)])
        before_png = state.screenshot
        current_keys = {s["key"] for s in state.signals}
        regions, tag = [], f"{vp.name}__{_slug(state.step)}"

        for i, finding in enumerate(findings):
            healed, current_keys = self._heal_one(driver, state, finding, before_png, current_keys, f"{tag}__{i}")
            report.findings.append(healed)
            if finding.bbox:
                regions.append(imaging.crop_region(_size(before_png), finding.bbox, pad=60))

        if any(h.status == "fixed" for h in report.findings):
            after = driver.screenshot()
            name = f"shots/{tag}__after.png"
            (self.run_dir / name).write_bytes(after)
            report.after = name
            report.layout_shift = round(imaging.layout_shift(before_png, after, regions), 4)
        return report

    # ---------------------------------------------------------------------------------
    def _clean_check(self, driver, state, report):
        return report

    def _heal_one(self, driver, state, finding: Finding, before_png: bytes, current_keys: set, tag: str):
        attempts: list[FixAttempt] = []
        history: list[dict] = []
        region = imaging.crop_region(_size(before_png), finding.bbox) if finding.bbox else None
        before_crop = imaging.crop(before_png, region) if region else imaging.downscale(before_png)
        fix = finding.fix if (finding.fix and finding.fix.css.strip()) else None
        last_after = before_crop

        for n in range(1, self.max_attempts + 1):
            # PLAN (attempt 1 uses the analysis fix; later attempts ask the VLM to refine using the failed outcome)
            if fix is None or n > 1:
                fix = self.engine.refine(finding, history, [before_crop, last_after], driver.probe(), state.dom)
            if fix is None or not fix.to_css():
                attempts.append(FixAttempt(n=n, css="", notes="model returned no usable fix"))
                break

            # EXECUTE
            handle = driver.apply_css(fix.to_css())
            driver.settle()
            signals_now = driver.probe()
            after_png = driver.screenshot()
            after_crop = imaging.crop(after_png, region) if region else imaging.downscale(after_png)
            last_after = after_crop

            # EVALUATE: objective geometry first (cheap), VLM judgement only if geometry passes
            now_keys = {s["key"] for s in signals_now}
            geo_ok = (finding.signal_key not in now_keys) if finding.signal_key else None
            new_keys = now_keys - current_keys
            regression = bool(new_keys)
            vlm_ok, notes = None, ""
            if geo_ok is False:
                notes = "browser still measures this issue after the fix"
            else:
                ver = self.engine.verify(finding, [before_crop, after_crop], signals_now)
                vlm_ok, notes = ver.fixed, ver.notes
                regression = regression or ver.regression
            if regression and new_keys:
                notes += f" | new issues: {sorted(new_keys)}"
            ok = geo_ok is not False and bool(vlm_ok) and not regression
            attempts.append(FixAttempt(n=n, css=fix.to_css(), geometry_ok=geo_ok, vlm_ok=vlm_ok, regression=regression, notes=notes.strip()))

            if ok:
                (self.run_dir / "shots").mkdir(exist_ok=True)
                names = {}
                for label, data in (("before", before_crop), ("after", after_crop)):
                    names[label] = f"shots/{tag}__{label}_crop.png"
                    (self.run_dir / names[label]).write_bytes(data)
                focus = imaging.focus_fraction(finding.bbox, region) if (finding.bbox and region) else None
                return (HealedFinding(finding=finding, status="fixed", attempts=attempts, fix=fix, before_crop=names["before"],
                                      after_crop=names["after"], focus=focus), now_keys)

            # EVALUATE failed -> roll back and feed the failure into the next PLAN
            driver.remove_css(handle)
            history.append({"attempt": n, "css": fix.to_css(), "result": notes or "not resolved"})
            fix = None

        return HealedFinding(finding=finding, status="unresolved", attempts=attempts), current_keys


def _size(png: bytes) -> tuple[int, int]:
    import io
    from PIL import Image

    return Image.open(io.BytesIO(png)).size


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]
