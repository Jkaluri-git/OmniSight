"""Turn free-form model output into structured data: JSON objects and executable code blocks."""
from __future__ import annotations

import json
import re

FENCE = re.compile(r"```(?P<lang>[a-zA-Z0-9_+-]*)[ \t]*\n(?P<body>.*?)```", re.S)


def extract_code_blocks(text: str) -> list[dict]:
    """All fenced code blocks as [{'lang','code'}] (the 'extraction check' from the mid-project review)."""
    return [{"lang": m.group("lang").lower(), "code": m.group("body").strip()} for m in FENCE.finditer(text or "")]


def _balanced_objects(text: str):
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start : i + 1]


def _loads_lenient(s: str):
    try:
        return json.loads(s)
    except Exception:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", s))  # trailing commas


def extract_json(text: str) -> dict | None:
    """Best-effort: fenced ```json block, raw JSON, or the largest balanced {...} embedded in prose."""
    if not text:
        return None
    candidates = [b["code"] for b in extract_code_blocks(text) if b["lang"] in ("json", "")] + [text.strip()]
    candidates += sorted(_balanced_objects(text), key=len, reverse=True)
    for c in candidates:
        try:
            v = _loads_lenient(c)
            if isinstance(v, dict):
                return v
        except Exception:
            continue
    return None


_RULE = re.compile(r"(?P<sel>[^{}@]+)\{(?P<body>[^{}]*)\}")


def fix_from_code_blocks(text: str):
    """Fallback when the model ignored the JSON schema: pull the first CSS rule out of a ```css block."""
    from ..models import Fix

    for b in extract_code_blocks(text):
        if b["lang"] in ("css", "tailwind", ""):
            m = _RULE.search(b["code"])
            if m:
                media = re.search(r"@media\s*([^{]+)\{", b["code"])
                return Fix(selector=m.group("sel").strip(), css=m.group("body").strip(), media=media.group(1).strip() if media else None,
                           rationale="extracted from code block")
    return None
