import { setupView }  from './views/setup.js';
import { gameView }   from './views/game.js';
import { scoreView }  from './views/score.js';
import { gameoverView } from './views/gameover.js';

const app = document.getElementById('app');

export function navigate(view, state) {
  app.innerHTML = '';
  if (view === 'setup')   setupView(app, navigate);
  else if (view === 'game')    gameView(app, state, navigate);
  else if (view === 'score')   scoreView(app, state, navigate);
  else if (view === 'gameover') gameoverView(app, state, navigate);
}

navigate('setup');
