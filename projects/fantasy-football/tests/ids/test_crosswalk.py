"""Tests for ffdraft.ids.crosswalk -- no network, nflreadpy is fully stubbed."""

import polars as pl
import pytest

from ffdraft.ids import crosswalk
from ffdraft.ids.crosswalk import apply_overrides, build_crosswalk, resolve_by_name


@pytest.fixture
def ff_playerids_fixture() -> pl.DataFrame:
    """Small synthetic stand-in for nflreadpy.load_ff_playerids()."""
    return pl.DataFrame(
        {
            "gsis_id": ["00-0033873", "00-0034796", None],
            "sleeper_id": [4046, 522, 9999],
            "espn_id": [3139477, 15847, 55555],
            "fantasypros_id": [16420, 10731, None],
            "name": ["Patrick Mahomes", "Travis Kelce", "Nobody Special"],
            "position": ["QB", "TE", "WR"],
            "team": ["KC", "KC", "FA"],
        }
    )


@pytest.fixture
def reference_fixture() -> pl.DataFrame:
    """Reference table already in build_crosswalk's output shape."""
    return pl.DataFrame(
        {
            "player_id": ["00-1111", "00-2222", "00-3333"],
            "sleeper_id": [1, 2, 3],
            "espn_id": [10, 20, 30],
            "fantasypros_id": [100, 200, 300],
            "name": ["Michael Thomas", "Michael Thomas", "Justin Jefferson"],
            "normalized_name": ["michael thomas", "michael thomas", "justin jefferson"],
            "position": ["WR", "WR", "WR"],
            "team": ["NO", "HOU", "MIN"],
        }
    )


class TestBuildCrosswalk:
    def test_calls_nflreadpy_and_renames_gsis_id(
        self, monkeypatch, ff_playerids_fixture
    ):
        monkeypatch.setattr(
            crosswalk.nfl, "load_ff_playerids", lambda: ff_playerids_fixture
        )

        result = build_crosswalk()

        assert result.columns == crosswalk.REFERENCE_COLUMNS
        assert result["player_id"].to_list() == ["00-0033873", "00-0034796", None]

    def test_adds_normalized_name_column(self, monkeypatch, ff_playerids_fixture):
        monkeypatch.setattr(
            crosswalk.nfl, "load_ff_playerids", lambda: ff_playerids_fixture
        )

        result = build_crosswalk()

        row = result.filter(pl.col("name") == "Patrick Mahomes").row(0, named=True)
        assert row["normalized_name"] == "patrick mahomes"


class TestResolveByName:
    def test_single_unambiguous_match(self, reference_fixture):
        result = resolve_by_name("Justin Jefferson", "WR", "MIN", reference_fixture)
        assert result == "00-3333"

    def test_matches_ignoring_suffix_and_punctuation(self, reference_fixture):
        # normalize_name should collapse "Justin Jefferson" and any
        # decorated variant to the same key before lookup.
        result = resolve_by_name("Justin  Jefferson", "wr", "MIN", reference_fixture)
        assert result == "00-3333"

    def test_tiebreaks_on_team_when_names_collide(self, reference_fixture):
        no_result = resolve_by_name("Michael Thomas", "WR", "NO", reference_fixture)
        hou_result = resolve_by_name("Michael Thomas", "WR", "HOU", reference_fixture)
        assert no_result == "00-1111"
        assert hou_result == "00-2222"
        assert no_result != hou_result

    def test_ambiguous_without_team_returns_none(self, reference_fixture):
        assert resolve_by_name("Michael Thomas", "WR", None, reference_fixture) is None

    def test_ambiguous_with_non_matching_team_returns_none(self, reference_fixture):
        # team is supplied but doesn't correspond to either candidate --
        # must not guess.
        assert resolve_by_name("Michael Thomas", "WR", "SEA", reference_fixture) is None

    def test_genuine_non_match_returns_none_not_raises(self, reference_fixture):
        result = resolve_by_name(
            "Totally Fictional Player", "RB", "DAL", reference_fixture
        )
        assert result is None

    def test_wrong_position_is_a_non_match(self, reference_fixture):
        # same normalized name, but no row at this position.
        assert (
            resolve_by_name("Justin Jefferson", "RB", "MIN", reference_fixture) is None
        )

    def test_unmatched_rows_can_be_collected_by_the_caller(self, reference_fixture):
        """Documents the chosen unmatched-report mechanism: resolve_by_name
        returns None, and callers filter their own frame for it."""
        inputs = pl.DataFrame(
            {
                "player_name_raw": ["Justin Jefferson", "Totally Fictional Player"],
                "position": ["WR", "RB"],
                "team": ["MIN", "DAL"],
            }
        )
        resolved = inputs.with_columns(
            pl.struct(["player_name_raw", "position", "team"])
            .map_elements(
                lambda row: resolve_by_name(
                    row["player_name_raw"],
                    row["position"],
                    row["team"],
                    reference_fixture,
                ),
                return_dtype=pl.String,
            )
            .alias("player_id")
        )
        unmatched = resolved.filter(pl.col("player_id").is_null())
        assert unmatched.height == 1
        assert unmatched["player_name_raw"].item() == "Totally Fictional Player"

    def test_dst_resolves_via_normalize_dst_without_reference_lookup(
        self, reference_fixture
    ):
        result = resolve_by_name("49ers D/ST", "DST", None, reference_fixture)
        assert result == "DST_SF"

    def test_dst_uses_team_column_when_name_is_uninformative(self, reference_fixture):
        result = resolve_by_name(
            "Defense/Special Teams", "DEF", "KC", reference_fixture
        )
        assert result == "DST_KC"


