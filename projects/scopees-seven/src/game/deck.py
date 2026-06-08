import random
from dataclasses import dataclass
from enum import Enum


class CardType(Enum):
    NUMBER = "number"
    ACTION = "action"
    MODIFIER = "modifier"


@dataclass(frozen=True)
class Card:
    type: CardType
    value: int | None = None  # NUMBER cards: 0–12
    action: str | None = None  # "flip_three", "freeze", "second_chance"
    modifier: str | None = None  # "+2", "+4", "+6", "+8", "+10", "x2"

    def __str__(self) -> str:
        if self.type == CardType.NUMBER:
            return str(self.value)
        if self.type == CardType.ACTION:
            return self.action or ""
        return self.modifier or ""


def build_deck() -> list[Card]:
    cards: list[Card] = []

    # 0 appears once; 1–12 appear N times each
    cards.append(Card(type=CardType.NUMBER, value=0))
    for n in range(1, 13):
        cards.extend(Card(type=CardType.NUMBER, value=n) for _ in range(n))

    # 3 of each action card
    for action in ("flip_three", "freeze", "second_chance"):
        cards.extend(Card(type=CardType.ACTION, action=action) for _ in range(3))

    # 1 of each modifier card
    for modifier in ("+2", "+4", "+6", "+8", "+10", "x2"):
        cards.append(Card(type=CardType.MODIFIER, modifier=modifier))

    random.shuffle(cards)
    return cards
