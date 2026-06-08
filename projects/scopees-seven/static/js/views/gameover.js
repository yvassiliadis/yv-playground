export function gameoverView(container, state, navigate) {
  const sorted = [...state.players].sort((a, b) => b.total_score - a.total_score);
  const winner = sorted[0];

  const romanNumerals = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'];

  container.innerHTML = `
    <div class="gameover-screen">
      <div class="winner-frame">
        <p class="winner-eyebrow">◆ Winner ◆</p>
        <h1 class="winner-name">${winner.name}</h1>
        <p class="winner-pts">${winner.total_score} points</p>
      </div>
      <div class="standings">
        ${sorted.map((p, i) => `
          <div class="standing-row">
            <span class="standing-rank">${romanNumerals[i] ?? i + 1}</span>
            <span class="standing-name">${p.is_ai ? '🤖 ' : '👤 '}${p.name}</span>
            <span class="standing-pts">${p.total_score} pts</span>
          </div>
        `).join('')}
      </div>
      <button class="btn btn-primary again-btn" id="play-again">Play Again</button>
    </div>
  `;

  container.querySelector('#play-again').addEventListener('click', () => navigate('setup'));
}
