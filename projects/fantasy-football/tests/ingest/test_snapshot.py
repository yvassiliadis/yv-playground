"""Tests for ffdraft.ingest.snapshot -- proves the try/except-continue
behavior genuinely tolerates one source raising, rather than merely
asserting it does."""

import polars as pl

from ffdraft.ingest import snapshot
from ffdraft.sources.base import CANONICAL_COLUMNS


def _canonical_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [2026],
            "week": [0],
            "source": ["stub"],
            "snapshot_date": [None],
            "source_player_id": ["1"],
            "player_id": ["00-1111"],
            "player_name_raw": ["Someone"],
            "team": ["KC"],
            "position": ["QB"],
            "stat_name": ["passing yard"],
            "stat_value": [100.0],
        }
    )


class _WorkingSource:
    def __init__(self, name: str):
        self.name = name
        self.calls = 0

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame:
        self.calls += 1
        return _canonical_df()


class _FailingSource:
    name = "broken"

    def fetch(self, season: int, week: int = 0) -> pl.DataFrame:
        raise RuntimeError("simulated upstream failure")


class TestRunSnapshot:
    def test_one_failing_source_does_not_stop_the_others(
        self, monkeypatch, tmp_path, capsys
    ):
        working_a = _WorkingSource("working_a")
        working_b = _WorkingSource("working_b")
        failing = _FailingSource()
        monkeypatch.setattr(
            snapshot,
            "REGISTRY",
            {"working_a": working_a, "broken": failing, "working_b": working_b},
        )

        result = snapshot.run_snapshot(season=2026, out_dir=tmp_path)

        # both working sources actually ran (not just "would have run") --
        # this is the genuine-tolerance assertion, not just a summary check.
        assert working_a.calls == 1
        assert working_b.calls == 1
        assert result["succeeded"] == ["working_a", "working_b"]
        assert result["failed"] == ["broken"]

    def test_summary_line_matches_expected_wording(self, monkeypatch, tmp_path, capsys):
        working = _WorkingSource("working")
        failing = _FailingSource()
        monkeypatch.setattr(
            snapshot, "REGISTRY", {"working": working, "broken": failing}
        )

        snapshot.run_snapshot(season=2026, out_dir=tmp_path)

        captured = capsys.readouterr()
        assert "1/2 sources succeeded" in captured.out

    def test_all_sources_succeed_summary(self, monkeypatch, tmp_path, capsys):
        working_a = _WorkingSource("working_a")
        working_b = _WorkingSource("working_b")
        monkeypatch.setattr(
            snapshot, "REGISTRY", {"working_a": working_a, "working_b": working_b}
        )

        snapshot.run_snapshot(season=2026, out_dir=tmp_path)

        captured = capsys.readouterr()
        assert "2/2 sources succeeded" in captured.out

    def test_failing_source_writes_no_output_file(self, monkeypatch, tmp_path):
        failing = _FailingSource()
        monkeypatch.setattr(snapshot, "REGISTRY", {"broken": failing})

        snapshot.run_snapshot(season=2026, out_dir=tmp_path)

        assert not any(tmp_path.rglob("*.parquet"))

    def test_successful_source_writes_partitioned_parquet(self, monkeypatch, tmp_path):
        working = _WorkingSource("working")
        monkeypatch.setattr(snapshot, "REGISTRY", {"working": working})

        snapshot.run_snapshot(season=2026, week=0, out_dir=tmp_path)

        written = list(tmp_path.rglob("*.parquet"))
        assert len(written) == 1
        assert "source=working" in str(written[0])
        assert "season=2026" in str(written[0])
        assert "week=0" in str(written[0])

        df = pl.read_parquet(written[0])
        assert set(CANONICAL_COLUMNS) <= set(df.columns)

    def test_sources_filter_only_runs_requested_sources(self, monkeypatch, tmp_path):
        working_a = _WorkingSource("working_a")
        working_b = _WorkingSource("working_b")
        monkeypatch.setattr(
            snapshot, "REGISTRY", {"working_a": working_a, "working_b": working_b}
        )

        result = snapshot.run_snapshot(
            season=2026, sources=["working_a"], out_dir=tmp_path
        )

        assert working_a.calls == 1
        assert working_b.calls == 0
        assert result["succeeded"] == ["working_a"]

    def test_unknown_source_name_counts_as_failed(self, monkeypatch, tmp_path):
        working = _WorkingSource("working")
        monkeypatch.setattr(snapshot, "REGISTRY", {"working": working})

        result = snapshot.run_snapshot(
            season=2026, sources=["working", "nonexistent"], out_dir=tmp_path
        )

        assert result["succeeded"] == ["working"]
        assert result["failed"] == ["nonexistent"]
