import random

from .deck import Card, CardType, build_deck
from .state import GamePhase, GameState, PendingAction, Player


def create_game(game_id: str, player_names: list[str], human_count: int = 1) -> GameState:
    players = [
        Player(id=f"player_{i}", name=name, is_ai=(i >= human_count))
        for i, name in enumerate(player_names)
    ]
    return GameState(
        game_id=game_id,
        phase=GamePhase.DEALING,
        players=players,
        deck=build_deck(),
        discard=[],
        dealer_index=0,
        current_player_index=0,
    )


def _draw_card(state: GameState) -> Card:
    if not state.deck:
        state.deck = state.discard[:]
        state.discard = []
        random.shuffle(state.deck)
    return state.deck.pop(0)


def _resolve_card_for_player(state: GameState, player: Player, card: Card) -> None:
    """Apply a drawn card to a player. Handles bust, Majestic Flip, modifiers, and actions."""
    if card.type == CardType.NUMBER:
        assert card.value is not None
        if player.has_number(card.value):
            if player.has_second_chance:
                player.has_second_chance = False
                state.discard.append(card)
            else:
                player.has_busted = True
                state.discard.extend(player.number_cards)
                state.discard.extend(player.modifier_cards)
                player.number_cards = []
                player.modifier_cards = []
                state.discard.append(card)
        else:
            player.number_cards.append(card)
            if len(player.number_cards) == 7:
                state.majestic_flip_winner_id = player.id
                player.has_stayed = True
    elif card.type == CardType.MODIFIER:
        player.modifier_cards.append(card)
    elif card.type == CardType.ACTION:
        state.discard.append(card)
        if card.action == "second_chance":
            if player.has_second_chance:
                others = [
                    p
                    for p in state.active_players()
                    if p.id != player.id and not p.has_second_chance
                ]
                if others:
                    others[0].has_second_chance = True
            else:
                player.has_second_chance = True
        elif card.action == "freeze":
            player.has_stayed = True
        elif card.action == "flip_three":
            player.flip_three_remaining += 3


def _resolve_flip_three(state: GameState, player: Player) -> None:
    while player.flip_three_remaining > 0 and player.is_active and state.majestic_flip_winner_id is None:
        player.flip_three_remaining -= 1
        card = _draw_card(state)
        _resolve_card_for_player(state, player, card)


def deal_initial_round(state: GameState) -> GameState:
    for player in state.players:
        card = _draw_card(state)
        _resolve_card_for_player(state, player, card)
        _resolve_flip_three(state, player)

    state.phase = GamePhase.PLAYING
    state.current_player_index = (state.dealer_index + 1) % len(state.players)
    _advance_to_next_active(state)
    return state


def _advance_to_next_active(state: GameState) -> None:
    n = len(state.players)
    for _ in range(n):
        if state.players[state.current_player_index].is_active:
            return
        state.current_player_index = (state.current_player_index + 1) % n
    state.phase = GamePhase.ROUND_OVER


def player_stay(state: GameState, player_id: str) -> GameState:
    player = state.player_by_id(player_id)
    if not player.is_active:
        return state
    player.has_stayed = True
    if not state.active_players():
        state.phase = GamePhase.ROUND_OVER
    else:
        _advance_current_player(state)
    return state


def player_hit(state: GameState, player_id: str) -> GameState:
    player = state.player_by_id(player_id)
    if not player.is_active:
        return state
    card = _draw_card(state)
    # Freeze and Flip Three require the drawer to choose a target before resolving.
    # During the initial deal these are auto-applied; here the player decides.
    if card.type == CardType.ACTION and card.action in ("freeze", "flip_three"):
        state.discard.append(card)
        state.pending_action = PendingAction(action=card.action, drawn_by=player_id)
        return state
    _resolve_card_for_player(state, player, card)
    _resolve_flip_three(state, player)
    if state.majestic_flip_winner_id:
        state.phase = GamePhase.ROUND_OVER
        return state
    if player.has_busted:
        if not state.active_players():
            state.phase = GamePhase.ROUND_OVER
        else:
            _advance_current_player(state)
    return state


def resolve_action(state: GameState, target_player_id: str) -> GameState:
    """Apply a pending Freeze or Flip Three to the chosen target player."""
    if not state.pending_action:
        return state
    action = state.pending_action.action
    state.pending_action = None
    target = state.player_by_id(target_player_id)

    if action == "freeze":
        target.has_stayed = True
    elif action == "flip_three":
        target.flip_three_remaining += 3
        _resolve_flip_three(state, target)
        if state.majestic_flip_winner_id:
            state.phase = GamePhase.ROUND_OVER
            return state

    if not state.active_players():
        state.phase = GamePhase.ROUND_OVER
    elif not state.current_player().is_active:
        _advance_current_player(state)
    return state


def _advance_current_player(state: GameState) -> None:
    n = len(state.players)
    for _ in range(n):
        state.current_player_index = (state.current_player_index + 1) % n
        if state.players[state.current_player_index].is_active:
            return
    state.phase = GamePhase.ROUND_OVER


def calculate_round_score(player: Player, majestic_flip: bool) -> int:
    if player.has_busted:
        return 0
    total = sum(c.value for c in player.number_cards if c.value is not None)
    if any(c.modifier == "x2" for c in player.modifier_cards):
        total *= 2
    for card in player.modifier_cards:
        if card.modifier and card.modifier.startswith("+"):
            total += int(card.modifier[1:])
    if majestic_flip:
        total += 15
    return total


def end_round(state: GameState) -> GameState:
    majestic_flip = state.majestic_flip_winner_id is not None
    for player in state.players:
        is_winner = player.id == state.majestic_flip_winner_id
        player.round_score = calculate_round_score(player, majestic_flip=is_winner and majestic_flip)
        player.total_score += player.round_score
        player.has_second_chance = False

    # Only declare a winner when exactly one player leads at 200+.
    # Ties at 200+ trigger additional rounds until a single leader emerges.
    at_200 = [p for p in state.players if p.total_score >= 200]
    if at_200:
        top_score = max(p.total_score for p in at_200)
        sole_leaders = [p for p in at_200 if p.total_score == top_score]
        if len(sole_leaders) == 1:
            state.phase = GamePhase.GAME_OVER
            state.game_winner_id = sole_leaders[0].id
    return state


def start_next_round(state: GameState) -> GameState:
    for player in state.players:
        state.discard.extend(player.number_cards)
        state.discard.extend(player.modifier_cards)
        player.number_cards = []
        player.modifier_cards = []
        player.has_stayed = False
        player.has_busted = False
        player.flip_three_remaining = 0
        player.round_score = 0

    state.round_number += 1
    state.dealer_index = (state.dealer_index + 1) % len(state.players)
    state.majestic_flip_winner_id = None
    state.phase = GamePhase.DEALING
    return deal_initial_round(state)
