import { api } from '../api.js';
import { showToast } from '../app.js';
import { refreshPortfolio } from './portfolio.js';
import { initMembers } from './members.js';
import { initPerformance } from './performance.js';

export async function initSettings() {
  const view = document.getElementById('view-settings');
  let settings = { excluded_tickers: [], excluded_sectors: [] };

  try { settings = await api.getSettings(); } catch (_) {}

  function renderTags(items, key) {
    return items.map(item => `
      <span class="settings-tag">
        ${item}
        <button class="settings-tag-remove" data-key="${key}" data-val="${item}">×</button>
      </span>`).join('');
  }

  function rerenderTags() {
    document.getElementById('ticker-tags').innerHTML = renderTags(settings.excluded_tickers, 'ticker');
    document.getElementById('sector-tags').innerHTML = renderTags(settings.excluded_sectors, 'sector');
  }

  view.innerHTML = `
    <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:32px;">
      Settings
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:40px;max-width:800px;">
      <div>
        <div style="font-family:var(--font-serif);font-size:1.1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Excluded Tickers</div>
        <div id="ticker-tags" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;">
          ${renderTags(settings.excluded_tickers, 'ticker')}
        </div>
        <div style="display:flex;gap:8px;">
          <input class="settings-input" id="new-ticker" placeholder="e.g. AAPL" style="text-transform:uppercase;width:120px;">
          <button class="settings-btn primary" id="add-ticker-btn">Add</button>
        </div>
      </div>
      <div>
        <div style="font-family:var(--font-serif);font-size:1.1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Excluded Sectors</div>
        <div id="sector-tags" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px;">
          ${renderTags(settings.excluded_sectors, 'sector')}
        </div>
        <div style="display:flex;gap:8px;">
          <input class="settings-input" id="new-sector" placeholder="e.g. Utilities" style="width:160px;">
          <button class="settings-btn primary" id="add-sector-btn">Add</button>
        </div>
      </div>
    </div>`;

  async function save() {
    try {
      await api.updateSettings(settings);
      showToast('Settings saved');
      const updated = await api.getLatestRun().catch(() => null);
      if (updated) {
        refreshPortfolio(updated);
        initMembers(updated);
        await initPerformance(updated);
      }
    } catch (e) {
      showToast(e.message, 'error');
    }
  }

  view.addEventListener('click', async e => {
    if (!e.target.classList.contains('settings-tag-remove')) return;
    const { key, val } = e.target.dataset;
    if (key === 'ticker') settings.excluded_tickers = settings.excluded_tickers.filter(x => x !== val);
    if (key === 'sector') settings.excluded_sectors = settings.excluded_sectors.filter(x => x !== val);
    await save();
    rerenderTags();
  });

  async function addTicker() {
    const v = document.getElementById('new-ticker').value.toUpperCase().trim();
    if (!v || settings.excluded_tickers.includes(v)) return;
    settings.excluded_tickers.push(v);
    settings.excluded_tickers.sort();
    await save();
    rerenderTags();
    document.getElementById('new-ticker').value = '';
  }

  async function addSector() {
    const v = document.getElementById('new-sector').value.trim();
    if (!v || settings.excluded_sectors.includes(v)) return;
    settings.excluded_sectors.push(v);
    settings.excluded_sectors.sort();
    await save();
    rerenderTags();
    document.getElementById('new-sector').value = '';
  }

  document.getElementById('add-ticker-btn').addEventListener('click', addTicker);
  document.getElementById('add-sector-btn').addEventListener('click', addSector);
  document.getElementById('new-ticker').addEventListener('keydown', e => { if (e.key === 'Enter') addTicker(); });
  document.getElementById('new-sector').addEventListener('keydown', e => { if (e.key === 'Enter') addSector(); });
}
