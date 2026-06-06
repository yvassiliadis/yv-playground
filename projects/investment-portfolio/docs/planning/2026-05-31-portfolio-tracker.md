# Portfolio Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Tracker" tab where users define real portfolios (with share counts and optional cost basis), compare their performance against each other and against SPY, VTI, and the committee's recommended portfolio.

**Architecture:** Portfolio definitions live in `data/portfolios.json`. A new `src/portfolios.py` handles persistence, CSV/Excel parsing, and enrichment (current prices, total values, per-position returns). A new `tracked_portfolios_performance()` in `src/performance.py` computes shares-weighted normalized return series (not the weight-based approach used for the committee). Four new FastAPI endpoints serve the data. A new `static/js/views/tracker.js` view follows the existing module pattern, using Chart.js for comparison. The committee dataset starts hidden in the chart — Chart.js legend clicks toggle it, same for SPY/VTI.

**Tech Stack:** Python / FastAPI / Pydantic (backend), yfinance for prices, openpyxl for Excel, pandas (already a transitive dep), vanilla ES modules / Chart.js (frontend), pytest / uv (tests).

---

## File Map

| File | Change |
|---|---|
| `src/models.py` | Add `PortfolioPosition`, `TrackedPortfolio` |
| `src/enrichment.py` | Add public `get_current_prices(tickers)` |
| `src/portfolios.py` | Create: `load`, `save`, `parse_csv`, `parse_excel`, `enrich`, `get_enriched_portfolios` |
| `src/performance.py` | Add `tracked_portfolios_performance()` |
| `api.py` | Add `GET/PUT /api/portfolios`, `POST /api/portfolios/import`, `GET /api/portfolios/performance`, `DELETE /api/portfolios/{name}` |
| `data/portfolios.json` | Create: initial `[]` |
| `static/index.html` | Add nav link + view container |
| `static/js/api.js` | Add `getPortfolios`, `savePortfolios`, `importPortfolio`, `getPortfoliosPerformance`, `deletePortfolio` |
| `static/js/app.js` | Import + call `initTracker(latestRun)` |
| `static/js/views/tracker.js` | Create: full view |
| `pyproject.toml` | Add `openpyxl>=3.1.0` |
| `tests/test_portfolios.py` | Create: unit tests |
| `tests/test_portfolios_performance.py` | Create: unit tests |

---

## Task 1: Add Pydantic models and empty data file

**Files:**
- Modify: `src/models.py`
- Create: `data/portfolios.json`
- Create: `tests/test_portfolios.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_portfolios.py
from src.models import PortfolioPosition, TrackedPortfolio


def test_position_without_avg_cost():
    pos = PortfolioPosition(ticker="AAPL", shares=10.0)
    assert pos.avg_cost is None
    assert pos.ticker == "AAPL"


def test_portfolio_round_trips_json():
    portfolio = TrackedPortfolio(
        name="My Portfolio",
        positions=[
            PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=150.0),
            PortfolioPosition(ticker="VTI", shares=50.0),
        ],
    )
    data = portfolio.model_dump()
    restored = TrackedPortfolio.model_validate(data)
    assert restored.name == "My Portfolio"
    assert len(restored.positions) == 2
    assert restored.positions[0].avg_cost == 150.0
    assert restored.positions[1].avg_cost is None
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd /Users/yannisvassiliadis/yv-playground/projects/investment-portfolio
uv run pytest tests/test_portfolios.py -v
```

Expected: `ImportError` (models not defined yet)

- [ ] **Step 3: Add models to src/models.py**

Add after the `AdvisorResponse` class at the bottom of the file:

```python
class PortfolioPosition(BaseModel):
    ticker: str
    shares: float
    avg_cost: float | None = None


class TrackedPortfolio(BaseModel):
    name: str
    positions: list[PortfolioPosition]
```

- [ ] **Step 4: Create data/portfolios.json**

```json
[]
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
uv run pytest tests/test_portfolios.py -v
```

Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/models.py data/portfolios.json tests/test_portfolios.py
git commit -m "feat: add PortfolioPosition and TrackedPortfolio models"
```

---

## Task 2: Create src/portfolios.py (load, save, parse, enrich)

**Files:**
- Modify: `src/enrichment.py`
- Create: `src/portfolios.py`
- Modify: `tests/test_portfolios.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add openpyxl to pyproject.toml**

In the `dependencies` list add:
```toml
"openpyxl>=3.1.0",
```

Then run:
```bash
uv sync
```

- [ ] **Step 2: Check for pytest-asyncio**

```bash
uv run python -c "import pytest_asyncio; print('ok')" 2>/dev/null || echo "missing"
```

If missing, add to `[dependency-groups] dev`:
```toml
"pytest-asyncio>=0.24.0",
```

And add to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Then run `uv sync`.

- [ ] **Step 3: Write failing tests**

Append to `tests/test_portfolios.py`:

