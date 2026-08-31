# Fantasy Football Draft-Prep Pipeline

A personal fantasy football tool that blends per-stat player projections from multiple free sources, calibrates per-source weights on historical projection vs. actual data, scores with custom league scoring rules, and ranks players by Value Over Replacement (VOR) plus week-to-week consistency metrics — outputting a draft board (CSV + terminal table).

## Installation

```bash
cd projects/fantasy-football
uv sync
```

## CLI Commands

```bash
# Ingest historical actual stats (nflreadpy)
uv run ffdraft ingest actuals --seasons 2008-2025

# Ingest historical archived projections
uv run ffdraft ingest archives

# Fetch current-season projections from multiple sources
uv run ffdraft ingest snapshot --season 2026 [--sources sleeper,espn]

# Calibrate per-source projection weights via cross-validation
uv run ffdraft calibrate [--lambda 0.35]

# Generate final draft board
uv run ffdraft board --out board.csv
```

## Development

- **Tests:** `uv run -m pytest -v`
- **Format:** `uvx ruff format .`
- **Import sorting:** `uvx ruff check --select I --fix .`
