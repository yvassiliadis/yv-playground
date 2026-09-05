# Fantasy Football Draft-Prep Pipeline

A personal fantasy football tool that blends per-stat player projections from multiple free sources, calibrates per-source weights on historical projection vs. actual data, scores with custom league scoring rules, and ranks players by Value Over Replacement (VOR) plus week-to-week consistency metrics — outputting a draft board (CSV + terminal table).

## Installation

```bash
cd projects/fantasy-football
uv sync
```

## Data layout

The raw layer is hive-partitioned, append-only Parquet:

```
data/raw/projections/source=<src>/season=<yyyy>/week=<n>/snap_<date>.parquet
data/raw/actuals/season=<yyyy>/actuals.parquet
data/historical/{ffa,ffdp}.parquet
data/derived/source_weights.parquet
```

Because projections are spread across one file per source/season/snapshot, every
command that reads them takes a **glob pattern**, not a single file — e.g.
`'data/raw/projections/**/*.parquet'`. Quote the pattern so your shell passes it
through to Polars rather than expanding it itself.

Repeated ingest runs append rather than overwrite. That is safe: `scoring.score()`
runs `scoring.latest_snapshot_rows()` over everything it scores, keeping exactly
one row per `(season, week, source, player_id, stat_name)` — the newest
`snapshot_date`, and just one row even when several rows tie at that date (two
ingest runs on the same day). So a repeated snapshot of the same season never
double-counts, whether or not the re-run lands on a new date. Rows with no
`snapshot_date` column at all (e.g. `derive.py`'s synthesised DST frame) are
passed through untouched.

## CLI commands

```bash
# 1. Historical actual stats from nflverse (via nflreadpy).
#    Writes data/raw/actuals/season=<yyyy>/actuals.parquet
uv run ffdraft ingest actuals --seasons 2008-2025
uv run ffdraft ingest actuals --seasons 2023,2024        # comma-separated also works

# 2. Historical archived projections. --path is REQUIRED and points at a
#    directory containing already-downloaded 'ffa/' and 'ffdp/' subdirectories
#    (no network access; either subdirectory may be absent).
#    Writes data/historical/ffa.parquet and data/historical/ffdp.parquet
uv run ffdraft ingest archives --path data/archives

# 3. Current-season projections from every registered source (or a subset).
#    Writes one snap_<date>.parquet per source under data/raw/projections/
uv run ffdraft ingest snapshot --season 2026
uv run ffdraft ingest snapshot --season 2026 --week 0 --sources sleeper,espn

# 4. Calibrate per-source blend weights (LOSO across six calibrators).
#    NOTE: the command is `model calibrate`, not `calibrate`.
uv run ffdraft model calibrate \
  --projections 'data/historical/*.parquet' \
  --actuals 'data/raw/actuals/**/*.parquet' \
  --scoring-rules data/scoring_rules.csv \
  --lambda 0.35 \
  --out data/derived/source_weights.parquet

# 5. Draft board. Every option below except --teams/--scoring-rules/--weights
#    is required.
uv run ffdraft board \
  --projections 'data/raw/projections/source=*/season=2026/**/*.parquet' \
  --adp data/adp.csv \
  --actuals 'data/raw/actuals/**/*.parquet' \
  --consistency-seasons 2024-2025 \
  --weights data/derived/source_weights.parquet \
  --scoring-rules data/scoring_rules.csv \
  --teams 10 \
  --out board.csv

uv run ffdraft version
```

`--adp` has no ingestion command behind it: no task in this project sources
average draft position, so it must be a hand-maintained CSV or Parquet with
`player_id, position, adp` columns. If a position is missing from the top of that
file entirely, `compute_vor` logs a warning and falls back to the roster-math
replacement rank rather than silently emitting null VOR for everyone at that
position.

### Cron

`scripts/weekly_snapshot.py` wraps `ingest snapshot` for cron. It writes to an
absolute path derived from the script's own location and derives the NFL season
from the month (January–February belong to the previous season), so it is safe to
invoke from any working directory:

```cron
0 6 * * 2 cd /path/to/projects/fantasy-football && uv run scripts/weekly_snapshot.py
```

## Known gaps

These are real, deliberate limitations, not bugs to be surprised by:

- **No kicker projections.** None of the eight source modules under
  `src/ffdraft/sources/` request the K position, so the board has **zero K rows**.
  `derive.derive_kicker_fg_buckets` is written and tested, ready to split a total
  FG-made stat into the scoring CSV's distance buckets, but it has no input until
  a future task adds a kicker-projecting source. See
  `metrics/vor.py`'s `K_DST_REPLACEMENT_N`.
- **DST projections depend on FantasyPros, which usually fails.** FantasyPros is
  the only source that projects team defenses, and its projection pages require an
  authenticated session — expect `ingest snapshot` to log a FantasyPros failure
  against real traffic. As a fallback, `board.build_board` fills in DST expected
  points from historical team stats via `derive.derive_dst_seasonal_ep`, so the
  board still has defenses on it; those rows are a decay-weighted historical
  extrapolation, not a projection.
- **1st downs are not scored at all.** No source reports rushing/receiving 1st
  downs (0.5 pts each), and `ingest/actuals.py` deliberately does not map
  nflreadpy's `rushing_first_downs`/`receiving_first_downs` either, so no 1st-down
  points enter projections *or* actuals. `derive.derive_first_downs` can estimate
  them from a shrunk historical per-volume rate and stays tested, but it is
  unwired: `model calibrate` trains on historical projection snapshots that carry
  no 1st-down rows, so scoring 1st downs only in the target makes the calibrators
  absorb an implicit uplift that an explicit board-time term would double-count.
  Wiring it back in needs a season-aware training-side rate table first. See
  `derive.py`'s module docstring.

## Development

- **Tests:** `uv run -m pytest -v`
- **Format:** `uvx ruff format .`
- **Import sorting:** `uvx ruff check --select I --fix .`