```python
import pytest
from src import portfolios
from src.models import PortfolioPosition, TrackedPortfolio


def test_load_returns_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolios, "_PORTFOLIOS_PATH", tmp_path / "portfolios.json")
    assert portfolios.load() == []


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolios, "_PORTFOLIOS_PATH", tmp_path / "portfolios.json")
    data = [
        TrackedPortfolio(
            name="Retirement",
            positions=[PortfolioPosition(ticker="VTI", shares=100.0, avg_cost=220.50)],
        )
    ]
    portfolios.save(data)
    loaded = portfolios.load()
    assert len(loaded) == 1
    assert loaded[0].name == "Retirement"
    assert loaded[0].positions[0].shares == 100.0
    assert loaded[0].positions[0].avg_cost == 220.50


def test_parse_csv_basic():
    csv_content = "ticker,shares,avg_cost\nAAPL,10,150.00\nVTI,50,\n"
    positions = portfolios.parse_csv(csv_content)
    assert len(positions) == 2
    assert positions[0].ticker == "AAPL"
    assert positions[0].shares == 10.0
    assert positions[0].avg_cost == 150.0
    assert positions[1].ticker == "VTI"
    assert positions[1].avg_cost is None


def test_parse_csv_skips_invalid_rows():
    csv_content = "ticker,shares,avg_cost\nAAPL,10,\n,15,100\nMSFT,notanumber,\n"
    positions = portfolios.parse_csv(csv_content)
    assert len(positions) == 1
    assert positions[0].ticker == "AAPL"


def test_parse_csv_handles_excel_bom():
    # Excel-exported CSVs often start with a UTF-8 BOM
    csv_content = "﻿ticker,shares,avg_cost\nAAPL,10,\n"
    positions = portfolios.parse_csv(csv_content)
    assert len(positions) == 1
    assert positions[0].ticker == "AAPL"


async def test_enrich_computes_value_and_return():
    portfolio = TrackedPortfolio(
        name="Test",
        positions=[
            PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=150.0),
            PortfolioPosition(ticker="VTI", shares=100.0),
        ],
    )
    prices = {"AAPL": 200.0, "VTI": 250.0}
    result = await portfolios.enrich(portfolio, prices)
    aapl = next(p for p in result["positions"] if p["ticker"] == "AAPL")
    assert aapl["total_value"] == 2000.0
    assert aapl["return_pct"] == pytest.approx(33.3, abs=0.1)
    assert result["total_value"] == 27000.0
    assert result["total_return_pct"] is None  # VTI has no avg_cost


async def test_enrich_total_return_when_all_have_cost():
    portfolio = TrackedPortfolio(
        name="Test",
        positions=[PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=150.0)],
    )
    prices = {"AAPL": 180.0}
    result = await portfolios.enrich(portfolio, prices)
    assert result["total_return_pct"] == pytest.approx(20.0)


async def test_enrich_handles_missing_price():
    portfolio = TrackedPortfolio(
        name="Test",
        positions=[PortfolioPosition(ticker="AAPL", shares=10.0, avg_cost=150.0)],
    )
    prices = {"AAPL": None}
    result = await portfolios.enrich(portfolio, prices)
    assert result["total_value"] is None
    assert result["positions"][0]["total_value"] is None
```

- [ ] **Step 4: Run to confirm failures**

```bash
uv run pytest tests/test_portfolios.py -v
```

Expected: failures (portfolios module not yet created)

- [ ] **Step 5: Add get_current_prices to src/enrichment.py**

Add after the `enrich_picks_with_prices` function:

```python
async def get_current_prices(tickers: list[str]) -> dict[str, float | None]:
    """Returns current prices for tickers, using and updating the enrichment cache."""
    cache = _load_enrichment_cache()
    now = datetime.now(timezone.utc)

    stale = [
        t for t in tickers
        if t not in cache
        or (now - datetime.fromisoformat(cache[t]["cached_at"])).total_seconds()
        > _ENRICHMENT_CACHE_TTL_SECONDS
    ]

    if stale:
        results = await asyncio.gather(*[_fetch_ticker_data(t) for t in stale])
        for ticker, data in zip(stale, results):
            cache[ticker] = {**data, "cached_at": now.isoformat()}
        _save_enrichment_cache(cache)

    return {t: cache.get(t, {}).get("current_price") for t in tickers}
```

- [ ] **Step 6: Create src/portfolios.py**

