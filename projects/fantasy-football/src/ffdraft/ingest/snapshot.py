"""Snapshot-ingestion orchestrator: run every registered projection source.

`run_snapshot` is deliberately tolerant of a single source failing (a
network blip, a source changing its response shape, a parsing bug in one
source's module) -- one bad source should never prevent the others from
writing their snapshot for the day. Each source's `fetch()` call is wrapped
in its own `try/except Exception`, logged, and skipped; a summary line is
always printed at the end.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from ffdraft.sources import REGISTRY

logger = logging.getLogger(__name__)

RAW_PROJECTIONS_DIR = Path("data/raw/projections")


def run_snapshot(
    season: int,
    week: int = 0,
    sources: list[str] | None = None,
    out_dir: Path = RAW_PROJECTIONS_DIR,
) -> dict[str, list[str]]:
    """Fetch every source in `sources` (or all of `REGISTRY` if omitted).

    Writes each successful source's output to
    `<out_dir>/source=<src>/season=<season>/week=<week>/snap_<date>.parquet`
    and returns `{"succeeded": [...], "failed": [...]}`. Always prints a
    final `"{N}/{M} sources succeeded"` summary line, where M is the number
    of sources attempted (including any unknown name in `sources`) and N is
    how many actually produced output.
    """
    names = sources if sources is not None else list(REGISTRY)
    snapshot_date = dt.datetime.now(tz=dt.UTC).date()

    succeeded: list[str] = []
    failed: list[str] = []

    for name in names:
        source = REGISTRY.get(name)
        if source is None:
            logger.warning("run_snapshot(): unknown source %r, skipping", name)
            failed.append(name)
            continue

        try:
            df = source.fetch(season, week)
        except Exception as exc:  # noqa: BLE001 - a bad source must not stop the rest
            logger.warning(
                "run_snapshot(): source %r failed: %s: %s",
                name,
                type(exc).__name__,
                exc,
            )
            failed.append(name)
            continue

        part_dir = out_dir / f"source={name}" / f"season={season}" / f"week={week}"
        part_dir.mkdir(parents=True, exist_ok=True)
        out_path = part_dir / f"snap_{snapshot_date}.parquet"
        df.write_parquet(out_path)
        succeeded.append(name)

    print(f"{len(succeeded)}/{len(names)} sources succeeded")
    return {"succeeded": succeeded, "failed": failed}
