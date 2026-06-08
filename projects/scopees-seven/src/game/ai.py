from .state import Player

_STAY_THRESHOLD = 4


def ai_should_hit(player: Player) -> bool:
    return player.card_count < _STAY_THRESHOLD