```python
import csv
import io
import json
from pathlib import Path

from .enrichment import get_current_prices
from .models import PortfolioPosition, TrackedPortfolio

_PORTFOLIOS_PATH = Path(__file__).parent.parent / "data" / "portfolios.json"


def load() -> list[TrackedPortfolio]:
    if not _PORTFOLIOS_PATH.exists():
        return []
    try:
        data = json.loads(_PORTFOLIOS_PATH.read_text())
        return [TrackedPortfolio.model_validate(p) for p in data]
    except Exception:
        return []


def save(tracked: list[TrackedPortfolio]) -> None:
    _PORTFOLIOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PORTFOLIOS_PATH.write_text(
        json.dumps([p.model_dump() for p in tracked], indent=2)
    )


def parse_csv(content: str) -> list[PortfolioPosition]:
    # Strip BOM that Excel adds when exporting CSV
    content = content.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(content))
    positions = []
    for row in reader:
        ticker = (row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        try:
            shares = float((row.get("shares") or "").strip())
        except ValueError:
            continue
        avg_cost_str = (row.get("avg_cost") or "").strip()
        avg_cost = float(avg_cost_str) if avg_cost_str else None
        positions.append(PortfolioPosition(ticker=ticker, shares=shares, avg_cost=avg_cost))
    return positions


def parse_excel(file_bytes: bytes) -> list[PortfolioPosition]:
    import pandas as pd

    df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
    df.columns = [str(c).lower().strip() for c in df.columns]
    positions = []
    for _, row in df.iterrows():
        ticker = str(row.get("ticker", "")).upper().strip()
        if not ticker or ticker == "NAN":
            continue
        try:
            shares = float(row["shares"])
        except (ValueError, TypeError, KeyError):
            continue
        raw_cost = row.get("avg_cost")
        try:
            avg_cost = float(raw_cost) if raw_cost is not None and not pd.isna(raw_cost) else None
        except (ValueError, TypeError):
            avg_cost = None
        positions.append(PortfolioPosition(ticker=ticker, shares=shares, avg_cost=avg_cost))
    return positions


async def enrich(portfolio: TrackedPortfolio, prices: dict[str, float | None]) -> dict:
    """Returns portfolio data dict with current prices, values, weights, and returns."""
    enriched_positions = []
    total_value = 0.0
    total_cost = 0.0
    all_have_cost = True

    for pos in portfolio.positions:
        price = prices.get(pos.ticker)
        value = pos.shares * price if price is not None else None
        if value is not None:
            total_value += value
        if pos.avg_cost is not None:
            total_cost += pos.shares * pos.avg_cost
        else:
            all_have_cost = False
        enriched_positions.append({
            "ticker": pos.ticker,
            "shares": pos.shares,
            "avg_cost": pos.avg_cost,
            "current_price": price,
            "total_value": round(value, 2) if value is not None else None,
        })

    for ep in enriched_positions:
        ep["weight"] = (
            round(ep["total_value"] / total_value * 100, 1)
            if total_value and ep["total_value"] is not None
            else None
        )
        if ep["avg_cost"] is not None and ep["current_price"] is not None:
            ep["return_pct"] = round(
                (ep["current_price"] - ep["avg_cost"]) / ep["avg_cost"] * 100, 1
            )
        else:
            ep["return_pct"] = None

    total_return_pct = None
    if all_have_cost and total_cost > 0 and total_value > 0:
        total_return_pct = round((total_value - total_cost) / total_cost * 100, 1)

    return {
        "name": portfolio.name,
        "positions": enriched_positions,
        "total_value": round(total_value, 2) if total_value else None,
        "total_return_pct": total_return_pct,
    }


async def get_enriched_portfolios() -> list[dict]:
    """Loads portfolios, fetches current prices, and returns enriched data."""
    tracked = load()
    if not tracked:
        return []
    all_tickers = list({pos.ticker for p in tracked for pos in p.positions})
    prices = await get_current_prices(all_tickers)
    return [await enrich(p, prices) for p in tracked]
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/test_portfolios.py -v
```

Expected: All passing

- [ ] **Step 8: Commit**

```bash
git add src/enrichment.py src/portfolios.py pyproject.toml uv.lock tests/test_portfolios.py
git commit -m "feat: add portfolios module (load, save, parse CSV/Excel, enrich)"
```

---

## Task 3: Add tracked_portfolios_performance to src/performance.py

**Files:**
- Modify: `src/performance.py`
- Create: `tests/test_portfolios_performance.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_portfolios_performance.py
from unittest.mock import patch
from datetime import date

import pandas as pd
import pytest

from src.models import PortfolioPosition, TrackedPortfolio
from src.performance import tracked_portfolios_performance


def _fake_closes(tickers, n=10):
    """All tickers start at 100, grow 1%/day."""
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame({t: [100 * (1.01 ** i) for i in range(n)] for t in tickers}, index=idx)


def test_portfolio_series_per_portfolio():
    portfolios = [
        TrackedPortfolio("P1", [PortfolioPosition(ticker="AAPL", shares=10.0)]),
        TrackedPortfolio("P2", [PortfolioPosition(ticker="MSFT", shares=5.0)]),
    ]
    closes = _fake_closes(["AAPL", "MSFT", "SPY", "VTI"])
    with patch("src.performance.yf.download", return_value=closes):
        result = tracked_portfolios_performance(portfolios)
    assert "P1" in result
    assert "P2" in result
    assert result["P1"]["type"] == "portfolio"


def test_benchmarks_always_included():
    portfolios = [TrackedPortfolio("P1", [PortfolioPosition(ticker="AAPL", shares=10.0)])]
    closes = _fake_closes(["AAPL", "SPY", "VTI"])
    with patch("src.performance.yf.download", return_value=closes):
        result = tracked_portfolios_performance(portfolios)
    assert "spy" in result
    assert "vti" in result
    assert result["spy"]["type"] == "benchmark"


def test_committee_included_when_provided():
    portfolios = [TrackedPortfolio("P1", [PortfolioPosition(ticker="AAPL", shares=10.0)])]
    closes = _fake_closes(["AAPL", "SPY", "VTI", "MSFT"])
    with patch("src.performance.yf.download", return_value=closes):
        result = tracked_portfolios_performance(
            portfolios,
            committee={"tickers": ["AAPL", "MSFT"], "weights": [60.0, 40.0]},
        )
    assert "committee" in result
    assert result["committee"]["type"] == "committee"


def test_series_normalized_to_one_at_start():
    portfolios = [TrackedPortfolio("P1", [PortfolioPosition(ticker="AAPL", shares=10.0)])]
    closes = _fake_closes(["AAPL", "SPY", "VTI"])
    with patch("src.performance.yf.download", return_value=closes):
        result = tracked_portfolios_performance(portfolios)
    first_val = list(result["P1"]["series"].values())[0]
    assert abs(first_val - 1.0) < 1e-6


def test_empty_portfolios_returns_benchmarks_only():
    closes = _fake_closes(["SPY", "VTI"])
    with patch("src.performance.yf.download", return_value=closes):
        result = tracked_portfolios_performance([])
    assert "spy" in result
    assert "vti" in result
    assert all(v["type"] == "benchmark" for v in result.values())
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/test_portfolios_performance.py -v
```

