# Portfolio Atlas — Full App Redesign Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Streamlit UI with a FastAPI backend + vanilla JS SPA that matches the "Portfolio Atlas" visual language established in `portfolio_prototype.html`.

**Architecture:** FastAPI serves a REST API consumed by a single-page HTML/CSS/JS frontend. All AI and data logic stays in `src/` unchanged — we only replace the presentation layer (`app.py`). The frontend uses ESM modules, Chart.js from CDN for the performance chart, and the custom squarify treemap from the prototype. Hash-based routing (`#portfolio`, `#performance`, etc.) keeps it stateless and simple.

**Tech Stack:** FastAPI, uvicorn, vanilla JS (ESM), Chart.js (CDN), IBM Plex Mono + Playfair Display (Google Fonts CDN)

---

## Why Drop Streamlit

Streamlit re-renders the whole Python script on every interaction, forces its own component model, and fights custom CSS at every turn. The prototype proves we can build a far better UI with zero framework overhead. Local dev is equally simple: `uv run uvicorn api:app --reload` instead of `streamlit run app.py`.

---

## File Map

```
api.py                          REPLACE app.py — FastAPI app, REST endpoints, static file serving
static/
  index.html                    NEW — app shell: fonts, nav, view containers, script imports
  css/
    vars.css                    NEW — design tokens (all --variables from prototype)
    base.css                    NEW — reset, body, typography
    layout.css                  NEW — header, nav, main area, drawer scaffold
    components.css              NEW — cards, pills, badges, signal strip, member chips
  js/
    api.js                      NEW — fetch wrappers for all backend endpoints
    treemap.js                  NEW — squarify algorithm (extracted from prototype)
    drawer.js                   NEW — generic slide-in drawer component
    app.js                      NEW — router, nav activation, global run/advisor controls
    views/
      portfolio.js              NEW — treemap + signal strip + drawer data binding
      performance.js            NEW — metrics cards + Chart.js line chart
      members.js                NEW — 3-column editorial AI picks layout
      history.js                NEW — run table + diff comparison
      research.js               NEW — advisor log table + expanded entries
      settings.js               NEW — excluded tickers/sectors forms
pyproject.toml                  MODIFY — remove streamlit, add fastapi + uvicorn
```

Files **not touched:** everything in `src/`, `tests/`, `data/`, `.env`, `.streamlit/`.

---

## Task 1: Backend — FastAPI app + dependencies

**Files:**
- Create: `api.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Swap dependencies**

In `pyproject.toml`, replace `"streamlit>=1.57.0"` with:
```toml
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "python-multipart>=0.0.12",
```

Run:
```bash
uv sync
```
Expected: resolves without error.

- [ ] **Step 2: Create `api.py` with all endpoints**

```python
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from openai import AsyncOpenAI
import anthropic

from src import advisor_log
from src import config as exclusions
from src.advisor import ask_committee
from src.models import AdvisorResponse
from src.performance import portfolio_vs_benchmarks
from src.runner import load_all_runs, load_latest_run, run_committee

load_dotenv()
exclusions.load()

app = FastAPI()

# Serve static files under /static
app.mount("/static", StaticFiles(directory="static"), name="static")


def _clients():
    return (
        anthropic.AsyncAnthropic(),
        AsyncOpenAI(),
        genai.Client(api_key=os.environ["GOOGLE_API_KEY"]),
    )


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/api/runs")
async def get_runs():
    runs = load_all_runs()
    return [r.model_dump(mode="json") for r in runs]


@app.get("/api/runs/latest")
async def get_latest_run():
    run = load_latest_run()
    if not run:
        raise HTTPException(status_code=404, detail="No runs yet")
    return run.model_dump(mode="json")


@app.post("/api/runs")
async def trigger_run():
    ac, oc, gc = _clients()
    run = await run_committee(ac, oc, gc)
    return run.model_dump(mode="json")


@app.get("/api/performance")
async def get_performance(tickers: str, weights: str):
    ticker_list = tickers.split(",")
    weight_list = [float(w) for w in weights.split(",")]
    try:
        data = portfolio_vs_benchmarks(ticker_list, weight_list)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return data


@app.post("/api/advisor")
async def get_advisor_opinion(payload: dict):
    ticker = payload.get("ticker", "").upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker required")
    latest = load_latest_run()
    portfolio = latest.portfolio if latest else []
    ac, oc, gc = _clients()
    try:
        advice = await ask_committee(ticker, ac, oc, gc, portfolio)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    advisor_log.append(advice)
    return advice.model_dump(mode="json")


@app.get("/api/advisor/log")
async def get_advisor_log():
    return advisor_log.load()


@app.get("/api/settings")
async def get_settings():
    return {
        "excluded_tickers": sorted(exclusions.EXCLUDED_TICKERS),
        "excluded_sectors": sorted(exclusions.EXCLUDED_SECTORS),
    }


@app.put("/api/settings")
async def update_settings(payload: dict):
    if "excluded_tickers" in payload:
        exclusions.EXCLUDED_TICKERS = set(payload["excluded_tickers"])
    if "excluded_sectors" in payload:
        exclusions.EXCLUDED_SECTORS = set(payload["excluded_sectors"])
    exclusions.save()
    return {"ok": True}
```

- [ ] **Step 3: Smoke-test the API**

```bash
uv run uvicorn api:app --reload --port 8000
```

In another terminal:
```bash
curl http://localhost:8000/api/runs/latest
```
Expected: either `{"detail":"No runs yet"}` (404) or a JSON run object. Either is correct — just confirm the server starts and responds.

- [ ] **Step 4: Commit**

```bash
git add api.py pyproject.toml uv.lock
git commit -m "feat: replace streamlit with fastapi backend"
```

---

## Task 2: Frontend scaffold — shell, CSS design system

**Files:**
- Create: `static/index.html`
- Create: `static/css/vars.css`
- Create: `static/css/base.css`
- Create: `static/css/layout.css`
- Create: `static/css/components.css`

- [ ] **Step 1: Create `static/css/vars.css`**

```css
:root {
  /* Surfaces */
  --bg:             #0c0a07;
  --surface:        #16130e;
  --surface-2:      #1e1a13;
  --surface-3:      #252018;
  --border:         #2a2218;
  --border-bright:  #3d3020;

  /* Accent: amber (core) */
  --amber:          #d4a027;
  --amber-dim:      rgba(212,160,39,0.12);
  --amber-glow:     rgba(212,160,39,0.30);
  --amber-tile-bg:  #1e1a12;
  --amber-tile-bg2: #272114;

  /* Accent: purple (moonshot) */
  --purple:         #9d7ff5;
  --purple-dim:     rgba(157,127,245,0.12);
  --purple-glow:    rgba(157,127,245,0.28);
  --purple-tile-bg: #14101e;
  --purple-tile-bg2:#1e1530;

  /* Semantic */
  --green:          #22c55e;
  --green-dim:      rgba(34,197,94,0.12);
  --green-glow:     rgba(34,197,94,0.35);
  --red:            #ef4444;
  --red-dim:        rgba(239,68,68,0.12);

  /* Text scale */
  --text:           #f0e6cc;
  --text-2:         #c4b49a;
  --text-3:         #9a8e78;
  --text-4:         #7a6e5c;

  /* Typography */
  --font-serif: 'Playfair Display', Georgia, serif;
  --font-mono:  'IBM Plex Mono', 'Courier New', monospace;
  --font-sans:  'IBM Plex Sans', system-ui, sans-serif;
}
```

- [ ] **Step 2: Create `static/css/base.css`**

```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html { font-size: 16px; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  background: radial-gradient(ellipse 120% 80% at 50% 0%, rgba(212,160,39,0.03) 0%, transparent 65%);
  pointer-events: none;
  z-index: 0;
}

a { color: inherit; text-decoration: none; }
button { cursor: pointer; font-family: inherit; border: none; background: none; }
```

- [ ] **Step 3: Create `static/css/layout.css`**

```css
/* ── App shell ── */
#app { position: relative; z-index: 1; min-height: 100vh; }

/* ── Top nav ── */
.nav {
  display: flex;
  align-items: stretch;
  padding: 0 48px;
  border-bottom: 1px solid var(--border);
  height: 52px;
  gap: 0;
  position: sticky;
  top: 0;
  background: var(--bg);
  z-index: 50;
}

.nav-brand {
  font-family: var(--font-serif);
  font-size: 1.05rem;
  font-weight: 700;
  color: var(--text);
  display: flex;
  align-items: center;
  padding-right: 32px;
  border-right: 1px solid var(--border);
  margin-right: 12px;
  white-space: nowrap;
}

.nav-brand em { font-style: italic; color: var(--amber); }

.nav-links {
  display: flex;
  align-items: stretch;
  gap: 0;
  flex: 1;
}

.nav-link {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  letter-spacing: 0.05em;
  color: var(--text-4);
  padding: 0 16px;
  display: flex;
  align-items: center;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: color 0.15s, border-color 0.15s;
  white-space: nowrap;
}

.nav-link:hover { color: var(--text-3); }
.nav-link.active { color: var(--text); border-bottom-color: var(--amber); }

.nav-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-left: 16px;
}

