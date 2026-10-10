# OmniSight — Multimodal UI Self-Healing & RPA Agent

OmniSight sits in your CI/CD pipeline. When a staging build is live it drives the app in a headless browser at
phone/tablet/desktop sizes, screenshots every state, asks a Vision-Language Model to act as a QA engineer, **generates the CSS /
Tailwind fix, proves it worked by re-rendering the page**, and opens a GitHub Pull Request with before/after evidence.
A review dashboard lets QA approve or reject each PR.

```
CI webhook ─► FastAPI gateway ─► Navigator (Playwright) ─► VLM engine (GPT-4o / Claude / LLaVA)
                                     │  screenshots + DOM + geometry signals        │ findings + fixes (JSON)
                                     ▼                                              ▼
                              Self-healing loop:  apply CSS → re-render → geometry check + VLM verify → retry/refine
                                     ▼
                         PyGithub: branch + CSS overrides + proof images + PR  ─►  React dashboard: Approve / Reject
```

## Quick start (no API keys needed)

```bash
pip install -r requirements.txt && playwright install chromium
python -m omnisight demo          # starts the gateway, scans the bundled buggy shop, prints the dashboard URL
python -m pytest                  # 18 tests incl. a real-browser end-to-end run
```
With no keys, the **heuristic** backend is used: it cannot "see", it acts on the browser's measured signals. It exists so the whole pipeline
(loop, PR, dashboard) is runnable and testable offline. For real visual reasoning set a VLM in `.env`:

| Backend | Set | Notes |
|---|---|---|
| GPT-4o | `OPENAI_API_KEY` | best out-of-the-box spatial reasoning |
| Claude | `ANTHROPIC_API_KEY`, `OMNISIGHT_ANTHROPIC_MODEL` | |
| LLaVA (local, free) | run Ollama, `ollama pull llava` | weakest spatial reasoning; relies more on the signals |

`OMNISIGHT_VLM=auto` picks the first available. Copy `.env.example` → `.env`.

## How each module maps to the brief

| Module | Where | What it does |
|---|---|---|
| Headless Navigator | `omnisight/navigator/` | Playwright flows (`flows/*.json`, role/text/CSS locators) **or** selector-free `crawl`; multi-viewport; touch/mobile emulation; full-page screenshots; trimmed DOM. In-page probes measure clipping, hidden/obscured controls, text overlap, WCAG contrast, horizontal overflow. |
| VLM engine | `omnisight/vlm/` | Screenshot + DOM excerpt + signals → strict JSON (Pydantic). `extract.py` isolates JSON/code blocks from prose and falls back to a ```css block if the model ignores the schema. |
| Agentic orchestrator | `omnisight/orchestrator/loop.py` | Plan → Execute → Evaluate. Injects the fix into the live DOM, re-screenshots, checks geometry **and** asks the VLM; on failure rolls back and feeds the failed attempt + new measurements into a refine call (default 3 attempts). Also reports layout shift outside the target region. |
| Token/latency optimisation | `orchestrator/imaging.py` | Full screenshot is downscaled for analysis; verification/refinement send only a **crop around the defect** (bbox from the browser), not the whole page. |
| CI/CD gateway | `omnisight/gateway/app.py` | `POST /webhook/github` (HMAC-verified `deployment_status`), `POST /webhook/ci` (Jenkins/curl), background jobs, SQLite store, job API, artifact hosting. |
| GitHub bot | `omnisight/github_bot/pr.py` | PyGithub: branch → exact-match source patches (only if unambiguous) → `src/omnisight-fixes.css` overrides → proof images → PR + label. Approve = label (+ optional squash-merge); Reject = comment + close. No token ⇒ dry-run preview in `runs/<job>/pr_preview/`. |
| Dashboard | `dashboard/index.html` | Review queue, before/after proof slider with the defect outlined, CSS + Tailwind diff, per-attempt verification log, Approve/Reject. Served at `/dashboard`. |

## Wiring into CI
* GitHub: add a repo webhook (event *Deployments*) → `https://<host>/webhook/github`, set `GITHUB_WEBHOOK_SECRET`; or use `examples/github-actions.yml`.
* Jenkins: `examples/Jenkinsfile`. Protect with `OMNISIGHT_API_KEY`.
* Needs a `GITHUB_TOKEN` with `contents:write` + `pull_requests:write` on the target repo.
* Docker: `docker compose up` (Playwright base image includes browsers).

## Writing a flow
`flows/mock_shop.json` shows the format: `goto | click | fill | press | select | wait | scroll`, `capture: true` snapshots a state.
If a step can't be performed because the UI is broken (e.g. the *Place order* button is invisible on mobile), that blocked state is itself captured, healed and the step retried.

## Honest limitations
* Verified in this repo: browser capture, probes, healing loop, PR preview, gateway, tests — all run against the bundled buggy shop with the heuristic backend.
  **Not exercised here** (needs your credentials/network): live OpenAI/Anthropic/Ollama calls, real GitHub PR creation, and visual rendering of the dashboard (it loads React/Babel/fonts from CDNs).
* CSS overrides are the safe default patch. Mapping a fix back into JSX/Tailwind class strings relies on the model returning an unambiguous `source_patch`; otherwise it falls back to the override file.
* Fixes are verified at the viewport where the bug was found; scan all viewports (default four) to catch regressions elsewhere. Layout shift is reported, not enforced.
* Auto-generated PRs are meant to be reviewed; do not enable auto-merge on production repos without that review.
