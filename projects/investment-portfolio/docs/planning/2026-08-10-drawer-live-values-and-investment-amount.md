# Portfolio Drawer: Configurable Investment Amount + Live Values

**Goal:** Two changes to the recommendations flow: (1) let the user specify the total dollar amount being invested per committee run instead of a hardcoded $10,000, and (2) make the drawer's Current Price / Mean Target / Median Target show live data with the recommendation-day snapshot displayed smaller alongside, so the user can see how much upside has already been realized.

**Architecture:** No new subsystems — extends the existing `CommitteeRun` model, the existing enrichment/caching layer in `src/enrichment.py`, and the existing drawer component in `static/js/drawer.js`.

**Tech Stack:** FastAPI, Pydantic (backend), vanilla JS (frontend) — unchanged.

## Section A: Investment amount per run

**Problem:** `drawer.js` and `research.js` each hardcode `$10,000` as the notional portfolio size when converting a holding's `weight` (%) into a dollar figure. There's no way to say "I'm actually investing $10,784 this time."

**Backend**
- `src/models.py` — `CommitteeRun` gains `investment_amount: float = 10000.0`. Old run JSON files on disk still load fine (Pydantic fills the default).
- `src/runner.py` — `run_committee()` takes a new `investment_amount: float = 10000.0` param, stores it on the `CommitteeRun` it builds.
- `api.py` — `POST /api/runs` reads `investment_amount` from the request body (optional, default 10000), passes it through to `run_committee()`.

**Frontend**
- Clicking **Run Committee** opens a small modal (same visual pattern as the existing "Import Portfolio" modal in `tracker.js`) asking "How much are you investing?" — prefilled with the previous run's `investment_amount` if one exists, else $10,000. Confirm triggers the run with that amount; Cancel does nothing.
- `drawer.js` and `research.js` stop hardcoding `10000`, reading `run.investment_amount` instead (`research.js` imports the shared `latestRun` from `app.js`, same way it already imports `showToast`).
- Drawer text changes from "Recommended investment: $X of $10,000" to "...of $10,784" — reflecting what was actually entered.

## Section B: Live values with recommendation-day snapshot

**Problem:** The drawer's "Current Price" / "Mean Target" / "Median Target" cards are frozen at the moment the committee run was created (via `enrich_picks_with_prices` in `src/enrichment.py`, baked into the run's JSON file, never refreshed). "Mean Target" / "Median Target" display upside *percentages* (`mean_upside_pct` / `median_upside_pct`), not dollar target prices — that stays as-is, per the "keep as is" decision below.

**Backend**
- `src/enrichment.py` — new `get_live_quote(ticker: str) -> dict`, reusing the existing `_fetch_ticker_data` + enrichment-cache machinery (same 2-hour TTL used everywhere else in the app — "live" means "as fresh as the rest of the app," not real-time). Returns `{"current_price", "mean_upside_pct", "median_upside_pct"}` — same shape and formula as the fields already on `PortfolioHolding`.
- `api.py` — new `GET /api/quote/{ticker}`, calling `get_live_quote`. Exceptions are caught and logged; returns nulls on failure rather than a 500, since this is a secondary/enhancement value that shouldn't break the drawer.
- No changes to `PortfolioHolding` / `CommitteeRun` — the recommendation-day snapshot (`current_price`, `mean_upside_pct`, `median_upside_pct`) is already stored there, frozen at run time. That becomes the "small" value.

**Frontend (`drawer.js`)**

Each of the three cards gets a big value (live) and a smaller value below it (recommendation-day) — smaller font/muted color, no parentheses:

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  CURRENT PRICE   │  │   MEAN TARGET    │  │  MEDIAN TARGET   │
│                  │  │                  │  │                  │
│     $300.00      │  │      +4.2%       │  │      +3.8%       │
│     $150.00      │  │      +65.0%      │  │      +61.5%      │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

- On open: render immediately using the frozen holding data for both slots (small value final, big value shown as a loading placeholder), then fetch `/api/quote/{ticker}` and fill in the live numbers once they resolve.
- Race guard: if the drawer is closed or a different ticker opened before the fetch resolves, the stale response is discarded (tracked by ticker).
- Current Price card: live $ price big, recommendation-day $ price small, no color coding.
- Mean/Median Target cards: live upside % big (green/red by sign), recommendation-day upside % small (muted) — e.g. "65% → 4%" shows visually that the upside has largely been realized.

## Out of scope
- No changes to the Tracker view (`tracker.js`) — it already fetches live prices independently and has no target-price display.
- No changes to `PortfolioHolding`/`Pick` dollar target fields (`mean_target`/`median_target`) — percentages remain the drawer's format.