.nav-run-btn {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--bg);
  background: var(--amber);
  border-radius: 5px;
  padding: 6px 14px;
  transition: opacity 0.15s;
}
.nav-run-btn:hover { opacity: 0.9; }
.nav-run-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.nav-advisor-input {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--text);
  padding: 5px 10px;
  width: 110px;
  transition: border-color 0.15s, width 0.2s;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.nav-advisor-input::placeholder { color: var(--text-4); text-transform: none; }
.nav-advisor-input:focus { outline: none; border-color: var(--amber); width: 140px; }

.nav-ask-btn {
  font-family: var(--font-mono);
  font-size: 0.68rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--amber);
  background: var(--amber-dim);
  border: 1px solid var(--amber-glow);
  border-radius: 5px;
  padding: 6px 12px;
  transition: background 0.15s;
}
.nav-ask-btn:hover { background: rgba(212,160,39,0.2); }

/* ── Run age badge in nav ── */
.nav-age {
  font-family: var(--font-mono);
  font-size: 0.62rem;
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid;
}
.nav-age.fresh   { color: var(--green);  background: var(--green-dim);  border-color: var(--green-glow); }
.nav-age.recent  { color: var(--amber);  background: var(--amber-dim);  border-color: var(--amber-glow); }
.nav-age.stale   { color: var(--red);    background: var(--red-dim);    border-color: rgba(239,68,68,0.3); }

/* ── View containers ── */
.view {
  display: none;
  padding: 36px 48px 60px;
  animation: fadeIn 0.2s ease;
}
.view.active { display: block; }

@keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

/* ── Section header ── */
.section-header {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 20px;
}
.section-label {
  font-family: var(--font-mono);
  font-size: 0.62rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--text-4);
  white-space: nowrap;
}
.section-rule { flex: 1; height: 1px; background: var(--border); }

/* ── Loading overlay ── */
.loading-overlay {
  position: fixed;
  inset: 0;
  background: rgba(12,10,7,0.85);
  z-index: 300;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s;
  backdrop-filter: blur(4px);
}
.loading-overlay.active { opacity: 1; pointer-events: auto; }

.loading-spinner {
  width: 32px;
  height: 32px;
  border: 2px solid var(--border-bright);
  border-top-color: var(--amber);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.loading-text {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-3);
  letter-spacing: 0.06em;
}

/* ── Toast ── */
.toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  padding: 10px 16px;
  border-radius: 6px;
  z-index: 400;
  transform: translateY(20px);
  opacity: 0;
  transition: transform 0.25s, opacity 0.25s;
  pointer-events: none;
}
.toast.show { transform: none; opacity: 1; }
.toast.success { background: var(--green-dim); border: 1px solid var(--green-glow); color: var(--green); }
.toast.error   { background: var(--red-dim);   border: 1px solid rgba(239,68,68,0.3); color: var(--red); }
```

- [ ] **Step 4: Create `static/css/components.css`**

```css
/* ── Drawer ── */
.backdrop {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.55);
  z-index: 200; opacity: 0; pointer-events: none;
  transition: opacity 0.25s;
  backdrop-filter: blur(2px);
}
.backdrop.open { opacity: 1; pointer-events: auto; }

.drawer {
  position: fixed; top: 0; right: 0; bottom: 0;
  width: 420px;
  background: var(--surface);
  border-left: 1px solid var(--border-bright);
  z-index: 201;
  transform: translateX(100%);
  transition: transform 0.3s cubic-bezier(0.4,0,0.2,1);
  display: flex; flex-direction: column; overflow: hidden;
}
.drawer.open { transform: translateX(0); }

.drawer-top-bar { height: 4px; flex-shrink: 0; }
.drawer-top-bar.core     { background: linear-gradient(90deg, var(--amber), transparent); }
.drawer-top-bar.moonshot { background: linear-gradient(90deg, var(--purple), transparent); }

.drawer-header {
  padding: 24px 28px 20px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  position: relative;
}
.drawer-close {
  position: absolute; top: 20px; right: 20px;
  width: 30px; height: 30px;
  background: var(--surface-2); border: 1px solid var(--border);
  border-radius: 6px; color: var(--text-3);
  font-family: var(--font-mono); font-size: 1rem;
  display: flex; align-items: center; justify-content: center;
  transition: color 0.15s, border-color 0.15s; padding-bottom: 1px;
}
.drawer-close:hover { color: var(--text); border-color: var(--border-bright); }

.drawer-ticker {
  font-family: var(--font-mono); font-size: 2.1rem; font-weight: 600;
  color: var(--text); letter-spacing: 0.04em; line-height: 1; margin-bottom: 4px;
}
.drawer-company { font-size: 0.82rem; color: var(--text-3); margin-bottom: 16px; }

.drawer-pills { display: flex; gap: 8px; flex-wrap: wrap; }

.drawer-body {
  flex: 1; overflow-y: auto; padding: 24px 28px 32px;
  scrollbar-width: thin; scrollbar-color: var(--border-bright) transparent;
}
.drawer-body::-webkit-scrollbar { width: 3px; }
.drawer-body::-webkit-scrollbar-thumb { background: var(--border-bright); border-radius: 1.5px; }

.drawer-section { margin-bottom: 24px; }
.drawer-section-label {
  font-family: var(--font-mono); font-size: 0.6rem;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--text-4); margin-bottom: 10px;
}

/* ── Pills ── */
.pill {
  font-family: var(--font-mono); font-size: 0.66rem; font-weight: 600;
  letter-spacing: 0.07em; text-transform: uppercase; padding: 4px 10px; border-radius: 4px;
}
.pill.core     { color: var(--amber);  background: var(--amber-dim);  border: 1px solid var(--amber-glow); }
.pill.moonshot { color: var(--purple); background: var(--purple-dim); border: 1px solid var(--purple-glow); }
.pill.weight   { color: var(--text-2); background: var(--surface-3);  border: 1px solid var(--border-bright); }
.pill.consensus{ color: var(--green);  background: var(--green-dim);  border: 1px solid var(--green-glow); }

/* ── Member dots ── */
.dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; display: inline-block; }
.dot.claude  { background: #f59e0b; }
.dot.gpt     { background: #10b981; }
.dot.gemini  { background: #3b82f6; }

/* ── Member chip (drawer + members tab) ── */
.member-chip {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 12px;
  background: var(--surface-2); border: 1px solid var(--border); border-radius: 20px;
  font-size: 0.75rem; color: var(--text-2);
}

/* ── Upside cards (drawer) ── */
.upside-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.upside-card {
  background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px; padding: 12px 14px;
}
.upside-card-label { font-size: 0.67rem; color: var(--text-3); margin-bottom: 5px; }
.upside-card-val   { font-family: var(--font-mono); font-size: 1.45rem; font-weight: 600; line-height: 1; }
.upside-card-val.pos { color: var(--green); }
.upside-card-val.neg { color: var(--red); }

/* ── Rationale text ── */
.rationale { font-size: 0.84rem; line-height: 1.8; color: var(--text-2); }

/* ── Generic table ── */
.atlas-table {
  width: 100%; border-collapse: collapse;
  font-size: 0.82rem;
}
.atlas-table th {
  font-family: var(--font-mono); font-size: 0.62rem; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--text-4); padding: 8px 12px;
  border-bottom: 1px solid var(--border); text-align: left; font-weight: 500;
}
.atlas-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); color: var(--text-2); }
.atlas-table tr:last-child td { border-bottom: none; }
.atlas-table tr:hover td { background: var(--surface-2); }
.atlas-table td.mono { font-family: var(--font-mono); }

/* ── Rec badge (research log) ── */
.rec-badge {
  font-family: var(--font-mono); font-size: 0.68rem; font-weight: 600;
  letter-spacing: 0.08em; padding: 3px 8px; border-radius: 4px; text-transform: uppercase;
}
.rec-badge.buy       { color: var(--green);  background: var(--green-dim);  border: 1px solid var(--green-glow); }
.rec-badge.watch     { color: var(--amber);  background: var(--amber-dim);  border: 1px solid var(--amber-glow); }
.rec-badge.pass      { color: var(--red);    background: var(--red-dim);    border: 1px solid rgba(239,68,68,0.3); }
.rec-badge.portfolio { color: var(--purple); background: var(--purple-dim); border: 1px solid var(--purple-glow); }

/* ── Metric card ── */
.metric-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 20px 24px;
}
.metric-label { font-family: var(--font-mono); font-size: 0.65rem; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-4); margin-bottom: 8px; }
.metric-val   { font-family: var(--font-mono); font-size: 2rem; font-weight: 600; line-height: 1; color: var(--text); }
.metric-val.pos   { color: var(--green); }
.metric-val.neg   { color: var(--red); }
.metric-delta { font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-4); margin-top: 4px; }
.metric-delta.pos { color: rgba(34,197,94,0.7); }
.metric-delta.neg { color: rgba(239,68,68,0.7); }

/* ── Empty state ── */
.empty-state {
  text-align: center; padding: 64px 0; color: var(--text-4);
  font-family: var(--font-mono); font-size: 0.75rem; letter-spacing: 0.06em;
}

