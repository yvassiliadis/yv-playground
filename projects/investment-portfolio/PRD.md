# PRD: AI Investment Committee Dashboard

## Problem Statement

Keeping a high-quality personal stock portfolio requires ongoing research across many companies, sectors, and market conditions — work that is hard to do consistently as an individual. The user wants the collective intelligence of multiple AI models acting as an investment committee, producing an opinionated, weighted portfolio on demand, while also being able to quickly gut-check individual tickers they hear about before committing capital. Separately, the user needs to know whether the committee's picks are actually beating their existing benchmarks (SPY, IGM, NVDA).

---

## Solution

A local Streamlit dashboard backed by a Python engine that:

1. Runs an AI committee (Claude + GPT + Gemini) on demand to generate a weighted portfolio of 10–20 core picks + 3 moonshots
2. Applies consensus weighting — tickers nominated by multiple members receive proportionally higher allocation
3. Tracks portfolio performance vs SPY and the user's existing holdings (IGM + NVDA blend) via yfinance
4. Provides a conversational advisor mode where the user enters any ticker and gets an honest, structured opinion from each committee member
5. Persists every run to disk so the user can review history and observe how committee recommendations evolve

---

## User Stories

1. As an investor, I want to trigger an AI committee run with a single button click, so that I can get fresh recommendations without writing any code.
2. As an investor, I want the dashboard to show how many days ago the last run was, so that I know whether my portfolio view is stale.
3. As an investor, I want the dashboard to warn me when the last run was more than 7 days ago, so that I'm nudged to re-run before market conditions drift too far.
4. As an investor, I want the dashboard to escalate to an error-level warning after 30 days, so that I don't forget I'm operating on very old recommendations.
5. As an investor, I want the committee to return 10–20 core picks (up to 25), so that I have a focused portfolio without over-diversification.
6. As an investor, I want the committee to return exactly 3 moonshot picks, so that high-risk bets are bounded and don't dominate the portfolio.
7. As an investor, I want picks to follow an aggressive growth, long-term bias philosophy, so that the committee's mandate matches my own investment goals.
8. As an investor, I want crypto excluded from all picks, so that I don't need to manually screen those out.
9. As an investor, I want traditional energy companies excluded, so that the portfolio aligns with my sector preferences.
10. As an investor, I want the committee to apply ESG-lite criteria (prefer mission-aligned, diverse companies when all else equal), so that my portfolio reflects my values without being overly constrained.
11. As an investor, I want consensus picks (nominated by more than one AI member) to receive higher portfolio weight, so that high-conviction shared ideas are more prominently represented.
12. As an investor, I want each portfolio holding to show which committee members nominated it, so that I understand the source and strength of conviction behind each pick.
13. As an investor, I want to see a clear visual distinction between core picks and moonshots, so that I can quickly assess the risk profile of the overall portfolio.
14. As an investor, I want each holding to include a rationale from the committee, so that I understand the thesis before executing a trade.
15. As an investor, I want portfolio weights to sum to 100%, so that the output maps directly to allocation decisions in my SoFi account.
16. As an investor, I want to see portfolio performance vs SPY over the past year, so that I can benchmark against the standard market index.
17. As an investor, I want to see portfolio performance vs my existing IGM + NVDA blend, so that I know whether the committee is adding value over what I currently hold.
18. As an investor, I want a cumulative return line chart comparing all three series, so that I can see divergence and convergence over time visually.
19. As an investor, I want summary metric cards showing total return % for portfolio, SPY, and the benchmark blend, so that I can compare performance at a glance.
20. As an investor, I want to see each committee member's individual picks in a side-by-side breakdown, so that I can understand where the members agree and disagree.
21. As an investor, I want to type in any stock ticker and ask the committee "should I invest?", so that I can get a quick sanity check on tickers I hear about.
22. As an investor, I want the advisor to tell me if the ticker is already in the current portfolio, so that I don't accidentally double-weight a position.
23. As an investor, I want each committee member to give an individual buy/watch/pass verdict on the queried ticker, so that I understand the range of opinion.
24. As an investor, I want the advisor to tell me whether the stock fits the committee's investment philosophy, so that I know if it's a structurally compatible pick.
25. As an investor, I want the advisor result to remain visible on the page after I submit a query, so that I can read the rationale before navigating away.
26. As an investor, I want all previous committee runs to be saved to disk, so that I can look back at how recommendations have changed over time.
27. As an investor, I want the run timestamp recorded on every portfolio view, so that I always know when the displayed data was generated.
28. As an investor, I want all three committee members to make their API calls in parallel during a committee run, so that the wait time is minimized.

