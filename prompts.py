SYSTEM = """You are OmniSight, an autonomous senior QA engineer who reviews web UI screenshots.
You are given a screenshot of one application state, the viewport it was rendered at, a trimmed DOM excerpt, and
AUTOMATED SIGNALS measured in the browser (clipping, overlap, hidden controls, contrast). Signals are evidence, not truth:
use the screenshot to confirm each one and to spot visual problems the signals missed (misalignment, text cut off, unreadable text).
Respond with ONE JSON object and nothing else (no prose, no markdown fences).

Fix rules:
- Minimal, scoped CSS. Prefer fixing the offending element; never hide or delete content to 'fix' a layout bug.
- Scope mobile-only bugs with a media query in `media` (e.g. "(max-width: 480px)"), so desktop is not affected.
- If the DOM shows Tailwind utility classes, also give `tailwind` {add:[...], remove:[...]} for that element.
- `selector` must be a valid CSS selector that appears in the signals or DOM excerpt.
"""

ANALYZE_SCHEMA = """{"findings":[{"type":"clipping|overlap|hidden_element|contrast|overflow|misalignment|other",
"severity":"low|medium|high|critical","description":"what a user sees and why it matters","selector":"css selector or null",
"other_selector":"css selector or null","confidence":0.0-1.0,
"fix":{"selector":"css selector","css":"declarations only, e.g. margin-left:0; max-width:100%","media":"(max-width: 480px) or null",
"tailwind":{"add":[],"remove":[]},"source_patch":null,"rationale":"one sentence"}}]}"""

VERIFY_SCHEMA = '{"fixed": true|false, "regression": true|false, "notes": "what is still wrong, or what changed"}'
REFINE_SCHEMA = '{"fix":{"selector":"...","css":"...","media":null,"tailwind":{"add":[],"remove":[]},"rationale":"..."}}'


def analyze_prompt(state, signals_json: str, dom_json: str) -> str:
    vp = state.viewport
    return f"""TASK: ANALYZE
App state: "{state.step}" at {state.url}
Viewport: {vp.name} {vp.width}x{vp.height}{' (mobile)' if vp.mobile else ''}
{('Flow was blocked: ' + state.meta['blocked_reason']) if state.meta.get('blocked_reason') else ''}
<signals>{signals_json}</signals>
<dom>{dom_json}</dom>
Return JSON: {ANALYZE_SCHEMA}
Only include real, user-visible problems. An empty findings list is a valid answer."""


def refine_prompt(finding_json: str, history: str, signals_now: str, dom_json: str) -> str:
    return f"""TASK: REFINE
Your previous CSS fix did not resolve this issue. Images: BEFORE (first) and AFTER the failed fix (second).
<finding>{finding_json}</finding>
<attempts>{history}</attempts>
<signals_after>{signals_now}</signals_after>
<dom>{dom_json}</dom>
Propose a different, still minimal fix. Return JSON: {REFINE_SCHEMA}"""


def verify_prompt(finding_json: str, signals_now: str) -> str:
    return f"""TASK: VERIFY
Images: BEFORE (first) and AFTER the proposed fix (second), cropped around the affected component.
<finding>{finding_json}</finding>
<signals_after>{signals_now}</signals_after>
Is the issue visually resolved without new problems (nothing clipped, overlapped, unreadable or moved unexpectedly)?
Return JSON: {VERIFY_SCHEMA}"""
