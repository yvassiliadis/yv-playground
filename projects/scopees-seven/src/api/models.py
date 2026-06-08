from pydantic import BaseModel, field_validator


class CreateGameRequest(BaseModel):
    player_names: list[str]
    human_count: int = 1

    @field_validator("player_names")
    @classmethod
    def validate_player_count(cls, v: list[str]) -> list[str]:
        if not (2 <= len(v) <= 10):
            raise ValueError("player_names must have 2–10 players")
        return v

    @field_validator("human_count")
    @classmethod
    def validate_human_count(cls, v: int, info) -> int:
        if v < 1:
            raise ValueError("human_count must be at least 1")
        return v


class CardOut(BaseModel):
    type: str
    value: int | None = None
    action: str | None = None
    modifier: str | None = None


class PlayerOut(BaseModel):
    id: str
    name: str
    is_ai: bool
    number_cards: list[CardOut]
    modifier_cards: list[CardOut]
    has_second_chance: bool
    has_stayed: bool
    has_busted: bool
    flip_three_remaining: int
    round_score: int
    total_score: int
    is_active: bool


class PendingActionOut(BaseModel):
    action: str
    drawn_by: str


class GameStateOut(BaseModel):
    game_id: str
    phase: str
    players: list[PlayerOut]
    dealer_index: int
    current_player_index: int
    round_number: int
    majestic_flip_winner_id: str | None
    game_winner_id: str | None
    deck_remaining: int
    pending_action: PendingActionOut | None = None