/* ── Settings form ── */
.settings-input {
  font-family: var(--font-mono); font-size: 0.8rem;
  background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text); padding: 8px 12px; width: 100%;
  transition: border-color 0.15s;
}
.settings-input:focus { outline: none; border-color: var(--amber); }
.settings-btn {
  font-family: var(--font-mono); font-size: 0.7rem; font-weight: 600; letter-spacing: 0.06em;
  padding: 8px 16px; border-radius: 6px; border: 1px solid; transition: background 0.15s;
}
.settings-btn.primary { color: var(--bg); background: var(--amber); border-color: var(--amber); }
.settings-btn.danger  { color: var(--red); background: var(--red-dim); border-color: rgba(239,68,68,0.3); }
.settings-btn.primary:hover { opacity: 0.9; }
.settings-btn.danger:hover  { background: rgba(239,68,68,0.2); }

.settings-tag {
  display: inline-flex; align-items: center; gap: 6px;
  font-family: var(--font-mono); font-size: 0.72rem;
  padding: 4px 10px; border-radius: 4px;
  background: var(--surface-2); border: 1px solid var(--border); color: var(--text-2);
}
.settings-tag-remove {
  color: var(--text-4); font-size: 0.85rem; line-height: 1;
  transition: color 0.15s; padding: 0 2px;
}
.settings-tag-remove:hover { color: var(--red); }
```

- [ ] **Step 5: Create `static/index.html`** (the app shell)

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Portfolio Atlas</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;0,700;1,400&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/css/vars.css">
<link rel="stylesheet" href="/static/css/base.css">
<link rel="stylesheet" href="/static/css/layout.css">
<link rel="stylesheet" href="/static/css/components.css">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
<div id="app">

  <nav class="nav">
    <span class="nav-brand">Portfolio <em>Atlas</em></span>
    <div class="nav-links">
      <a class="nav-link" href="#portfolio"  data-view="portfolio">Portfolio</a>
      <a class="nav-link" href="#performance" data-view="performance">Performance</a>
      <a class="nav-link" href="#members"    data-view="members">Members</a>
      <a class="nav-link" href="#history"    data-view="history">History</a>
      <a class="nav-link" href="#research"   data-view="research">Research</a>
      <a class="nav-link" href="#settings"   data-view="settings">Settings</a>
    </div>
    <div class="nav-actions">
      <span class="nav-age" id="nav-age"></span>
      <input class="nav-advisor-input" id="nav-ticker" placeholder="Ask: TSLA" maxlength="8">
      <button class="nav-ask-btn" id="nav-ask-btn">Ask</button>
      <button class="nav-run-btn" id="nav-run-btn">Run Committee</button>
    </div>
  </nav>

  <div id="view-portfolio"   class="view active"></div>
  <div id="view-performance" class="view"></div>
  <div id="view-members"     class="view"></div>
  <div id="view-history"     class="view"></div>
  <div id="view-research"    class="view"></div>
  <div id="view-settings"    class="view"></div>

</div>

<!-- Shared drawer -->
<div class="backdrop" id="backdrop"></div>
<div class="drawer" id="drawer">
  <div class="drawer-top-bar" id="d-bar"></div>
  <div class="drawer-header">
    <button class="drawer-close" id="d-close">×</button>
    <div class="drawer-ticker" id="d-ticker"></div>
    <div class="drawer-company" id="d-company"></div>
    <div class="drawer-pills" id="d-pills"></div>
  </div>
  <div class="drawer-body">
    <div class="drawer-section">
      <div class="drawer-section-label">Upside to Target</div>
      <div class="upside-row" id="d-upside"></div>
    </div>
    <div class="drawer-section">
      <div class="drawer-section-label">Committee Members</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap" id="d-members"></div>
    </div>
    <div class="drawer-section">
      <div class="drawer-section-label">Investment Thesis</div>
      <div class="rationale" id="d-rationale"></div>
    </div>
  </div>
</div>

<!-- Loading overlay -->
<div class="loading-overlay" id="loading">
  <div class="loading-spinner"></div>
  <div class="loading-text" id="loading-text">Working…</div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<script type="module" src="/static/js/app.js"></script>
</body>
</html>
```

- [ ] **Step 6: Verify the shell loads**

```bash
uv run uvicorn api:app --reload --port 8000
```
Open `http://localhost:8000`. Expected: blank page with nav bar, no console errors.

- [ ] **Step 7: Commit**

```bash
git add static/index.html static/css/
git commit -m "feat: add frontend shell and CSS design system"
```

---

## Task 3: JS infrastructure — API client, router, drawer, treemap

**Files:**
- Create: `static/js/api.js`
- Create: `static/js/drawer.js`
- Create: `static/js/treemap.js`
- Create: `static/js/app.js`

- [ ] **Step 1: Create `static/js/api.js`**

```js
const BASE = '';

async function request(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

export const api = {
  getLatestRun:     ()       => request('GET',  '/api/runs/latest'),
  getAllRuns:        ()       => request('GET',  '/api/runs'),
  triggerRun:       ()       => request('POST', '/api/runs'),
  getPerformance:   (t, w)   => request('GET',  `/api/performance?tickers=${t}&weights=${w}`),
  getAdvisorLog:    ()       => request('GET',  '/api/advisor/log'),
  askAdvisor:       (ticker) => request('POST', '/api/advisor', { ticker }),
  getSettings:      ()       => request('GET',  '/api/settings'),
  updateSettings:   (data)   => request('PUT',  '/api/settings', data),
};
```

- [ ] **Step 2: Create `static/js/drawer.js`**

```js
const MEMBER_NAMES = { claude: 'Claude', gpt: 'GPT-4o', gemini: 'Gemini' };

export function openDrawer(holding) {
  const d = holding;

  document.getElementById('d-bar').className = `drawer-top-bar ${d.conviction}`;
  document.getElementById('d-ticker').textContent  = d.ticker;
  document.getElementById('d-company').textContent = d.company_name;

  const conLabel = d.conviction === 'moonshot' ? '🌙 Moonshot' : 'Core';
  const consPill = d.nominated_by?.length === 3
    ? `<span class="pill consensus">3-way consensus</span>` : '';
  document.getElementById('d-pills').innerHTML = `
    <span class="pill ${d.conviction}">${conLabel}</span>
    <span class="pill weight">${d.weight}% weight</span>
    ${consPill}`;

  const uCls  = (d.mean_upside_pct ?? 0) >= 0 ? 'pos' : 'neg';
  const uSign = (d.mean_upside_pct ?? 0) >= 0 ? '+' : '';
  const mSign = (d.median_upside_pct ?? 0) >= 0 ? '+' : '';
  const mCls  = (d.median_upside_pct ?? 0) >= 0 ? 'pos' : 'neg';
  document.getElementById('d-upside').innerHTML = `
    <div class="upside-card">
      <div class="upside-card-label">Mean Target</div>
      <div class="upside-card-val ${uCls}">${d.mean_upside_pct != null ? uSign + d.mean_upside_pct.toFixed(1) + '%' : '—'}</div>
    </div>
    <div class="upside-card">
      <div class="upside-card-label">Median Target</div>
      <div class="upside-card-val ${mCls}">${d.median_upside_pct != null ? mSign + d.median_upside_pct.toFixed(1) + '%' : '—'}</div>
    </div>`;

  document.getElementById('d-members').innerHTML = (d.nominated_by || []).map(m => `
    <div class="member-chip"><span class="dot ${m.toLowerCase()}"></span>${MEMBER_NAMES[m.toLowerCase()] || m}</div>`).join('');

  document.getElementById('d-rationale').textContent = d.rationale;

  document.getElementById('backdrop').classList.add('open');
  document.getElementById('drawer').classList.add('open');
}

export function closeDrawer() {
  document.getElementById('backdrop').classList.remove('open');
  document.getElementById('drawer').classList.remove('open');
}

export function initDrawer() {
  document.getElementById('d-close').addEventListener('click', closeDrawer);
  document.getElementById('backdrop').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
}
```

- [ ] **Step 3: Create `static/js/treemap.js`**

```js
export function squarify(items, x0, y0, x1, y1, weightKey = 'weight') {
  const totalArea   = (x1 - x0) * (y1 - y0);
  const totalWeight = items.reduce((s, d) => s + d[weightKey], 0);

  const nodes = items.slice()
    .sort((a, b) => b[weightKey] - a[weightKey])
    .map(d => ({ ...d, _a: (d[weightKey] / totalWeight) * totalArea }));

  const rects = [];

  function worst(row, stripLen) {
    if (!row.length) return Infinity;
    const s    = row.reduce((acc, d) => acc + d._a, 0);
    const rMax = Math.max(...row.map(d => d._a));
    const rMin = Math.min(...row.map(d => d._a));
    return Math.max(stripLen * stripLen * rMax / (s * s), s * s / (stripLen * stripLen * rMin));
  }

  function layout(nodes, x0, y0, x1, y1) {
    if (!nodes.length) return;
    const w = x1 - x0, h = y1 - y0;
    if (nodes.length === 1) { rects.push({ data: nodes[0], x: x0, y: y0, w, h }); return; }

    const isWide   = w >= h;
    const stripLen = isWide ? w : h;

    let row = [], i = 0;
    while (i < nodes.length) {
      const cand = [...row, nodes[i]];
      if (!row.length || worst(cand, stripLen) <= worst(row, stripLen)) { row.push(nodes[i]); i++; }
      else break;
    }

    const rowArea = row.reduce((s, d) => s + d._a, 0);
    const thick   = rowArea / stripLen;
    let   offset  = 0;

    row.forEach(d => {
      const len = (d._a / rowArea) * stripLen;
      if (isWide) rects.push({ data: d, x: x0 + offset, y: y0,        w: len,   h: thick });
      else        rects.push({ data: d, x: x0,           y: y0 + offset, w: thick, h: len  });
      offset += len;
    });

    const rem = nodes.slice(row.length);
    if (rem.length) {
      if (isWide) layout(rem, x0, y0 + thick, x1, y1);
      else        layout(rem, x0 + thick, y0, x1, y1);
    }
  }

  layout(nodes, x0, y0, x1, y1);
  return rects;
}
```

