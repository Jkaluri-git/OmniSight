"""Headless Navigator: drives Playwright through a flow (or crawls) at several viewports,
captures screenshots + DOM + geometry signals at every state, and hands each live page to a callback
(the self-healing orchestrator) so fixes can be applied and verified on the same page."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from ..config import Viewport
from .probes import DOM_EXCERPT_JS, PROBE_JS

FREEZE_CSS = "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}"
MAX_SHOT_HEIGHT = 8000
STEP_TIMEOUT_MS = 5000


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48] or "state"


def group_key(sig: dict) -> str:
    """Stable identity for a signal; instances of the same component collapse into one group."""
    gen = lambda s: re.sub(r":nth-(?:of-type|child)\(\d+\)", "", s or "")
    return f"{sig['type']}|{gen(sig.get('selector'))}|{gen(sig.get('other_selector'))}"


def normalize_signals(raw: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for s in raw:
        k = group_key(s)
        if k in groups:
            groups[k]["count"] += 1
        else:
            groups[k] = {**s, "key": k, "count": 1}
    return list(groups.values())


@dataclass
class CapturedState:
    step: str
    viewport: Viewport
    url: str
    screenshot: bytes
    signals: list[dict]
    dom: dict
    path: str = ""  # saved screenshot, relative to run dir
    meta: dict = field(default_factory=dict)


class PlaywrightDriver:
    """The small surface the orchestrator needs; tests can substitute a fake."""

    def __init__(self, page):
        self.page = page

    def settle(self):
        try:
            self.page.add_style_tag(content=FREEZE_CSS)
        except Exception:
            pass
        self.page.wait_for_timeout(120)

    def probe(self) -> list[dict]:
        return normalize_signals(self.page.evaluate(PROBE_JS)["signals"])

    def probe_full(self) -> dict:
        return self.page.evaluate(PROBE_JS)

    def screenshot(self) -> bytes:
        h = self.page.evaluate("document.documentElement.scrollHeight")
        vw = self.page.evaluate("document.documentElement.clientWidth")
        if h > MAX_SHOT_HEIGHT:
            return self.page.screenshot(full_page=True, clip={"x": 0, "y": 0, "width": vw, "height": MAX_SHOT_HEIGHT})
        return self.page.screenshot(full_page=True)

    def apply_css(self, css: str):
        return self.page.add_style_tag(content=css)

    def remove_css(self, handle):
        try:
            handle.evaluate("e => e.remove()")
        except Exception:
            pass

    def dom_excerpt(self, selectors: list[str], limit: int = 9000, each: int = 1800) -> dict:
        try:
            return self.page.evaluate(DOM_EXCERPT_JS, {"selectors": selectors, "limit": limit, "each": each})
        except Exception:
            return {"focus": [], "page": ""}


class Navigator:
    def __init__(self, base_url: str, viewports: list[Viewport], run_dir: Path, headless: bool = True):
        p = urlparse(base_url)
        self.origin = f"{p.scheme}://{p.netloc}"
        self.base = f"{self.origin}{p.path.rstrip('/')}"
        self.query = p.query
        self.viewports = viewports
        self.run_dir = Path(run_dir)
        self.shots = self.run_dir / "shots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.headless = headless

    def _url(self, target: str) -> str:
        if target.startswith("http"):
            return target
        url = self.base + (target if target.startswith("/") else "/" + target)
        if self.query and "?" not in url:
            url += "?" + self.query
        return url

    def _capture(self, driver: PlaywrightDriver, step: str, vp: Viewport, tag: str = "before") -> CapturedState:
        driver.settle()
        full = driver.probe_full()
        signals = normalize_signals(full["signals"])
        shot = driver.screenshot()
        name = f"{_slug(vp.name)}__{_slug(step)}__{tag}.png"
        (self.shots / name).write_bytes(shot)
        sels = [s["selector"] for s in signals if s.get("selector")] + [s["other_selector"] for s in signals if s.get("other_selector")]
        return CapturedState(step=step, viewport=vp, url=driver.page.url, screenshot=shot, signals=signals,
                             dom=driver.dom_excerpt(sels), path=f"shots/{name}", meta=full["meta"])

    def _do(self, page, step: dict):
        act, target, value = step["action"], step.get("target", ""), step.get("value", "")
        if act == "goto":
            page.goto(self._url(target), wait_until="load")
        elif act == "click":
            page.locator(target).first.click(timeout=STEP_TIMEOUT_MS)
            page.wait_for_load_state("load")
        elif act == "fill":
            page.locator(target).first.fill(value, timeout=STEP_TIMEOUT_MS)
        elif act == "press":
            page.locator(target).first.press(value, timeout=STEP_TIMEOUT_MS)
        elif act == "select":
            page.locator(target).first.select_option(value, timeout=STEP_TIMEOUT_MS)
        elif act == "wait":
            page.locator(target).first.wait_for(timeout=STEP_TIMEOUT_MS)
        elif act == "scroll":
            page.mouse.wheel(0, int(value or 600))
        else:
            raise ValueError(f"unknown action {act!r}")

    def _context(self, browser, vp: Viewport):
        kw = dict(viewport={"width": vp.width, "height": vp.height}, device_scale_factor=1)
        if vp.mobile:
            kw.update(is_mobile=True, has_touch=True,
                      user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
        return browser.new_context(**kw)

    def _launch(self, pw):
        import os
        exe = os.getenv("OMNISIGHT_CHROMIUM_PATH") or None
        return pw.chromium.launch(headless=self.headless, executable_path=exe, args=["--no-sandbox", "--disable-gpu"])

    def run_flow(self, flow: dict, on_state: Callable[[PlaywrightDriver, CapturedState], object]) -> list:
        from playwright.sync_api import sync_playwright

        results = []
        with sync_playwright() as pw:
            browser = self._launch(pw)
            for vp in self.viewports:
                ctx = self._context(browser, vp)
                page = ctx.new_page()
                driver = PlaywrightDriver(page)
                for step in flow["steps"]:
                    name = step.get("name") or f'{step["action"]} {step.get("target", "")}'
                    try:
                        self._do(page, step)
                    except Exception as e:  # a blocked flow is itself a finding (e.g. hidden submit button)
                        if step["action"] == "goto":  # page unreachable: not a UI bug, fail the job loudly
                            raise RuntimeError(f"staging unreachable at {self._url(step['target'])}: {str(e).splitlines()[0]}") from e
                        blocked = self._capture(driver, f"{name} (blocked)", vp)
                        blocked.meta["blocked_reason"] = str(e).splitlines()[0][:200]
                        results.append(on_state(driver, blocked))
                        try:  # retry once with any accepted fix still injected
                            self._do(page, step)
                        except Exception:
                            break
                    if step.get("capture"):
                        results.append(on_state(driver, self._capture(driver, name, vp)))
                ctx.close()
            browser.close()
        return results

    def crawl(self, on_state: Callable[[PlaywrightDriver, CapturedState], object], max_pages: int = 8) -> list:
        """Selector-free exploration: BFS over same-origin links from the base URL."""
        from playwright.sync_api import sync_playwright

        results = []
        with sync_playwright() as pw:
            browser = self._launch(pw)
            for vp in self.viewports:
                ctx = self._context(browser, vp)
                page = ctx.new_page()
                driver = PlaywrightDriver(page)
                queue, seen = [self._url("/")], set()
                while queue and len(seen) < max_pages:
                    url = queue.pop(0)
                    key = url.split("#")[0]
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        page.goto(url, wait_until="load")
                    except Exception:
                        continue
                    results.append(on_state(driver, self._capture(driver, urlparse(url).path or "/", vp)))
                    for href in page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"):
                        if href.startswith(self.origin) and href.split("#")[0] not in seen:
                            queue.append(href)
                ctx.close()
            browser.close()
        return results


def load_flow(path) -> dict:
    return json.loads(Path(path).read_text())
