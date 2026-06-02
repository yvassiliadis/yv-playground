import { api } from '../api.js';
import { showToast } from '../app.js';

let chartInstance = null;

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function filterSeries(rawDict, rangeOpt) {
  const entries = Object.entries(rawDict)
    .map(([k, v]) => [new Date(k), v])
    .sort((a, b) => a[0] - b[0]);
  if (!entries.length) return { labels: [], values: [] };
  const cutoffs = {
    '1D':  new Date(Date.now() - 1  * 86400000),
    '3D':  new Date(Date.now() - 3  * 86400000),
    '1W':  new Date(Date.now() - 7  * 86400000),
    '1M':  new Date(Date.now() - 30 * 86400000),
    '3M':  new Date(Date.now() - 90 * 86400000),
    'YTD': new Date(new Date().getFullYear(), 0, 1),
    '1Y':  null,
  };
  const cutoff = cutoffs[rangeOpt] ?? null;
  const filtered = cutoff ? entries.filter(([d]) => d >= cutoff) : entries;
  if (!filtered.length) return { labels: [], values: [] };
  const base = filtered[0][1];
  return {
    labels: filtered.map(([d]) => d),
    values: filtered.map(([, v]) => (v - base) * 100),
  };
}

function formatCurrency(v) {
  if (v == null) return '–';
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function formatReturn(v) {
  if (v == null) return '<span style="color:var(--text-4);">–</span>';
  const color = v >= 0 ? 'var(--green)' : 'var(--red)';
  const txt = (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
  return `<span style="color:${color};">${txt}</span>`;
}

// ── Portfolio cards ───────────────────────────────────────────────────────────

function sortPositions(positions, sort) {
  if (!sort) return positions;
  const { col, dir } = sort;
  const m = dir === 'desc' ? -1 : 1;
  return [...positions].sort((a, b) => ((a[col] ?? -Infinity) - (b[col] ?? -Infinity)) * m);
}

function sortIcon(col, sort) {
  if (!sort || sort.col !== col) return '';
  return sort.dir === 'desc' ? ' ↓' : ' ↑';
}

const SORT_TH = 'text-align:right;padding:4px 8px;cursor:pointer;user-select:none;';
const SORT_TH_ACTIVE = 'color:var(--amber);';

function holdingsTable(positions, sort) {
  if (!positions.length) {
    return '<div style="color:var(--text-4);font-family:var(--font-mono);font-size:0.75rem;padding:12px 0;">No positions imported yet.</div>';
  }
  const sorted = sortPositions(positions, sort);
  const thStyle = (col) => SORT_TH + (sort?.col === col ? SORT_TH_ACTIVE : '');
  return `
    <table style="width:100%;border-collapse:collapse;font-family:var(--font-mono);font-size:0.72rem;margin-top:12px;">
      <thead>
        <tr style="color:var(--text-3);border-bottom:1px solid var(--border);">
          <th style="text-align:left;padding:4px 8px 8px 0;">Ticker</th>
          <th style="text-align:right;padding:4px 8px;">Shares</th>
          <th style="text-align:right;padding:4px 8px;">Price</th>
          <th data-sort-col="total_value" style="${thStyle('total_value')}">Value${sortIcon('total_value', sort)}</th>
          <th data-sort-col="weight" style="${thStyle('weight')}">Weight${sortIcon('weight', sort)}</th>
          <th data-sort-col="return_pct" style="${thStyle('return_pct')}padding-right:0;padding-left:8px;">Return${sortIcon('return_pct', sort)}</th>
        </tr>
      </thead>
      <tbody>
        ${sorted.map(p => `
          <tr style="border-bottom:1px solid var(--border);color:var(--text-2);">
            <td style="padding:6px 8px 6px 0;font-weight:500;color:var(--text);">${esc(p.ticker)}</td>
            <td style="text-align:right;padding:6px 8px;">${p.shares}</td>
            <td style="text-align:right;padding:6px 8px;">${p.current_price != null ? '$' + p.current_price.toFixed(2) : '–'}</td>
            <td style="text-align:right;padding:6px 8px;">${formatCurrency(p.total_value)}</td>
            <td style="text-align:right;padding:6px 8px;">${p.weight != null ? p.weight.toFixed(1) + '%' : '–'}</td>
            <td style="text-align:right;padding:6px 0 6px 8px;">${formatReturn(p.return_pct)}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

function portfolioCard(p, sort) {
  return `
    <div class="tracker-card" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px;">
        <div style="font-family:var(--font-serif);font-size:1.1rem;font-weight:600;color:var(--text);">${esc(p.name)}</div>
        <div style="display:flex;gap:8px;">
          <button class="settings-btn" data-import="${esc(p.name)}" style="font-size:0.65rem;padding:4px 10px;">Import CSV</button>
          <button class="settings-btn" data-remove="${esc(p.name)}" style="font-size:0.65rem;padding:4px 10px;color:var(--red);border-color:var(--red);">Remove</button>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:16px;">
        <div>
          <div style="font-family:var(--font-mono);font-size:0.62rem;color:var(--text-4);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Total Value</div>
          <div style="font-family:var(--font-mono);font-size:1rem;color:var(--text);">${formatCurrency(p.total_value)}</div>
        </div>
        <div>
          <div style="font-family:var(--font-mono);font-size:0.62rem;color:var(--text-4);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Total Return</div>
          <div style="font-family:var(--font-mono);font-size:1rem;">${formatReturn(p.total_return_pct)}</div>
        </div>
        <div>
          <div style="font-family:var(--font-mono);font-size:0.62rem;color:var(--text-4);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Positions</div>
          <div style="font-family:var(--font-mono);font-size:1rem;color:var(--text);">${p.positions.length}</div>
        </div>
      </div>
      <details data-portfolio="${esc(p.name)}">
        <summary style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-3);cursor:pointer;list-style:none;display:flex;align-items:center;gap:6px;user-select:none;">
          <span style="font-size:0.6rem;">▸</span> View Holdings
        </summary>
        <div class="holdings-content" style="overflow-x:auto;">
          ${holdingsTable(p.positions, sort)}
        </div>
      </details>
    </div>`;
}

// ── Comparison cards ──────────────────────────────────────────────────────────

const PORTFOLIO_COLORS = ['#06b6d4', '#22c55e', '#f97316', '#a855f7', '#ec4899', '#eab308'];

function renderComparisonCards(perfData, rangeOpt) {
  const container = document.getElementById('tracker-comparison-cards');
  if (!container || !perfData) return;

  const spyRet = perfData.spy ? (() => { const { values } = filterSeries(perfData.spy.series, rangeOpt); return values.length ? values[values.length - 1] : null; })() : null;

  const portfolioEntries = Object.entries(perfData).filter(([, v]) => v.type === 'portfolio');

  if (!portfolioEntries.length) {
    container.innerHTML = '';
    return;
  }

  let colorIdx = 0;
  container.innerHTML = portfolioEntries.map(([key, val]) => {
    const color = PORTFOLIO_COLORS[colorIdx++ % PORTFOLIO_COLORS.length];
    const { values } = filterSeries(val.series, rangeOpt);
    const ret = values.length ? values[values.length - 1] : null;
    const retTxt = ret != null ? (ret >= 0 ? '+' : '') + ret.toFixed(1) + '%' : '–';
    const retColor = ret == null ? 'var(--text-4)' : ret >= 0 ? 'var(--green)' : 'var(--red)';
    const delta = ret != null && spyRet != null ? ret - spyRet : null;
    const deltaTxt = delta != null ? `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}pp vs SPY` : '';
    const deltaColor = delta == null ? '' : delta >= 0 ? 'var(--green)' : 'var(--red)';
    return `
      <div class="metric-card" style="border-top: 2px solid ${color};">
        <div class="metric-label">${esc(key)}</div>
        <div class="metric-val" style="color:${retColor};">${retTxt}</div>
        ${delta != null ? `<div class="metric-delta" style="color:${deltaColor};">${deltaTxt}</div>` : ''}
      </div>`;
  }).join('');
}

// ── Chart ─────────────────────────────────────────────────────────────────────

function renderChart(perfData, rangeOpt) {
  const ctx = document.getElementById('tracker-chart')?.getContext('2d');
  if (!ctx) return;

  // Capture which datasets the user has toggled off before destroying
  const hiddenByLabel = {};
  if (chartInstance) {
    chartInstance.data.datasets.forEach((ds, i) => {
      hiddenByLabel[ds.label] = !chartInstance.isDatasetVisible(i);
    });
    chartInstance.destroy();
    chartInstance = null;
  }

  const isHidden = (label, defaultHidden = false) =>
    label in hiddenByLabel ? hiddenByLabel[label] : defaultHidden;

  const datasets = [];
  let colorIdx = 0;

  // User portfolios
  Object.entries(perfData).forEach(([key, val]) => {
    if (val.type !== 'portfolio') return;
    const color = PORTFOLIO_COLORS[colorIdx++ % PORTFOLIO_COLORS.length];
    const { labels, values } = filterSeries(val.series, rangeOpt);
    datasets.push({
      label: key,
      hidden: isHidden(key),
      data: labels.map((x, i) => ({ x, y: values[i] })),
      borderColor: color,
      borderWidth: 2.5,
      borderDash: [],
      pointRadius: 0,
      tension: 0.3,
    });
  });

  // Benchmarks
  const BENCH = { spy: { color: '#7a6e5c', dash: [3, 3] }, vti: { color: '#8b7355', dash: [2, 4] } };
  Object.entries(BENCH).forEach(([key, style]) => {
    if (!perfData[key]) return;
    const { labels, values } = filterSeries(perfData[key].series, rangeOpt);
    datasets.push({
      label: key.toUpperCase(),
      hidden: isHidden(key.toUpperCase()),
      data: labels.map((x, i) => ({ x, y: values[i] })),
      borderColor: style.color,
      borderWidth: 1.5,
      borderDash: style.dash,
      pointRadius: 0,
      tension: 0.3,
    });
  });

  // Committee — hidden by default until user clicks legend; toggle state persists across range changes
  if (perfData.committee) {
    const { labels, values } = filterSeries(perfData.committee.series, rangeOpt);
    datasets.push({
      label: 'Committee',
      hidden: isHidden('Committee', true),
      data: labels.map((x, i) => ({ x, y: values[i] })),
      borderColor: '#d4a027',
      borderWidth: 2,
      borderDash: [6, 3],
      pointRadius: 0,
      tension: 0.3,
    });
  }

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: {
        legend: {
          labels: { color: '#9a8e78', font: { family: 'IBM Plex Mono', size: 11 }, boxWidth: 24, padding: 20 },
        },
        tooltip: {
          mode: 'index', intersect: false,
          backgroundColor: '#1e1a13', borderColor: '#3d3020', borderWidth: 1,
          titleColor: '#9a8e78', bodyColor: '#c4b49a',
          titleFont: { family: 'IBM Plex Mono', size: 11 },
          bodyFont:  { family: 'IBM Plex Mono', size: 11 },
          callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y >= 0 ? '+' : ''}${ctx.parsed.y.toFixed(2)}%` },
        },
      },
      scales: {
        x: {
          type: 'time',
          grid: { color: '#2a2218' },
          ticks: { color: '#7a6e5c', font: { family: 'IBM Plex Mono', size: 10 } },
        },
        y: {
          grid: { color: '#2a2218' },
          ticks: {
            color: '#7a6e5c', font: { family: 'IBM Plex Mono', size: 10 },
            callback: v => (v >= 0 ? '+' : '') + v.toFixed(1) + '%',
          },
        },
      },
    },
  });
}

// ── Main init ─────────────────────────────────────────────────────────────────

export async function initTracker(latestRun) {
  const view = document.getElementById('view-tracker');
  let rangeOpt = '1Y';
  let portfoliosData = [];
  let perfData = null;
  const sortState = {};

  const PILL_BASE   = 'font-family:var(--font-mono);font-size:0.7rem;letter-spacing:0.06em;padding:5px 12px;border-radius:5px;border:1px solid var(--border);color:var(--text-3);background:transparent;cursor:pointer;transition:all 0.15s;';
  const PILL_ACTIVE = 'color:var(--bg);background:var(--amber);border-color:var(--amber);';

  view.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;">
      <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);">My Portfolios</div>
      <button id="tracker-add-btn" class="settings-btn primary" style="font-size:0.75rem;padding:7px 16px;">+ Add Portfolio</button>
    </div>
    <div id="tracker-cards" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(460px,1fr));gap:16px;margin-bottom:40px;"></div>

    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
      <div style="font-family:var(--font-serif);font-size:1.3rem;font-weight:600;color:var(--text-2);">Performance Comparison</div>
      <div style="display:flex;gap:4px;" id="tracker-range-pills">
        ${['1D','3D','1W','1M','3M','YTD','1Y'].map(r =>
          `<button class="range-pill" data-range="${r}"
            style="${PILL_BASE}${r === rangeOpt ? PILL_ACTIVE : ''}"
          >${r}</button>`).join('')}
      </div>
    </div>
    <div id="tracker-comparison-cards" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:20px;"></div>
    <div id="tracker-chart-container" style="height:400px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:12px;">
      <canvas id="tracker-chart"></canvas>
    </div>
    <div style="font-family:var(--font-mono);font-size:0.65rem;color:var(--text-4);margin-bottom:40px;">
      Past performance does not predict future results. Click legend items to toggle series. Committee portfolio hidden by default.
    </div>

    <div id="import-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:300;align-items:center;justify-content:center;">
      <div style="background:var(--surface-2);border:1px solid var(--border-bright);border-radius:10px;padding:32px;min-width:360px;max-width:440px;">
        <div style="font-family:var(--font-serif);font-size:1.2rem;font-weight:600;color:var(--text);margin-bottom:20px;">Import Portfolio</div>
        <div style="margin-bottom:16px;">
          <label style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-3);display:block;margin-bottom:6px;">Portfolio Name</label>
          <input id="import-name" class="settings-input" placeholder="e.g. Retirement" style="width:100%;box-sizing:border-box;">
        </div>
        <div style="margin-bottom:8px;">
          <label style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-3);display:block;margin-bottom:6px;">File (CSV or Excel)</label>
          <input id="import-file" type="file" accept=".csv,.xlsx" style="font-family:var(--font-mono);font-size:0.72rem;color:var(--text-2);width:100%;">
        </div>
        <div style="font-family:var(--font-mono);font-size:0.62rem;color:var(--text-4);margin-bottom:20px;">
          CSV columns: <em>ticker, shares, avg_cost</em> (avg_cost optional)
        </div>
        <div style="display:flex;gap:10px;">
          <button id="import-submit" class="settings-btn primary" style="flex:1;">Import</button>
          <button id="import-cancel" class="settings-btn" style="flex:1;">Cancel</button>
        </div>
      </div>
    </div>`;

  // ── Modal helpers ────────────────────────────────────────────────────────────
  const modal = document.getElementById('import-modal');

  function openModal(prefilledName = '') {
    document.getElementById('import-name').value = prefilledName;
    document.getElementById('import-file').value = '';
    modal.style.display = 'flex';
    document.getElementById('import-name').focus();
  }

  function closeModal() {
    modal.style.display = 'none';
  }

  document.getElementById('import-cancel').addEventListener('click', closeModal);
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });

  document.getElementById('import-submit').addEventListener('click', async () => {
    const name = document.getElementById('import-name').value.trim();
    const file = document.getElementById('import-file').files[0];
    if (!name) { showToast('Portfolio name is required', 'error'); return; }
    if (!file) { showToast('Please select a file', 'error'); return; }
    try {
      await api.importPortfolio(name, file);
      closeModal();
      await reload();
      showToast(`${name} imported successfully`);
    } catch (e) {
      showToast(e.message, 'error');
    }
  });

  document.getElementById('import-name').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('import-submit').click();
    if (e.key === 'Escape') closeModal();
  });

  // ── Add portfolio button ─────────────────────────────────────────────────────
  document.getElementById('tracker-add-btn').addEventListener('click', () => openModal(''));

  // ── Card event delegation ─────────────────────────────────────────────────────
  document.getElementById('tracker-cards').addEventListener('click', async e => {
    const importBtn = e.target.closest('[data-import]');
    const removeBtn = e.target.closest('[data-remove]');
    const sortTh = e.target.closest('th[data-sort-col]');

    if (importBtn) {
      openModal(importBtn.dataset.import);
      return;
    }

    if (removeBtn) {
      const name = removeBtn.dataset.remove;
      if (!confirm(`Remove "${name}"?`)) return;
      try {
        await api.deletePortfolio(name);
        await reload();
        showToast(`${name} removed`);
      } catch (e) {
        showToast(e.message, 'error');
      }
    }

    if (sortTh) {
      const col = sortTh.dataset.sortCol;
      const details = sortTh.closest('details[data-portfolio]');
      if (!details) return;
      const name = details.dataset.portfolio;
      const cur = sortState[name];
      sortState[name] = cur?.col === col
        ? { col, dir: cur.dir === 'desc' ? 'asc' : 'desc' }
        : { col, dir: 'desc' };
      const portfolio = portfoliosData.find(p => p.name === name);
      if (portfolio) {
        const content = details.querySelector('.holdings-content');
        if (content) content.innerHTML = holdingsTable(portfolio.positions, sortState[name]);
      }
    }
  });

  // ── Range pills ──────────────────────────────────────────────────────────────
  view.querySelectorAll('.range-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      rangeOpt = btn.dataset.range;
      view.querySelectorAll('.range-pill').forEach(b => { b.style.cssText = PILL_BASE; });
      btn.style.cssText = PILL_BASE + PILL_ACTIVE;
      if (perfData) {
        renderComparisonCards(perfData, rangeOpt);
        renderChart(perfData, rangeOpt);
      }
    });
  });

  // ── Data loading ─────────────────────────────────────────────────────────────
  function renderCards() {
    const container = document.getElementById('tracker-cards');
    if (!portfoliosData.length) {
      container.innerHTML = `
        <div style="grid-column:1/-1;font-family:var(--font-mono);font-size:0.8rem;color:var(--text-4);padding:24px 0;">
          No portfolios yet. Click "+ Add Portfolio" and import a CSV to get started.
        </div>`;
      return;
    }
    container.innerHTML = portfoliosData.map(p => portfolioCard(p, sortState[p.name])).join('');
  }

  async function reload() {
    try {
      portfoliosData = await api.getPortfolios();
    } catch (e) {
      showToast(e.message, 'error');
      return;
    }
    renderCards();

    if (portfoliosData.length) {
      perfData = null;  // clear before fetch so stale data doesn't linger on error
      document.getElementById('tracker-chart-container').innerHTML =
        '<div style="color:var(--text-4);font-family:var(--font-mono);font-size:0.8rem;padding:20px;">Loading performance data…</div>';
      try {
        perfData = await api.getPortfoliosPerformance();
        document.getElementById('tracker-chart-container').innerHTML =
          '<canvas id="tracker-chart"></canvas>';
        if (Object.keys(perfData).length) {
          renderComparisonCards(perfData, rangeOpt);
          renderChart(perfData, rangeOpt);
        }
      } catch (e) {
        document.getElementById('tracker-chart-container').innerHTML =
          `<div style="color:var(--red);font-family:var(--font-mono);font-size:0.8rem;padding:20px;">Could not load performance data.</div>`;
      }
    }
  }

  await reload();
}
