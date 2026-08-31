"""Tests for ffdraft.board."""

from __future__ import annotations

import polars as pl
import pytest

from ffdraft import board, derive

RULES = {"reception": 1.0}


def _blended(rows: list[tuple]) -> pl.DataFrame:
    """A synthetic `apply_blend` output: one row per (season, player_id)."""
    return pl.DataFrame(
        [
            {
                "season": season,
                "player_id": player_id,
                "position": position,
                "EP": ep,
                "team": team,
                "player_name_raw": name,
            }
            for season, player_id, position, ep, team, name in rows
        ]
    )


def _weekly_actuals(rows: list[tuple]) -> pl.DataFrame:
    """Canonical long-schema weekly actuals for `compute_consistency`."""
    return pl.DataFrame(
        [
            {
                "season": season,
                "week": week,
                "source": "nflverse",
                "snapshot_date": "2025-01-01",
                "source_player_id": player_id,
                "player_id": player_id,
                "player_name_raw": player_id,
                "team": "SF",
                "position": position,
                "stat_name": "reception",
                "stat_value": float(value),
            }
            for season, week, player_id, position, value in rows
        ]
    )


@pytest.fixture
def blended_df():
    return _blended(
        [
            (2025, "wr1", "WR", 200.0, "SF", "WR One"),
            (2025, "wr2", "WR", 150.0, "SF", "WR Two"),
            (2025, "rb1", "RB", 180.0, "KC", "RB One"),
            (2025, "rb2", "RB", 90.0, "KC", "RB Two"),
            # Rookie: no weekly actuals at all, must still get a board row.
            (2025, "wr3", "WR", 60.0, "NE", "WR Rookie"),
        ]
    )


@pytest.fixture
def adp_df():
    return pl.DataFrame(
        {
            "player_id": ["wr1", "wr2", "wr3", "rb1", "rb2"],
            "position": ["WR", "WR", "WR", "RB", "RB"],
            "adp": [1, 2, 50, 3, 40],
        }
    )


@pytest.fixture
def weekly_actuals_df():
    # 4 qualifying weeks each for wr1/wr2/rb1/rb2 (>= MIN_QUALIFYING_WEEKS).
    return _weekly_actuals(
        [
            (2024, week, pid, pos, value)
            for pid, pos, values in [
                ("wr1", "WR", [20.0, 18.0, 25.0, 15.0]),
                ("wr2", "WR", [14.0, 12.0, 16.0, 10.0]),
                ("rb1", "RB", [22.0, 19.0, 21.0, 18.0]),
                ("rb2", "RB", [8.0, 9.0, 7.0, 6.0]),
            ]
            for week, value in enumerate(values, start=1)
        ]
    )


def test_build_board_columns(
    monkeypatch, blended_df, adp_df, weekly_actuals_df, tmp_path
):
    monkeypatch.setattr(board, "apply_blend", lambda *a, **k: blended_df)

    result = board.build_board(
        projections=pl.DataFrame(),
        weights_path=tmp_path / "unused.parquet",
        adp=adp_df,
        weekly_actuals=weekly_actuals_df,
        consistency_seasons=[2024],
        rules=RULES,
        teams=1,
    )

    assert result.columns == board.BOARD_COLUMNS
    assert result.height == blended_df.height


def test_build_board_sorted_by_vor_desc_with_rank_assigned(
    monkeypatch, blended_df, adp_df, weekly_actuals_df, tmp_path
):
    monkeypatch.setattr(board, "apply_blend", lambda *a, **k: blended_df)

    result = board.build_board(
        projections=pl.DataFrame(),
        weights_path=tmp_path / "unused.parquet",
        adp=adp_df,
        weekly_actuals=weekly_actuals_df,
        consistency_seasons=[2024],
        rules=RULES,
        teams=1,
    )

    vor_values = result["vor"].to_list()
    assert vor_values == sorted(vor_values, reverse=True)
    assert result["rank"].to_list() == list(range(1, result.height + 1))
    # Rank 1 is the highest-VOR player.
    assert (
        result.row(0, named=True)["player"]
        == result.sort("vor", descending=True).row(0, named=True)["player"]
    )


def test_build_board_carries_consistency_columns_and_nulls_for_rookies(
    monkeypatch, blended_df, adp_df, weekly_actuals_df, tmp_path
):
    monkeypatch.setattr(board, "apply_blend", lambda *a, **k: blended_df)

    result = board.build_board(
        projections=pl.DataFrame(),
        weights_path=tmp_path / "unused.parquet",
        adp=adp_df,
        weekly_actuals=weekly_actuals_df,
        consistency_seasons=[2024],
        rules=RULES,
        teams=1,
    )

    wr1_row = result.filter(pl.col("player") == "WR One").row(0, named=True)
    assert wr1_row["stddev"] is not None
    assert wr1_row["floor"] is not None
    assert wr1_row["ceiling"] is not None
    assert wr1_row["pct_weeks_above_baseline"] is not None

    rookie_row = result.filter(pl.col("player") == "WR Rookie").row(0, named=True)
    assert rookie_row["stddev"] is None
    assert rookie_row["floor"] is None
    assert rookie_row["ceiling"] is None
    assert rookie_row["pct_weeks_above_baseline"] is None
    # Still present in the board, with an EP/VOR, despite no weekly history.
    assert rookie_row["ep"] == pytest.approx(60.0)


