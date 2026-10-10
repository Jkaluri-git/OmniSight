"""VLM engine: builds multimodal prompts (screenshot + DOM + signals), parses/validates responses."""
from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from ..models import Analysis, Finding, Fix, Verification
from . import prompts
from .clients import VLMClient
from .extract import extract_json, fix_from_code_blocks

log = logging.getLogger("omnisight.vlm")
_SLIM = ("type", "selector", "other_selector", "text", "detail", "key", "count")


def slim_signals(signals: list[dict]) -> str:
    return json.dumps([{k: s[k] for k in _SLIM if k in s} for s in signals], separators=(",", ":"))


def slim_dom(dom: dict) -> str:
    return json.dumps({"focus": dom.get("focus", [])[:6], "page": (dom.get("page") or "")[:5000]}, separators=(",", ":"))


class VLMEngine:
    def __init__(self, client: VLMClient):
        self.client = client

    @property
    def name(self) -> str:
        return self.client.name

    # -- ANALYZE ---------------------------------------------------------------
    def analyze(self, state, images: list[bytes]) -> list[Finding]:
        text = self.client.complete(prompts.SYSTEM, prompts.analyze_prompt(state, slim_signals(state.signals), slim_dom(state.dom)), images)
        findings = self._parse_findings(text)
        return self._merge(findings, state.signals)

    def _parse_findings(self, text: str) -> list[Finding]:
        data = extract_json(text)
        try:
            if data is not None:
                return Analysis.model_validate(data if "findings" in data else {"findings": [data]}).findings
        except ValidationError as e:
            log.warning("analysis schema mismatch: %s", e)
        fix = fix_from_code_blocks(text)  # extraction fallback
        if fix:
            return [Finding(description="Issue reported in free text", selector=fix.selector, fix=fix, confidence=0.3)]
        return []

    @staticmethod
    def _merge(findings: list[Finding], signals: list[dict]) -> list[Finding]:
        """Attach browser evidence to each VLM finding. Geometry-backed hard failures the VLM missed are kept."""
        used = set()
        for f in findings:
            for s in signals:
                if s["key"] in used:
                    continue
                if f.selector and f.selector in (s.get("selector"), s.get("other_selector")) or (f.type == s["type"] and f.selector is None):
                    f.signal_key, f.bbox = s["key"], f.bbox or _bbox(s)
                    f.selector = f.selector or s.get("selector")
                    used.add(s["key"])
                    break
            else:
                for s in signals:  # same type + same group via generalised selector
                    if s["key"] not in used and f.type == s["type"] and f.selector and f.selector.split(":")[0] in (s.get("selector") or ""):
                        f.signal_key, f.bbox = s["key"], f.bbox or _bbox(s)
                        used.add(s["key"])
                        break
        for s in signals:
            if s["key"] not in used and s["type"] in ("hidden_element", "clipping", "overflow"):
                findings.append(Finding(type=s["type"], severity="high", selector=s.get("selector"), other_selector=s.get("other_selector"),
                                        description=f"{s['type']} measured by the browser (not flagged by the VLM): {json.dumps(s.get('detail', {}))}",
                                        bbox=_bbox(s), signal_key=s["key"], confidence=0.6, geometry_only=True))
        # page-level overflow is redundant when the offending element is already reported as clipping
        clip_sel = {f.selector for f in findings if f.type == "clipping"}
        findings = [f for f in findings if not (f.type == "overflow" and f.selector in clip_sel)]
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        return sorted(findings, key=lambda f: order[f.severity])

    # -- REFINE ------------------------------------------------------------------
    def refine(self, finding: Finding, history: list[dict], images: list[bytes], signals_now: list[dict], dom: dict) -> Fix | None:
        fj = finding.model_dump_json(exclude={"fix", "bbox"})
        text = self.client.complete(prompts.SYSTEM, prompts.refine_prompt(fj, json.dumps(history), slim_signals(signals_now), slim_dom(dom)), images)
        data = extract_json(text) or {}
        try:
            if data.get("fix"):
                return Fix.model_validate(data["fix"])
            if "selector" in data and "css" in data:
                return Fix.model_validate(data)
        except ValidationError:
            pass
        return fix_from_code_blocks(text)

    # -- VERIFY ------------------------------------------------------------------
    def verify(self, finding: Finding, images: list[bytes], signals_now: list[dict]) -> Verification:
        fj = finding.model_dump_json(exclude={"bbox"})
        text = self.client.complete(prompts.SYSTEM, prompts.verify_prompt(fj, slim_signals(signals_now)), images)
        data = extract_json(text) or {}
        try:
            return Verification.model_validate(data)
        except ValidationError:
            return Verification(fixed=False, notes="unparseable verification response")


def _bbox(s: dict):
    from ..models import BBox

    return BBox(**s["bbox"]) if s.get("bbox") else None