---

## Implementation Decisions

- **Committee member interface**: Each member module (claude_member, gpt_member, gemini_member) exposes two async functions — `get_picks(client)` returning `list[Pick]`, and `get_stock_opinion(client, ticker)` returning a structured dict.

- **Aggregator**: Pure function `build_portfolio(all_picks: list[Pick]) -> list[PortfolioHolding]`. Weight scales with nomination count: 1× solo, 2× two-of-three, 3× all-three. Moonshots carry a base weight of `0.5` vs `1.0` for core picks to prevent them from dominating even when they achieve consensus.

- **Persistence**: Each run writes a timestamped JSON file to `data/runs/`. The runner loads the most recent file on startup. No database — flat files are sufficient for a personal tool with one run per day at most.

- **Performance tracking**: All price data fetched from Yahoo Finance via yfinance. Portfolio return is computed as a weighted sum of cumulative returns since 1 year ago. Benchmark blend is hardcoded as 50% IGM / 50% NVDA. SPY is fetched separately. Performance is cached in-process for 1 hour (`@st.cache_data(ttl=3600)`) so it always reflects recent prices without hitting yfinance on every tab switch.

- **Advisor result**: All three AI opinions are fetched in parallel via `asyncio.gather`. Final recommendation is a permissive vote: if any member says "buy", output is "buy"; if none say "buy" but any say "watch", output is "watch"; otherwise "pass". `fits_philosophy` requires all three members to agree. The user sees each member's take individually regardless.

- **Data models**:
  ```
  Pick: ticker, company_name, rationale, conviction ("core"|"moonshot"), member
  PortfolioHolding: ticker, company_name, conviction, weight (%), nominated_by, rationale
  CommitteeRun: run_id, timestamp, claude_picks, gpt_picks, gemini_picks, portfolio
  AdvisorResponse: ticker, company_name, recommendation, claude_take, gpt_take, gemini_take, fits_philosophy
  ```

- **API keys**: Loaded from a `.env` file via `python-dotenv`. Not committed. An `.env.example` is provided.

- **Models used**: `claude-opus-4-7` for Claude, `gpt-4o` for GPT, `gemini-2.0-flash` for Gemini.

---

## Testing Decisions

Good tests for this project verify external behavior, not implementation details. The key behaviors worth testing are:

- **Aggregator** (`build_portfolio`): Pure function with no I/O — highest value to test. Verify consensus weighting math, that moonshots receive lower base weight, that sorting is correct (consensus core → solo core → moonshots), and that weights sum to 100%.
- **Runner persistence**: Verify that a `CommitteeRun` round-trips cleanly to/from JSON (i.e., `model_dump_json` → `model_validate` produces an equal object).
- **Advisor recommendation logic**: Test the voting logic in isolation — given mocked member opinions, verify the correct final recommendation is produced (both say buy → buy; one says watch → watch; both say pass → pass; ticker in portfolio → "already in portfolio").

AI member modules and performance fetching are not worth unit testing — their behavior is defined by external APIs and live data.

---

## Out of Scope

- **Brokerage API integration** — trades are executed manually in SoFi; no read or write access to brokerage accounts
- **Portfolio tracking of actual holdings** — the performance tracker models the committee's recommended weights, not the user's actual executed positions
- **Push notifications or scheduling** — the tool is run on demand; no cron jobs or alerts
- **Multi-user support** — single-user local tool only
- **Mobile or hosted deployment** — runs locally via `streamlit run app.py`
- **Richer historical analysis** — the "What Changed" tab shows a summary table of all runs and supports comparing any two runs, but deeper trend analysis (e.g. weight trajectories over time) is not implemented

---

## Further Notes

- The `Optional` import in `models.py` is currently unused and can be removed once models stabilize.
- The advisor recommendation voting logic is intentionally permissive ("buy" wins over "watch" wins over "pass"). With 3 members this means a single "buy" vote can override two "pass" votes — a majority-vote model would be more conservative if needed.