Expected: `ImportError` (function not defined)

- [ ] **Step 3: Add tracked_portfolios_performance to src/performance.py**

Add after the `portfolio_vs_benchmarks` function:

```python
def tracked_portfolios_performance(
    portfolios: list,  # list[TrackedPortfolio]
    since: Optional[date] = None,
    committee: Optional[dict] = None,  # {"tickers": [...], "weights": [...]}
) -> dict:
    """
    Compares tracked portfolios (defined by share counts) against SPY, VTI,
    and optionally a weight-based committee portfolio.
    Returns a dict keyed by portfolio name / benchmark ticker.
    """
    if since is None:
        since = date.today() - timedelta(days=365)
    end = date.today()

    all_tickers: set[str] = {"SPY", "VTI"}
    for p in portfolios:
        for pos in p.positions:
            all_tickers.add(pos.ticker)
    if committee:
        all_tickers.update(committee["tickers"])

    raw = yf.download(list(all_tickers), start=since, end=end, auto_adjust=True, progress=False)
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw

    def summary(series: pd.Series) -> dict:
        normalized = series / series.iloc[0]
        total_return = float((normalized.iloc[-1] - 1) * 100)
        return {
            "total_return_pct": round(total_return, 2),
            "series": normalized.to_dict(),
        }

    result: dict = {}

    for p in portfolios:
        available = [pos for pos in p.positions if pos.ticker in closes.columns]
        if not available:
            continue
        shares = {pos.ticker: pos.shares for pos in available}
        daily_value = pd.Series(0.0, index=closes.index)
        for t, s in shares.items():
            daily_value += s * closes[t].ffill()
        daily_value = daily_value[daily_value > 0]
        if daily_value.empty:
            continue
        result[p.name] = {"type": "portfolio", **summary(daily_value)}

    for ticker in ["SPY", "VTI"]:
        if ticker in closes.columns:
            result[ticker.lower()] = {"type": "benchmark", **summary(closes[ticker])}

    if committee:
        ct = committee["tickers"]
        cw = {t: w / 100 for t, w in zip(ct, committee["weights"])}
        available_ct = [t for t in ct if t in closes.columns]
        if available_ct:
            comm_value = sum(closes[t] * cw[t] for t in available_ct)
            result["committee"] = {"type": "committee", **summary(comm_value)}

    return result
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_portfolios_performance.py -v
```

Expected: All passing

- [ ] **Step 5: Commit**

```bash
git add src/performance.py tests/test_portfolios_performance.py
git commit -m "feat: add tracked_portfolios_performance for shares-based portfolio comparison"
```

---

## Task 4: Add FastAPI endpoints

**Files:**
- Modify: `api.py`

- [ ] **Step 1: Add imports to api.py**

At the top of api.py, alongside the other `src` imports, add:

```python
from fastapi import File, Form, UploadFile
from src import portfolios
from src.models import TrackedPortfolio
from src.performance import tracked_portfolios_performance
```

- [ ] **Step 2: Add the four endpoints to api.py**

Add after the `update_settings` endpoint:

```python
@app.get("/api/portfolios")
async def get_portfolios():
    return await portfolios.get_enriched_portfolios()


@app.put("/api/portfolios")
async def save_portfolios(payload: list[TrackedPortfolio]):
    portfolios.save(payload)
    return {"ok": True}


@app.delete("/api/portfolios/{name}")
async def delete_portfolio(name: str):
    tracked = portfolios.load()
    updated = [p for p in tracked if p.name != name]
    if len(updated) == len(tracked):
        raise HTTPException(status_code=404, detail=f"Portfolio '{name}' not found")
    portfolios.save(updated)
    return {"ok": True}


@app.post("/api/portfolios/import")
async def import_portfolio(name: str = Form(...), file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename or ""
    try:
        if filename.endswith(".xlsx"):
            positions = portfolios.parse_excel(content)
        else:
            positions = portfolios.parse_csv(content.decode("utf-8-sig"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {e}")

    if not positions:
        raise HTTPException(status_code=400, detail="No valid positions found in file")

    tracked = portfolios.load()
    existing = next((p for p in tracked if p.name == name), None)
    if existing:
        existing.positions = positions
    else:
        tracked.append(TrackedPortfolio(name=name, positions=positions))
    portfolios.save(tracked)
    return {"name": name, "count": len(positions)}


@app.get("/api/portfolios/performance")
async def get_portfolios_performance():
    tracked = portfolios.load()
    latest = load_latest_run()
    committee = None
    if latest:
        committee = {
            "tickers": [h.ticker for h in latest.portfolio],
            "weights": [h.weight for h in latest.portfolio],
        }
    try:
        data = tracked_portfolios_performance(tracked, committee=committee)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return data
```

Note: `TrackedPortfolio` is now imported at module level (Step 1), so it's available throughout api.py. The `PUT` endpoint uses it directly as the request body type — FastAPI will validate and parse the incoming JSON via Pydantic automatically, no manual `model_validate` needed.

- [ ] **Step 3: Verify the server starts without errors**

```bash
uv run uvicorn api:app --reload --port 8000 &
sleep 3
curl -s http://localhost:8000/api/portfolios | python3 -c "import sys,json; print(json.load(sys.stdin))"
kill %1
```

Expected: `[]` (empty list, no errors)

- [ ] **Step 4: Commit**

```bash
git add api.py
git commit -m "feat: add portfolio tracker API endpoints (CRUD + performance)"
```

---

## Task 5: Add HTML tab, api.js methods, and app.js router update

**Files:**
- Modify: `static/index.html`
- Modify: `static/js/api.js`
- Modify: `static/js/app.js`

- [ ] **Step 1: Add nav link and view container to static/index.html**

In the `<div class="nav-links">` section, add after the `settings` link:
```html
<a class="nav-link" href="#tracker" data-view="tracker">Tracker</a>
```

After `<div id="view-settings" class="view"></div>`, add:
```html
<div id="view-tracker"     class="view"></div>
```

- [ ] **Step 2: Add API methods to static/js/api.js**

Add to the `api` object:

```javascript
getPortfolios:            ()           => request('GET',    '/api/portfolios'),
savePortfolios:           (data)       => request('PUT',    '/api/portfolios', data),
deletePortfolio:          (name)       => request('DELETE', `/api/portfolios/${encodeURIComponent(name)}`),
getPortfoliosPerformance: ()           => request('GET',    '/api/portfolios/performance'),
importPortfolio: async (name, file) => {
  const form = new FormData();
  form.append('name', name);
  form.append('file', file);
  const res = await fetch('/api/portfolios/import', { method: 'POST', body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
},
```

- [ ] **Step 3: Update static/js/app.js**

Add import at the top (alongside other view imports):
```javascript
import { initTracker } from './views/tracker.js';
```

In the `VIEWS` array, add `'tracker'`:
```javascript
const VIEWS = ['portfolio', 'performance', 'members', 'history', 'research', 'settings', 'tracker'];
```

In the `init()` function, after `await initSettings()`, add:
```javascript
await initTracker(latestRun);
```

- [ ] **Step 4: Commit**

```bash
git add static/index.html static/js/api.js static/js/app.js
git commit -m "feat: wire Tracker tab into nav, router, and API client"
```

> ⚠️ The server will error on startup after this commit until Task 6 is complete, because `app.js` imports `tracker.js` which doesn't exist yet. Complete Task 6 before running the dev server again.

---

## Task 6: Create static/js/views/tracker.js

**Files:**
- Create: `static/js/views/tracker.js`

- [ ] **Step 1: Create the file**

