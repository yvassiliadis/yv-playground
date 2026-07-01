from datetime import datetime, timezone

import slack_poll


def test_flag_computes_from_iso2():
    assert slack_poll.flag("Mexico") == "🇲🇽"
    assert slack_poll.flag("Ecuador") == "🇪🇨"


def test_flag_handles_footballdata_name_variants():
    assert slack_poll.flag("United States") == "🇺🇸"
    assert slack_poll.flag("DR Congo") == "🇨🇩"


def test_flag_unknown_team_returns_empty():
    assert slack_poll.flag("Wakanda") == ""


def test_todays_fixtures_filters_to_et_date_and_sorts():
    matches = [
        {"id": 2, "homeTeam": {"name": "England"}, "awayTeam": {"name": "DR Congo"},
         "utcDate": "2026-07-01T22:00:00Z", "stage": "LAST_32"},
        {"id": 1, "homeTeam": {"name": "Mexico"}, "awayTeam": {"name": "Ecuador"},
         "utcDate": "2026-07-01T19:00:00Z", "stage": "LAST_32"},
        {"id": 3, "homeTeam": {"name": "Spain"}, "awayTeam": {"name": "Japan"},
         "utcDate": "2026-07-02T19:00:00Z", "stage": "LAST_32"},
    ]
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)  # noon UTC = 8am ET, July 1
    out = slack_poll.todays_fixtures(matches, now)
    assert [g["game_id"] for g in out] == ["1", "2"]
    assert out[0]["home"] == "Mexico"
    assert out[0]["away"] == "Ecuador"
    assert out[0]["kickoff_utc"] == "2026-07-01T19:00:00Z"


def test_todays_fixtures_skips_matches_without_kickoff():
    matches = [{"id": 9, "homeTeam": {"name": "Spain"}, "awayTeam": {"name": "Japan"}}]
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert slack_poll.todays_fixtures(matches, now) == []
