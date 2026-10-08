import { api } from '../api.js';
import { showToast } from '../app.js';
import { refreshPortfolio } from './portfolio.js';
import { initMembers } from './members.js';
import { initPerformance } from './performance.js';

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Rounds to a handful of decimal places so a stored fraction like 0.29
// doesn't display as 28.999999999999996 due to float noise, while still
// keeping more precision than a user would ever type.
function taxRatePercent(taxRate) {
  return Math.round((taxRate ?? 0) * 100 * 10000) / 10000;
}

export async function initSettings() {
  const view = document.getElementById('view-settings');
  let settings = {
    excluded_tickers: [], excluded_sectors: [],
    investment_amount: 10000, tax_rate: 0, min_trade: 0, rebalance_portfolio: null,
  };
  let portfolios = [];

  try { settings = await api.getSettings(); } catch (_) {}
  try { portfolios = await api.getPortfolios(); } catch (_) {}

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
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:40px;max-width:800px;margin-top:40px;">
      <div>
        <div style="font-family:var(--font-serif);font-size:1.1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Investment Amount</div>
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">
          <input class="settings-input" id="investment-amount-input" type="number" min="0.01" step="any" value="${settings.investment_amount ?? ''}" style="width:140px;">
          <button class="settings-btn" id="use-current-value-btn">Use current value</button>
        </div>
        <div id="current-portfolio-value-label" style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-4);"></div>
      </div>
      <div>
        <div style="font-family:var(--font-serif);font-size:1.1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Default Rebalance Portfolio</div>
        <select class="settings-input" id="rebalance-portfolio-input" style="width:200px;">
          <option value="">— none —</option>
          ${portfolios.map(p => `<option value="${esc(p.name)}"${p.name === settings.rebalance_portfolio ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}
        </select>
      </div>
      <div>
        <div style="font-family:var(--font-serif);font-size:1.1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Tax Rate %</div>
        <input class="settings-input" id="tax-rate-input" type="number" min="0" max="99.99" step="any" value="${taxRatePercent(settings.tax_rate)}" style="width:100px;">
      </div>
      <div>
        <div style="font-family:var(--font-serif);font-size:1.1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Min Trade $</div>
        <input class="settings-input" id="min-trade-input" type="number" min="0" step="any" value="${settings.min_trade ?? ''}" style="width:120px;">
      </div>
    </div>`;

  function currentMatchedPortfolio() {
    return portfolios.find(p => p.name === settings.rebalance_portfolio) ?? null;
  }

  function rerenderCurrentValueLabel() {
    const match = currentMatchedPortfolio();
    const label = document.getElementById('current-portfolio-value-label');
    const btn = document.getElementById('use-current-value-btn');
    if (!label || !btn) return;
    if (match && match.total_value != null) {
      label.textContent = `${match.name} current value: $${match.total_value.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
      btn.disabled = false;
    } else if (match) {
      label.textContent = `${match.name} current value: unavailable`;
      btn.disabled = true;
    } else {
      label.textContent = 'No rebalance portfolio selected.';
      btn.disabled = true;
    }
  }

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
      return true;
    } catch (e) {
      showToast(e.message, 'error');
      return false;
    }
  }

  // Re-reads the DOM inputs from `settings` after a rollback, so a rejected
  // save can't leave a bad value sitting in both the in-memory object and
  // the visible input (which would otherwise poison every later save).
  function syncNumericInputs() {
    document.getElementById('investment-amount-input').value = settings.investment_amount ?? '';
    document.getElementById('tax-rate-input').value = taxRatePercent(settings.tax_rate);
    document.getElementById('min-trade-input').value = settings.min_trade ?? '';
    document.getElementById('rebalance-portfolio-input').value = settings.rebalance_portfolio ?? '';
  }

  view.addEventListener('click', async e => {
    if (!e.target.classList.contains('settings-tag-remove')) return;
    const { key, val } = e.target.dataset;
    const snapshot = { ...settings };
    if (key === 'ticker') settings.excluded_tickers = settings.excluded_tickers.filter(x => x !== val);
    if (key === 'sector') settings.excluded_sectors = settings.excluded_sectors.filter(x => x !== val);
    if (!(await save())) settings = snapshot;
    rerenderTags();
  });

  async function addTicker() {
    const v = document.getElementById('new-ticker').value.toUpperCase().trim();
    if (!v || settings.excluded_tickers.includes(v)) return;
    const snapshot = { ...settings };
    settings.excluded_tickers = [...settings.excluded_tickers, v].sort();
    const ok = await save();
    if (!ok) settings = snapshot;
    rerenderTags();
    if (ok) document.getElementById('new-ticker').value = '';
  }

  async function addSector() {
    const v = document.getElementById('new-sector').value.trim();
    if (!v || settings.excluded_sectors.includes(v)) return;
    const snapshot = { ...settings };
    settings.excluded_sectors = [...settings.excluded_sectors, v].sort();
    const ok = await save();
    if (!ok) settings = snapshot;
    rerenderTags();
    if (ok) document.getElementById('new-sector').value = '';
  }

  document.getElementById('add-ticker-btn').addEventListener('click', addTicker);
  document.getElementById('add-sector-btn').addEventListener('click', addSector);
  document.getElementById('new-ticker').addEventListener('keydown', e => { if (e.key === 'Enter') addTicker(); });
  document.getElementById('new-sector').addEventListener('keydown', e => { if (e.key === 'Enter') addSector(); });

  document.getElementById('investment-amount-input').addEventListener('change', async e => {
    const v = parseFloat(e.target.value);
    if (Number.isNaN(v)) return;
    const snapshot = { ...settings };
    settings.investment_amount = v;
    if (!(await save())) { settings = snapshot; syncNumericInputs(); }
  });

  document.getElementById('tax-rate-input').addEventListener('change', async e => {
    const v = parseFloat(e.target.value);
    if (Number.isNaN(v)) return;
    const snapshot = { ...settings };
    settings.tax_rate = v / 100;
    if (!(await save())) { settings = snapshot; syncNumericInputs(); }
  });

  document.getElementById('min-trade-input').addEventListener('change', async e => {
    const v = parseFloat(e.target.value);
    if (Number.isNaN(v)) return;
    const snapshot = { ...settings };
    settings.min_trade = v;
    if (!(await save())) { settings = snapshot; syncNumericInputs(); }
  });

  document.getElementById('rebalance-portfolio-input').addEventListener('change', async e => {
    const snapshot = { ...settings };
    settings.rebalance_portfolio = e.target.value || null;
    rerenderCurrentValueLabel();
    if (!(await save())) { settings = snapshot; syncNumericInputs(); rerenderCurrentValueLabel(); }
  });

  document.getElementById('use-current-value-btn').addEventListener('click', async () => {
    // Re-fetch portfolios fresh rather than reusing the array captured at
    // initSettings() load time — prices refresh every 2 hours, so a stale
    // snapshot here can fill in a number that's already wrong and then fail
    // the (fixed) amount-exceeds-current-value 400 check on save.
    let freshPortfolios;
    try {
      freshPortfolios = await api.getPortfolios();
    } catch (e) {
      showToast(e.message, 'error');
      return;
    }
    const match = freshPortfolios.find(p => p.name === settings.rebalance_portfolio) ?? null;
    if (!match || match.total_value == null) return;
    portfolios = freshPortfolios;
    const snapshot = { ...settings };
    settings.investment_amount = match.total_value;
    document.getElementById('investment-amount-input').value = match.total_value;
    if (!(await save())) { settings = snapshot; syncNumericInputs(); }
  });

  rerenderCurrentValueLabel();
}
