# Research-to-Portfolio Add Feature

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a ticker comes back as "strong buy" in the research log, show an "Add to Portfolio" button that injects the holding into the latest run with a down-weighted allocation.

**Architecture:** Pure backend function builds the updated portfolio (no I/O, testable), a thin API endpoint mutates the run file, and the research view gains per-entry buttons that refresh the portfolio view on success. The `suggested_allocation_pct` from the advisor is used as the base weight, discounted by 40% (factor 0.6) to prevent a manually added ticker from displacing committee consensus picks. Existing holdings are scaled proportionally so the portfolio always sums to 100%.

**Tech Stack:** Python / FastAPI / Pydantic (backend), vanilla ES modules (frontend), pytest (tests), uv for running.

---

## File Map

| File | Change |
|---|---|
| `src/models.py` | Add `manually_added: bool = False` to `PortfolioHolding` |
| `src/runner.py` | Add `_build_portfolio_with_holding()` (pure) + `add_holding_to_run()` |
| `api.py` | Add `POST /api/portfolio/include` endpoint |
| `static/js/api.js` | Add `addToPortfolio(ticker)` method |
| `static/js/app.js` | Export `setLatestRun(run)`, pass run into `initResearch(run)` |
| `static/js/views/research.js` | Accept run arg, show "+ Add" button for eligible strong buy entries |
| `tests/test_portfolio_include.py` | New: unit tests for `_build_portfolio_with_holding` |

---

## Task 1: Extend PortfolioHolding with `manually_added`

**Files:**
- Modify: `src/models.py`

- [ ] **Step 1: Add field to PortfolioHolding**

In `src/models.py`, update `PortfolioHolding`:

```python
class PortfolioHolding(BaseModel):
    ticker: str
    company_name: str
    conviction: str
    weight: float
    nominated_by: list[str]
    rationale: str
    current_price: float | None = None
    mean_upside_pct: float | None = None
    median_upside_pct: float | None = None
    manually_added: bool = False
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
uv run pytest tests/ -v
```

Expected: all existing tests pass (default `False` is backward-compatible with all existing JSON files and test fixtures).

- [ ] **Step 3: Commit**

```bash
git add src/models.py
git commit -m "feat: add manually_added flag to PortfolioHolding"
```

---

## Task 2: Backend — portfolio injection logic (TDD)

**Files:**
- Modify: `src/runner.py`
- Create: `tests/test_portfolio_include.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_portfolio_include.py`:

```python
import pytest
from datetime import datetime, timezone
from src.models import AdvisorResponse, CommitteeRun, PortfolioHolding
from src.runner import MANUAL_WEIGHT_FACTOR, _build_portfolio_with_holding


def _holding(ticker: str, weight: float) -> PortfolioHolding:
    return PortfolioHolding(
        ticker=ticker,
        company_name=f"{ticker} Inc.",
        conviction="core",
        weight=weight,
        nominated_by=["claude", "gpt"],
        rationale="test",
    )


def _run(*holdings: PortfolioHolding) -> CommitteeRun:
    return CommitteeRun(
        run_id="test",
        timestamp=datetime.now(timezone.utc),
        claude_picks=[],
        gpt_picks=[],
        portfolio=list(holdings),
    )


def _advice(ticker: str, suggested: float | None = 8.0) -> AdvisorResponse:
    return AdvisorResponse(
        ticker=ticker,
        company_name=f"{ticker} Inc.",
        recommendation="strong buy",
        claude_take="very bullish",
        gpt_take="also bullish",
        gemini_take="bullish",
        fits_philosophy=True,
        suggested_allocation_pct=suggested,
        mean_upside_pct=25.0,
        median_upside_pct=20.0,
    )


def test_weights_sum_to_100():
    run = _run(_holding("AAPL", 60.0), _holding("MSFT", 40.0))
    result = _build_portfolio_with_holding(run, _advice("NVDA"))
    total = sum(h.weight for h in result)
    assert abs(total - 100.0) < 0.02


def test_new_holding_weight_is_discounted():
    run = _run(_holding("AAPL", 100.0))
    advice = _advice("NVDA", suggested=10.0)
    result = _build_portfolio_with_holding(run, advice)
    nvda = next(h for h in result if h.ticker == "NVDA")
    assert nvda.weight == round(10.0 * MANUAL_WEIGHT_FACTOR, 2)


def test_manually_added_flag_set():
    run = _run(_holding("AAPL", 100.0))
    result = _build_portfolio_with_holding(run, _advice("NVDA"))
    nvda = next(h for h in result if h.ticker == "NVDA")
    assert nvda.manually_added is True


def test_existing_holdings_flag_not_set():
    run = _run(_holding("AAPL", 100.0))
    result = _build_portfolio_with_holding(run, _advice("NVDA"))
    aapl = next(h for h in result if h.ticker == "AAPL")
    assert aapl.manually_added is False


def test_nominated_by_all_three_members():
    run = _run(_holding("AAPL", 100.0))
    result = _build_portfolio_with_holding(run, _advice("NVDA"))
    nvda = next(h for h in result if h.ticker == "NVDA")
    assert set(nvda.nominated_by) == {"claude", "gpt", "gemini"}


def test_raises_if_already_in_portfolio():
    run = _run(_holding("AAPL", 100.0))
    with pytest.raises(ValueError, match="already in the portfolio"):
        _build_portfolio_with_holding(run, _advice("AAPL"))


def test_case_insensitive_duplicate_check():
    run = _run(_holding("AAPL", 100.0))
    with pytest.raises(ValueError):
        _build_portfolio_with_holding(run, _advice("aapl"))


def test_fallback_weight_when_no_suggested_allocation():
    run = _run(_holding("AAPL", 60.0), _holding("MSFT", 40.0))
    result = _build_portfolio_with_holding(run, _advice("NVDA", suggested=None))
    total = sum(h.weight for h in result)
    assert abs(total - 100.0) < 0.02
    nvda = next(h for h in result if h.ticker == "NVDA")
    assert nvda.weight > 0


def test_existing_weight_ordering_preserved():
    """AAPL had more weight than MSFT before; it should still after."""
    run = _run(_holding("AAPL", 70.0), _holding("MSFT", 30.0))
    result = _build_portfolio_with_holding(run, _advice("NVDA"))
    aapl = next(h for h in result if h.ticker == "AAPL")
    msft = next(h for h in result if h.ticker == "MSFT")
    assert aapl.weight > msft.weight
```

