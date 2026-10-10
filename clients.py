"""VLM backends behind one interface: OpenAI (GPT-4o), Anthropic (Claude), Ollama (LLaVA), and an
offline rule-based 'heuristic' backend that consumes the browser signals (CI smoke tests / no-key demo)."""
from __future__ import annotations

import base64
import json
import re
import time

import httpx

from ..config import Settings


class VLMClient:
    name = "base"

    def complete(self, system: str, user: str, images: list[bytes]) -> str:  # pragma: no cover
        raise NotImplementedError


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _post(url: str, headers: dict, payload: dict, timeout: float = 120) -> dict:
    last = None
    for attempt in range(3):
        try:
            r = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 529):
                last = RuntimeError(f"{r.status_code}: {r.text[:200]}")
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"VLM request failed: {last}")


class OpenAIClient(VLMClient):
    def __init__(self, key: str, model: str):
        self.key, self.model, self.name = key, model, f"openai:{model}"

    def complete(self, system, user, images):
        content = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_b64(i)}", "detail": "high"}} for i in images]
        d = _post("https://api.openai.com/v1/chat/completions", {"Authorization": f"Bearer {self.key}"},
                  {"model": self.model, "temperature": 0.1, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]})
        return d["choices"][0]["message"]["content"]


class AnthropicClient(VLMClient):
    def __init__(self, key: str, model: str):
        self.key, self.model, self.name = key, model, f"anthropic:{model}"

    def complete(self, system, user, images):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _b64(i)}} for i in images]
        content.append({"type": "text", "text": user})
        d = _post("https://api.anthropic.com/v1/messages", {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                  {"model": self.model, "max_tokens": 3000, "temperature": 0.1, "system": system,
                   "messages": [{"role": "user", "content": content}]})
        return "".join(b.get("text", "") for b in d["content"])


class OllamaClient(VLMClient):
    def __init__(self, host: str, model: str):
        self.host, self.model, self.name = host.rstrip("/"), model, f"ollama:{model}"

    def complete(self, system, user, images):
        d = _post(f"{self.host}/api/chat", {}, {"model": self.model, "stream": False, "format": "json", "options": {"temperature": 0.1},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user, "images": [_b64(i) for i in images]}]}, timeout=300)
        return d["message"]["content"]


# --------------------------------------------------------------------------- offline backend
def _tag(prompt: str, tag: str):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", prompt, re.S)
    try:
        return json.loads(m.group(1)) if m else None
    except Exception:
        return None


def _gen(sel: str | None) -> str | None:
    return re.sub(r":nth-(?:of-type|child)\(\d+\)", "", sel or "") or None


def _short(sel: str | None) -> str | None:
    """Last two path segments, so one rule covers every instance of a component."""
    s = _gen(sel)
    return " > ".join(s.split(" > ")[-2:]) if s else None


def _luma(hexc: str) -> float:
    r, g, b = (int(hexc[i : i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def heuristic_fix(sig: dict, attempt: int = 1) -> dict:
    t, sel, d = sig["type"], sig.get("selector"), sig.get("detail", {})
    vw = d.get("viewportWidth")
    media = f"(max-width: {max(480, vw)}px)" if vw and t == "clipping" else None
    if t in ("clipping", "overflow"):
        css = "margin-left: 0; max-width: 100%; box-sizing: border-box"
        tw = {"add": ["ml-0", "max-w-full"], "remove": []}
        if attempt > 1:
            css += "; position: static; transform: none; left: auto; right: auto; width: 100%; display: block"
            tw["add"] += ["static", "w-full"]
        return dict(selector=_short(sel), css=css, media=media, tailwind=tw, rationale="Pull the element back inside the viewport width.")
    if t == "hidden_element":
        css = "opacity: 1; visibility: visible; height: auto; min-height: 44px; padding: 12px 20px; overflow: visible; display: inline-block"
        if attempt > 1:
            css += "; position: static; z-index: auto; clip-path: none; pointer-events: auto; width: auto"
        return dict(selector=_short(sel), css=css, tailwind={"add": ["opacity-100", "visible", "block"], "remove": ["hidden", "invisible", "opacity-0", "h-0"]},
                    rationale="Make the control visible and give it a tappable size.")
    if t == "contrast":
        bg = d.get("background", "#ffffff")
        dark = _luma(bg) > 0.4
        css = f"color: {'#1f2933' if dark else '#ffffff'}"
        if attempt > 1:
            css = f"color: {'#000000' if dark else '#ffffff'}"
        return dict(selector=_short(sel), css=css, tailwind={"add": ["text-gray-900" if dark else "text-white"], "remove": ["text-gray-300", "text-gray-400"]},
                    rationale=f"Contrast ratio {d.get('ratio')} is below {d.get('required')}:1; use a darker/lighter text colour.")
    if t == "overlap":
        css = "position: static; margin-top: 8px; transform: none"
        if attempt > 1:
            css += "; display: block; line-height: 1.4; clear: both"
        return dict(selector=_short(sig.get("other_selector") or sel), css=css, tailwind={"add": ["mt-2", "static"], "remove": ["-mt-6", "absolute"]},
                    rationale="Restore normal flow spacing so the text blocks no longer collide.")
    return dict(selector=_short(sel), css="", rationale="No automatic fix.")


class HeuristicClient(VLMClient):
    """Deterministic stand-in for a VLM: it cannot 'see', so it trusts browser signals. Marked as such in reports."""
    name = "heuristic(offline)"

    def complete(self, system, user, images):
        if "TASK: ANALYZE" in user:
            out = []
            for s in _tag(user, "signals") or []:
                sev = {"hidden_element": "critical", "clipping": "high", "overflow": "high", "overlap": "medium", "contrast": "medium"}.get(s["type"], "low")
                out.append({"type": s["type"], "severity": sev, "confidence": 0.7, "selector": s.get("selector"), "other_selector": s.get("other_selector"),
                            "description": f"{s['type'].replace('_', ' ')} detected on {s.get('text') or s.get('selector')} ({json.dumps(s.get('detail', {}))})",
                            "fix": heuristic_fix(s)})
            return json.dumps({"findings": out})
        if "TASK: REFINE" in user:
            f = _tag(user, "finding") or {}
            n = len(_tag(user, "attempts") or []) + 1
            sig = {"type": f.get("type", "other"), "selector": f.get("selector"), "other_selector": f.get("other_selector"), "detail": {}}
            return json.dumps({"fix": heuristic_fix(sig, attempt=n)})
        if "TASK: VERIFY" in user:
            f, now = _tag(user, "finding") or {}, _tag(user, "signals_after") or []
            still = [s for s in now if s.get("key") == f.get("signal_key")]
            return json.dumps({"fixed": not still, "regression": False, "notes": "signal cleared" if not still else "signal still present"})
        return "{}"


def make_client(s: Settings) -> VLMClient:
    mode = s.vlm
    if mode == "auto":
        mode = "openai" if s.openai_key else "anthropic" if s.anthropic_key else "heuristic"
        if mode == "heuristic":
            try:
                if httpx.get(s.ollama_host + "/api/tags", timeout=1.5).status_code == 200:
                    mode = "ollama"
            except Exception:
                pass
    if mode == "openai":
        return OpenAIClient(s.openai_key, s.openai_model)
    if mode == "anthropic":
        return AnthropicClient(s.anthropic_key, s.anthropic_model)
    if mode == "ollama":
        return OllamaClient(s.ollama_host, s.ollama_model)
    return HeuristicClient()
