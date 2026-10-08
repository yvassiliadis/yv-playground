# Minimal-Turnover Rebalance Planner

**Goal:** Given a tracked portfolio and the latest committee run's target weights, compute a minimal-turnover trade plan — buys/sells/holds sized off a configurable investment amount — and show the estimated short-term capital-gains tax against what a full liquidation would have cost, so the user can rebalance toward the committee's picks without unnecessarily eating tax.

**Architecture:** One new pure module (`src/rebalance.py`) consumed by a new read-only endpoint (`GET /api/rebalance`); a new `Rebalance` tab (`static/js/views/rebalance.js`) renders its output. Settings that used to be scattered (an ad-hoc "how much are you investing" modal, a hardcoded exclusions file) are consolidated into one `src/config.py` module and one `GET`/`PUT /api/settings` pair, which both the rebalance plan and committee runs now read from. A second, independent change ensures tickers already held in the user's designated "rebalance portfolio" always reach the screening universe and the committee prompt, so a held position can't silently vanish from a run and then show up as a forced, untaxed-reasoning sell in the rebalance plan.

**Tech Stack:** FastAPI, Pydantic (backend), vanilla JS (frontend) — unchanged.

## Section A: Rebalance engine

**Problem:** There was no way to go from "here's what I hold" + "here's what the committee recommends" to a concrete, tax-aware list of trades. Naively selling everything and rebuying the target portfolio realizes gains on positions that don't need to move.

**Backend**
- `src/models.py` — new `RebalanceTrade` (`ticker`, `company_name`, `action` [`buy`/`sell`/`hold`], `current_value`, `target_value`, `trade_value` signed by direction, `shares`, and nullable `realized_gain`/`est_tax` for sells) and `RebalancePlan` (`trades`, `current_total`, `target_total`, `cash_withdrawn`, `total_buys`, `total_sells`, `est_tax`, `full_liquidation_tax`, `warnings`).
- `src/rebalance.py::plan_rebalance(holdings, targets, prices, amount, tax_rate, min_trade=25.0)` — the pure engine:
  - Merges held positions and target weights by `canonical_ticker` (alias-aware, e.g. `GOOGL`/`GOOG`, `BRK-B`/`BRK.B`), preserving the held ticker's own spelling for display and falling back to the target's spelling for anything not currently held.
  - Skips tickers with no available price, recording a warning.
  - Computes each row's `current_value` (shares × price) and `target_value` (`amount × weight / 100`); the delta becomes a `buy`/`sell`/`hold`, with holds for deltas under `min_trade` (default $25) to avoid dust trades.
  - Scales planned buys down so `Σbuys == Σsells − cash_withdrawn` exactly — i.e. sell proceeds (minus any net cash being withdrawn, `current_total − amount`) fund the buys rather than assuming outside cash. If sells can't cover the buys plus withdrawal, buys are clamped to 0 with a warning.
  - Each sell's own `realized_gain` is `shares × (price − avg_cost)` and can be negative (a loss); its row-level `est_tax` floors that at 0 individually (`max(0, realized_gain) × tax_rate`), so a per-row loss shows `est_tax = 0` rather than a negative number (unknown `avg_cost` excludes that row from both `realized_gain` and `est_tax`, with a warning). The plan-level `est_tax`, however, is not a sum of those per-row values — it nets every sell's `realized_gain` (including negative ones, for rows with known `avg_cost`) first and only then floors the total at 0: `est_tax = max(0, Σ realized_gain across sells with known avg_cost) × tax_rate`. This lets a loss on one sell offset a gain on another before tax is computed. `full_liquidation_tax` recomputes the same net-then-floor logic over every held position sold outright, so the frontend can show "tax saved by rebalancing instead of selling everything."
  - All assumed short-term gains at a single flat `tax_rate` — no per-lot/holding-period tracking (explicitly out of scope for v1).

## Section B: Settings consolidation

**Problem:** Investment amount lived behind a per-run "how much are you investing?" modal; tax rate, minimum trade size, and which tracked portfolio to rebalance against had no home at all.

**Backend**
- `src/config.py` (previously just exclusions) gained `INVESTMENT_AMOUNT` (default 10000.0), `TAX_RATE` (default 0.24), `MIN_TRADE` (default 25.0), and `REBALANCE_PORTFOLIO` (default `None`) — all persisted to/loaded from the same `data/exclusions.json` file as the ticker/sector exclusions.
- `api.py`:
  - `GET /api/settings` now also returns `investment_amount`, `tax_rate`, `min_trade`, `rebalance_portfolio` alongside the existing exclusion lists.
  - `PUT /api/settings` validates and updates any subset of those fields (`investment_amount > 0`, `tax_rate` in `[0, 1)`, `min_trade >= 0`) before saving.
  - `POST /api/runs` no longer accepts an amount in the request body — it reads `exclusions.INVESTMENT_AMOUNT` and passes it straight to `run_committee()`.