- [ ] **Step 4: Create `static/js/app.js`** (router + global controls)

```js
import { api }         from './api.js';
import { initDrawer }  from './drawer.js';
import { initPortfolio, refreshPortfolio } from './views/portfolio.js';
import { initPerformance }  from './views/performance.js';
import { initMembers }      from './views/members.js';
import { initHistory }      from './views/history.js';
import { initResearch }     from './views/research.js';
import { initSettings }     from './views/settings.js';

// ── State ─────────────────────────────────────────────────────────────────────
export let latestRun = null;
export let allRuns   = [];

// ── UI helpers ────────────────────────────────────────────────────────────────
export function showLoading(msg = 'Working…') {
  document.getElementById('loading-text').textContent = msg;
  document.getElementById('loading').classList.add('active');
}
export function hideLoading() { document.getElementById('loading').classList.remove('active'); }

export function showToast(msg, type = 'success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type} show`;
  setTimeout(() => el.classList.remove('show'), 3000);
}

// ── Nav age badge ─────────────────────────────────────────────────────────────
function updateAgeBadge(run) {
  const el = document.getElementById('nav-age');
  if (!run) { el.textContent = ''; return; }
  const days = Math.floor((Date.now() - new Date(run.timestamp)) / 86400000);
  if (days === 0)      { el.textContent = 'today';       el.className = 'nav-age fresh'; }
  else if (days <= 7)  { el.textContent = `${days}d ago`; el.className = 'nav-age recent'; }
  else                  { el.textContent = `${days}d ago`; el.className = 'nav-age stale'; }
}

// ── Router ────────────────────────────────────────────────────────────────────
const VIEWS = ['portfolio', 'performance', 'members', 'history', 'research', 'settings'];

function activate(viewName) {
  VIEWS.forEach(v => {
    document.getElementById(`view-${v}`).classList.toggle('active', v === viewName);
  });
  document.querySelectorAll('.nav-link').forEach(a => {
    a.classList.toggle('active', a.dataset.view === viewName);
  });
}

function route() {
  const hash = location.hash.replace('#', '') || 'portfolio';
  const view = VIEWS.includes(hash) ? hash : 'portfolio';
  activate(view);
}

// ── Run Committee ─────────────────────────────────────────────────────────────
async function runCommittee() {
  const btn = document.getElementById('nav-run-btn');
  btn.disabled = true;
  showLoading('Committee deliberating… (~60–90 seconds)');
  try {
    latestRun = await api.triggerRun();
    allRuns   = await api.getAllRuns();
    updateAgeBadge(latestRun);
    await refreshPortfolio(latestRun);
    showToast('Committee run complete!');
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    hideLoading();
    btn.disabled = false;
  }
}

