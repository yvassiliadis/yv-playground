"""Tests for scripts/weekly_snapshot.py -- season derivation, cron-safe paths,
and the exit-code contract."""

import datetime as dt
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS_PATH = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS_PATH) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_PATH))

import weekly_snapshot


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (dt.date(2026, 8, 30), 2026),  # mid-season
        (dt.date(2026, 12, 31), 2026),  # December still the same season
        (dt.date(2027, 1, 4), 2026),  # January playoffs: previous season
        (dt.date(2027, 2, 8), 2026),  # Super Bowl month: previous season
        (dt.date(2027, 3, 1), 2027),  # league year opens: new season
        (dt.date(2027, 7, 1), 2027),  # offseason, already the new season
    ],
)
def test_current_season_uses_nfl_season_year_not_calendar_year(today, expected):
    assert weekly_snapshot.current_season(today) == expected


def test_weekly_snapshot_passes_absolute_out_dir():
    """Cron runs with an arbitrary CWD, so out_dir must be absolute."""
    with patch("weekly_snapshot.run_snapshot") as mock_run_snapshot:
        mock_run_snapshot.return_value = {"succeeded": ["source_a"], "failed": []}
        weekly_snapshot.main()

    kwargs = mock_run_snapshot.call_args.kwargs
    assert kwargs["season"] == weekly_snapshot.current_season()
    assert kwargs["out_dir"].is_absolute()
    assert kwargs["out_dir"] == (
        Path(weekly_snapshot.__file__).resolve().parent.parent
        / "data"
        / "raw"
        / "projections"
    )


def test_weekly_snapshot_exits_zero_when_sources_succeed():
    with patch("weekly_snapshot.run_snapshot") as mock_run_snapshot:
        mock_run_snapshot.return_value = {
            "succeeded": ["source_a", "source_b"],
            "failed": [],
        }
        assert weekly_snapshot.main() == 0


def test_weekly_snapshot_exits_nonzero_when_all_sources_fail():
    with patch("weekly_snapshot.run_snapshot") as mock_run_snapshot:
        mock_run_snapshot.return_value = {"succeeded": [], "failed": ["source_a"]}
        assert weekly_snapshot.main() != 0
