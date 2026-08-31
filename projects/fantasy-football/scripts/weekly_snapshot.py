#!/usr/bin/env python3
"""Weekly projection snapshot cron script.

Thin wrapper around ffdraft.ingest.snapshot.run_snapshot() for cron invocation.
Runs the snapshot for the current season (identified by year) and exits with
code 0 if at least one source succeeded, non-zero if all sources failed.
Output is written to stdout/stderr suitable for cron mailbox delivery.
"""

import datetime as dt
import sys
from pathlib import Path

# Add parent directory to path so we can import ffdraft
sys.path.insert(0, str(Path(__file__).parent.parent))

from ffdraft.ingest.snapshot import run_snapshot


def main() -> int:
    """Run snapshot for current season, return exit code."""
    season = dt.datetime.now().year
    result = run_snapshot(season=season)
    succeeded_count = len(result["succeeded"])

    # Exit 0 if at least one source succeeded, non-zero otherwise
    return 0 if succeeded_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