// ── Ask Advisor ───────────────────────────────────────────────────────────────
async function askAdvisor() {
  const ticker = document.getElementById('nav-ticker').value.trim().toUpperCase();
  if (!ticker) return;
  showLoading(`Asking committee about ${ticker}…`);
  try {
    const advice = await api.askAdvisor(ticker);
    // Navigate to research log which will show the new entry
    location.hash = '#research';
    await initResearch();
    showToast(`Opinion on ${ticker} ready`);
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    hideLoading();
    document.getElementById('nav-ticker').value = '';
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  initDrawer();

  try {
    latestRun = await api.getLatestRun();
    allRuns   = await api.getAllRuns();
  } catch (_) {
    // 404 = no runs yet, that's fine
  }

  updateAgeBadge(latestRun);

  // Init all views with data
  initPortfolio(latestRun);
  initPerformance(latestRun);
  initMembers(latestRun);
  initHistory(allRuns);
  initResearch();
  initSettings();

  // Wire controls
  document.getElementById('nav-run-btn').addEventListener('click', runCommittee);
  document.getElementById('nav-ask-btn').addEventListener('click', askAdvisor);
  document.getElementById('nav-ticker').addEventListener('keydown', e => {
    if (e.key === 'Enter') askAdvisor();
  });

  // Hash routing
  window.addEventListener('hashchange', route);
  route();
}

init();
```

- [ ] **Step 5: Verify JS loads without errors**

```bash
uv run uvicorn api:app --reload --port 8000
```
Open `http://localhost:8000`. Check browser console. Expected: one 404 for `/api/runs/latest` (no runs yet) — no other errors. Nav links should be visible and clickable.

- [ ] **Step 6: Commit**

```bash
git add static/js/api.js static/js/drawer.js static/js/treemap.js static/js/app.js
git commit -m "feat: add JS infrastructure (api client, router, drawer, treemap)"
```

---

## Task 4: Portfolio view

**Files:**
- Create: `static/js/views/portfolio.js`

- [ ] **Step 1: Create `static/js/views/portfolio.js`**

```js
import { squarify }   from '../treemap.js';
import { openDrawer } from '../drawer.js';

const CONTAINER_ID = 'view-portfolio';
const GAP = 3;

// ── Section header helper ──────────────────────────────────────────────────
function sectionHeader(label, right = '') {
  return `
    <div class="section-header">
      <span class="section-label">${label}</span>
      <div class="section-rule"></div>
      ${right}
    </div>`;
}

// ── Treemap ────────────────────────────────────────────────────────────────
function renderTreemap(container, holdings) {
  const W = container.offsetWidth;
  const H = 480;
  container.style.height = `${H}px`;
  container.innerHTML = '';

  const rects = squarify(holdings, 0, 0, W, H);

  rects.forEach((r, idx) => {
    const d    = r.data;
    const area = r.w * r.h;

    const tile = document.createElement('div');
    tile.className = `tile ${d.conviction} c${(d.nominated_by || []).length}`;
    if (area < 7000)  tile.classList.add('sz-sm');
    if (area < 3500)  tile.classList.add('sz-xs');

    tile.style.cssText = `
      position:absolute;
      left:${r.x + GAP / 2}px; top:${r.y + GAP / 2}px;
      width:${r.w - GAP}px; height:${r.h - GAP}px;
      animation-delay:${idx * 25}ms;
    `;

    const fs   = Math.min(1.15, Math.max(0.78, r.w / 90)) + 'rem';
    const wfs  = Math.min(1.0,  Math.max(0.72, r.w / 120)) + 'rem';
    const up   = d.mean_upside_pct;
    const uCls = up != null && up >= 0 ? 'pos' : 'neg';
    const uTxt = up != null ? (up >= 0 ? `+${up.toFixed(1)}%` : `${up.toFixed(1)}%`) : '';

    tile.innerHTML = `
      <div class="tile-inner">
        <div class="tile-top">
          <div class="tile-ticker" style="font-size:${fs}">${d.ticker}</div>
          <div class="tile-company">${d.company_name}</div>
          <div class="tile-dots">${(d.nominated_by || []).map(m => `<div class="dot ${m.toLowerCase()}"></div>`).join('')}</div>
        </div>
        <div class="tile-bottom">
          <span class="tile-weight" style="font-size:${wfs}">${d.weight}%</span>
          ${uTxt ? `<span class="tile-upside ${uCls}">${uTxt}</span>` : ''}
        </div>
      </div>`;

    tile.addEventListener('click', () => openDrawer(d));
    container.appendChild(tile);
  });
}

// ── Signal strip ──────────────────────────────────────────────────────────
function signalList(items, type) {
  const maxU = Math.max(...items.map(h => Math.abs(h.mean_upside_pct ?? 0)), 1);
  return items
    .slice()
    .sort((a, b) => (b.mean_upside_pct ?? 0) - (a.mean_upside_pct ?? 0))
    .map((h, i) => {
      const u    = h.mean_upside_pct ?? 0;
      const uCls = u >= 0 ? 'pos' : 'neg';
      const uTxt = u >= 0 ? `+${u.toFixed(1)}%` : `${u.toFixed(1)}%`;
      const pct  = (Math.abs(u) / maxU * 100).toFixed(1);
      return `
        <div class="signal-item" data-ticker="${h.ticker}">
          <span class="signal-rank">${i + 1}</span>
          <span class="signal-ticker">${h.ticker}</span>
          <div class="signal-bar-track">
            <div class="signal-bar-fill ${type}" data-pct="${pct}"></div>
          </div>
          <span class="signal-pct ${uCls}">${uTxt}</span>
          <span class="signal-weight">${h.weight}%</span>
        </div>`;
    }).join('');
}

function animateBars(container) {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    container.querySelectorAll('.signal-bar-fill').forEach(b => { b.style.width = b.dataset.pct + '%'; });
  }));
}

// ── Public API ─────────────────────────────────────────────────────────────
export function initPortfolio(run) {
  const view = document.getElementById(CONTAINER_ID);

  if (!run) {
    view.innerHTML = `<div class="empty-state">No portfolio yet — click Run Committee to begin.</div>`;
    return;
  }

  const ts   = new Date(run.timestamp).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
  const core = run.portfolio.filter(h => h.conviction === 'core');
  const moon = run.portfolio.filter(h => h.conviction === 'moonshot');

  view.innerHTML = `
    <div style="margin-bottom:24px;">
      <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:6px;">
        Current Portfolio <em style="font-style:italic;color:var(--amber)">Atlas</em>
      </div>
      <div style="font-family:var(--font-mono);font-size:0.68rem;color:var(--text-4);">
        ${run.portfolio.length} holdings · Run ${ts}
      </div>
    </div>

    ${sectionHeader('Allocation Map', `
      <div style="display:flex;gap:16px;align-items:center;font-family:var(--font-mono);font-size:0.62rem;color:var(--text-4);">
        <span style="display:flex;align-items:center;gap:5px;">
          <span style="width:10px;height:10px;border-radius:2px;background:#272114;border:1px solid #d4a027;display:inline-block;"></span>Core
        </span>
        <span style="display:flex;align-items:center;gap:5px;">
          <span style="width:10px;height:10px;border-radius:2px;background:#1e1530;border:1px solid #9d7ff5;display:inline-block;"></span>Moonshot
        </span>
        <span style="display:flex;align-items:center;gap:4px;">
          <span class="dot claude" style="width:6px;height:6px;"></span>
          <span class="dot gpt" style="width:6px;height:6px;"></span>
          <span class="dot gemini" style="width:6px;height:6px;"></span>
          &nbsp;3-way = green border
        </span>
      </div>`)}

    <div id="treemap-wrap" style="position:relative;border:1px solid var(--border);border-radius:6px;overflow:hidden;background:var(--surface);margin-bottom:48px;"></div>

    ${sectionHeader('Upside Opportunity', '<span class="section-label" style="white-space:nowrap;">analyst consensus vs. current price</span>')}

    <div style="display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--border);border-radius:6px;overflow:hidden;">
      <div style="padding:24px 28px;border-right:1px solid var(--border);">
        <div style="font-family:var(--font-serif);font-size:1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:20px;display:flex;align-items:center;gap:10px;">
          <div style="width:3px;height:16px;border-radius:1.5px;background:var(--amber);flex-shrink:0;"></div>Core Positions
        </div>
        <div id="core-signal"></div>
      </div>
      <div style="padding:24px 28px;">
        <div style="font-family:var(--font-serif);font-size:1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:20px;display:flex;align-items:center;gap:10px;">
          <div style="width:3px;height:16px;border-radius:1.5px;background:var(--purple);flex-shrink:0;"></div>Moonshot Positions
        </div>
        <div id="moon-signal"></div>
      </div>
    </div>
  `;

  // Treemap tile styles (inline since they're dynamic)
  if (!document.getElementById('tile-styles')) {
    const s = document.createElement('style');
    s.id = 'tile-styles';
    s.textContent = `
      .tile { position:absolute; padding:2px; animation: tileIn 0.32s cubic-bezier(0.22,1,0.36,1) both; }
      @keyframes tileIn { from { opacity:0; transform:scale(0.94); } to { opacity:1; transform:scale(1); } }
      .tile-inner {
        width:100%; height:100%; border-radius:4px; padding:14px 14px 12px;
        cursor:pointer; display:flex; flex-direction:column; justify-content:space-between;
        overflow:hidden; position:relative; transition: filter 0.15s;
      }
      .tile-inner::after { content:''; position:absolute; inset:0; border-radius:4px; opacity:0; transition:opacity 0.2s; background:rgba(255,255,255,0.04); pointer-events:none; }
      .tile:hover .tile-inner::after { opacity:1; }
      .tile:hover .tile-inner { filter:brightness(1.12); }
      .tile.core .tile-inner     { background:linear-gradient(145deg,#1e1a12,#272114); border:1px solid #3a2e1a; }
      .tile.moonshot .tile-inner { background:linear-gradient(145deg,#14101e,#1e1530); border:1px solid #2e1e56; }
      .tile.c1 .tile-inner  { box-shadow:inset 0 0 0 1px rgba(212,160,39,0.1); }
      .tile.c2 .tile-inner  { box-shadow:inset 0 0 0 1px rgba(212,160,39,0.35), 0 0 14px rgba(212,160,39,0.06); }
      .tile.c3 .tile-inner  { box-shadow:inset 0 0 0 1.5px rgba(34,197,94,0.55), 0 0 20px rgba(34,197,94,0.10); }
      .tile.moonshot.c2 .tile-inner { box-shadow:inset 0 0 0 1px rgba(157,127,245,0.45), 0 0 14px rgba(157,127,245,0.08); }
      .tile-ticker { font-family:var(--font-mono); font-weight:600; line-height:1; letter-spacing:0.04em; color:var(--text); }
      .tile-company { font-size:0.67rem; color:var(--text-3); line-height:1.35; margin-top:3px; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
      .tile-dots { display:flex; gap:3px; margin-top:5px; }
      .tile-bottom { display:flex; justify-content:space-between; align-items:flex-end; margin-top:6px; }
      .tile-weight { font-family:var(--font-mono); font-weight:600; color:var(--amber); line-height:1; }
      .tile.moonshot .tile-weight { color:var(--purple); }
      .tile-upside { font-family:var(--font-mono); font-size:0.75rem; line-height:1; }
      .tile-upside.pos { color:var(--green); }
      .tile-upside.neg { color:var(--red); }
      .tile.sz-sm .tile-company, .tile.sz-sm .tile-dots { display:none; }
      .tile.sz-xs .tile-company, .tile.sz-xs .tile-dots, .tile.sz-xs .tile-bottom { display:none; }
      .tile.sz-xs .tile-ticker { font-size:0.78rem !important; }
      .signal-item { display:flex; align-items:center; gap:10px; padding:9px 0; border-bottom:1px solid var(--border); cursor:pointer; transition:padding 0.1s, background 0.1s; border-radius:0; margin:0; }
      .signal-item:last-child { border-bottom:none; }
      .signal-item:hover { padding:9px 10px; margin:0 -10px; background:var(--surface-2); border-radius:4px; }
      .signal-rank { font-family:var(--font-mono); font-size:0.62rem; color:var(--text-4); width:14px; text-align:right; flex-shrink:0; }
      .signal-ticker { font-family:var(--font-mono); font-size:0.8rem; font-weight:600; color:var(--text); width:48px; flex-shrink:0; letter-spacing:0.03em; }
      .signal-bar-track { flex:1; height:3px; background:var(--border); border-radius:1.5px; overflow:hidden; }
      .signal-bar-fill { height:100%; border-radius:1.5px; width:0; transition:width 0.7s cubic-bezier(0.22,1,0.36,1); }
      .signal-bar-fill.core     { background:linear-gradient(90deg, var(--amber-dim), var(--amber)); }
      .signal-bar-fill.moonshot { background:linear-gradient(90deg, var(--purple-dim), var(--purple)); }
      .signal-pct { font-family:var(--font-mono); font-size:0.72rem; font-weight:500; width:52px; text-align:right; flex-shrink:0; }
      .signal-pct.pos { color:var(--green); }
      .signal-pct.neg { color:var(--red); }
      .signal-weight { font-family:var(--font-mono); font-size:0.68rem; color:var(--text-4); width:28px; text-align:right; flex-shrink:0; }
    `;
    document.head.appendChild(s);
  }

  // Render treemap
  const wrap = document.getElementById('treemap-wrap');
  renderTreemap(wrap, run.portfolio);

  // Render signal strips
  document.getElementById('core-signal').innerHTML = signalList(core, 'core');
  document.getElementById('moon-signal').innerHTML = signalList(moon, 'moonshot');

  // Wire signal item clicks
  view.querySelectorAll('.signal-item').forEach(el => {
    el.addEventListener('click', () => {
      const h = run.portfolio.find(x => x.ticker === el.dataset.ticker);
      if (h) openDrawer(h);
    });
  });

  // Animate bars
  animateBars(view);

  // Re-render treemap on resize
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => renderTreemap(wrap, run.portfolio), 120);
  });
}

export async function refreshPortfolio(run) {
  initPortfolio(run);
}
```

- [ ] **Step 2: Verify portfolio tab renders**

Open `http://localhost:8000`. If you have runs, the treemap should appear. If not, click **Run Committee** and verify the tab renders after ~90 seconds.

- [ ] **Step 3: Commit**

```bash
git add static/js/views/portfolio.js
git commit -m "feat: portfolio view — treemap + signal strip"
```

---

## Task 5: Performance view

**Files:**
- Create: `static/js/views/performance.js`

- [ ] **Step 1: Create `static/js/views/performance.js`**

```js
import { api } from '../api.js';
import { showLoading, hideLoading, showToast } from '../app.js';

let chartInstance = null;

function filterSeries(rawDict, rangeOpt) {
  const entries = Object.entries(rawDict)
    .map(([k, v]) => [new Date(k), v])
    .sort((a, b) => a[0] - b[0]);

  if (!entries.length) return { labels: [], values: [] };

  const now = new Date();
  const cutoffs = {
    '1W':  new Date(now - 7  * 86400000),
    '1M':  new Date(now - 30 * 86400000),
    '3M':  new Date(now - 90 * 86400000),
    'YTD': new Date(now.getFullYear(), 0, 1),
    '1Y':  null,
  };
  const cutoff = cutoffs[rangeOpt] ?? null;
  const filtered = cutoff ? entries.filter(([d]) => d >= cutoff) : entries;
  if (!filtered.length) return { labels: [], values: [] };

  const base = filtered[0][1];
  return {
    labels: filtered.map(([d]) => d),
    values: filtered.map(([, v]) => (v - base) * 100),
  };
}

function lastReturn(rawDict, rangeOpt) {
  const { values } = filterSeries(rawDict, rangeOpt);
  return values.length ? values[values.length - 1] : 0;
}

function metricCard(label, val, delta) {
  const vCls = val >= 0 ? 'pos' : 'neg';
  const vTxt = (val >= 0 ? '+' : '') + val.toFixed(1) + '%';
  const dCls = delta != null ? (delta >= 0 ? 'pos' : 'neg') : '';
  const dTxt = delta != null ? `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}pp vs portfolio` : '';
  return `
    <div class="metric-card">
      <div class="metric-label">${label}</div>
      <div class="metric-val ${vCls}">${vTxt}</div>
      ${delta != null ? `<div class="metric-delta ${dCls}">${dTxt}</div>` : ''}
    </div>`;
}

function renderChart(perf, rangeOpt) {
  const ctx = document.getElementById('perf-chart').getContext('2d');
  if (chartInstance) { chartInstance.destroy(); chartInstance = null; }

  const datasets = [
    { key: 'portfolio', label: 'Portfolio',  color: '#06b6d4', width: 2.5, dash: [] },
    { key: 'benchmark', label: 'IGM+NVDA',   color: '#d4a027', width: 2,   dash: [6,3] },
    { key: 'spy',       label: 'SPY',         color: '#7a6e5c', width: 1.5, dash: [3,3] },
  ].filter(d => perf[d.key]);

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: datasets.map(d => {
        const { labels, values } = filterSeries(perf[d.key].series, rangeOpt);
        return {
          label: d.label,
          data: labels.map((x, i) => ({ x, y: values[i] })),
          borderColor: d.color,
          borderWidth: d.width,
          borderDash: d.dash,
          pointRadius: 0,
          tension: 0.3,
        };
      }),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: {
        legend: {
          labels: { color: '#9a8e78', font: { family: 'IBM Plex Mono', size: 11 }, boxWidth: 24, padding: 20 },
        },
        tooltip: {
          mode: 'index', intersect: false,
          backgroundColor: '#1e1a13', borderColor: '#3d3020', borderWidth: 1,
          titleColor: '#9a8e78', bodyColor: '#c4b49a',
          titleFont: { family: 'IBM Plex Mono', size: 11 },
          bodyFont:  { family: 'IBM Plex Mono', size: 11 },
          callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y >= 0 ? '+' : ''}${ctx.parsed.y.toFixed(2)}%` },
        },
      },
      scales: {
        x: {
          type: 'time',
          grid: { color: '#2a2218' },
          ticks: { color: '#7a6e5c', font: { family: 'IBM Plex Mono', size: 10 } },
        },
        y: {
          grid: { color: '#2a2218' },
          ticks: {
            color: '#7a6e5c', font: { family: 'IBM Plex Mono', size: 10 },
            callback: v => (v >= 0 ? '+' : '') + v.toFixed(1) + '%',
          },
        },
      },
    },
  });
}

