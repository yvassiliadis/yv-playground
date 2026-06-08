import uuid

from fastapi import APIRouter, HTTPException

from src.api.models import CardOut, CreateGameRequest, GameStateOut, PendingActionOut, PlayerOut
from src.game.ai import ai_should_hit
from src.game.engine import (
    create_game,
    deal_initial_round,
    end_round,
    player_hit,
    player_stay,
    resolve_action,
    start_next_round,
)
from src.game.state import GamePhase, GameState

router = APIRouter(prefix="/games", tags=["games"])

_games: dict[str, GameState] = {}


def _card_out(card) -> CardOut:
    return CardOut(
        type=card.type.value, value=card.value, action=card.action, modifier=card.modifier
    )


def _state_out(state: GameState) -> GameStateOut:
    return GameStateOut(
        game_id=state.game_id,
        phase=state.phase.value,
        players=[
            PlayerOut(
                id=p.id,
                name=p.name,
                is_ai=p.is_ai,
                number_cards=[_card_out(c) for c in p.number_cards],
                modifier_cards=[_card_out(c) for c in p.modifier_cards],
                has_second_chance=p.has_second_chance,
                has_stayed=p.has_stayed,
                has_busted=p.has_busted,
                flip_three_remaining=p.flip_three_remaining,
                round_score=p.round_score,
                total_score=p.total_score,
                is_active=p.is_active,
            )
            for p in state.players
        ],
        dealer_index=state.dealer_index,
        current_player_index=state.current_player_index,
        round_number=state.round_number,
        majestic_flip_winner_id=state.majestic_flip_winner_id,
        game_winner_id=state.game_winner_id,
        deck_remaining=len(state.deck),
        pending_action=(
            PendingActionOut(
                action=state.pending_action.action, drawn_by=state.pending_action.drawn_by
            )
            if state.pending_action
            else None
        ),
    )


def _finalize_if_round_over(state: GameState) -> GameState:
    if state.phase == GamePhase.ROUND_OVER:
        state = end_round(state)
    return state


def _run_ai_turns(state: GameState) -> GameState:
    while state.phase == GamePhase.PLAYING:
        # If there's a pending action, resolve it before deciding hit/stay
        if state.pending_action:
            drawer = state.player_by_id(state.pending_action.drawn_by)
            if drawer.is_ai:
                state = resolve_action(state, drawer.id)  # AI always self-targets
                continue
            else:
                break  # human must choose a target via the resolve-action endpoint
        current = state.current_player()
        if not current.is_ai:
            break
        if ai_should_hit(current):
            state = player_hit(state, current.id)
        else:
            state = player_stay(state, current.id)
    return state


@router.post("", response_model=GameStateOut)
def create_game_route(req: CreateGameRequest) -> GameStateOut:
    game_id = str(uuid.uuid4())
    state = create_game(game_id, req.player_names, req.human_count)
    state = deal_initial_round(state)
    state = _run_ai_turns(state)
    state = _finalize_if_round_over(state)
    _games[game_id] = state
    return _state_out(state)


@router.get("/{game_id}", response_model=GameStateOut)
def get_game(game_id: str) -> GameStateOut:
    state = _games.get(game_id)
    if not state:
        raise HTTPException(status_code=404, detail="Game not found")
    return _state_out(state)


@router.post("/{game_id}/players/{player_id}/hit", response_model=GameStateOut)
def hit(game_id: str, player_id: str) -> GameStateOut:
    state = _games.get(game_id)
    if not state:
        raise HTTPException(status_code=404, detail="Game not found")
    try:
        state = player_hit(state, player_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    state = _run_ai_turns(state)
    state = _finalize_if_round_over(state)
    _games[game_id] = state
    return _state_out(state)


@router.post("/{game_id}/players/{player_id}/stay", response_model=GameStateOut)
def stay(game_id: str, player_id: str) -> GameStateOut:
    state = _games.get(game_id)
    if not state:
        raise HTTPException(status_code=404, detail="Game not found")
    try:
        state = player_stay(state, player_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    state = _run_ai_turns(state)
    state = _finalize_if_round_over(state)
    _games[game_id] = state
    return _state_out(state)


@router.post("/{game_id}/resolve-action/{target_player_id}", response_model=GameStateOut)
def resolve_action_route(game_id: str, target_player_id: str) -> GameStateOut:
    state = _games.get(game_id)
    if not state:
        raise HTTPException(status_code=404, detail="Game not found")
    if not state.pending_action:
        raise HTTPException(status_code=400, detail="No pending action to resolve")
    state = resolve_action(state, target_player_id)
    state = _run_ai_turns(state)
    state = _finalize_if_round_over(state)
    _games[game_id] = state
    return _state_out(state)


@router.post("/{game_id}/next-round", response_model=GameStateOut)
def next_round(game_id: str) -> GameStateOut:
    state = _games.get(game_id)
    if not state:
        raise HTTPException(status_code=404, detail="Game not found")
    if state.phase == GamePhase.GAME_OVER:
        return _state_out(state)
    if state.phase != GamePhase.ROUND_OVER:
        raise HTTPException(status_code=400, detail="Round is still in progress")
    state = start_next_round(state)
    state = _run_ai_turns(state)
    state = _finalize_if_round_over(state)
    _games[game_id] = state
    return _state_out(state)
