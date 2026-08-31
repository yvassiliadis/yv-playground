#!/usr/bin/env python3
"""Weekly projection snapshot cron script.

Thin wrapper around ffdraft.ingest.snapshot.run_snapshot() for cron invocation.
Runs the snapshot for the current NFL season and exits with code 0 if at least
one source succeeded, non-zero if all sources failed. Output is written to
stdout/stderr suitable for cron mailbox delivery.

Cron safety
-----------
Cron runs a job with an arbitrary working directory, so nothing here may
depend on the process's CWD:

* `OUT_DIR` is absolute, derived from this file's own location, because
  `run_snapshot`'s `out_dir` default (`Path("data/raw/projections")`) is
  relative -- under cron that would silently write the snapshot into
  whatever directory cron happened to start in, while still exiting 0.
* `season()` derives the NFL season from the month, not just the year: the
  2025 season runs into January 2026, so a January-June run must report the
  previous calendar year. March is the cut-over (the league year starts in
  mid-March), so Jan/Feb of year Y still belong to season Y-1.

This script does no `sys.path` manipulation. `ffdraft` is installed into the
environment by `uv sync` (src layout, so the project root is not itself an
importable package directory) -- run it as `uv run scripts/weekly_snapshot.py`
or with the project's venv interpreter.
"""

import datetime as dt
import sys
from pathlib import Path

from ffdraft.ingest.snapshot import run_snapshot

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data" / "raw" / "projections"

#: First month of a new NFL season year (the league year opens mid-March).
SEASON_START_MONTH = 3


def current_season(today: dt.date | None = None) -> int:
    """The NFL season year for `today` (defaults to the local current date)."""
    today = today or dt.datetime.now(tz=dt.UTC).date()
    return today.year if today.month >= SEASON_START_MONTH else today.year - 1


def main() -> int:
    """Run snapshot for the current season, return exit code."""
    result = run_snapshot(season=current_season(), out_dir=OUT_DIR)
    succeeded_count = len(result["succeeded"])

    # Exit 0 if at least one source succeeded, non-zero otherwise
    return 0 if succeeded_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