class TestApplyOverrides:
    @pytest.fixture
    def base_df(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "source": ["sleeper", "sleeper", "espn"],
                "source_player_id": ["111", "222", "abc"],
                "player_id": ["00-1111", None, "00-3333"],
                "player_name_raw": ["Wrong Player", "Some Rookie", "Right Player"],
                "position": ["WR", "RB", "TE"],
            }
        )

    def test_no_data_rows_returns_df_unchanged(self, tmp_path, base_df):
        overrides_path = tmp_path / "overrides.csv"
        overrides_path.write_text(
            "source,source_player_id,player_id,player_name,position,team,note\n"
        )

        result = apply_overrides(base_df, overrides_path)

        assert result["player_id"].to_list() == base_df["player_id"].to_list()

    def test_override_by_source_and_source_player_id_wins(self, tmp_path, base_df):
        overrides_path = tmp_path / "overrides.csv"
        overrides_path.write_text(
            "source,source_player_id,player_id,player_name,position,team,note\n"
            "sleeper,111,00-9999,Corrected Player,WR,SEA,fixed bad auto-match\n"
        )

        result = apply_overrides(base_df, overrides_path)

        row = result.filter(pl.col("source_player_id") == "111").row(0, named=True)
        assert row["player_id"] == "00-9999"
        # other rows untouched
        other = result.filter(pl.col("source_player_id") == "abc").row(0, named=True)
        assert other["player_id"] == "00-3333"

    def test_override_fixes_a_previously_unmatched_row(self, tmp_path, base_df):
        overrides_path = tmp_path / "overrides.csv"
        overrides_path.write_text(
            "source,source_player_id,player_id,player_name,position,team,note\n"
            "sleeper,222,00-5555,Some Rookie,RB,ARI,manual add - rookie not in nflreadpy yet\n"
        )

        result = apply_overrides(base_df, overrides_path)

        row = result.filter(pl.col("source_player_id") == "222").row(0, named=True)
        assert row["player_id"] == "00-5555"

    def test_override_by_name_and_position_when_source_player_id_blank(
        self, tmp_path, base_df
    ):
        overrides_path = tmp_path / "overrides.csv"
        overrides_path.write_text(
            "source,source_player_id,player_id,player_name,position,team,note\n"
            ",,00-7777,Right Player,TE,KC,name-based override\n"
        )

        result = apply_overrides(base_df, overrides_path)

        row = result.filter(pl.col("player_name_raw") == "Right Player").row(
            0, named=True
        )
        assert row["player_id"] == "00-7777"

    def test_override_does_not_affect_rows_it_does_not_match(self, tmp_path, base_df):
        overrides_path = tmp_path / "overrides.csv"
        overrides_path.write_text(
            "source,source_player_id,player_id,player_name,position,team,note\n"
            "sleeper,111,00-9999,Corrected Player,WR,SEA,fixed bad auto-match\n"
        )

        result = apply_overrides(base_df, overrides_path)

        untouched = result.filter(pl.col("source_player_id") == "222").row(
            0, named=True
        )
        assert untouched["player_id"] is None
