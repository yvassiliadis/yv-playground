"""Smoke test for scripts/weekly_snapshot.py -- confirm it calls run_snapshot
with the current season and exits appropriately based on success count."""

import datetime as dt
from unittest.mock import patch


def test_weekly_snapshot_calls_run_snapshot_with_current_season():
    """Smoke test: weekly_snapshot calls run_snapshot with current season."""
    # Import here to avoid early import issues
    import sys
    from pathlib import Path

    # Add scripts to path
    scripts_path = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_path))
    from weekly_snapshot import main

    current_season = dt.datetime.now().year

    with patch("weekly_snapshot.run_snapshot") as mock_run_snapshot:
        mock_run_snapshot.return_value = {"succeeded": ["source_a"], "failed": []}
        exit_code = main()

        mock_run_snapshot.assert_called_once_with(season=current_season)
        assert exit_code == 0


def test_weekly_snapshot_exits_zero_when_sources_succeed():
    """Smoke test: weekly_snapshot exits 0 when at least one source succeeded."""
    import sys
    from pathlib import Path

    scripts_path = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_path))
    from weekly_snapshot import main

    with patch("weekly_snapshot.run_snapshot") as mock_run_snapshot:
        mock_run_snapshot.return_value = {
            "succeeded": ["source_a", "source_b"],
            "failed": [],
        }
        exit_code = main()
        assert exit_code == 0


def test_weekly_snapshot_exits_nonzero_when_all_sources_fail():
    """Smoke test: weekly_snapshot exits non-zero when all sources failed."""
    import sys
    from pathlib import Path

    scripts_path = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_path))
    from weekly_snapshot import main

    with patch("weekly_snapshot.run_snapshot") as mock_run_snapshot:
        mock_run_snapshot.return_value = {"succeeded": [], "failed": ["source_a"]}
        exit_code = main()
        assert exit_code != 0
