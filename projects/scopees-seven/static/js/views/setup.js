import { api } from '../api.js';

export function setupView(container, navigate) {
  let playerCount = 6;
  let names = Array.from({ length: playerCount }, (_, i) => `Player ${i + 1}`);

  const saveNames = () => {
    container.querySelectorAll('.name-input').forEach((inp, i) => {
      names[i] = inp.value.trim() || `Player ${i + 1}`;
    });
  };

  const render = () => {
    container.innerHTML = `
      <div class="setup-screen">
        <img src="/images/original_scopee.png" alt="Scopee" class="scopee-mascot" />
        <h1 class="game-title">Scopee's Seven</h1>
        <p class="setup-subtitle">A Press Your Luck Card Game</p>
        <div class="setup-panel">
          <div class="setup-count-row">
            <span class="setup-count-label">Players</span>
            <div class="count-control">
              <button class="btn btn-secondary count-btn" id="dec">−</button>
              <span class="count-value">${playerCount}</span>
              <button class="btn btn-secondary count-btn" id="inc">+</button>
            </div>
          </div>
          <span class="setup-names-label">Player Names</span>
          <div class="setup-names" id="names-container" data-cols="${playerCount <= 4 ? 1 : playerCount <= 8 ? 2 : 3}">
            ${Array.from({ length: playerCount }, (_, i) => `
              <div class="name-row">
                <span class="name-icon">${i === 0 ? '👤' : '🤖'}</span>
                <span class="name-tag">${i === 0 ? 'You' : 'Bot'}</span>
                <input class="name-input" data-index="${i}"
                  value="${names[i] ?? `Player ${i + 1}`}" maxlength="16" />
              </div>
            `).join('')}
          </div>
          <button class="btn btn-primary start-btn" id="start">Deal Cards</button>
        </div>
      </div>
    `;

    container.querySelector('#dec').addEventListener('click', () => {
      if (playerCount > 2) { saveNames(); playerCount--; names = names.slice(0, playerCount); render(); }
    });
    container.querySelector('#inc').addEventListener('click', () => {
      if (playerCount < 12) { saveNames(); names.push(`Player ${playerCount + 1}`); playerCount++; render(); }
    });

    container.querySelector('#start').addEventListener('click', async () => {
      const inputs = container.querySelectorAll('.name-input');
      const names = Array.from(inputs).map(inp => inp.value.trim() || inp.placeholder);
      const btn = container.querySelector('#start');
      btn.disabled = true;
      btn.textContent = 'Dealing…';
      try {
        const state = await api.createGame(names, 1);
        if (state.phase === 'round_over' || state.phase === 'game_over') {
          navigate('score', state);
        } else {
          navigate('game', state);
        }
      } catch (e) {
        btn.disabled = false;
        btn.textContent = 'Deal Cards';
        alert('Failed to start: ' + e.message);
      }
    });
  };

  render();
}
