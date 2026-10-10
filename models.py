"""Pydantic schemas shared by every module (VLM output, healing results, job reports)."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

IssueType = Literal["clipping", "overlap", "hidden_element", "contrast", "overflow", "misalignment", "other"]
Severity = Literal["low", "medium", "high", "critical"]
_TYPES = {"clipping", "overlap", "hidden_element", "contrast", "overflow", "misalignment", "other"}
_SEV = {"low", "medium", "high", "critical"}


class BBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class TailwindFix(BaseModel):
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class SourcePatch(BaseModel):
    """Exact-match find/replace in a source file (applied only if `find` occurs exactly once)."""

    file: str
    find: str
    replace: str


def _with_important(decls: str) -> str:
    out = []
    for d in decls.split(";"):
        d = d.strip()
        if not d or ":" not in d:
            continue
        out.append(d if "!important" in d else f"{d} !important")
    return "; ".join(out)


class Fix(BaseModel):
    selector: str
    css: str = ""
    media: Optional[str] = None
    tailwind: Optional[TailwindFix] = None
    source_patch: Optional[SourcePatch] = None
    rationale: str = ""

    def to_css(self) -> str:
        """Render as a CSS rule. Declarations get !important so the override wins over the buggy rule."""
        if not self.css.strip():
            return ""
        body = self.css.strip()
        rule = body if "{" in body else f"{self.selector} {{ {_with_important(body)}; }}"
        return f"@media {self.media} {{ {rule} }}" if self.media else rule


class Finding(BaseModel):
    type: IssueType = "other"
    severity: Severity = "medium"
    description: str = ""
    selector: Optional[str] = None
    other_selector: Optional[str] = None
    bbox: Optional[BBox] = None
    confidence: float = 0.5
    fix: Optional[Fix] = None
    signal_key: Optional[str] = None
    geometry_only: bool = False

    @field_validator("type", mode="before")
    @classmethod
    def _t(cls, v):
        v = re.sub(r"[\s-]+", "_", str(v or "other").lower())
        return v if v in _TYPES else {"hidden": "hidden_element", "hidden_button": "hidden_element", "clipped": "clipping", "text_overlap": "overlap", "low_contrast": "contrast"}.get(v, "other")

    @field_validator("severity", mode="before")
    @classmethod
    def _s(cls, v):
        v = str(v or "medium").lower()
        return v if v in _SEV else "medium"

    @field_validator("confidence", mode="before")
    @classmethod
    def _c(cls, v):
        try:
            return max(0.0, min(1.0, float(v)))
        except Exception:
            return 0.5


class Analysis(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class Verification(BaseModel):
    fixed: bool = False
    regression: bool = False
    notes: str = ""


# ---------- results ----------
class FixAttempt(BaseModel):
    n: int
    css: str
    geometry_ok: Optional[bool] = None
    vlm_ok: Optional[bool] = None
    regression: bool = False
    notes: str = ""


class HealedFinding(BaseModel):
    finding: Finding
    status: Literal["fixed", "unresolved"]
    attempts: list[FixAttempt] = Field(default_factory=list)
    fix: Optional[Fix] = None
    before_crop: Optional[str] = None
    after_crop: Optional[str] = None
    focus: Optional[BBox] = None  # bbox as fractions of the crop (for the dashboard overlay)


class StateReport(BaseModel):
    step: str
    viewport: str
    width: int
    height: int
    url: str
    before: str
    after: Optional[str] = None
    layout_shift: float = 0.0
    findings: list[HealedFinding] = Field(default_factory=list)


class JobReport(BaseModel):
    states: list[StateReport] = Field(default_factory=list)
    vlm: str = ""
    summary: dict = Field(default_factory=dict)

    def accepted_fixes(self) -> list[Fix]:
        seen, out = set(), []
        for s in self.states:
            for h in s.findings:
                if h.status == "fixed" and h.fix and (h.fix.selector, h.fix.css, h.fix.media) not in seen:
                    seen.add((h.fix.selector, h.fix.css, h.fix.media))
                    out.append(h.fix)
        return out
