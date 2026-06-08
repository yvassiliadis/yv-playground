import { api } from '../api.js';

function calcRoundScore(player) {
  let total = player.number_cards.reduce((sum, c) => sum + (c.value ?? 0), 0);
  if (player.modifier_cards.some(c => c.modifier === 'x2')) total *= 2;
  for (const c of player.modifier_cards) {
    if (c.modifier?.startsWith('+')) total += parseInt(c.modifier.slice(1), 10);
  }
  return total;
}

function numberCardHTML(card) {
  const cls = card.value === 7 ? 'card seven'
    : card.value === 0 ? 'card zero'
    : 'card';
  return `<div class="${cls}" data-val="${card.value}"><span class="card-val">${card.value}</span></div>`;
}

function modifierCardHTML(card) {
  return `<div class="card modifier" data-val="${card.modifier}"><span class="card-val">${card.modifier}</span></div>`;
}

function actionCardHTML(label) {
  return `<div class="card action" data-val="★"><span class="card-val">${label}</span></div>`;
}

const FACE_DOWN = '<div class="card face-down"></div>';

// Renders a player's cards in the center zone (full size, two rows).
function playerCardsHTML(player) {
  if (player.has_stayed || player.has_busted) {
    const topCount = player.modifier_cards.length + (player.has_second_chance ? 1 : 0);
    const topRow = topCount > 0 ? FACE_DOWN.repeat(topCount) : '';
    const bottomRow = player.number_cards.length > 0 ? FACE_DOWN.repeat(player.number_cards.length) : '';
    if (!topRow && !bottomRow) return '<span class="no-cards">—</span>';
    return `<div class="cards-top">${topRow}</div><div class="cards-bottom">${bottomRow}</div>`;
  }
  const topRow = [
    ...player.modifier_cards.map(modifierCardHTML),
    ...(player.has_second_chance ? [actionCardHTML('2nd\nChance')] : []),
  ].join('');
  const bottomRow = player.number_cards.map(numberCardHTML).join('');
  if (!topRow && !bottomRow) return '<span class="no-cards">—</span>';
  return `<div class="cards-top">${topRow}</div><div class="cards-bottom">${bottomRow}</div>`;
}

// Compact rotated pile for busted players — just a visual, not per-card.
function seatPileHTML() {
  return `
    <div class="card-pile">
      <div class="card face-down pile-card p1"></div>
      <div class="card face-down pile-card p2"></div>
      <div class="card face-down pile-card p3"></div>
    </div>`;
}

// Overlapping fan for active/stayed players at their seat (up to 6 cards).
function seatFanHTML(player, faceDown) {
  const cards = [
    ...player.modifier_cards.map(c => faceDown ? FACE_DOWN : modifierCardHTML(c)),
    ...(player.has_second_chance ? [faceDown ? FACE_DOWN : actionCardHTML('2nd\nChance')] : []),
    ...player.number_cards.map(c => faceDown ? FACE_DOWN : numberCardHTML(c)),
  ].slice(0, 6);

  if (cards.length === 0) return '';
  return `<div class="card-fan">${cards.join('')}</div>`;
}

