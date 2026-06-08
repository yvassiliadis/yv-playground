from collections import Counter

from src.game.deck import Card, CardType, build_deck


def test_deck_has_94_cards():
    deck = build_deck()
    assert len(deck) == 94


def test_number_card_distribution():
    deck = build_deck()
    numbers = [c for c in deck if c.type == CardType.NUMBER]
    counts = Counter(c.value for c in numbers)
    assert counts[0] == 1
    assert counts[1] == 1
    for n in range(2, 13):
        assert counts[n] == n, f"Expected {n} copies of {n}, got {counts[n]}"


def test_action_card_counts():
    deck = build_deck()
    actions = [c for c in deck if c.type == CardType.ACTION]
    counts = Counter(c.action for c in actions)
    assert counts["flip_three"] == 3
    assert counts["freeze"] == 3
    assert counts["second_chance"] == 3


def test_modifier_card_counts():
    deck = build_deck()
    mods = [c for c in deck if c.type == CardType.MODIFIER]
    assert len(mods) == 6
    mod_values = Counter(c.modifier for c in mods)
    for m in ["+2", "+4", "+6", "+8", "+10", "x2"]:
        assert mod_values[m] == 1


def test_shuffle_changes_order():
    deck1 = build_deck()
    deck2 = build_deck()
    values1 = [c.value for c in deck1 if c.type == CardType.NUMBER]
    values2 = [c.value for c in deck2 if c.type == CardType.NUMBER]
    assert values1 != values2