- [ ] **Step 2: Run tests — expect import errors**

```bash
uv run pytest tests/test_portfolio_include.py -v
```

Expected: `ImportError` — `MANUAL_WEIGHT_FACTOR` and `_build_portfolio_with_holding` don't exist yet.

- [ ] **Step 3: Implement in runner.py**

Add after the `RUNS_DIR` constant in `src/runner.py`:

```python
MANUAL_WEIGHT_FACTOR = 0.6
```

Add this function before `run_committee`:

```python
def _build_portfolio_with_holding(
    run: CommitteeRun,
    advice: "AdvisorResponse",
) -> list[PortfolioHolding]:
    from .models import AdvisorResponse  # local import avoids circular at module level

    ticker = advice.ticker.upper()
    if any(h.ticker.upper() == ticker for h in run.portfolio):
        raise ValueError(f"{ticker} is already in the portfolio")

    if advice.suggested_allocation_pct is not None:
        new_weight = round(advice.suggested_allocation_pct * MANUAL_WEIGHT_FACTOR, 2)
    else:
        existing = [h.weight for h in run.portfolio]
        median_w = sorted(existing)[len(existing) // 2] if existing else 5.0
        new_weight = round(median_w * MANUAL_WEIGHT_FACTOR, 2)

    old_total = sum(h.weight for h in run.portfolio)
    remaining = 100.0 - new_weight

    if old_total > 0:
        scale = remaining / old_total
        scaled = [
            h.model_copy(update={"weight": round(h.weight * scale, 2)})
            for h in run.portfolio
        ]
    else:
        scaled = list(run.portfolio)

    # Fix floating-point drift: adjust the heaviest holding
    scaled_sum = sum(h.weight for h in scaled)
    diff = round(remaining - scaled_sum, 2)
    if diff != 0.0 and scaled:
        idx = max(range(len(scaled)), key=lambda i: scaled[i].weight)
        scaled[idx] = scaled[idx].model_copy(
            update={"weight": round(scaled[idx].weight + diff, 2)}
        )

    new_holding = PortfolioHolding(
        ticker=ticker,
        company_name=advice.company_name,
        conviction="core",
        weight=new_weight,
        nominated_by=["claude", "gpt", "gemini"],
        rationale=advice.claude_take or advice.gpt_take or advice.gemini_take or "Strong buy consensus.",
        mean_upside_pct=advice.mean_upside_pct,
        median_upside_pct=advice.median_upside_pct,
        manually_added=True,
    )

    return sorted(
        scaled + [new_holding],
        key=lambda h: (h.conviction == "moonshot", len(h.nominated_by) == 1, -h.weight),
    )
```

Add the I/O wrapper after `_build_portfolio_with_holding`:

```python
def add_holding_to_run(run: CommitteeRun, advice: "AdvisorResponse") -> CommitteeRun:
    updated_portfolio = _build_portfolio_with_holding(run, advice)
    updated_run = run.model_copy(update={"portfolio": updated_portfolio})
    run_files = sorted(RUNS_DIR.glob("*.json"), reverse=True)
    if run_files:
        run_files[0].write_text(updated_run.model_dump_json(indent=2))
    return updated_run
```