```javascript
import { api } from '../api.js';
import { showToast } from '../app.js';

let chartInstance = null;

// ── Utilities ─────────────────────────────────────────────────────────────────

function filterSeries(rawDict, rangeOpt) {
  const entries = Object.entries(rawDict)
    .map(([k, v]) => [new Date(k), v])
    .sort((a, b) => a[0] - b[0]);
  if (!entries.length) return { labels: [], values: [] };
  const cutoffs = {
    '1W':  new Date(Date.now() - 7  * 86400000),
    '1M':  new Date(Date.now() - 30 * 86400000),
    '3M':  new Date(Date.now() - 90 * 86400000),
    'YTD': new Date(new Date().getFullYear(), 0, 1),
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

function formatCurrency(v) {
  if (v == null) return '–';
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function formatReturn(v) {
  if (v == null) return '<span style="color:var(--text-4);">–</span>';
  const color = v >= 0 ? 'var(--green)' : 'var(--red)';
  const txt = (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
  return `<span style="color:${color};">${txt}</span>`;
}

// ── Portfolio cards ───────────────────────────────────────────────────────────

function holdingsTable(positions) {
  if (!positions.length) {
    return '<div style="color:var(--text-4);font-family:var(--font-mono);font-size:0.75rem;padding:12px 0;">No positions imported yet.</div>';
  }
  return `
    <table style="width:100%;border-collapse:collapse;font-family:var(--font-mono);font-size:0.72rem;margin-top:12px;">
      <thead>
        <tr style="color:var(--text-3);border-bottom:1px solid var(--border);">
          <th style="text-align:left;padding:4px 8px 8px 0;">Ticker</th>
          <th style="text-align:right;padding:4px 8px;">Shares</th>
          <th style="text-align:right;padding:4px 8px;">Price</th>
          <th style="text-align:right;padding:4px 8px;">Value</th>
          <th style="text-align:right;padding:4px 8px;">Weight</th>
          <th style="text-align:right;padding:4px 0 4px 8px;">Return</th>
        </tr>
      </thead>
      <tbody>
        ${positions.map(p => `
          <tr style="border-bottom:1px solid var(--border);color:var(--text-2);">
            <td style="padding:6px 8px 6px 0;font-weight:500;color:var(--text);">${p.ticker}</td>
            <td style="text-align:right;padding:6px 8px;">${p.shares}</td>
            <td style="text-align:right;padding:6px 8px;">${p.current_price != null ? '$' + p.current_price.toFixed(2) : '–'}</td>
            <td style="text-align:right;padding:6px 8px;">${formatCurrency(p.total_value)}</td>
            <td style="text-align:right;padding:6px 8px;">${p.weight != null ? p.weight.toFixed(1) + '%' : '–'}</td>
            <td style="text-align:right;padding:6px 0 6px 8px;">${formatReturn(p.return_pct)}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

function portfolioCard(p) {
  return `
    <div class="tracker-card" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px;">
        <div style="font-family:var(--font-serif);font-size:1.1rem;font-weight:600;color:var(--text);">${p.name}</div>
        <div style="display:flex;gap:8px;">
          <button class="settings-btn" data-import="${p.name}" style="font-size:0.65rem;padding:4px 10px;">Import CSV</button>
          <button class="settings-btn" data-remove="${p.name}" style="font-size:0.65rem;padding:4px 10px;color:var(--red);border-color:var(--red);">Remove</button>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:16px;">
        <div>
          <div style="font-family:var(--font-mono);font-size:0.62rem;color:var(--text-4);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Total Value</div>
          <div style="font-family:var(--font-mono);font-size:1rem;color:var(--text);">${formatCurrency(p.total_value)}</div>
        </div>
        <div>
          <div style="font-family:var(--font-mono);font-size:0.62rem;color:var(--text-4);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Total Return</div>
          <div style="font-family:var(--font-mono);font-size:1rem;">${formatReturn(p.total_return_pct)}</div>
        </div>
        <div>
          <div style="font-family:var(--font-mono);font-size:0.62rem;color:var(--text-4);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Positions</div>
          <div style="font-family:var(--font-mono);font-size:1rem;color:var(--text);">${p.positions.length}</div>
        </div>
      </div>
      <details>
        <summary style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-3);cursor:pointer;list-style:none;display:flex;align-items:center;gap:6px;user-select:none;">
          <span style="font-size:0.6rem;">▸</span> View Holdings
        </summary>
        ${holdingsTable(p.positions)}
      </details>
    </div>`;
}

// ── Chart ─────────────────────────────────────────────────────────────────────

const PORTFOLIO_COLORS = ['#06b6d4', '#22c55e', '#f97316', '#a855f7', '#ec4899', '#eab308'];

function renderChart(perfData, rangeOpt) {
  const ctx = document.getElementById('tracker-chart')?.getContext('2d');
  if (!ctx) return;

  // Capture which datasets the user has toggled off before destroying
  const hiddenByLabel = {};
  if (chartInstance) {
    chartInstance.data.datasets.forEach((ds, i) => {
      hiddenByLabel[ds.label] = !chartInstance.isDatasetVisible(i);
    });
    chartInstance.destroy();
    chartInstance = null;
  }

  const isHidden = (label, defaultHidden = false) =>
    label in hiddenByLabel ? hiddenByLabel[label] : defaultHidden;

  const datasets = [];
  let colorIdx = 0;

  // User portfolios
  Object.entries(perfData).forEach(([key, val]) => {
    if (val.type !== 'portfolio') return;
    const color = PORTFOLIO_COLORS[colorIdx++ % PORTFOLIO_COLORS.length];
    const { labels, values } = filterSeries(val.series, rangeOpt);
    datasets.push({
      label: key,
      hidden: isHidden(key),
      data: labels.map((x, i) => ({ x, y: values[i] })),
      borderColor: color,
      borderWidth: 2.5,
      borderDash: [],
      pointRadius: 0,
      tension: 0.3,
    });
  });

  // Benchmarks
  const BENCH = { spy: { color: '#7a6e5c', dash: [3, 3] }, vti: { color: '#8b7355', dash: [2, 4] } };
  Object.entries(BENCH).forEach(([key, style]) => {
    if (!perfData[key]) return;
    const { labels, values } = filterSeries(perfData[key].series, rangeOpt);
    datasets.push({
      label: key.toUpperCase(),
      hidden: isHidden(key.toUpperCase()),
      data: labels.map((x, i) => ({ x, y: values[i] })),
      borderColor: style.color,
      borderWidth: 1.5,
      borderDash: style.dash,
      pointRadius: 0,
      tension: 0.3,
    });
  });

  // Committee — hidden by default until user clicks legend; toggle state persists across range changes
  if (perfData.committee) {
    const { labels, values } = filterSeries(perfData.committee.series, rangeOpt);
    datasets.push({
      label: 'Committee',
      hidden: isHidden('Committee', true),
      data: labels.map((x, i) => ({ x, y: values[i] })),
      borderColor: '#d4a027',
      borderWidth: 2,
      borderDash: [6, 3],
      pointRadius: 0,
      tension: 0.3,
    });
  }

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: { datasets },
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

// ── Main init ─────────────────────────────────────────────────────────────────

export async function initTracker(latestRun) {
  const view = document.getElementById('view-tracker');
  let rangeOpt = '1Y';
  let portfoliosData = [];
  let perfData = null;

  const PILL_BASE   = 'font-family:var(--font-mono);font-size:0.7rem;letter-spacing:0.06em;padding:5px 12px;border-radius:5px;border:1px solid var(--border);color:var(--text-3);background:transparent;cursor:pointer;transition:all 0.15s;';
  const PILL_ACTIVE = 'color:var(--bg);background:var(--amber);border-color:var(--amber);';

  view.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;">
      <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);">My Portfolios</div>
      <button id="tracker-add-btn" class="settings-btn primary" style="font-size:0.75rem;padding:7px 16px;">+ Add Portfolio</button>
    </div>
    <div id="tracker-cards" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;margin-bottom:40px;"></div>

    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
      <div style="font-family:var(--font-serif);font-size:1.3rem;font-weight:600;color:var(--text-2);">Performance Comparison</div>
      <div style="display:flex;gap:4px;" id="tracker-range-pills">
        ${['1W','1M','3M','YTD','1Y'].map(r =>
          `<button class="range-pill" data-range="${r}"
            style="${PILL_BASE}${r === rangeOpt ? PILL_ACTIVE : ''}"
          >${r}</button>`).join('')}
      </div>
    </div>
    <div id="tracker-chart-container" style="height:400px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:12px;">
      <canvas id="tracker-chart"></canvas>
    </div>
    <div style="font-family:var(--font-mono);font-size:0.65rem;color:var(--text-4);margin-bottom:40px;">
      Past performance does not predict future results. Click legend items to toggle series. Committee portfolio hidden by default.
    </div>

    <div id="import-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:300;align-items:center;justify-content:center;">
      <div style="background:var(--surface-2);border:1px solid var(--border-bright);border-radius:10px;padding:32px;min-width:360px;max-width:440px;">
        <div style="font-family:var(--font-serif);font-size:1.2rem;font-weight:600;color:var(--text);margin-bottom:20px;">Import Portfolio</div>
        <div style="margin-bottom:16px;">
          <label style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-3);display:block;margin-bottom:6px;">Portfolio Name</label>
          <input id="import-name" class="settings-input" placeholder="e.g. Retirement" style="width:100%;box-sizing:border-box;">
        </div>
        <div style="margin-bottom:8px;">
          <label style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-3);display:block;margin-bottom:6px;">File (CSV or Excel)</label>
          <input id="import-file" type="file" accept=".csv,.xlsx" style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-2);width:100%;">
        </div>
        <div style="font-family:var(--font-mono);font-size:0.62rem;color:var(--text-4);margin-bottom:20px;">
          CSV columns: <em>ticker, shares, avg_cost</em> (avg_cost optional)
        </div>
        <div style="display:flex;gap:10px;">
          <button id="import-submit" class="settings-btn primary" style="flex:1;">Import</button>
          <button id="import-cancel" class="settings-btn" style="flex:1;">Cancel</button>
        </div>
      </div>
    </div>`;

  // ── Modal helpers ────────────────────────────────────────────────────────────
  const modal = document.getElementById('import-modal');

  function openModal(prefilledName = '') {
    document.getElementById('import-name').value = prefilledName;
    document.getElementById('import-file').value = '';
    modal.style.display = 'flex';
    document.getElementById('import-name').focus();
  }

  function closeModal() {
    modal.style.display = 'none';
  }

  document.getElementById('import-cancel').addEventListener('click', closeModal);
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });

  document.getElementById('import-submit').addEventListener('click', async () => {
    const name = document.getElementById('import-name').value.trim();
    const file = document.getElementById('import-file').files[0];
    if (!name) { showToast('Portfolio name is required', 'error'); return; }
    if (!file) { showToast('Please select a file', 'error'); return; }
    try {
      await api.importPortfolio(name, file);
      closeModal();
      await reload();
      showToast(`${name} imported successfully`);
    } catch (e) {
      showToast(e.message, 'error');
    }
  });

  document.getElementById('import-name').addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
  });

  // ── Add portfolio button ─────────────────────────────────────────────────────
  document.getElementById('tracker-add-btn').addEventListener('click', () => openModal(''));

  // ── Card event delegation ─────────────────────────────────────────────────────
  document.getElementById('tracker-cards').addEventListener('click', async e => {
    const importBtn = e.target.closest('[data-import]');
    const removeBtn = e.target.closest('[data-remove]');

    if (importBtn) {
      openModal(importBtn.dataset.import);
      return;
    }

    if (removeBtn) {
      const name = removeBtn.dataset.remove;
      if (!confirm(`Remove "${name}"?`)) return;
      try {
        await api.deletePortfolio(name);
        await reload();
        showToast(`${name} removed`);
      } catch (e) {
        showToast(e.message, 'error');
      }
    }
  });

  // ── Range pills ──────────────────────────────────────────────────────────────
  view.querySelectorAll('.range-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      rangeOpt = btn.dataset.range;
      view.querySelectorAll('.range-pill').forEach(b => { b.style.cssText = PILL_BASE; });
      btn.style.cssText = PILL_BASE + PILL_ACTIVE;
      if (perfData) renderChart(perfData, rangeOpt);
    });
  });

  // ── Data loading ─────────────────────────────────────────────────────────────
  function renderCards() {
    const container = document.getElementById('tracker-cards');
    if (!portfoliosData.length) {
      container.innerHTML = `
        <div style="grid-column:1/-1;font-family:var(--font-mono);font-size:0.8rem;color:var(--text-4);padding:24px 0;">
          No portfolios yet. Click "+ Add Portfolio" and import a CSV to get started.
        </div>`;
      return;
    }
    container.innerHTML = portfoliosData.map(p => portfolioCard(p)).join('');
  }

  async function reload() {
    portfoliosData = await api.getPortfolios();
    renderCards();

    if (portfoliosData.length) {
      document.getElementById('tracker-chart-container').innerHTML =
        '<div style="color:var(--text-4);font-family:var(--font-mono);font-size:0.8rem;padding:20px;">Loading performance data…</div>';
      try {
        perfData = await api.getPortfoliosPerformance();
        document.getElementById('tracker-chart-container').innerHTML =
          '<canvas id="tracker-chart"></canvas>';
        if (Object.keys(perfData).length) renderChart(perfData, rangeOpt);
      } catch (e) {
        document.getElementById('tracker-chart-container').innerHTML =
          `<div style="color:var(--red);font-family:var(--font-mono);font-size:0.8rem;padding:20px;">Could not load performance data.</div>`;
      }
    }
  }

  try {
    await reload();
  } catch (e) {
    showToast(e.message, 'error');
  }
}
```

- [ ] **Step 2: Verify the tracker tab renders correctly in the browser**

With the server running (`uv run uvicorn api:app --reload --port 8000`):
1. Open `http://localhost:8000` and click "Tracker" in the nav
2. Verify the "My Portfolios" heading and "+ Add Portfolio" button appear
3. Click "+ Add Portfolio" — confirm the modal opens
4. Create a test CSV:
   ```
   ticker,shares,avg_cost
   SPY,10,400.00
   AAPL,5,150.00
   VTI,20,
   ```
