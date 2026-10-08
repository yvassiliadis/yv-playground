import { api } from '../api.js';

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatCurrency(v) {
  if (v == null) return '–';
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

const ACTION_ORDER = { sell: 0, buy: 1, hold: 2 };

function sortTrades(trades) {
  return [...trades].sort((a, b) => ACTION_ORDER[a.action] - ACTION_ORDER[b.action]);
}

function actionColor(action) {
  if (action === 'sell') return 'var(--red)';
  if (action === 'buy') return 'var(--green)';
  return 'var(--text-4)';
}

function metricCard(label, value, color) {
  return `
    <div class="metric-card">
      <div class="metric-label">${esc(label)}</div>
      <div class="metric-val"${color ? ` style="color:${color};"` : ''}>${value}</div>
    </div>`;
}

function tradesTable(trades) {
  if (!trades.length) {
    return '<div style="color:var(--text-4);font-family:var(--font-mono);font-size:0.75rem;padding:12px 0;">No trades — portfolio already matches target.</div>';
  }
  const sorted = sortTrades(trades);
  return `
    <table style="width:100%;border-collapse:collapse;font-family:var(--font-mono);font-size:0.72rem;margin-top:12px;">
      <thead>
        <tr style="color:var(--text-3);border-bottom:1px solid var(--border);">
          <th style="text-align:left;padding:4px 8px 8px 0;">Ticker</th>
          <th style="text-align:right;padding:4px 8px;">Action</th>
          <th style="text-align:right;padding:4px 8px;">Current $</th>
          <th style="text-align:right;padding:4px 8px;">Target $</th>
          <th style="text-align:right;padding:4px 8px;">Trade $</th>
          <th style="text-align:right;padding:4px 8px;">Shares</th>
          <th style="text-align:right;padding:4px 0;">Est. Tax</th>
        </tr>
      </thead>
      <tbody>
        ${sorted.map(t => `
          <tr style="border-bottom:1px solid var(--border);color:var(--text-2);">
            <td style="padding:6px 8px 6px 0;font-weight:500;color:var(--text);">${esc(t.ticker)}</td>
            <td style="text-align:right;padding:6px 8px;color:${actionColor(t.action)};text-transform:uppercase;">${esc(t.action)}</td>
            <td style="text-align:right;padding:6px 8px;">${formatCurrency(t.current_value)}</td>
            <td style="text-align:right;padding:6px 8px;">${formatCurrency(t.target_value)}</td>
            <td style="text-align:right;padding:6px 8px;">${formatCurrency(t.trade_value)}</td>
            <td style="text-align:right;padding:6px 8px;">${t.shares.toFixed(4)}</td>
            <td style="text-align:right;padding:6px 0;">${t.est_tax != null ? formatCurrency(t.est_tax) : '–'}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

export async function initRebalance() {
  const view = document.getElementById('view-rebalance');

  let portfolios = [];
  let settings = {};

  view.innerHTML = `<div style="color:var(--text-4);font-family:var(--font-mono);font-size:0.8rem;padding:20px 0;">Loading…</div>`;

  try {
    [portfolios, settings] = await Promise.all([api.getPortfolios(), api.getSettings()]);
  } catch (e) {
    view.innerHTML = `<div style="color:var(--red);font-family:var(--font-mono);font-size:0.8rem;padding:20px 0;">${esc(e.message)}</div>`;
    return;
  }

  const defaultPortfolio = portfolios.find(p => p.name === settings.rebalance_portfolio)
    ? settings.rebalance_portfolio
    : (portfolios[0]?.name ?? null);

  view.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;flex-wrap:wrap;gap:12px;">
      <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);">Rebalance</div>
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
        <div style="display:flex;align-items:center;gap:8px;">
          <label style="font-family:var(--font-mono);font-size:0.7rem;color:var(--text-3);">Portfolio</label>
          <select id="rebalance-portfolio-select" class="settings-input">
            ${portfolios.map(p => `<option value="${esc(p.name)}"${p.name === defaultPortfolio ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}
          </select>
        </div>
        <div style="font-family:var(--font-mono);font-size:0.7rem;color:var(--text-3);">
          Amount: <span style="color:var(--text);">${formatCurrency(settings.investment_amount)}</span>
          <a href="#settings" style="color:var(--amber);margin-left:4px;">(edit in Settings)</a>
        </div>
      </div>
    </div>
    <div id="rebalance-content"></div>`;

  const content = document.getElementById('rebalance-content');
  const select = document.getElementById('rebalance-portfolio-select');

  async function loadPlan(portfolioName) {
    if (!portfolioName) {
      content.innerHTML = `<div style="color:var(--text-4);font-family:var(--font-mono);font-size:0.8rem;padding:20px 0;">No tracked portfolios yet. Add one in the Tracker tab.</div>`;
      return;
    }
    content.innerHTML = `<div style="color:var(--text-4);font-family:var(--font-mono);font-size:0.8rem;padding:20px 0;">Loading plan…</div>`;
    let plan;
    try {
      plan = await api.getRebalance(portfolioName);
    } catch (e) {
      content.innerHTML = `<div style="color:var(--red);font-family:var(--font-mono);font-size:0.8rem;padding:20px 0;">${esc(e.message)}</div>`;
      return;
    }

    const saved = plan.full_liquidation_tax - plan.est_tax;
    const savedColor = saved >= 0 ? 'var(--green)' : 'var(--red)';

    content.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:24px;">
        ${metricCard('Sells', formatCurrency(plan.total_sells), 'var(--red)')}
        ${metricCard('Buys', formatCurrency(plan.total_buys), 'var(--green)')}
        ${metricCard('Est. Tax', formatCurrency(plan.est_tax))}
        ${metricCard('Saved vs. Sell-All', formatCurrency(saved), savedColor)}
        ${metricCard('Cash Withdrawn', formatCurrency(plan.cash_withdrawn))}
      </div>
      ${plan.warnings.length ? `
        <div style="font-family:var(--font-mono);font-size:0.68rem;color:var(--amber);margin-bottom:16px;">
          ${plan.warnings.map(w => esc(w)).join('<br>')}
        </div>` : ''}
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;overflow-x:auto;">
        ${tradesTable(plan.trades)}
      </div>`;
  }

  select.addEventListener('change', () => loadPlan(select.value));

  await loadPlan(defaultPortfolio);
}