export async function initPerformance(run) {
  const view = document.getElementById('view-performance');

  if (!run) {
    view.innerHTML = `<div class="empty-state">Run the committee first to see performance data.</div>`;
    return;
  }

  // Range selector state
  let rangeOpt = '1Y';
  let perf = null;

  view.innerHTML = `
    <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:24px;">
      Performance
    </div>
    <div style="display:flex;gap:4px;margin-bottom:24px;" id="range-pills">
      ${['1W','1M','3M','YTD','1Y'].map(r => `
        <button class="range-pill${r === rangeOpt ? ' active' : ''}" data-range="${r}"
          style="font-family:var(--font-mono);font-size:0.7rem;letter-spacing:0.06em;padding:5px 12px;border-radius:5px;border:1px solid var(--border);color:var(--text-3);background:transparent;cursor:pointer;transition:all 0.15s;"
        >${r}</button>`).join('')}
    </div>
    <div id="metric-cards" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:28px;"></div>
    <div style="height:400px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;">
      <canvas id="perf-chart"></canvas>
    </div>
    <div style="font-family:var(--font-mono);font-size:0.65rem;color:var(--text-4);margin-top:12px;">
      Past performance does not predict future results.
    </div>`;

  // Range pill active style
  const pillActiveStyle = `color:var(--bg);background:var(--amber);border-color:var(--amber);`;
  view.querySelectorAll('.range-pill').forEach(btn => {
    if (btn.dataset.range === rangeOpt) btn.style.cssText += pillActiveStyle;
    btn.addEventListener('click', () => {
      rangeOpt = btn.dataset.range;
      view.querySelectorAll('.range-pill').forEach(b => { b.style.cssText = ''; });
      btn.style.cssText += pillActiveStyle;
      if (perf) updateView();
    });
  });

  function updateView() {
    const pRet = perf.portfolio ? lastReturn(perf.portfolio.series, rangeOpt) : 0;
    const bRet = perf.benchmark ? lastReturn(perf.benchmark.series, rangeOpt) : null;
    const sRet = perf.spy       ? lastReturn(perf.spy.series, rangeOpt)       : null;

    document.getElementById('metric-cards').innerHTML =
      metricCard('Portfolio', pRet, null) +
      (bRet != null ? metricCard('IGM+NVDA Blend', bRet, bRet - pRet) : '') +
      (sRet != null ? metricCard('SPY', sRet, sRet - pRet) : '');

    renderChart(perf, rangeOpt);
  }

  showLoading('Fetching price data…');
  try {
    const tickers = run.portfolio.map(h => h.ticker).join(',');
    const weights = run.portfolio.map(h => h.weight).join(',');
    perf = await api.getPerformance(tickers, weights);
    updateView();
  } catch (e) {
    showToast(e.message, 'error');
    document.getElementById('metric-cards').innerHTML = `<div class="empty-state">Could not load performance data.</div>`;
  } finally {
    hideLoading();
  }
}
```

- [ ] **Step 2: Verify chart renders**

Click "Performance" in the nav. Expected: three metric cards and a line chart with amber/teal/gray lines.

- [ ] **Step 3: Commit**

```bash
git add static/js/views/performance.js
git commit -m "feat: performance view with Chart.js line chart"
```

---

## Task 6: Members view

**Files:**
- Create: `static/js/views/members.js`

- [ ] **Step 1: Create `static/js/views/members.js`**

```js
const MEMBER_CONFIG = {
  claude: { label: 'Claude',  color: '#f59e0b', cls: 'claude' },
  gpt:    { label: 'GPT-4o',  color: '#10b981', cls: 'gpt'    },
  gemini: { label: 'Gemini',  color: '#3b82f6', cls: 'gemini' },
};

function pickEntryHtml(pick, color) {
  const convCls   = pick.conviction === 'moonshot' ? 'moonshot' : 'core';
  const convLabel = pick.conviction === 'moonshot' ? '🌙 Moonshot' : 'Core';
  const up     = pick.mean_upside_pct;
  const uSign  = up != null && up >= 0 ? '+' : '';
  const uCls   = up != null && up >= 0 ? 'pos' : 'neg';
  const uTxt   = up != null ? `<span class="signal-pct ${uCls}" style="width:auto">${uSign}${up.toFixed(1)}%</span>` : '';
  const edge   = pick.variant_perception
    ? `<p style="font-size:0.78rem;color:var(--text-3);margin-top:8px;line-height:1.65;">
         <strong style="color:var(--text-2);">Edge:</strong> ${pick.variant_perception}</p>` : '';
  const rationale = pick.rationale
    ? `<p style="font-size:0.83rem;line-height:1.75;color:var(--text-2);">${pick.rationale}</p>` : '';

  return `
    <details style="border-bottom:1px solid var(--border);padding:12px 0;" class="pick-entry">
      <summary style="list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
        <div style="flex:1;min-width:0;">
          <div style="font-family:var(--font-mono);font-size:1.02rem;font-weight:700;letter-spacing:0.04em;color:${color};line-height:1;">${pick.ticker}</div>
          <div style="font-size:0.72rem;color:var(--text-3);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${pick.company_name}</div>
          <div style="display:flex;align-items:center;gap:6px;margin-top:5px;flex-wrap:wrap;">
            <span class="pill ${convCls}" style="font-size:0.6rem;">${convLabel}</span>
            ${uTxt}
          </div>
        </div>
        <span style="font-family:var(--font-mono);font-size:0.8rem;color:var(--text-4);flex-shrink:0;padding-top:2px;">+</span>
      </summary>
      <div style="padding:10px 0 4px;">${rationale}${edge}</div>
    </details>`;
}