5. Import it with name "Test Portfolio"
6. Verify the card appears with total value and per-position return for AAPL/SPY
7. Verify VTI shows `–` for return (no avg_cost)
8. Wait for performance chart to load — verify 3 lines: Test Portfolio, SPY, VTI
9. Click "Committee" in chart legend to make it visible
10. Click range pills to confirm chart updates

- [ ] **Step 3: Delete the test portfolio**

Click "Remove" on the Test Portfolio card, confirm it disappears.

- [ ] **Step 4: Commit**

```bash
git add static/js/views/tracker.js
git commit -m "feat: add Portfolio Tracker view with cards, holdings table, and performance chart"
```

---

## Self-Review Checklist

After completing all tasks, verify:

- [ ] **Spec coverage**
  - [x] New tab showing user-defined portfolios
  - [x] Ticker, shares, total value per position
  - [x] Total return (when avg_cost provided)
  - [x] Performance chart comparing portfolios against each other
  - [x] SPY and VTI benchmarks in chart (toggleable via legend)
  - [x] Committee portfolio in chart (hidden by default, toggleable via legend)
  - [x] CSV import
  - [x] Excel import

- [ ] **Edge cases handled**
  - [x] Empty portfolio list → empty state message
  - [x] Position with no avg_cost → `–` in return column
  - [x] Portfolio with no avg_cost on any position → no total return shown
  - [x] Ticker not found in yfinance → `–` shown, no crash
  - [x] Excel BOM in CSV → stripped before parsing

- [ ] **API consistency**
  - [x] No route conflict: there is no `GET /api/portfolios/{name}` route, so `GET /api/portfolios/performance` resolves cleanly. The only `{name}` param route is `DELETE /api/portfolios/{name}` — different HTTP method, no conflict.
  - [x] `POST /api/portfolios/import` is a fixed path — FastAPI matches it before any dynamic segment routes on POST, no ordering concern.

---

**Plan complete and saved to `docs/planning/2026-05-31-portfolio-tracker.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks

**2. Inline Execution** — Execute tasks in this session with checkpoints

Which approach?
