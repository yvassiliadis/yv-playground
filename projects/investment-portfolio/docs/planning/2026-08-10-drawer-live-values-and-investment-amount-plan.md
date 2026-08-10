# Configurable Investment Amount + Live Drawer Values Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user specify a per-run investment amount (default $10,000) instead of a hardcoded value, and make the portfolio drawer show live current price/upside alongside the frozen recommendation-day snapshot.

**Architecture:** Backend: `CommitteeRun` gains an `investment_amount` field set at run time; a new `get_live_quote()` reuses the existing enrichment cache to serve fresh price/upside data on demand via a new endpoint. Frontend: a new modal captures the amount when "Run Committee" is clicked; the drawer renders the frozen snapshot immediately and swaps in live values once fetched.

**Tech Stack:** FastAPI, Pydantic (backend), vanilla JS/ES modules (frontend), pytest + pytest-asyncio (`asyncio_mode = "auto"`, per `pyproject.toml`).

See `docs/planning/2026-08-10-drawer-live-values-and-investment-amount.md` for the full design rationale.

## Global Constraints

- There is no JS test runner in this repo — frontend tasks are verified manually (run the app, exercise the UI), not via automated tests.
- Python tests use `pytest` with `asyncio_mode = "auto"` — write test functions as `async def test_...(): ... await ...` directly, no `asyncio.run()` wrapper needed.
- Reuse existing CSS classes (`.settings-input`, `.settings-btn`) for new modal chrome, matching the existing Import Portfolio modal in `static/js/views/tracker.js` — don't introduce new modal-chrome classes.
- `get_live_quote()` reuses the existing 2-hour enrichment cache TTL (`_ENRICHMENT_CACHE_TTL_SECONDS` in `src/enrichment.py`) — no new cache or shorter TTL.

---

## File Map

| File | Change |
|---|---|
| `src/models.py` | Add `investment_amount: float = 10000.0` to `CommitteeRun` |
| `src/runner.py` | `run_committee()` takes `investment_amount` param, stores it on the run |
| `src/enrichment.py` | Add `get_live_quote(ticker)` |
| `api.py` | `POST /api/runs` reads `investment_amount` from body; add `GET /api/quote/{ticker}` |
| `static/js/api.js` | `triggerRun(investmentAmount)`, add `getQuote(ticker)` |
| `static/index.html` | Add Run Committee amount modal markup |
| `static/js/app.js` | Wire modal open/confirm/cancel around `runCommittee()` |
| `static/js/drawer.js` | Live/frozen dual-value cards; use `run.investment_amount` |
| `static/js/views/research.js` | Use `investment_amount` from the latest run instead of hardcoded 10000 |
| `static/css/components.css` | Add `.upside-card-sub` |
| `tests/test_runner.py` | Create: unit tests for `investment_amount` |
| `tests/test_enrichment.py` | Create: unit tests for `get_live_quote` |
| `tests/test_api.py` | Create: unit tests for the two endpoint changes |

---

## Task 1: `CommitteeRun.investment_amount` field

**Files:**
- Modify: `src/models.py:37-44`
- Test: `tests/test_runner.py` (create)