def test_build_board_includes_adp_and_alt_vor_columns(
    monkeypatch, blended_df, adp_df, weekly_actuals_df, tmp_path
):
    monkeypatch.setattr(board, "apply_blend", lambda *a, **k: blended_df)

    result = board.build_board(
        projections=pl.DataFrame(),
        weights_path=tmp_path / "unused.parquet",
        adp=adp_df,
        weekly_actuals=weekly_actuals_df,
        consistency_seasons=[2024],
        rules=RULES,
        teams=1,
    )

    wr1_row = result.filter(pl.col("player") == "WR One").row(0, named=True)
    assert wr1_row["adp"] == 1
    assert wr1_row["risk_adjusted_vor"] is not None
    assert wr1_row["roster_math_vor"] is not None
    assert wr1_row["positional_zscore"] is not None


# ---------------------------------------------------------------------------
# derive.py wiring (C2): the miss was the wiring, so these prove the wiring,
# not derive.py's own already-tested maths.
# ---------------------------------------------------------------------------


def _projection_rows(rows: list[tuple]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "season": 2025,
                "week": 0,
                "source": source,
                "snapshot_date": "2025-08-01",
                "source_player_id": player_id,
                "player_id": player_id,
                "player_name_raw": player_id,
                "team": "SF",
                "position": position,
                "stat_name": stat_name,
                "stat_value": float(value),
            }
            for source, player_id, position, stat_name, value in rows
        ]
    )


def test_build_board_passes_projections_to_apply_blend_unaugmented(
    monkeypatch, blended_df, adp_df, tmp_path
):
    """1st-down derivation is deliberately unwired (it double-counted against
    the calibrators' implicit uplift), so projections must reach `apply_blend`
    exactly as handed in."""
    captured: dict[str, pl.DataFrame] = {}

    def _capture(projections, *a, **k):
        captured["projections"] = projections
        return blended_df

    monkeypatch.setattr(board, "apply_blend", _capture)

    projections = _projection_rows([("sleeper", "rb1", "RB", "rushing yard", 1000.0)])
    history = _projection_rows(
        [
            ("nflverse", "rb1", "RB", "rushing yard", 1000.0),
            ("nflverse", "rb1", "RB", "rushing 1st down", 100.0),
        ]
    ).with_columns(pl.lit(2024).alias("season"), pl.lit(1).alias("week"))

    board.build_board(
        projections=projections,
        weights_path=tmp_path / "unused.parquet",
        adp=adp_df,
        weekly_actuals=history,
        consistency_seasons=[2024],
        rules={"rushing yard": 0.1, "rushing 1st down": 0.5},
        teams=1,
    )

    assert captured["projections"].equals(projections)


def test_build_board_adds_dst_rows_derived_from_historical_team_stats(
    monkeypatch, adp_df, tmp_path
):
    """FantasyPros is the only DST source and usually fails, so the board must
    fall back to `derive.derive_dst_seasonal_ep` rather than shipping no DSTs."""
    blended_without_dst = _blended([(2025, "wr1", "WR", 200.0, "SF", "WR One")])
    monkeypatch.setattr(board, "apply_blend", lambda *a, **k: blended_without_dst)

    dst_history = pl.DataFrame(
        [
            {
                "season": 2024,
                "week": week,
                "source": "nflverse",
                "snapshot_date": "2025-01-01",
                "source_player_id": "DST_SF",
                "player_id": "DST_SF",
                "player_name_raw": "SF",
                "team": "SF",
                "position": "DST",
                "stat_name": "defense 3 and out",
                "stat_value": 4.0,
            }
            for week in range(1, 5)
        ]
    )

    result = board.build_board(
        projections=pl.DataFrame(),
        weights_path=tmp_path / "unused.parquet",
        adp=adp_df,
        weekly_actuals=dst_history,
        consistency_seasons=[2024],
        rules={"defense 3 and out": 0.25},
        teams=1,
    )

    dst_rows = result.filter(pl.col("pos") == "DST")
    assert dst_rows.height == 1, "no DST row was derived into the board"
    row = dst_rows.row(0, named=True)
    assert row["player"] == "SF DST"
    assert row["team"] == "SF"
    # 4 three-and-outs/game * 0.25 pts * 17 games.
    assert row["ep"] == pytest.approx(4.0 * 0.25 * derive.GAMES_PER_SEASON)
