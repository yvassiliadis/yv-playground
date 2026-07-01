from datetime import date, datetime, timezone

import trivia


def test_month_day_parses_day_then_month():
    assert trivia._month_day("on 27 June 1954 something") == "06-27"


def test_month_day_parses_month_then_day():
    assert trivia._month_day("scored on July 13, 1930") == "07-13"


def test_month_day_returns_none_without_date():
    assert trivia._month_day("no calendar date in here") is None


def test_load_facts_adds_month_day_to_every_fact():
    facts = trivia.load_facts()
    assert len(facts) == 98
    assert all("monthDay" in f for f in facts)


def test_partition_splits_dated_and_undated():
    facts = trivia.load_facts()
    dated, undated = trivia.partition_facts(facts)
    assert len(dated) == 19
    assert len(undated) == 79
    assert all(f["monthDay"] for f in dated)
    assert all(f["monthDay"] is None for f in undated)


def test_did_you_know_is_deterministic_for_a_date():
    _, undated = trivia.partition_facts(trivia.load_facts())
    a = trivia.did_you_know(undated, date(2026, 6, 27))
    b = trivia.did_you_know(undated, date(2026, 6, 27))
    assert a[0]["id"] == b[0]["id"]


def test_did_you_know_known_pick_for_june_27():
    _, undated = trivia.partition_facts(trivia.load_facts())
    pick = trivia.did_you_know(undated, date(2026, 6, 27))
    assert pick[0]["id"] == "1954-most-goals-per-match"


def test_did_you_know_count_returns_distinct_facts():
    _, undated = trivia.partition_facts(trivia.load_facts())
    picks = trivia.did_you_know(undated, date(2026, 6, 27), count=2)
    assert len(picks) == 2
    assert picks[0]["id"] != picks[1]["id"]


def test_days_to_final_counts_down():
    from datetime import datetime, timezone
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)  # 18 days before Jul 19 19:00Z
    assert trivia.days_to_final(now) == 18


def test_days_to_final_clamps_to_zero_after_final():
    from datetime import datetime, timezone
    after = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert trivia.days_to_final(after) == 0


def test_trivia_blocks_has_title_fact_and_days_countdown():
    from datetime import datetime, timezone
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    blocks, fallback = trivia.trivia_blocks(now)
    texts = [b["text"]["text"] for b in blocks if b.get("type") == "section"]
    assert any("Did You Know?" in t for t in texts)
    assert any("💡" in t for t in texts)
    assert any("days until the 2026 World Cup Final" in t for t in texts)
    assert "Scopee's Challenge" in texts[-1]
    assert "Did You Know?" in fallback


def test_trivia_blocks_has_no_calendar_lines():
    from datetime import datetime, timezone
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    blocks, _ = trivia.trivia_blocks(now)
    texts = " ".join(b["text"]["text"] for b in blocks if b.get("type") == "section")
    assert "On this exact day" not in texts
    assert "Also in" not in texts