**Interfaces:**
- Produces: `CommitteeRun.investment_amount: float` (default `10000.0`) — consumed by Task 2 (`run_committee`), Task 3 (`api.py`), Task 7 (`drawer.js`/`research.js`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runner.py
from src.models import CommitteeRun


def test_committee_run_defaults_investment_amount_to_10000():
    run = CommitteeRun(
        run_id="abc",
        timestamp="2026-01-01T00:00:00Z",
        claude_picks=[],
        gpt_picks=[],
        portfolio=[],
    )
    assert run.investment_amount == 10000.0


def test_committee_run_investment_amount_round_trips():
    run = CommitteeRun(
        run_id="abc",
        timestamp="2026-01-01T00:00:00Z",
        claude_picks=[],
        gpt_picks=[],
        portfolio=[],
        investment_amount=10784.0,
    )
    data = run.model_dump()
    restored = CommitteeRun.model_validate(data)
    assert restored.investment_amount == 10784.0


def test_committee_run_loads_old_data_without_investment_amount():
    old_data = {
        "run_id": "abc",
        "timestamp": "2026-01-01T00:00:00Z",
        "claude_picks": [],
        "gpt_picks": [],
        "portfolio": [],
    }
    run = CommitteeRun.model_validate(old_data)
    assert run.investment_amount == 10000.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/yannisvassiliadis/yv-playground/projects/investment-portfolio
uv run pytest tests/test_runner.py -v
```

Expected: FAIL — `AttributeError: 'CommitteeRun' object has no attribute 'investment_amount'` (pydantic silently drops the unknown kwarg since `investment_amount` isn't a declared field yet).

- [ ] **Step 3: Add the field**

In `src/models.py`, modify the `CommitteeRun` class (currently lines 37-44):

```python
class CommitteeRun(BaseModel):
    run_id: str
    timestamp: datetime
    claude_picks: list[Pick]
    gpt_picks: list[Pick]
    gemini_picks: list[Pick] = []
    portfolio: list[PortfolioHolding]
    claude_sources: list[WebSource] = []
    investment_amount: float = 10000.0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_runner.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_runner.py
git commit -m "feat: add investment_amount field to CommitteeRun"
```

---

## Task 2: `run_committee()` stores `investment_amount`

**Files:**
- Modify: `src/runner.py:101-105` (function signature) and `src/runner.py:220-228` (`CommitteeRun(...)` construction)
- Test: `tests/test_runner.py` (extend)

**Interfaces:**
- Consumes: `CommitteeRun.investment_amount` (Task 1)
- Produces: `run_committee(anthropic_client, openai_client, gemini_client, investment_amount: float = 10000.0) -> CommitteeRun` — consumed by Task 3 (`api.py`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_runner.py` (below the existing tests):

```python
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src import runner
from src.models import Pick


def _valid_picks(member: str) -> list[Pick]:
    core = [
        Pick(
            ticker=f"CORE{i}",
            company_name=f"Core {i}",
            rationale="test",
            conviction="core",
            member=member,
        )
        for i in range(10)
    ]
    moonshots = [
        Pick(
            ticker=f"MOON{i}",
            company_name=f"Moon {i}",
            rationale="test",
            conviction="moonshot",
            member=member,
        )
        for i in range(3)
    ]
    return core + moonshots


@pytest.fixture(autouse=True)
def _isolate_runner_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(runner, "_PICKS_CACHE_DIR", tmp_path / "picks_cache")


async def _run_committee_with_mocks(**kwargs):
    with patch("src.runner.screen_universe", return_value=[]), \
         patch("src.runner.format_for_prompt", return_value=""), \
         patch("src.runner.claude_member.get_research", return_value=("research", [])), \
         patch("src.runner.claude_member.get_picks", return_value=_valid_picks("claude")), \
         patch("src.runner.gpt_member.get_picks", return_value=_valid_picks("gpt")), \
         patch("src.runner.gemini_member.get_picks", return_value=_valid_picks("gemini")), \
         patch("src.runner.enrich_picks_with_prices", side_effect=lambda picks: picks):
        return await runner.run_committee(AsyncMock(), AsyncMock(), AsyncMock(), **kwargs)


def test_run_committee_defaults_investment_amount_to_10000():
    run = asyncio.run(_run_committee_with_mocks())
    assert run.investment_amount == 10000.0


def test_run_committee_stores_custom_investment_amount():
    run = asyncio.run(_run_committee_with_mocks(investment_amount=10784.0))
    assert run.investment_amount == 10784.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_runner.py -v
```

Expected: FAIL — `TypeError: run_committee() got an unexpected keyword argument 'investment_amount'`

- [ ] **Step 3: Update `run_committee()`**

In `src/runner.py`, change the signature (currently lines 101-105):

```python
async def run_committee(
    anthropic_client: anthropic.AsyncAnthropic,
    openai_client: AsyncOpenAI,
    gemini_client: genai.Client,
    investment_amount: float = 10000.0,
) -> CommitteeRun:
```

And update the `CommitteeRun(...)` construction (currently lines 220-228):

```python
    run = CommitteeRun(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        claude_picks=claude_picks,
        gpt_picks=gpt_picks,
        gemini_picks=gemini_picks,
        portfolio=portfolio,
        claude_sources=claude_sources,
        investment_amount=investment_amount,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_runner.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/runner.py tests/test_runner.py
git commit -m "feat: thread investment_amount through run_committee"
```

---

## Task 3: `POST /api/runs` accepts `investment_amount`

**Files:**
- Modify: `api.py:67-71`
- Test: `tests/test_api.py` (create)

**Interfaces:**
- Consumes: `run_committee(ac, oc, gc, investment_amount=...)` (Task 2)
- Produces: `POST /api/runs` with optional JSON body `{"investment_amount": <float>}` — consumed by Task 6 (`static/js/api.js` `triggerRun`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api.py
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import api
from src.models import CommitteeRun


def _dummy_run(investment_amount: float) -> CommitteeRun:
    return CommitteeRun(
        run_id="abc",
        timestamp="2026-01-01T00:00:00Z",
        claude_picks=[],
        gpt_picks=[],
        portfolio=[],
        investment_amount=investment_amount,
    )


def test_trigger_run_defaults_to_10000():
    client = TestClient(api.app)
    with patch("api._clients", return_value=(AsyncMock(), AsyncMock(), AsyncMock())):
        with patch(
            "api.run_committee",
            AsyncMock(side_effect=lambda ac, oc, gc, investment_amount: _dummy_run(investment_amount)),
        ):
            response = client.post("/api/runs")
    assert response.status_code == 200
    assert response.json()["investment_amount"] == 10000.0


def test_trigger_run_uses_custom_investment_amount():
    client = TestClient(api.app)
    with patch("api._clients", return_value=(AsyncMock(), AsyncMock(), AsyncMock())):
        with patch(
            "api.run_committee",
            AsyncMock(side_effect=lambda ac, oc, gc, investment_amount: _dummy_run(investment_amount)),
        ):
            response = client.post("/api/runs", json={"investment_amount": 10784.0})
    assert response.status_code == 200
    assert response.json()["investment_amount"] == 10784.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_api.py -v
```

Expected: FAIL — `TypeError: <lambda>() missing 1 required positional argument: 'investment_amount'` (the endpoint doesn't pass it yet)

- [ ] **Step 3: Update the endpoint**

In `api.py`, replace the `trigger_run` endpoint (currently lines 67-71):

```python
@app.post("/api/runs")
async def trigger_run(payload: dict | None = None):
    investment_amount = (payload or {}).get("investment_amount", 10000.0)
    ac, oc, gc = _clients()
    run = await run_committee(ac, oc, gc, investment_amount=investment_amount)
    return run.model_dump(mode="json")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_api.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add api.py tests/test_api.py
git commit -m "feat: accept investment_amount on POST /api/runs"
```

---

## Task 4: `get_live_quote()` in `src/enrichment.py`

**Files:**
- Modify: `src/enrichment.py`
- Test: `tests/test_enrichment.py` (create)

**Interfaces:**
- Consumes: existing `_fetch_ticker_data`, `_load_enrichment_cache`, `_save_enrichment_cache`, `_ENRICHMENT_CACHE_TTL_SECONDS`, `_ENRICHMENT_CACHE_PATH`
- Produces: `async def get_live_quote(ticker: str) -> dict` returning `{"current_price": float | None, "mean_upside_pct": float | None, "median_upside_pct": float | None}` — consumed by Task 5 (`api.py`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_enrichment.py
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src import enrichment


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(enrichment, "_ENRICHMENT_CACHE_PATH", tmp_path / "enrichment_cache.json")


async def test_get_live_quote_fetches_and_computes_upside():
    with patch(
        "src.enrichment._fetch_ticker_data",
        return_value={"current_price": 300.0, "mean_target": 330.0, "median_target": 315.0},
    ):
        result = await enrichment.get_live_quote("AAPL")
    assert result["current_price"] == 300.0
    assert result["mean_upside_pct"] == pytest.approx(10.0)
    assert result["median_upside_pct"] == pytest.approx(5.0)


async def test_get_live_quote_handles_missing_targets():
    with patch(
        "src.enrichment._fetch_ticker_data",
        return_value={"current_price": 300.0, "mean_target": None, "median_target": None},
    ):
        result = await enrichment.get_live_quote("AAPL")
    assert result["current_price"] == 300.0
    assert result["mean_upside_pct"] is None
    assert result["median_upside_pct"] is None


async def test_get_live_quote_uses_cache_within_ttl():
    enrichment._ENRICHMENT_CACHE_PATH.write_text(json.dumps({
        "AAPL": {
            "current_price": 300.0,
            "mean_target": 330.0,
            "median_target": 315.0,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
    }))
    with patch("src.enrichment._fetch_ticker_data") as mock_fetch:
        result = await enrichment.get_live_quote("AAPL")
    mock_fetch.assert_not_called()
    assert result["current_price"] == 300.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_enrichment.py -v
```

Expected: FAIL — `AttributeError: module 'src.enrichment' has no attribute 'get_live_quote'`

- [ ] **Step 3: Add `get_live_quote()`**

Add to `src/enrichment.py`, after `get_current_prices` (end of file):

```python
async def get_live_quote(ticker: str) -> dict:
    """Returns current price and upside percentages for a single ticker, using and updating the enrichment cache."""
    cache = _load_enrichment_cache()
    now = datetime.now(timezone.utc)

    stale = (
        ticker not in cache
        or (now - datetime.fromisoformat(cache[ticker]["cached_at"])).total_seconds()
        > _ENRICHMENT_CACHE_TTL_SECONDS
    )

    if stale:
        data = await _fetch_ticker_data(ticker)
        cache[ticker] = {**data, "cached_at": now.isoformat()}
        _save_enrichment_cache(cache)

    d = cache.get(ticker, {})
    price = d.get("current_price")
    mean_t = d.get("mean_target")
    median_t = d.get("median_target")

    return {
        "current_price": price,
        "mean_upside_pct": ((mean_t - price) / price * 100) if price and mean_t else None,
        "median_upside_pct": ((median_t - price) / price * 100) if price and median_t else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_enrichment.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/enrichment.py tests/test_enrichment.py
git commit -m "feat: add get_live_quote for on-demand price/upside lookups"
```

---

## Task 5: `GET /api/quote/{ticker}`

**Files:**
- Modify: `api.py`
- Test: `tests/test_api.py` (extend)

**Interfaces:**
- Consumes: `get_live_quote(ticker)` (Task 4)
- Produces: `GET /api/quote/{ticker}` → `{"current_price": ..., "mean_upside_pct": ..., "median_upside_pct": ...}`, always HTTP 200 (nulls on failure) — consumed by Task 8 (`static/js/api.js` `getQuote`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api.py`:

```python
from unittest.mock import AsyncMock, patch


def test_get_quote_returns_live_values():
    client = TestClient(api.app)
    with patch(
        "api.get_live_quote",
        AsyncMock(return_value={"current_price": 300.0, "mean_upside_pct": 10.0, "median_upside_pct": 5.0}),
    ):
        response = client.get("/api/quote/AAPL")
    assert response.status_code == 200
    assert response.json() == {"current_price": 300.0, "mean_upside_pct": 10.0, "median_upside_pct": 5.0}


def test_get_quote_returns_nulls_on_failure():
    client = TestClient(api.app)
    with patch("api.get_live_quote", AsyncMock(side_effect=RuntimeError("yfinance boom"))):
        response = client.get("/api/quote/AAPL")
    assert response.status_code == 200
    assert response.json() == {"current_price": None, "mean_upside_pct": None, "median_upside_pct": None}
```

(`TestClient`, `api`, and `CommitteeRun` are already imported at the top of `tests/test_api.py` from Task 3 — only the new `patch`/`AsyncMock` usages above are needed here, and those are also already imported from Task 3.)

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_api.py -v
```

Expected: FAIL — `AttributeError: module 'api' has no attribute 'get_live_quote'`

- [ ] **Step 3: Add the endpoint**

In `api.py`, add `logging` to the imports at the top and set up a module logger (after the existing imports, before `load_dotenv()`):

```python
import logging
```

```python
logger = logging.getLogger(__name__)
```

Add `get_live_quote` to the enrichment import — since `api.py` doesn't import from `src.enrichment` yet, add a new import line near the other `from src...` imports:

```python
from src.enrichment import get_live_quote
```

Add the endpoint (after `get_advisor_log`, near the other `GET` endpoints):

```python
@app.get("/api/quote/{ticker}")
async def get_quote(ticker: str):
    try:
        return await get_live_quote(ticker.upper())
    except Exception:
        logger.warning("Failed to fetch live quote for %s", ticker, exc_info=True)
        return {"current_price": None, "mean_upside_pct": None, "median_upside_pct": None}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_api.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add api.py tests/test_api.py
git commit -m "feat: add GET /api/quote/{ticker} for live drawer values"
```

---

## Task 6: Run Committee investment-amount modal

**Files:**
- Modify: `static/js/api.js:17` (`triggerRun`)
- Modify: `static/index.html` (add modal markup)
- Modify: `static/js/app.js` (wire modal to the Run Committee flow)

**Interfaces:**
- Consumes: `POST /api/runs` with optional `investment_amount` (Task 3)
- Produces: `api.triggerRun(investmentAmount)` — same call site as before, now takes an optional amount.

No automated tests (no JS test runner in this repo) — verify manually in Step 5.

- [ ] **Step 1: Update `api.js`**

In `static/js/api.js`, replace line 17:

```js
  triggerRun:       (investmentAmount) => request('POST', '/api/runs', investmentAmount != null ? { investment_amount: investmentAmount } : undefined),
```

- [ ] **Step 2: Add the modal markup to `index.html`**

In `static/index.html`, insert before the `<!-- Loading overlay -->` comment (currently line 88):

```html
<!-- Run Committee amount modal -->
<div id="run-amount-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:300;align-items:center;justify-content:center;">
  <div style="background:var(--surface-2);border:1px solid var(--border-bright);border-radius:10px;padding:32px;min-width:360px;max-width:440px;">
    <div style="font-family:var(--font-serif);font-size:1.2rem;font-weight:600;color:var(--text);margin-bottom:20px;">How much are you investing?</div>
    <div style="margin-bottom:20px;">
      <label style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-3);display:block;margin-bottom:6px;">Investment Amount ($)</label>
      <input id="run-amount-input" class="settings-input" type="number" min="1" step="1" style="width:100%;box-sizing:border-box;">
    </div>
    <div style="display:flex;gap:10px;">
      <button id="run-amount-confirm" class="settings-btn primary" style="flex:1;">Run Committee</button>
      <button id="run-amount-cancel" class="settings-btn" style="flex:1;">Cancel</button>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Wire the modal in `app.js`**

In `static/js/app.js`, replace the `runCommittee` function (currently lines 82-101):

```js
// ── Run Committee ─────────────────────────────────────────────────────────────
function openRunAmountModal() {
  const modal = document.getElementById('run-amount-modal');
  document.getElementById('run-amount-input').value = latestRun?.investment_amount ?? 10000;
  modal.style.display = 'flex';
  document.getElementById('run-amount-input').focus();
}

function closeRunAmountModal() {
  document.getElementById('run-amount-modal').style.display = 'none';
}

async function runCommittee(investmentAmount) {
  const btn = document.getElementById('nav-run-btn');
  btn.disabled = true;
  showLoading('Committee deliberating… (~60–90 seconds)');
  try {
    latestRun = await api.triggerRun(investmentAmount);
    allRuns   = await api.getAllRuns();
    updateAgeBadge(latestRun);
    await refreshPortfolio(latestRun);
    initPerformance(latestRun);
    initTracker(latestRun);
    showToast('Committee run complete!');
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    hideLoading();
    btn.disabled = false;
  }
}
```

Then in `init()` (currently lines 142-143), replace:

```js
  document.getElementById('nav-run-btn').addEventListener('click', runCommittee);
```

with:

```js
  document.getElementById('nav-run-btn').addEventListener('click', openRunAmountModal);
  document.getElementById('run-amount-cancel').addEventListener('click', closeRunAmountModal);
  document.getElementById('run-amount-modal').addEventListener('click', e => {
    if (e.target.id === 'run-amount-modal') closeRunAmountModal();
  });
  document.getElementById('run-amount-confirm').addEventListener('click', () => {
    const amount = Number(document.getElementById('run-amount-input').value);
    if (!Number.isFinite(amount) || amount <= 0) { showToast('Enter a valid amount', 'error'); return; }
    closeRunAmountModal();
    runCommittee(amount);
  });
  document.getElementById('run-amount-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('run-amount-confirm').click();
    if (e.key === 'Escape') closeRunAmountModal();
  });
```

- [ ] **Step 4: Start the app**

```bash
cd /Users/yannisvassiliadis/yv-playground/projects/investment-portfolio
uv run uvicorn api:app --reload --port 8000
```

- [ ] **Step 5: Manually verify**

Open `http://localhost:8000`. Click "Run Committee". Confirm:
- A modal appears titled "How much are you investing?", prefilled with `10000` (or the previous run's amount if one exists).
- Typing a custom amount (e.g. `10784`) and clicking "Run Committee" closes the modal and starts the run with the loading overlay.
- Clicking "Cancel" (or clicking the backdrop, or pressing Escape) closes the modal without starting a run.
- Entering `0` or a non-numeric value and clicking "Run Committee" shows an "Enter a valid amount" toast and does not start a run.

- [ ] **Step 6: Commit**

```bash
git add static/js/api.js static/index.html static/js/app.js
git commit -m "feat: add investment amount modal to Run Committee"
```

---

## Task 7: Drawer + Research use `run.investment_amount`

**Files:**
- Modify: `static/js/drawer.js:40-41`
- Modify: `static/js/views/research.js:16-24,52-59,141`

**Interfaces:**
- Consumes: `CommitteeRun.investment_amount` (Task 1), `run` param already passed to `openDrawer(holding, run)`
- Produces: no new interfaces — internal calculation change only.

No automated tests — verify manually in Step 3.

- [ ] **Step 1: Update `drawer.js`**

In `static/js/drawer.js`, replace lines 40-41:

```js
  const totalAmount = run?.investment_amount ?? 10000;
  const investAmt = Math.round(totalAmount * (d.weight / 100));
  document.getElementById('d-invest').textContent = `Recommended investment: $${investAmt.toLocaleString()} of $${totalAmount.toLocaleString()}`;
```

- [ ] **Step 2: Update `research.js`**

In `static/js/views/research.js`, replace the `allocCell` function (currently lines 16-24):

```js
function allocCell(e, portfolioByTicker, investmentAmount) {
  const inPort = portfolioByTicker[e.ticker];
  if (e.recommendation === 'already in portfolio' && inPort?.weight != null) {
    return '$' + Math.round(investmentAmount * inPort.weight / 100).toLocaleString();
  }
  return e.suggested_allocation_pct != null
    ? '$' + Math.round(investmentAmount * e.suggested_allocation_pct / 100).toLocaleString()
    : '—';
}
```

Then, inside `initResearch()`, replace the data-fetch block (currently lines 52-59):

```js
  let investmentAmount = 10000;

  try {
    [allEntries] = await Promise.all([
      api.getAdvisorLog(),
      api.getLatestRun().then(r => {
        if (r?.portfolio) r.portfolio.forEach(h => { portfolioByTicker[h.ticker] = h; });
        if (r?.investment_amount != null) investmentAmount = r.investment_amount;
      }).catch(() => {}),
    ]);
  } catch (e) { showToast(e.message, 'error'); }
```

Finally, update the call site (currently line 141):

```js
          <td class="mono">${allocCell(e, portfolioByTicker, investmentAmount)}</td>
```

- [ ] **Step 3: Manually verify**

With the app running (`uv run uvicorn api:app --reload --port 8000`):
- Run the committee with a custom amount (e.g. `10784`), then open a holding's drawer. Confirm the text reads "Recommended investment: $X of $10,784" (not "$10,000").
- Navigate to the Research tab. Confirm the "Suggested $" column is computed off the same $10,784 total.

- [ ] **Step 4: Commit**

```bash
git add static/js/drawer.js static/js/views/research.js
git commit -m "feat: use run investment_amount instead of hardcoded 10000"
```

---

## Task 8: Live/frozen dual-value drawer cards

**Files:**
- Modify: `static/js/api.js` (add `getQuote`)
- Modify: `static/css/components.css` (add `.upside-card-sub`)
- Modify: `static/js/drawer.js` (dual-value rendering, live fetch, race guard)

**Interfaces:**
- Consumes: `GET /api/quote/{ticker}` (Task 5)
- Produces: no new interfaces — internal rendering change only.

No automated tests — verify manually in Step 5.

- [ ] **Step 1: Add `getQuote` to `api.js`**

In `static/js/api.js`, add to the `api` object (after `updateSettings`):

```js
  getQuote:         (ticker)   => request('GET',  `/api/quote/${encodeURIComponent(ticker)}`),
```

- [ ] **Step 2: Add the CSS for the secondary value**

In `static/css/components.css`, add after `.upside-card-val.neg { color: var(--red); }` (currently line 112):

```css
.upside-card-sub { font-family: var(--font-mono); font-size: 0.7rem; color: var(--text-4); margin-top: 3px; }
.upside-card-sub.pos { color: var(--green); opacity: 0.75; }
.upside-card-sub.neg { color: var(--red); opacity: 0.75; }
```

- [ ] **Step 3: Rewrite the upside cards in `drawer.js`**

In `static/js/drawer.js`, add the `api` import at the top of the file (there are currently no imports):

```js
import { api } from './api.js';
```

Add a module-level ticker tracker and a small class-applying helper, right after the `MEMBER_NAMES` constant:

```js
let _openTicker = null;

function applyUpsideClass(el, val) {
  el.classList.remove('pos', 'neg');
  if (val != null) el.classList.add(val >= 0 ? 'pos' : 'neg');
}
```

Replace the upside-cards block (currently lines 18-35) with:

```js
  _openTicker = d.ticker;

  const frozenPriceStr = d.current_price != null
    ? '$' + d.current_price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '—';
  const frozenMeanCls    = d.mean_upside_pct != null   ? (d.mean_upside_pct >= 0 ? 'pos' : 'neg')   : '';
  const frozenMedianCls  = d.median_upside_pct != null ? (d.median_upside_pct >= 0 ? 'pos' : 'neg') : '';
  const frozenMeanStr    = d.mean_upside_pct != null   ? (d.mean_upside_pct >= 0 ? '+' : '')   + d.mean_upside_pct.toFixed(1)   + '%' : '—';
  const frozenMedianStr  = d.median_upside_pct != null ? (d.median_upside_pct >= 0 ? '+' : '') + d.median_upside_pct.toFixed(1) + '%' : '—';

  document.getElementById('d-upside').innerHTML = `
    <div class="upside-card">
      <div class="upside-card-label">Current Price</div>
      <div class="upside-card-val" id="d-price-live">…</div>
      <div class="upside-card-sub">${frozenPriceStr}</div>
    </div>
    <div class="upside-card">
      <div class="upside-card-label">Mean Target</div>
      <div class="upside-card-val" id="d-mean-live">…</div>
      <div class="upside-card-sub ${frozenMeanCls}">${frozenMeanStr}</div>
    </div>
    <div class="upside-card">
      <div class="upside-card-label">Median Target</div>
      <div class="upside-card-val" id="d-median-live">…</div>
      <div class="upside-card-sub ${frozenMedianCls}">${frozenMedianStr}</div>
    </div>`;

  fetchLiveQuote(d.ticker);
```

Add the fetch function at the end of the file (after `initDrawer`):

```js
async function fetchLiveQuote(ticker) {
  let quote;
  try {
    quote = await api.getQuote(ticker);
  } catch (e) {
    quote = { current_price: null, mean_upside_pct: null, median_upside_pct: null };
  }
  if (_openTicker !== ticker) return;

  const priceEl  = document.getElementById('d-price-live');
  const meanEl   = document.getElementById('d-mean-live');
  const medianEl = document.getElementById('d-median-live');
  if (!priceEl) return;

  priceEl.textContent = quote.current_price != null
    ? '$' + quote.current_price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '—';

  meanEl.textContent = quote.mean_upside_pct != null
    ? (quote.mean_upside_pct >= 0 ? '+' : '') + quote.mean_upside_pct.toFixed(1) + '%'
    : '—';
  applyUpsideClass(meanEl, quote.mean_upside_pct);

  medianEl.textContent = quote.median_upside_pct != null
    ? (quote.median_upside_pct >= 0 ? '+' : '') + quote.median_upside_pct.toFixed(1) + '%'
    : '—';
  applyUpsideClass(medianEl, quote.median_upside_pct);
}
```

- [ ] **Step 4: Start the app**

```bash
cd /Users/yannisvassiliadis/yv-playground/projects/investment-portfolio
uv run uvicorn api:app --reload --port 8000
```

- [ ] **Step 5: Manually verify**

Open `http://localhost:8000`, go to the Portfolio view, click a holding tile to open the drawer. Confirm:
- The three cards (Current Price, Mean Target, Median Target) immediately show the smaller recommendation-day value, with the big value showing "…" briefly.
- Within a couple seconds, the big value updates to a live number (a real price/percentage, not "…").
- Colors: the small Mean/Median Target values are muted green/red per sign; the big live values are full-brightness green/red per sign.
- Open the browser DevTools Network tab, throttle to "Slow 3G", open ticker A's drawer, then immediately open ticker B's drawer before A's `/api/quote/` request finishes. Confirm B's card values are the ones that end up populated (A's stale response never overwrites B's cards).

- [ ] **Step 6: Commit**

```bash
git add static/js/api.js static/css/components.css static/js/drawer.js
git commit -m "feat: show live price/upside in drawer alongside recommendation-day snapshot"
```
