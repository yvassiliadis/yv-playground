import { api }         from './api.js';
import { initDrawer }  from './drawer.js';
import { initPortfolio, refreshPortfolio, onPortfolioActivated } from './views/portfolio.js';
import { initPerformance }  from './views/performance.js';
import { initMembers }      from './views/members.js';
import { initHistory }      from './views/history.js';
import { initResearch }     from './views/research.js';
import { initSettings }     from './views/settings.js';
import { initTracker }      from './views/tracker.js';

// ── State ─────────────────────────────────────────────────────────────────────
export let latestRun = null;
export let allRuns   = [];

// ── UI helpers ────────────────────────────────────────────────────────────────
export function showLoading(msg = 'Working…') {
  document.getElementById('loading-text').textContent = msg;
  document.getElementById('loading').classList.add('active');
}
export function hideLoading() { document.getElementById('loading').classList.remove('active'); }

export function showToast(msg, type = 'success') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type} show`;
  setTimeout(() => el.classList.remove('show'), 3000);
}

// ── Nav age badge ─────────────────────────────────────────────────────────────
function updateAgeBadge(run) {
  const el = document.getElementById('nav-age');
  if (!run) { el.textContent = ''; return; }
  const days = Math.floor((Date.now() - new Date(run.timestamp)) / 86400000);
  if (days === 0)      { el.textContent = 'today';        el.className = 'nav-age fresh'; }
  else if (days <= 7)  { el.textContent = `${days}d ago`; el.className = 'nav-age recent'; }
  else                  { el.textContent = `${days}d ago`; el.className = 'nav-age stale'; }
}

// ── Router ────────────────────────────────────────────────────────────────────
const VIEWS = ['portfolio', 'performance', 'members', 'history', 'research', 'settings', 'tracker'];

function activate(viewName) {
  VIEWS.forEach(v => {
    document.getElementById(`view-${v}`).classList.toggle('active', v === viewName);
  });
  document.querySelectorAll('.nav-link').forEach(a => {
    a.classList.toggle('active', a.dataset.view === viewName);
  });
}

function route() {
  const hash = location.hash.replace('#', '') || 'portfolio';
  const view = VIEWS.includes(hash) ? hash : 'portfolio';
  activate(view);
  if (view === 'portfolio') onPortfolioActivated();
}

// ── Run Committee ─────────────────────────────────────────────────────────────
async function runCommittee() {
  const btn = document.getElementById('nav-run-btn');
  btn.disabled = true;
  showLoading('Committee deliberating… (~60–90 seconds)');
  try {
    latestRun = await api.triggerRun();
    allRuns   = await api.getAllRuns();
    updateAgeBadge(latestRun);
    await refreshPortfolio(latestRun);
    showToast('Committee run complete!');
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    hideLoading();
    btn.disabled = false;
  }
}

// ── Ask Advisor ───────────────────────────────────────────────────────────────
async function askAdvisor() {
  const ticker = document.getElementById('nav-ticker').value.trim().toUpperCase();
  if (!ticker) return;
  showLoading(`Asking committee about ${ticker}…`);
  try {
    await api.askAdvisor(ticker);
    location.hash = '#research';
    await initResearch();
    showToast(`Opinion on ${ticker} ready`);
  } catch (e) {
    showToast(e.message, 'error');
  } finally {
    hideLoading();
    document.getElementById('nav-ticker').value = '';
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  initDrawer();

  try {
    latestRun = await api.getLatestRun();
    allRuns   = await api.getAllRuns();
  } catch (e) {
    if (!e.message?.includes('No runs yet')) console.warn('init fetch:', e.message);
  }

  updateAgeBadge(latestRun);

  // Init all views with data
  initPortfolio(latestRun);
  initPerformance(latestRun);
  initMembers(latestRun);
  initHistory(allRuns);
  await initResearch();
  await initSettings();
  await initTracker(latestRun);

  // Wire controls
  document.getElementById('nav-run-btn').addEventListener('click', runCommittee);
  document.getElementById('nav-ask-btn').addEventListener('click', askAdvisor);
  document.getElementById('nav-ticker').addEventListener('keydown', e => {
    if (e.key === 'Enter') askAdvisor();
  });

  // Hash routing
  window.addEventListener('hashchange', route);
  route();
}

init();