function memberColHtml(memberKey, run, sources) {
  const cfg  = MEMBER_CONFIG[memberKey];
  const picks = run[`${memberKey}_picks`] || [];
  const core  = picks.filter(p => p.conviction === 'core');
  const moon  = picks.filter(p => p.conviction === 'moonshot');

  const entries = core.map(p => pickEntryHtml(p, cfg.color)).join('')
    + (moon.length ? `<div style="font-size:0.65rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:var(--purple);margin:20px 0 2px;padding-top:16px;border-top:1px solid var(--border);">🌙 Moonshots</div>` : '')
    + moon.map(p => pickEntryHtml(p, cfg.color)).join('');

  const sourcesHtml = sources?.length ? `
    <details style="margin-top:16px;">
      <summary style="font-family:var(--font-mono);font-size:0.65rem;letter-spacing:0.08em;text-transform:uppercase;color:var(--text-4);cursor:pointer;">
        Sources consulted (${sources.length})
      </summary>
      <ul style="margin-top:8px;padding-left:16px;font-size:0.75rem;color:var(--text-3);line-height:2;">
        ${sources.map(s => `<li><a href="${s.url}" target="_blank" style="color:var(--amber);text-decoration:none;">${s.title}</a></li>`).join('')}
      </ul>
    </details>` : '';

  return `
    <div>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;padding-bottom:16px;border-bottom:2px solid ${cfg.color};">
        <span class="dot ${cfg.cls}" style="width:12px;height:12px;"></span>
        <span style="font-family:var(--font-mono);font-size:0.95rem;font-weight:600;color:${cfg.color};">${cfg.label}</span>
        <span style="font-family:var(--font-mono);font-size:0.65rem;color:var(--text-4);">${picks.length} picks</span>
      </div>
      ${entries}
      ${sourcesHtml}
    </div>`;
}

