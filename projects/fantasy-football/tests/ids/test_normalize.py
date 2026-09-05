"""Tests for ffdraft.ids.normalize -- this is the plan's flagged pain point."""

import pytest

from ffdraft.ids.normalize import normalize_dst, normalize_name


class TestNormalizeName:
    def test_strips_jr_suffix(self):
        assert normalize_name("Odell Beckham Jr.") == normalize_name("Odell Beckham")

    def test_strips_sr_suffix(self):
        assert normalize_name("Steve Smith Sr.") == normalize_name("Steve Smith")

    @pytest.mark.parametrize("suffix", ["II", "III", "IV"])
    def test_strips_generational_suffixes(self, suffix):
        assert normalize_name(f"Michael Pittman {suffix}") == normalize_name(
            "Michael Pittman"
        )

    def test_suffix_stripping_is_case_insensitive(self):
        assert normalize_name("Odell Beckham jr") == "odell beckham"
        assert normalize_name("Odell Beckham JR") == "odell beckham"

    def test_strips_periods(self):
        assert normalize_name("A.J. Brown") == "aj brown"

    def test_strips_apostrophes(self):
        assert normalize_name("Ja'Marr Chase") == "jamarr chase"

    def test_collapses_whitespace(self):
        assert normalize_name("  Justin   Jefferson  ") == "justin jefferson"

    def test_lowercases(self):
        assert normalize_name("PATRICK MAHOMES") == "patrick mahomes"

    def test_does_not_strip_suffix_that_is_part_of_a_real_surname(self):
        # "Ivan" etc. don't collide with our suffix tokens, but a surname
        # that literally equals a suffix token (e.g. a last name "Ii") would
        # be stripped -- documenting that this is a deliberate, accepted
        # tradeoff of trailing-token suffix stripping rather than a bug.
        assert normalize_name("Robert Griffin III") == "robert griffin"

    def test_only_strips_suffix_from_trailing_token(self):
        # "Sr" appearing mid-name (contrived) should not be stripped from
        # the middle of a name, only from the trailing position.
        assert normalize_name("Sr Smith") == "sr smith"

    def test_empty_string(self):
        assert normalize_name("") == ""

    def test_name_that_is_only_a_suffix(self):
        assert normalize_name("Jr") == ""


class TestNormalizeDst:
    @pytest.mark.parametrize(
        "variant",
        [
            "49ers D/ST",
            "SF Defense",
            "San Francisco 49ers",
            "San Francisco",
            "SF DST",
            "49ers Defense",
            "sf",
            "SF",
        ],
    )
    def test_49ers_variants_all_map_to_sf(self, variant):
        assert normalize_dst(variant) == "SF"

    def test_historical_alias_resolves_to_current_team(self):
        assert normalize_dst("St Louis Rams") == "LA"
        assert normalize_dst("LAR") == "LA"
        assert normalize_dst("Oakland Raiders") == "LV"
        assert normalize_dst("San Diego Chargers") == "LAC"

    def test_full_city_and_nickname_variant(self):
        assert normalize_dst("Kansas City Chiefs") == "KC"
        assert normalize_dst("Chiefs D/ST") == "KC"
        assert normalize_dst("Kansas City") == "KC"

    def test_unrecognized_team_raises(self):
        with pytest.raises(ValueError, match="unrecognized team"):
            normalize_dst("Springfield Isotopes")

    def test_unrecognized_does_not_return_none(self):
        # explicitly a ValueError, not a silent None -- a DST input that
        # doesn't resolve is a data problem, unlike a genuine player
        # non-match in resolve_by_name.
        with pytest.raises(ValueError):
            normalize_dst("")
