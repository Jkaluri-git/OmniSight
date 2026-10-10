import json
from pathlib import Path

from omnisight.config import Viewport
from omnisight.models import Fix
from omnisight.navigator.navigator import CapturedState, normalize_signals
from omnisight.orchestrator.loop import SelfHealer
from omnisight.vlm.clients import HeuristicClient, VLMClient
from omnisight.vlm.engine import VLMEngine
from PIL import Image
import io


def png(w=375, h=600):
    b = io.BytesIO(); Image.new("RGB", (w, h), "white").save(b, "PNG"); return b.getvalue()


def state(signals):
    return CapturedState("cart", Viewport("iphone-14", 375, 812, True), "http://x/cart", png(), normalize_signals(signals), {"focus": [], "page": ""}, "shots/x.png")


CLIP = {"type": "clipping", "selector": "#checkout", "bbox": {"x": 276, "y": 260, "w": 140, "h": 48},
        "detail": {"clippedPx": 41, "viewportWidth": 375}}


class FakeDriver:
    """Page stand-in: the clipping signal disappears only once margin-left:0 has been applied."""
    def __init__(self, needs="margin-left: 0"): self.css, self.needs = [], needs
    def settle(self): pass
    def screenshot(self): return png()
    def probe(self): return [] if any(self.needs in c for c in self.css) else normalize_signals([CLIP])
    def apply_css(self, css): self.css.append(css); return len(self.css) - 1
    def remove_css(self, h): self.css[h] = ""


def test_heuristic_analyze_returns_schema_valid_findings():
    eng = VLMEngine(HeuristicClient())
    f = eng.analyze(state([CLIP]), [png()])
    assert f[0].type == "clipping" and f[0].signal_key and f[0].fix and "margin-left" in f[0].fix.to_css()
    assert f[0].fix.to_css().count("!important") >= 1 and "@media" in f[0].fix.to_css()


def test_geometry_only_hard_failures_are_kept_when_vlm_misses_them():
    class Blind(VLMClient):
        name = "blind"
        def complete(self, s, u, i): return json.dumps({"findings": []})
    f = VLMEngine(Blind()).analyze(state([CLIP]), [png()])
    assert len(f) == 1 and f[0].geometry_only


def test_loop_fixes_first_try(tmp_path):
    (tmp_path / "shots").mkdir()
    rep = SelfHealer(VLMEngine(HeuristicClient()), tmp_path).heal_state(FakeDriver(), state([CLIP]))
    h = rep.findings[0]
    assert h.status == "fixed" and len(h.attempts) == 1 and h.attempts[0].geometry_ok
    assert (tmp_path / h.before_crop).exists() and (tmp_path / h.after_crop).exists()


def test_loop_retries_with_refined_fix_then_succeeds(tmp_path):
    (tmp_path / "shots").mkdir()
    # attempt 1 (analysis fix) is wrong; the refine step must produce the working one
    class BadFirst(HeuristicClient):
        def complete(self, s, u, i):
            out = super().complete(s, u, i)
            if "TASK: ANALYZE" in u:
                d = json.loads(out); d["findings"][0]["fix"]["css"] = "color: red"; return json.dumps(d)
            return out
    rep = SelfHealer(VLMEngine(BadFirst()), tmp_path).heal_state(FakeDriver(needs="width: 100%"), state([CLIP]))
    h = rep.findings[0]
    assert h.status == "fixed" and len(h.attempts) == 2 and h.attempts[0].geometry_ok is False


def test_loop_gives_up_after_max_attempts(tmp_path):
    (tmp_path / "shots").mkdir()
    rep = SelfHealer(VLMEngine(HeuristicClient()), tmp_path, max_attempts=2).heal_state(FakeDriver(needs="NEVER"), state([CLIP]))
    assert rep.findings[0].status == "unresolved" and len(rep.findings[0].attempts) == 2


def test_fix_css_rendering():
    assert Fix(selector=".a", css="color:#000; margin:0", media="(max-width: 480px)").to_css() == "@media (max-width: 480px) { .a { color:#000 !important; margin:0 !important; } }"