Add the import for `AdvisorResponse` at the top of the imports block in `runner.py`:

```python
from .models import AdvisorResponse, CommitteeRun, Pick, WebSource
```

(Replace the existing `from .models import CommitteeRun, Pick, WebSource` line.)

- [ ] **Step 4: Run tests — expect all green**

```bash
uv run pytest tests/test_portfolio_include.py -v
```

Expected: 9 tests pass.

- [ ] **Step 5: Run full suite to check for regressions**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/runner.py tests/test_portfolio_include.py
git commit -m "feat: add _build_portfolio_with_holding and add_holding_to_run"
```

---

## Task 3: API endpoint

**Files:**
- Modify: `api.py`

- [ ] **Step 1: Add imports and endpoint**

At the top of `api.py`, the existing imports already include `from src.runner import load_all_runs, load_latest_run, run_committee`. Update that line:

```python
from src.runner import add_holding_to_run, load_all_runs, load_latest_run, run_committee
```

Also add `AdvisorResponse` to the models import:
```python
from src.models import AdvisorResponse
```

Add the endpoint after `@app.get("/api/advisor/log")`:

```python
@app.post("/api/portfolio/include")
async def include_in_portfolio(payload: dict):
    ticker = payload.get("ticker", "").upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker required")

    run = load_latest_run()
    if not run:
        raise HTTPException(status_code=404, detail="No portfolio run found")

    log = advisor_log.load()
    entry = next(
        (e for e in reversed(log) if e.get("ticker", "").upper() == ticker and e.get("recommendation") == "strong buy"),
        None,
    )
    if not entry:
        raise HTTPException(status_code=404, detail=f"No strong buy recommendation found for {ticker}")

    advice = AdvisorResponse.model_validate(entry)

    try:
        updated_run = add_holding_to_run(run, advice)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return updated_run.model_dump(mode="json")
```

- [ ] **Step 2: Smoke test the endpoint manually**

Start the server: `uv run uvicorn api:app --reload --port 8000`

In a second terminal (replace TICKER with a ticker you've already researched as strong buy from the advisor log):
```bash
curl -s -X POST http://localhost:8000/api/portfolio/include \
  -H "Content-Type: application/json" \
  -d '{"ticker": "TICKER"}' | python3 -m json.tool | head -40
