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


def test_ko_loser_on_penalties_uses_winner_field():
    # Drawn after full time, decided on penalties via the winner field.
    matches = [
        _match("Brazil", "Japan", "LAST_16", "FINISHED", 1, 1, "HOME_TEAM"),
    ]
    scores = server._build_scores(matches, [])
    assert scores["Japan"]["out"] is True
    assert scores["Brazil"]["out"] is False
