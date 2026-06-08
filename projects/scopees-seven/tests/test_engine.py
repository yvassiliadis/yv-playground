from src.game.deck import Card, CardType
from src.game.engine import (
    calculate_round_score,
    create_game,
    deal_initial_round,
    end_round,
    player_hit,
    player_stay,
    resolve_action,
    start_next_round,
)
from src.game.state import GamePhase


def test_create_game_builds_players():
    state = create_game("g1", ["Alice", "Bot 1", "Bot 2"], human_count=1)
    assert len(state.players) == 3
    assert state.players[0].name == "Alice"
    assert not state.players[0].is_ai
    assert state.players[1].is_ai
    assert state.players[2].is_ai


def test_create_game_has_full_deck():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    assert len(state.deck) == 94


def test_create_game_phase_is_dealing():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    assert state.phase == GamePhase.DEALING


def test_deal_phase_transitions_to_playing():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    state = deal_initial_round(state)
    assert state.phase == GamePhase.PLAYING


def test_deal_removes_cards_from_deck():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    state = deal_initial_round(state)
    assert len(state.deck) < 94


def _make_state(cards_for_p0: list[Card]):
    """Build a PLAYING state with player_0 holding specific cards."""
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    state.phase = GamePhase.PLAYING
    state.current_player_index = 0
    p = state.players[0]
    p.number_cards = [c for c in cards_for_p0 if c.type == CardType.NUMBER]
    p.modifier_cards = [c for c in cards_for_p0 if c.type == CardType.MODIFIER]
    return state


def test_player_stay_marks_player_inactive():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    state.phase = GamePhase.PLAYING
    state.current_player_index = 0
    state = player_stay(state, "player_0")
    assert state.players[0].has_stayed


def test_player_stay_advances_turn():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    state.phase = GamePhase.PLAYING
    state.current_player_index = 0
    state = player_stay(state, "player_0")
    assert state.current_player_index != 0 or state.phase == GamePhase.ROUND_OVER


def test_all_players_staying_ends_round():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    state.phase = GamePhase.PLAYING
    state.current_player_index = 0
    state = player_stay(state, "player_0")
    state = player_stay(state, "player_1")
    assert state.phase == GamePhase.ROUND_OVER


def test_player_hit_draws_a_card():
    state = create_game("g1", ["Alice"], human_count=1)
    state.phase = GamePhase.PLAYING
    state.current_player_index = 0
    deck_before = len(state.deck)
    state = player_hit(state, "player_0")
    assert len(state.deck) < deck_before


def test_duplicate_number_causes_bust():
    state = _make_state([Card(type=CardType.NUMBER, value=5)])
    state.deck.insert(0, Card(type=CardType.NUMBER, value=5))
    state = player_hit(state, "player_0")
    assert state.players[0].has_busted


def test_seven_unique_numbers_triggers_majestic_flip_and_ends_round():
    state = _make_state([Card(type=CardType.NUMBER, value=n) for n in range(6)])
    state.deck.insert(0, Card(type=CardType.NUMBER, value=6))
    state = player_hit(state, "player_0")
    assert state.majestic_flip_winner_id == "player_0"
    assert state.phase == GamePhase.ROUND_OVER


def test_freeze_card_sets_pending_action():
    state = _make_state([Card(type=CardType.NUMBER, value=3)])
    state.deck.insert(0, Card(type=CardType.ACTION, action="freeze"))
    state = player_hit(state, "player_0")
    assert state.pending_action is not None
    assert state.pending_action.action == "freeze"
    assert state.pending_action.drawn_by == "player_0"


def test_flip_three_card_sets_pending_action():
    state = _make_state([Card(type=CardType.NUMBER, value=3)])
    state.deck.insert(0, Card(type=CardType.ACTION, action="flip_three"))
    state = player_hit(state, "player_0")
    assert state.pending_action is not None
    assert state.pending_action.action == "flip_three"


def test_resolve_freeze_on_target_marks_them_stayed():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    state.phase = GamePhase.PLAYING
    state.current_player_index = 0
    state.deck.insert(0, Card(type=CardType.ACTION, action="freeze"))
    state = player_hit(state, "player_0")
    assert state.pending_action is not None
    state = resolve_action(state, "player_1")  # freeze the bot
    assert state.players[1].has_stayed
    assert state.pending_action is None


def test_score_sums_number_cards():
    state = _make_state([Card(type=CardType.NUMBER, value=5), Card(type=CardType.NUMBER, value=3)])
    assert calculate_round_score(state.players[0], majestic_flip=False) == 8


def test_score_applies_additive_modifier():
    state = _make_state(
        [
            Card(type=CardType.NUMBER, value=5),
            Card(type=CardType.MODIFIER, modifier="+4"),
        ]
    )
    assert calculate_round_score(state.players[0], majestic_flip=False) == 9


def test_score_x2_applied_before_additive():
    state = _make_state(
        [
            Card(type=CardType.NUMBER, value=5),
            Card(type=CardType.MODIFIER, modifier="x2"),
            Card(type=CardType.MODIFIER, modifier="+4"),
        ]
    )
    assert calculate_round_score(state.players[0], majestic_flip=False) == 14  # (5*2)+4


def test_score_adds_majestic_flip_bonus():
    state = _make_state([Card(type=CardType.NUMBER, value=n) for n in range(7)])
    score = calculate_round_score(state.players[0], majestic_flip=True)
    assert score == (0 + 1 + 2 + 3 + 4 + 5 + 6) + 15


def test_busted_player_scores_zero():
    state = _make_state([Card(type=CardType.NUMBER, value=5)])
    state.players[0].has_busted = True
    assert calculate_round_score(state.players[0], majestic_flip=False) == 0


def test_end_round_updates_totals():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    state.phase = GamePhase.ROUND_OVER
    state.players[0].number_cards = [Card(type=CardType.NUMBER, value=7)]
    state.players[1].number_cards = [Card(type=CardType.NUMBER, value=3)]
    state = end_round(state)
    assert state.players[0].total_score == 7
    assert state.players[1].total_score == 3


def test_end_round_detects_game_over():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    state.phase = GamePhase.ROUND_OVER
    state.players[0].total_score = 195
    state.players[0].number_cards = [Card(type=CardType.NUMBER, value=12)]
    state = end_round(state)
    assert state.phase == GamePhase.GAME_OVER
    assert state.game_winner_id == "player_0"


def test_end_round_continues_on_tie_at_200():
    state = create_game("g1", ["Alice", "Bot"], human_count=1)
    state.phase = GamePhase.ROUND_OVER
    state.players[0].total_score = 195
    state.players[1].total_score = 195
    state.players[0].number_cards = [Card(type=CardType.NUMBER, value=5)]
    state.players[1].number_cards = [Card(type=CardType.NUMBER, value=5)]
    state = end_round(state)
    assert state.phase == GamePhase.ROUND_OVER  # tied at 200 — no winner yet
    assert state.game_winner_id is None


from src.game.ai import ai_should_hit


def test_ai_hits_with_fewer_than_4_cards():
    state = _make_state(
        [
            Card(type=CardType.NUMBER, value=3),
            Card(type=CardType.NUMBER, value=7),
        ]
    )
    assert ai_should_hit(state.players[0]) is True


def test_ai_stays_with_4_or_more_cards():
    state = _make_state([Card(type=CardType.NUMBER, value=n) for n in [1, 3, 5, 7]])
    assert ai_should_hit(state.players[0]) is False
