from dataclasses import dataclass, field
from enum import Enum

from .deck import Card


class GamePhase(Enum):
    DEALING = "dealing"
    PLAYING = "playing"
    ROUND_OVER = "round_over"
    GAME_OVER = "game_over"


@dataclass
class PendingAction:
    action: str  # "freeze" or "flip_three"
    drawn_by: str  # player_id who drew the card; must choose a target


@dataclass
class Player:
    id: str  # "player_0", "player_1", …
    name: str
    is_ai: bool
    number_cards: list[Card] = field(default_factory=list)
    modifier_cards: list[Card] = field(default_factory=list)
    has_second_chance: bool = False
    has_stayed: bool = False
    has_busted: bool = False
    flip_three_remaining: int = 0  # cards still owed from a Flip Three
    round_score: int = 0
    total_score: int = 0

    @property
    def is_active(self) -> bool:
        return not self.has_stayed and not self.has_busted

    @property
    def card_count(self) -> int:
        return len(self.number_cards)

    def has_number(self, value: int) -> bool:
        return any(c.value == value for c in self.number_cards)


@dataclass
class GameState:
    game_id: str
    phase: GamePhase
    players: list[Player]
    deck: list[Card]
    discard: list[Card]
    dealer_index: int
    current_player_index: int
    round_number: int = 1
    majestic_flip_winner_id: str | None = None
    game_winner_id: str | None = None
    pending_action: PendingAction | None = None  # set when a Freeze/Flip Three needs a target

    def active_players(self) -> list[Player]:
        return [p for p in self.players if p.is_active]

    def player_by_id(self, player_id: str) -> Player:
        for p in self.players:
            if p.id == player_id:
                return p
        raise ValueError(f"Player {player_id!r} not found")

    def current_player(self) -> Player:
        return self.players[self.current_player_index]
