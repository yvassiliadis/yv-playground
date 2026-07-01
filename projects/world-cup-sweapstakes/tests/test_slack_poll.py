from datetime import datetime, timezone
import json

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


def _state(now):
    fixtures = [{
        "game_id": "1", "home": "Mexico", "away": "Ecuador",
        "kickoff_utc": "2026-07-01T19:00:00Z", "stage": "LAST_32",
    }]
    return slack_poll.initial_poll_state(fixtures, now, header_blocks=[])


def test_initial_state_has_empty_votes():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    state = _state(now)
    assert state["date"] == "2026-07-01"
    assert state["games"]["1"]["votes"] == {}


def test_apply_vote_records_a_vote():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    state, changed, error = slack_poll.apply_vote(_state(now), "1", "U1", "home", now)
    assert changed is True and error is None
    assert state["games"]["1"]["votes"]["U1"] == "home"


def test_apply_vote_change_moves_vote():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    state = _state(now)
    state, _, _ = slack_poll.apply_vote(state, "1", "U1", "home", now)
    state, changed, error = slack_poll.apply_vote(state, "1", "U1", "away", now)
    assert changed is True and error is None
    assert state["games"]["1"]["votes"]["U1"] == "away"


def test_apply_vote_same_pick_is_noop():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    state = _state(now)
    state, _, _ = slack_poll.apply_vote(state, "1", "U1", "home", now)
    state, changed, error = slack_poll.apply_vote(state, "1", "U1", "home", now)
    assert changed is False and error is None


def test_apply_vote_after_kickoff_is_closed():
    build = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    after = datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc)  # past 19:00 kickoff
    state, changed, error = slack_poll.apply_vote(_state(build), "1", "U1", "home", after)
    assert changed is False and error == "closed"


def test_apply_vote_unknown_game():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    state, changed, error = slack_poll.apply_vote(_state(now), "999", "U1", "home", now)
    assert changed is False and error == "unknown"


def _game_state(votes):
    return {
        "header_blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*hdr*"}}],
        "games": {
            "1": {"home": "Mexico", "away": "Ecuador",
                  "kickoff_utc": "2026-07-01T19:00:00Z", "votes": votes},
        },
    }


def test_poll_blocks_has_two_buttons_with_encoded_values():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    blocks = slack_poll.poll_blocks(_game_state({})["games"], now)
    actions = [b for b in blocks if b["type"] == "actions"][0]
    assert len(actions["elements"]) == 2
    home_btn = actions["elements"][0]
    assert home_btn["action_id"] == "vote_1_home"
    assert json.loads(home_btn["value"]) == {"game_id": "1", "pick": "home"}


def test_poll_blocks_no_votes_shows_placeholder():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    blocks = slack_poll.poll_blocks(_game_state({})["games"], now)
    context = [b for b in blocks if b["type"] == "context"][0]
    assert "no votes yet" in context["elements"][0]["text"]


def test_poll_blocks_renders_voter_mentions():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    blocks = slack_poll.poll_blocks(_game_state({"U1": "home", "U2": "away"})["games"], now)
    context = [b for b in blocks if b["type"] == "context"][0]["elements"][0]["text"]
    assert "<@U1>" in context and "<@U2>" in context


def test_poll_blocks_empty_when_no_games():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    assert slack_poll.poll_blocks({}, now) == []


def test_build_message_blocks_prepends_header():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    blocks = slack_poll.build_message_blocks(_game_state({}), now)
    assert blocks[0]["text"]["text"] == "*hdr*"
    assert any(b["type"] == "actions" for b in blocks)


def test_build_message_blocks_header_only_when_no_games():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    state = {"header_blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*hdr*"}}], "games": {}}
    blocks = slack_poll.build_message_blocks(state, now)
    assert blocks == state["header_blocks"]
