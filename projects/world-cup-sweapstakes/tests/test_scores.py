import server


def _match(home, away, stage, status, hg=None, ag=None, winner=None):
    return {
        "homeTeam": {"name": home},
        "awayTeam": {"name": away},
        "stage": stage,
        "status": status,
        "score": {"fullTime": {"home": hg, "away": ag}, "winner": winner},
    }


def test_ko_loser_is_marked_out():
    matches = [
        # Canada beat South Africa in the R32 -> South Africa is out, Canada is not.
        _match("South Africa", "Canada", "LAST_32", "FINISHED", 0, 1, "AWAY_TEAM"),
        # Germany vs Paraguay R32 not played yet -> neither is out.
        _match("Germany", "Paraguay", "LAST_32", "TIMED"),
    ]
    scores = server._build_scores(matches, [])
    assert scores["South Africa"]["out"] is True
    assert scores["Canada"]["out"] is False
    assert scores["Germany"]["out"] is False
    assert scores["Paraguay"]["out"] is False


def test_score_detail_penalty_shootout():
    # 1-1 after extra time, decided on penalties: headline is the on-pitch
    # score, penalties reported separately.
    score = {
        "duration": "PENALTY_SHOOTOUT",
        "fullTime": {"home": 5, "away": 6},
        "regularTime": {"home": 1, "away": 1},
        "extraTime": {"home": 0, "away": 0},
        "penalties": {"home": 3, "away": 4},
    }
    assert server._match_score_detail(score) == (1, 1, 3, 4)


def test_score_detail_extra_time_no_penalties():
    score = {
        "duration": "EXTRA_TIME",
        "fullTime": {"home": 2, "away": 1},
        "regularTime": {"home": 1, "away": 1},
        "extraTime": {"home": 1, "away": 0},
        "penalties": {"home": None, "away": None},
    }
    assert server._match_score_detail(score) == (2, 1, None, None)


def test_score_detail_regular_time_uses_full_time():
    score = {
        "duration": "REGULAR",
        "fullTime": {"home": 3, "away": 0},
        "halfTime": {"home": 1, "away": 0},
    }
    assert server._match_score_detail(score) == (3, 0, None, None)


def test_ko_loser_on_penalties_uses_winner_field():
    # Drawn after full time, decided on penalties via the winner field.
    matches = [
        _match("Brazil", "Japan", "LAST_16", "FINISHED", 1, 1, "HOME_TEAM"),
    ]
    scores = server._build_scores(matches, [])
    assert scores["Japan"]["out"] is True
    assert scores["Brazil"]["out"] is False
