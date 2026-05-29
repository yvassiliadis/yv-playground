# My Big Fat Greek Portfolio

An AI investment committee that debates stock picks. Three models — Claude, Gemini, and GPT — each independently research the market, nominate a portfolio, and get cross-examined on individual tickers. The app aggregates their picks into a consensus portfolio and tracks performance over time.

Built as a showcase of how I use LLMs iteratively to ship a real product. See [`docs/planning/`](docs/planning/) for the PRD and design plans that drove each major iteration.

## Screenshots

<table>
<tr>
  <td><img src="docs/screenshots/portfolio-treemap.png"><br><sub>Portfolio · allocation map</sub></td>
  <td><img src="docs/screenshots/portfolio-signals.png"><br><sub>Portfolio · signal strip</sub></td>
  <td><img src="docs/screenshots/portfolio-drawer.png"><br><sub>Stock detail drawer</sub></td>
</tr>
<tr>
  <td><img src="docs/screenshots/performance.png"><br><sub>Performance vs. benchmarks</sub></td>
  <td><img src="docs/screenshots/members.png"><br><sub>Committee members</sub></td>
  <td></td>
</tr>
</table>

## What it does

- **Committee runs** — all three models screen a universe of stocks, do independent macro research, and nominate picks with conviction levels and variant perception theses
- **Portfolio view** — treemap of the consensus portfolio weighted by cross-member conviction
- **Advisor** — ask the committee for an opinion on any ticker; responses are cached and logged
- **Performance** — portfolio vs. benchmarks (SPY, VGT, VTI) over a rolling 1-year window
- **Exclusions** — live filtering of tickers or sectors from the portfolio, applied without re-running

## How it works

```mermaid
flowchart TD
    U(["User"]) -->|"Run committee"| FV
    U -->|"Ask about a ticker"| TICK

    subgraph SCREEN["① Screen Universe"]
        FV["Finviz<br/>GM >40% · D/E <1x · Cap >$2B · PEG <3"]
        FV -->|"ROE ≥ 20%"| SG[Suggestions]
        FV -->|"-15% < ROE < 0%"| FCF["yfinance FCF check<br/>async · max 30 concurrent"]
        FCF -->|passes| OP[Opportunities]
    end

    SG & OP --> FMT["format_for_prompt<br/>~100–500 tickers · identical input to all 3 models"]

    TICK --> FUND["yfinance fundamentals<br/>+ portfolio context"]

    subgraph LLM["② Parallel LLM Calls — Claude · GPT-4o · Gemini"]
        direction LR
        CL["web_search<br/>macro + sectors<br/>→ picks / opinion"]
        GP["web_search<br/>→ picks / opinion"]
        GE["GoogleSearch<br/>→ picks / opinion"]
    end

    FMT --> LLM
    FUND --> LLM

    LLM -->|committee run| ENR["③ yfinance enrichment<br/>price + analyst targets → upside %"]
    LLM -->|advisor| VOTE["Vote → buy / watch / pass<br/>per-member take + allocation %"]

    ENR --> AGG["④ Aggregate<br/>drop < 2-member tickers<br/>weight = conviction × members × upside<br/>normalize → 100%"]

    AGG --> PORT[("Portfolio<br/>data/runs/*.json")]
    PORT --> VIEW["Portfolio View · History"]

    PORT -.->|"portfolio context"| FUND
```

## Getting started

### Demo mode (no API keys needed)

```bash
uv run uvicorn api:app --reload --port 8000
```

If no API keys are configured, the app seeds itself from the example data in `data/*.example.*` and runs in read-only mode. Live committee runs and advisor queries are disabled.

### Full mode

Create a `.env` file:

```
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GOOGLE_API_KEY=...
```

Then:

```bash
uv run uvicorn api:app --reload --port 8000
```

## Tech stack

- **Backend** — FastAPI, `anthropic`, `openai`, `google-genai`, `yfinance`
- **Frontend** — vanilla JS, custom CSS design system, Chart.js, no framework
- **Data** — yfinance for screening and performance; AI APIs for research and picks
- **Legacy** — Streamlit prototype in `scripts/app.py`

## Project structure

```
api.py              FastAPI app and all endpoints
src/                backend logic
  committee/        one module per AI member + aggregator
  advisor.py        per-ticker committee opinion
  screener.py       universe screening via yfinance
  performance.py    portfolio vs benchmark returns
  runner.py         full committee run orchestration
  demo.py           demo mode seeding and detection
static/             frontend (HTML, CSS, JS)
data/               caches, run history, exclusions
  *.example.*       seed data for demo mode
scripts/            one-off run and benchmark scripts
docs/
  planning/         PRD, design plans, prototype
  screenshots/      product screenshots
  quorum-pitch.*    product one-pager (HTML + PDF)
tests/
```

## Planning process

This project was built iteratively using Claude Code. The [`docs/planning/`](docs/planning/) folder contains the PRD and design documents that shaped each major phase — a real record of how I work with LLMs to go from idea to shipped product.

## Product pitch

A two-page product one-pager describing this as a commercial offering — [`docs/quorum-pitch.pdf`](docs/quorum-pitch.pdf).

<table>
<tr>
  <td><img src="docs/screenshots/pitch-page1.png"></td>
  <td><img src="docs/screenshots/pitch-page2.png"></td>
</tr>
</table>
