import { api } from '../api.js';

export function scoreView(container, state, navigate) {
  const rows = state.players.map(p => {
    const isMajesticFlip = p.id === state.majestic_flip_winner_id;
    return `
      <tr class="${p.has_busted ? 'row-bust' : ''}">
        <td>${p.is_ai ? '🤖' : '👤'} ${p.name}</td>
        <td class="score-num">${p.has_busted ? '—' : p.round_score}</td>
        <td class="score-num">${isMajesticFlip ? '+15 🎯' : ''}</td>
        <td class="score-num total">${p.total_score}</td>
      </tr>
    `;
  }).join('');

  const progressBars = state.players.map(p => `
    <div class="progress-row">
      <div class="progress-label">
        <span>${p.is_ai ? '🤖' : '👤'} ${p.name}</span>
        <span>${p.total_score} / 200</span>
      </div>
      <div class="progress-track">
        <div class="progress-fill" style="width:${Math.min(100, (p.total_score / 200) * 100).toFixed(1)}%"></div>
      </div>
    </div>
  `).join('');

  container.innerHTML = `
    <div class="score-screen">
      <h2 class="score-title">Round ${state.round_number} Results</h2>
      <p class="score-subtitle">First to 200 points wins</p>
      <table class="score-table">
        <thead>
          <tr><th>Player</th><th>Round</th><th>Bonus</th><th>Total</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="score-progress">${progressBars}</div>
      <button class="btn next-btn" id="next">Next Round →</button>
    </div>
  `;

  container.querySelector('#next').addEventListener('click', async () => {
    const btn = container.querySelector('#next');
    btn.disabled = true;
    btn.textContent = 'Dealing…';
    const next = await api.nextRound(state.game_id);
    if (next.phase === 'game_over') navigate('gameover', next);
    else navigate('game', next);
  });
}