**Frontend**
- The "Run Committee" amount-entry modal is gone (`static/js/app.js::runCommittee()` just calls `api.triggerRun()`); the amount is set once in Settings and reused for every run.
- `static/js/drawer.js` and `static/js/views/research.js` fetch `/api/settings` and use `settings.investment_amount` instead of a hardcoded 10000, falling back to the frozen `run.investment_amount` for older runs where the fetch fails or returns null.
- `static/js/views/settings.js` gained four new controls: Investment Amount (with a "Use current value" button that pulls the selected rebalance portfolio's live `total_value`), Default Rebalance Portfolio (dropdown over tracked portfolios), Tax Rate % (stored as a 0–1 fraction, displayed ×100), and Min Trade $. Each field saves independently via `PUT /api/settings` on change/blur.

## Section C: Held tickers always reach the committee

**Problem:** The Finviz/FCF screen that builds the universe the committee picks from could drop a ticker the user already holds (e.g. it no longer meets the ROE/margin thresholds), so a rebalance plan against the committee's output would show a forced "sell everything" for a position the user might still want considered.

**Backend**
- `src/screener.py::add_held_tickers(stocks, held)` — takes the screened universe plus the ticker list from the portfolio named in `config.REBALANCE_PORTFOLIO`, and for every held ticker not already present (alias-aware) and not manually excluded, fetches it via yfinance and appends it with a new `tier="additional"` (`ScreenedStock.tier` is now `"suggestion" | "opportunity" | "additional"`). Fetch failures still append a bare stub (ticker-only) rather than dropping it.
- `format_for_prompt()` renders a third prompt section, `ADDITIONAL — not from the screen, eligible picks judged on merits`, listed after SUGGESTIONS/OPPORTUNITIES — deliberately neutral labeling, not "incumbent"/"currently held," per the decision to avoid biasing the committee toward keeping positions (wash-sale/wholesale "keep incumbents" bias is explicitly out of scope for v1).
- `src/runner.py::run_committee()` builds the `held` list from `portfolios.load()` filtered to `config.REBALANCE_PORTFOLIO`, and wires it through `add_held_tickers(await screen_universe(), held)` before formatting the prompt. If no rebalance portfolio is configured, `held` is empty and behavior is unchanged from before this change.

## Section D: Rebalance API and UI

**Backend**
- `GET /api/rebalance?portfolio=<name>` (`api.py`) — loads the latest committee run (404 if none) and the named tracked portfolio (404 if not found), returns 400 if `INVESTMENT_AMOUNT <= 0`, then fetches live prices for the union of held + target tickers, enriches the portfolio to get its current total value (400 if `INVESTMENT_AMOUNT` exceeds it — you can't invest more than you're starting with), then calls `rebalance.plan_rebalance()` with the configured amount/tax rate/min trade and returns the resulting `RebalancePlan`.

**Frontend**
- New `static/js/views/rebalance.js` / `#view-rebalance` / nav link (`static/index.html`), wired into `app.js`'s `VIEWS` list and hash router (`route()` calls `initRebalance()` on `#rebalance`).
- On load, fetches tracked portfolios and settings in parallel, defaults the portfolio picker to `settings.rebalance_portfolio` (or the first tracked portfolio), and shows the configured investment amount read-only with a link to Settings to change it.
- Renders five metric cards (Sells, Buys, Est. Tax, Saved vs. Sell-All [`full_liquidation_tax − est_tax`], Cash Withdrawn), any plan warnings, and a trade table sorted sell → buy → hold, color-coded red/green/muted by action, with per-row current/target/trade dollar values, shares, and estimated tax.
- Switching the portfolio dropdown re-fetches and re-renders the plan; no portfolios yet shows a prompt to add one in the Tracker tab instead of erroring.

## Out of scope (v1)
- Per-lot tracking, purchase dates, or short- vs. long-term gain splitting — every gain is assumed short-term at the single configured `tax_rate`.
- Wash-sale detection.
- Any committee bias toward keeping incumbent positions, beyond the neutral "ADDITIONAL" labeling in Section C.