export function gameView(container, state, navigate) {
  const human   = state.players.find(p => !p.is_ai);
  const current = state.players[state.current_player_index];
  const pending = state.pending_action;
  const humanHasPending = pending && pending.drawn_by === human?.id;
  const isHumanTurn = current?.id === human?.id && state.phase === 'playing' && !pending;
  const canStay = isHumanTurn && (human?.number_cards?.length > 0 || human?.modifier_cards?.length > 0);
  const actionLabel = pending?.action === 'freeze' ? 'Freeze' : 'Flip Three';
  const actionIcon  = pending?.action === 'freeze' ? '❄️' : '🃏';

  const statusMsg = state.majestic_flip_winner_id
    ? `${state.players.find(p => p.id === state.majestic_flip_winner_id)?.name} Majestic Flip!`
    : humanHasPending ? `Pick a target — ${actionLabel}`
    : isHumanTurn     ? 'Your turn'
    : state.phase === 'playing' ? `${current?.name}'s turn…`
    : 'Round over…';

  // Center zone: current player's cards + action UI
  const roundPts   = current ? calcRoundScore(current) : 0;
  const centerCards = current
    ? `<div class="player-cards">${playerCardsHTML(current)}</div>` : '';

  const majesticFlipWinner = state.majestic_flip_winner_id
    ? state.players.find(p => p.id === state.majestic_flip_winner_id)
    : null;

  const isRoundOver = state.phase === 'round_over';

  let centerActions = '';
  if (isRoundOver) {
    const majesticFlipLine = majesticFlipWinner
      ? `<span class="majestic-flip-alert-desc">${majesticFlipWinner.name} collected all 7 — +15 bonus!</span>` : '';
    centerActions = `
      <div class="majestic-flip-alert${majesticFlipWinner ? '' : ' round-over-plain'}">
        ${majesticFlipWinner ? '<img class="majestic-flip-alert-icon" src="/images/spinning_scopee.gif" alt="Scopee!">' : '<span class="majestic-flip-alert-icon">🏁</span>'}
        <div class="majestic-flip-alert-body">
          <span class="majestic-flip-alert-name">${majesticFlipWinner ? 'Majestic Flip!' : 'Round over'}</span>
          ${majesticFlipLine}
          <button class="btn btn-primary next-round-btn" id="btn-next-round">Next Round →</button>
        </div>
      </div>`;
  } else if (majesticFlipWinner) {
    centerActions = `
      <div class="majestic-flip-alert">
        <img class="majestic-flip-alert-icon" src="/images/spinning_scopee.gif" alt="Scopee!">
        <div class="majestic-flip-alert-body">
          <span class="majestic-flip-alert-name">Majestic Flip!</span>
          <span class="majestic-flip-alert-desc">${majesticFlipWinner.name} collected all 7 — +15 bonus!</span>
        </div>
      </div>`;
  } else if (humanHasPending) {
    const actionDesc = pending.action === 'freeze'
      ? "Banks a player's points — they exit the round with what they have."
      : 'Forces a player to draw 3 more cards immediately.';
    centerActions = `
      <div class="action-alert">
        <span class="action-alert-icon">${actionIcon}</span>
        <div class="action-alert-body">
          <span class="action-alert-name">${actionLabel}</span>
          <span class="action-alert-desc">${actionDesc}</span>
        </div>
      </div>
      <div class="target-hint">tap a player seat to target</div>`;
  } else if (isHumanTurn) {
    centerActions = `
      <div class="game-actions">
        <button class="btn btn-primary" id="btn-hit">Hit</button>
        <button class="btn btn-secondary" id="btn-stay" ${!canStay ? 'disabled' : ''}>Stay</button>
      </div>`;
  }

  container.innerHTML = `
    <div class="game-screen${humanHasPending ? ' targeting-active' : ''}">
      <div class="game-header">
        <span class="round-label">Round ${state.round_number}</span>
        <span class="game-status">${statusMsg}</span>
        <span class="deck-count">🂠 ${state.deck_remaining}</span>
      </div>
      <div class="table-scene" id="tableScene">
        <div class="table-oval">
          <div class="deck-pile">
            <div class="deck-stack"></div>
            <span class="deck-label">${state.deck_remaining} left</span>
          </div>
          <div class="center-zone">
            ${current ? `<div class="center-player-label">▶ ${current.name} · ${roundPts} pts</div>` : ''}
            ${centerCards}
            ${centerActions}
          </div>
        </div>
      </div>
    </div>
  `;

  if (isRoundOver) {
    container.querySelector('#btn-next-round')?.addEventListener('click', async (e) => {
      e.target.disabled = true;
      e.target.textContent = 'Dealing…';
      const next = await api.nextRound(state.game_id);
      if (next.phase === 'game_over') navigate('gameover', next);
      else navigate('game', next);
    });
    return;
  }
  if (state.phase === 'game_over')  { setTimeout(() => navigate('gameover', state), 900); return; }

  // Position player seats around the oval
  const scene    = container.querySelector('#tableScene');
  const oval     = container.querySelector('.table-oval');
  const sceneW   = scene.offsetWidth;
  const sceneH   = scene.offsetHeight;
  const TABLE_W  = oval.offsetWidth;
  const TABLE_H  = oval.offsetHeight || TABLE_W * (475 / 760);
  const cx = sceneW / 2;
  const cy = sceneH / 2;
  // Cap radii so seats never clip outside the scene edges
  const rxSeat = Math.min(TABLE_W / 2 + 95, sceneW / 2 - 80);
  const rySeat = Math.min(TABLE_H / 2 + 115, sceneH / 2 - 65);

  state.players.forEach((player, i) => {
    // Human (index 0) always at the bottom; others distributed evenly.
    const angle      = Math.PI / 2 + (i / state.players.length) * 2 * Math.PI;
    const x          = cx + rxSeat * Math.cos(angle);
    const y          = cy + rySeat * Math.sin(angle);
    const isCurrent  = player.id === current?.id;
    const isDealer   = i === state.dealer_index;
    const isTargetable = humanHasPending && player.is_active;

    const pipClass = isCurrent           ? 'pip-current'
      : player.has_busted                ? 'pip-busted'
      : player.has_stayed                ? 'pip-stayed'
      : 'pip-active';

    const npClass = ['seat-nameplate',
      isCurrent      ? 'np-current'    : '',
      player.has_busted ? 'np-busted'  : '',
      player.has_stayed ? 'np-stayed'  : '',
      isTargetable   ? 'np-targetable' : '',
    ].filter(Boolean).join(' ');

    let seatCardsHTML = '';
    if (!isCurrent) {
      seatCardsHTML = (player.has_busted || player.has_stayed)
        ? `<div class="seat-cards">${seatPileHTML()}</div>`
        : `<div class="seat-cards">${seatFanHTML(player, false)}</div>`;
    }

    const roundPtsLabel = player.has_busted ? 'bust'
      : player.has_stayed ? 'stayed'
      : `+${calcRoundScore(player)}`;
    const scoreHTML = `
      <div class="seat-score${player.has_busted ? ' seat-bust-label' : ''}">
        <span class="seat-round-pts">${roundPtsLabel}</span>
        <span class="seat-total-pts">${player.total_score} total</span>
      </div>`;

    const seat = document.createElement('div');
    seat.className = ['seat',
      isCurrent      ? 'seat-current'    : '',
      player.has_busted ? 'seat-busted'  : '',
      player.has_stayed ? 'seat-stayed'  : '',
      isTargetable   ? 'seat-targetable' : '',
    ].filter(Boolean).join(' ');
    seat.dataset.playerId = player.id;
    seat.style.left = `${x}px`;
    seat.style.top  = `${y}px`;
    seat.style.transform = 'translate(-50%, -50%)';

    seat.innerHTML = `
      <div class="${npClass}">
        <span class="seat-pip ${pipClass}"></span>
        ${player.is_ai ? '🤖' : '👤'} ${player.name}
        ${isDealer    ? '<span class="dealer-chip">D</span>' : ''}
        ${isTargetable ? '<span class="target-cue">◆</span>' : ''}
      </div>
      ${seatCardsHTML}
      ${scoreHTML}
    `;

    scene.appendChild(seat);

    if (isTargetable) {
      seat.addEventListener('click', async () => {
        scene.querySelectorAll('.seat-targetable').forEach(s => s.style.pointerEvents = 'none');
        const next = await api.resolveAction(state.game_id, seat.dataset.playerId);
        gameView(container, next, navigate);
      });
    }
  });

  if (humanHasPending) return;

  const disable = () => {
    const h = container.querySelector('#btn-hit');
    const s = container.querySelector('#btn-stay');
    if (h) h.disabled = true;
    if (s) s.disabled = true;
  };

  container.querySelector('#btn-hit')?.addEventListener('click', async () => {
    disable();
    const next = await api.hit(state.game_id, human.id);
    gameView(container, next, navigate);
  });

  container.querySelector('#btn-stay')?.addEventListener('click', async () => {
    disable();
    const next = await api.stay(state.game_id, human.id);
    gameView(container, next, navigate);
  });
}