```

Expected: JSON response containing the updated `CommitteeRun` with the new holding.

- [ ] **Step 3: Commit**

```bash
git add api.py
git commit -m "feat: POST /api/portfolio/include endpoint"
```

---

## Task 4: Frontend — api.js

**Files:**
- Modify: `static/js/api.js`

- [ ] **Step 1: Add addToPortfolio method**

In `static/js/api.js`, add to the `api` object (after `askAdvisor`):

```js
addToPortfolio: (ticker) => request('POST', '/api/portfolio/include', { ticker }),
```

So the full api object becomes:
```js
export const api = {
  getLatestRun:     ()       => request('GET',  '/api/runs/latest'),
  getAllRuns:        ()       => request('GET',  '/api/runs'),
  triggerRun:       ()       => request('POST', '/api/runs'),
  getPerformance:   (t, w)   => request('GET',  `/api/performance?tickers=${t}&weights=${w}`),
  getAdvisorLog:    ()       => request('GET',  '/api/advisor/log'),
  askAdvisor:       (ticker) => request('POST', '/api/advisor', { ticker }),
  addToPortfolio:   (ticker) => request('POST', '/api/portfolio/include', { ticker }),
  getSettings:      ()       => request('GET',  '/api/settings'),
  updateSettings:   (data)   => request('PUT',  '/api/settings', data),
};
```

- [ ] **Step 2: Commit**

```bash
git add static/js/api.js
git commit -m "feat: addToPortfolio api method"
```

---

## Task 5: app.js — expose setLatestRun, pass run to research

**Files:**
- Modify: `static/js/app.js`

- [ ] **Step 1: Export setLatestRun**

After the state declarations (`export let latestRun = null;`), add:

```js
export function setLatestRun(run) {
  latestRun = run;
}
```

- [ ] **Step 2: Pass latestRun into initResearch calls**

There are two calls to `initResearch()` in `app.js`:
1. In `init()`: change `await initResearch()` → `await initResearch(latestRun)`
2. In `askAdvisor()`: change `await initResearch()` → `await initResearch(latestRun)`

- [ ] **Step 3: Commit**

```bash
git add static/js/app.js
git commit -m "feat: export setLatestRun, thread run into initResearch"
```

---

## Task 6: research.js — Add to Portfolio button

**Files:**
- Modify: `static/js/views/research.js`

- [ ] **Step 1: Import dependencies**

At the top of `research.js`, update the imports:

```js
import { api } from '../api.js';
import { showToast, setLatestRun } from '../app.js';
import { refreshPortfolio } from './portfolio.js';
```

- [ ] **Step 2: Update initResearch signature and helper**

Change `export async function initResearch()` to accept a run argument and add a helper to check eligibility:

```js
export async function initResearch(run = null) {
  const view = document.getElementById('view-research');
  const portfolioTickers = new Set(
    (run?.portfolio ?? []).map(h => h.ticker.toUpperCase())
  );

  let entries = [];
  try { entries = await api.getAdvisorLog(); } catch (e) { showToast(e.message, 'error'); }
  // ... rest of existing code unchanged ...
```

- [ ] **Step 3: Add the "+ Add" button to table rows and wire clicks**

In the table row template (the `${reversed.map(e => { ... }).join('')}` block), update the `<td>` for the rec column to include the button:

```js
const canAdd = e.recommendation === 'strong buy' && !portfolioTickers.has(e.ticker.toUpperCase());
return `
<tr>
  <td class="mono">${e.timestamp?.slice(0,10) ?? '—'}</td>
  <td class="mono" style="font-weight:600;color:var(--text);">${e.ticker}</td>
  <td>${e.company_name}</td>
  <td style="white-space:nowrap;">
    ${recBadge(e.recommendation)}
    ${canAdd ? `<button class="add-holding-btn" data-ticker="${e.ticker}" style="margin-left:8px;padding:2px 8px;font-family:var(--font-mono);font-size:0.65rem;background:var(--green);color:#000;border:none;border-radius:4px;cursor:pointer;letter-spacing:0.04em;font-weight:600;">+ ADD</button>` : ''}
  </td>
  <td class="mono">${e.suggested_allocation_pct != null ? e.suggested_allocation_pct + '%' : '—'}</td>
  <td class="mono" style="color:${uColor};">${upside}</td>
  <td class="mono" style="color:${e.fits_philosophy ? 'var(--green)' : 'var(--text-3)'};">${e.fits_philosophy ? 'Yes' : 'No'}</td>
</tr>`;
```

After setting `view.innerHTML`, wire the button clicks. Add this block at the end of `initResearch`, before the closing `}`:

```js
view.querySelectorAll('.add-holding-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const ticker = btn.dataset.ticker;
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const updatedRun = await api.addToPortfolio(ticker);
      setLatestRun(updatedRun);
      refreshPortfolio(updatedRun);
      btn.textContent = '✓ ADDED';
      btn.style.background = 'var(--text-3)';
      showToast(`${ticker} added to portfolio`);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = '+ ADD';
      showToast(e.message, 'error');
    }
  });
});
```

- [ ] **Step 4: Test in the browser**

Start the server: `uv run uvicorn api:app --reload --port 8000`

Open http://localhost:8000, go to Research tab.

Verify:
- Strong buy entries (not already in portfolio) show a green "+ ADD" button in the Rec column
- Non-strong-buy entries show no button
- Clicking "+ ADD" disables the button, shows "…" while loading, then "✓ ADDED"
- A toast confirms "TICKER added to portfolio"
- Navigate to Portfolio tab — new ticker appears in the treemap and signal strips with a smaller weight than the top committee picks
- Weights in the portfolio still sum to 100%
- If you click Add on a ticker already in the portfolio, the API returns 409 and a toast shows the error

- [ ] **Step 5: Commit**

```bash
git add static/js/views/research.js
git commit -m "feat: add to portfolio button in research log for strong buy entries"
```

---

## Self-Review

**Spec coverage:**
- ✅ Button appears in research log for any past strong buy
- ✅ Allocation uses advisor's suggested_allocation_pct, discounted by 0.6 factor
- ✅ Existing holdings rescaled to make room; total stays at 100%
- ✅ Persists by mutating the latest run JSON
- ✅ Portfolio view refreshes after add without page reload
- ✅ Error if ticker already in portfolio (409 + toast)
- ✅ Button state feedback (disabled → "…" → "✓ ADDED")

**Placeholder scan:** No TBDs or vague steps found.

**Type consistency:**
- `_build_portfolio_with_holding(run: CommitteeRun, advice: AdvisorResponse)` used in both test file and runner.py — consistent.
- `add_holding_to_run(run, advice)` called in `api.py` endpoint — consistent with definition.
- `setLatestRun` exported from `app.js`, imported in `research.js` — consistent.
- `refreshPortfolio` already exported from `portfolio.js`, imported in `research.js` — consistent.