export function initMembers(run) {
  const view = document.getElementById('view-members');

  if (!run) {
    view.innerHTML = `<div class="empty-state">No committee data yet — run the committee first.</div>`;
    return;
  }

  view.innerHTML = `
    <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:24px;">
      Member <em style="font-style:italic;color:var(--amber)">Picks</em>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:32px;">
      <div id="col-claude"></div>
      <div id="col-gpt"></div>
      <div id="col-gemini"></div>
    </div>`;

  document.getElementById('col-claude').innerHTML  = memberColHtml('claude',  run, run.claude_sources);
  document.getElementById('col-gpt').innerHTML     = memberColHtml('gpt',     run, null);
  document.getElementById('col-gemini').innerHTML  = memberColHtml('gemini',  run, null);

  // Flip + icon to − when open
  view.querySelectorAll('.pick-entry').forEach(el => {
    const toggle = el.querySelector('summary span:last-child');
    el.addEventListener('toggle', () => { if (toggle) toggle.textContent = el.open ? '−' : '+'; });
  });
}
```

- [ ] **Step 2: Verify three-column layout**

Click "Members" in nav. Expected: three columns with colored member names, expandable pick entries.

- [ ] **Step 3: Commit**

```bash
git add static/js/views/members.js
git commit -m "feat: members view — 3-column editorial pick layout"
```

---

## Task 7: History view

**Files:**
- Create: `static/js/views/history.js`

- [ ] **Step 1: Create `static/js/views/history.js`**

```js
function fmtDate(ts) {
  return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function diffView(runA, runB) {
  const curr = Object.fromEntries(runA.portfolio.map(h => [h.ticker, h]));
  const prev = Object.fromEntries(runB.portfolio.map(h => [h.ticker, h]));

  const added   = runA.portfolio.filter(h => !prev[h.ticker]);
  const removed = runB.portfolio.filter(h => !curr[h.ticker]);
  const weightChanges = runA.portfolio
    .filter(h => prev[h.ticker] && Math.abs(h.weight - prev[h.ticker].weight) >= 0.5)
    .map(h => ({ prev: prev[h.ticker], curr: h }));

  function diffBlock(title, rows, color) {
    if (!rows.length) return '';
    return `
      <div style="margin-bottom:20px;">
        <div style="font-family:var(--font-mono);font-size:0.62rem;letter-spacing:0.12em;text-transform:uppercase;color:${color};margin-bottom:10px;">${title} (${rows.length})</div>
        ${rows.map(h => `
          <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
            <span style="font-family:var(--font-mono);font-size:0.85rem;font-weight:600;color:${color};width:56px;">${h.ticker}</span>
            <span style="font-size:0.78rem;color:var(--text-3);flex:1;">${h.company_name}</span>
            <span style="font-family:var(--font-mono);font-size:0.78rem;color:var(--text-3);">${h.weight}%</span>
            ${h.nominated_by?.length > 1 ? '<span style="font-size:0.68rem;color:var(--green);">consensus</span>' : ''}
          </div>`).join('')}
      </div>`;
  }

  function wchangeBlock() {
    if (!weightChanges.length) return '';
    return `
      <div style="margin-bottom:20px;">
        <div style="font-family:var(--font-mono);font-size:0.62rem;letter-spacing:0.12em;text-transform:uppercase;color:var(--amber);margin-bottom:10px;">Weight Changes (${weightChanges.length})</div>
        ${weightChanges.map(({ prev: p, curr: c }) => {
          const delta = c.weight - p.weight;
          const arrow = delta > 0 ? '▲' : '▼';
          const col   = delta > 0 ? 'var(--green)' : 'var(--red)';
          return `
            <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
              <span style="font-family:var(--font-mono);font-size:0.85rem;font-weight:600;color:var(--text);width:56px;">${c.ticker}</span>
              <span style="font-size:0.78rem;color:var(--text-3);flex:1;">${c.company_name}</span>
              <span style="font-family:var(--font-mono);font-size:0.78rem;color:var(--text-3);">${p.weight}% → ${c.weight}%</span>
              <span style="font-family:var(--font-mono);font-size:0.78rem;color:${col};">${arrow} ${Math.abs(delta).toFixed(1)}pp</span>
            </div>`;
        }).join('')}
      </div>`;
  }

  if (!added.length && !removed.length && !weightChanges.length) {
    return `<div class="empty-state" style="padding:32px 0;">Portfolio unchanged between selected runs.</div>`;
  }

  return diffBlock('New Positions', added, 'var(--green)')
    + diffBlock('Exited Positions', removed, 'var(--red)')
    + wchangeBlock();
}

export function initHistory(allRuns) {
  const view = document.getElementById('view-history');

  if (!allRuns?.length) {
    view.innerHTML = `<div class="empty-state">No run history yet.</div>`;
    return;
  }

  const runLabels = allRuns.map(r => fmtDate(r.timestamp));

  view.innerHTML = `
    <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:24px;">
      Run <em style="font-style:italic;color:var(--amber)">History</em>
    </div>

    <table class="atlas-table" style="margin-bottom:40px;">
      <thead><tr>
        <th>Date</th><th>Core</th><th>Moonshots</th><th>Consensus</th>
      </tr></thead>
      <tbody>
        ${allRuns.map(r => `
          <tr>
            <td class="mono">${fmtDate(r.timestamp)}</td>
            <td class="mono">${r.portfolio.filter(h => h.conviction === 'core').length}</td>
            <td class="mono">${r.portfolio.filter(h => h.conviction === 'moonshot').length}</td>
            <td class="mono">${r.portfolio.filter(h => (h.nominated_by?.length ?? 0) > 1).length}</td>
          </tr>`).join('')}
      </tbody>
    </table>

    ${allRuns.length < 2 ? '<div class="empty-state">Run the committee again to enable comparison.</div>' : `
      <div style="font-family:var(--font-serif);font-size:1.2rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Compare Runs</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:28px;">
        <div>
          <div class="section-label" style="margin-bottom:6px;">Current run</div>
          <select id="sel-a" style="font-family:var(--font-mono);font-size:0.78rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--text-2);padding:8px 12px;width:100%;">
            ${runLabels.map((l, i) => `<option value="${i}">${l}</option>`).join('')}
          </select>
        </div>
        <div>
          <div class="section-label" style="margin-bottom:6px;">Compare against</div>
          <select id="sel-b" style="font-family:var(--font-mono);font-size:0.78rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--text-2);padding:8px 12px;width:100%;">
            ${runLabels.map((l, i) => `<option value="${i}"${i===1?' selected':''}>${l}</option>`).join('')}
          </select>
        </div>
      </div>
      <div id="diff-output"></div>
    `}`;

  function renderDiff() {
    const a = parseInt(document.getElementById('sel-a').value);
    const b = parseInt(document.getElementById('sel-b').value);
    const out = document.getElementById('diff-output');
    if (!out) return;
    if (a === b) { out.innerHTML = `<div class="empty-state">Select two different runs to compare.</div>`; return; }
    out.innerHTML = diffView(allRuns[a], allRuns[b]);
  }

  if (allRuns.length >= 2) {
    document.getElementById('sel-a').addEventListener('change', renderDiff);
    document.getElementById('sel-b').addEventListener('change', renderDiff);
    renderDiff();
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add static/js/views/history.js
git commit -m "feat: history view — run table and diff comparison"
```

---

## Task 8: Research log view

**Files:**
- Create: `static/js/views/research.js`

- [ ] **Step 1: Create `static/js/views/research.js`**

```js
import { api } from '../api.js';

function recBadge(rec) {
  const cls = { buy: 'buy', watch: 'watch', pass: 'pass', 'already in portfolio': 'portfolio' }[rec] ?? 'pass';
  return `<span class="rec-badge ${cls}">${rec.toUpperCase()}</span>`;
}

export async function initResearch() {
  const view = document.getElementById('view-research');
  let entries = [];

  try { entries = await api.getAdvisorLog(); } catch (_) {}

  if (!entries.length) {
    view.innerHTML = `
      <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:24px;">
        Research <em style="font-style:italic;color:var(--amber)">Log</em>
      </div>
      <div class="empty-state">No one-off checks yet. Enter a ticker in the nav bar and click Ask.</div>`;
    return;
  }

  const reversed = [...entries].reverse();

  view.innerHTML = `
    <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:24px;">
      Research <em style="font-style:italic;color:var(--amber)">Log</em>
    </div>

    <table class="atlas-table" style="margin-bottom:40px;">
      <thead><tr>
        <th>Date</th><th>Ticker</th><th>Company</th><th>Rec</th><th>Allocation</th><th>Fits?</th>
      </tr></thead>
      <tbody>
        ${reversed.map(e => `
          <tr>
            <td class="mono">${e.timestamp?.slice(0,10) ?? '—'}</td>
            <td class="mono" style="font-weight:600;color:var(--text);">${e.ticker}</td>
            <td>${e.company_name}</td>
            <td>${recBadge(e.recommendation)}</td>
            <td class="mono">${e.suggested_allocation_pct != null ? e.suggested_allocation_pct + '%' : '—'}</td>
            <td class="mono" style="color:${e.fits_philosophy ? 'var(--green)' : 'var(--text-3)'};">${e.fits_philosophy ? 'Yes' : 'No'}</td>
          </tr>`).join('')}
      </tbody>
    </table>

    <div style="font-family:var(--font-serif);font-size:1.2rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Details</div>
    <div id="research-details">
      ${reversed.map(e => `
        <details style="margin-bottom:8px;background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden;">
          <summary style="list-style:none;cursor:pointer;padding:14px 20px;display:flex;align-items:center;gap:12px;">
            <span style="font-family:var(--font-mono);font-size:0.9rem;font-weight:600;color:var(--text);width:56px;">${e.ticker}</span>
            <span style="font-size:0.82rem;color:var(--text-3);flex:1;">${e.company_name}</span>
            ${recBadge(e.recommendation)}
            <span style="font-family:var(--font-mono);font-size:0.68rem;color:var(--text-4);">${e.timestamp?.slice(0,10) ?? ''}</span>
          </summary>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:0;border-top:1px solid var(--border);">
            ${['claude','gpt','gemini'].map(m => `
              <div style="padding:16px 20px;${m !== 'gemini' ? 'border-right:1px solid var(--border);' : ''}">
                <div style="font-family:var(--font-mono);font-size:0.65rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--text-4);margin-bottom:8px;">${m}</div>
                <div style="font-size:0.82rem;line-height:1.7;color:var(--text-2);">${e[`${m}_take`] || '—'}</div>
              </div>`).join('')}
          </div>
        </details>`).join('')}
    </div>`;
}
```

- [ ] **Step 2: Commit**

```bash
git add static/js/views/research.js
git commit -m "feat: research log view"
```

---

## Task 9: Settings view

**Files:**
- Create: `static/js/views/settings.js`

- [ ] **Step 1: Create `static/js/views/settings.js`**

```js
import { api } from '../api.js';
import { showToast } from '../app.js';

export async function initSettings() {
  const view = document.getElementById('view-settings');
  let settings = { excluded_tickers: [], excluded_sectors: [] };

  try { settings = await api.getSettings(); } catch (_) {}

  function renderTags(items, key) {
    return items.map(item => `
      <span class="settings-tag">
        ${item}
        <button class="settings-tag-remove" data-key="${key}" data-val="${item}">×</button>
      </span>`).join('');
  }

  view.innerHTML = `
    <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:32px;">
      Settings
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:40px;max-width:800px;">
      <div>
        <div style="font-family:var(--font-serif);font-size:1.1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Excluded Tickers</div>
        <div id="ticker-tags" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;">
          ${renderTags(settings.excluded_tickers, 'ticker')}
        </div>
        <div style="display:flex;gap:8px;">
          <input class="settings-input" id="new-ticker" placeholder="e.g. AAPL" style="text-transform:uppercase;width:120px;">
          <button class="settings-btn primary" id="add-ticker-btn">Add</button>
        </div>
      </div>
      <div>
        <div style="font-family:var(--font-serif);font-size:1.1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Excluded Sectors</div>
        <div id="sector-tags" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;">
          ${renderTags(settings.excluded_sectors, 'sector')}
        </div>
        <div style="display:flex;gap:8px;">
          <input class="settings-input" id="new-sector" placeholder="e.g. Utilities" style="width:160px;">
          <button class="settings-btn primary" id="add-sector-btn">Add</button>
        </div>
      </div>
    </div>`;

  async function save() {
    try {
      await api.updateSettings(settings);
      showToast('Settings saved');
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  // Remove tag
  view.addEventListener('click', async e => {
    if (!e.target.classList.contains('settings-tag-remove')) return;
    const { key, val } = e.target.dataset;
    if (key === 'ticker')  settings.excluded_tickers = settings.excluded_tickers.filter(x => x !== val);
    if (key === 'sector')  settings.excluded_sectors = settings.excluded_sectors.filter(x => x !== val);
    await save();
    initSettings(); // re-render
  });

  // Add ticker
  async function addTicker() {
    const v = document.getElementById('new-ticker').value.toUpperCase().trim();
    if (!v || settings.excluded_tickers.includes(v)) return;
    settings.excluded_tickers.push(v);
    settings.excluded_tickers.sort();
    await save();
    initSettings();
  }

  // Add sector
  async function addSector() {
    const v = document.getElementById('new-sector').value.trim();
    if (!v || settings.excluded_sectors.includes(v)) return;
    settings.excluded_sectors.push(v);
    settings.excluded_sectors.sort();
    await save();
    initSettings();
  }

  document.getElementById('add-ticker-btn').addEventListener('click', addTicker);
  document.getElementById('add-sector-btn').addEventListener('click', addSector);
  document.getElementById('new-ticker').addEventListener('keydown', e => { if (e.key === 'Enter') addTicker(); });
  document.getElementById('new-sector').addEventListener('keydown', e => { if (e.key === 'Enter') addSector(); });
}
```

- [ ] **Step 2: Commit**

```bash
git add static/js/views/settings.js
git commit -m "feat: settings view"
```

---

## Task 10: Remove Streamlit, final cleanup

**Files:**
- Delete: `app.py` (after verifying all functionality is in the new app)
- Modify: `pyproject.toml` (plotly no longer needed)
- Modify: `README` or create run instructions

- [ ] **Step 1: Verify all six views work end-to-end**

Start the server:
```bash
uv run uvicorn api:app --reload --port 8000
```
Walk through each tab manually:
- Portfolio: treemap renders, tiles clickable, drawer opens
- Performance: chart loads with correct metrics
- Members: three columns with expandable picks
- History: run table shows, diff renders if 2+ runs
- Research: table visible (or empty state if no advisor queries)
- Settings: tags visible, add/remove works

- [ ] **Step 2: Remove Streamlit**

```bash
uv remove streamlit
```
Also remove `plotly` since Chart.js replaces it:
```bash
uv remove plotly
```

- [ ] **Step 3: Remove `app.py`**

Ask user to confirm, then:
```bash
git rm app.py
```

- [ ] **Step 4: Update run instructions**

Replace any Streamlit run instructions in `README.md` (if it exists) or add a comment in `api.py`:
```python
# Run: uv run uvicorn api:app --reload --port 8000
# Then open http://localhost:8000
```

- [ ] **Step 5: Final commit**

```bash
git add -p  # stage only intentional changes
git commit -m "feat: complete portfolio atlas redesign — remove streamlit, ship fastapi + vanilla js"
```

---

## Spec Coverage Check

| Requirement | Task |
|---|---|
| Drop Streamlit, use FastAPI | Task 1, 10 |
| Portfolio treemap + signal strip + drawer | Task 4 |
| Performance chart + metrics | Task 5 |
| Member breakdown (3 columns) | Task 6 |
| Run history + diff | Task 7 |
| Research log | Task 8 |
| Settings (tickers/sectors) | Task 9 |
| Run Committee button | Task 3 (app.js) |
| Ask Advisor (nav input) | Task 3 (app.js) |
| Run age badge | Task 3 (app.js) |
| CSS design system (tokens, layout, components) | Task 2 |
| Hash-based routing | Task 3 |
| Loading overlay + toast notifications | Task 2 (layout.css), Task 3 |
| Drawer (shared) | Task 3 (drawer.js) |
| Squarify treemap algorithm | Task 3 (treemap.js) |
