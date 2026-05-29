import { api } from '../api.js';
import { showToast } from '../app.js';

let chartInstance = null;

function filterSeries(rawDict, rangeOpt) {
  const entries = Object.entries(rawDict)
    .map(([k, v]) => [new Date(k), v])
    .sort((a, b) => a[0] - b[0]);

  if (!entries.length) return { labels: [], values: [] };

  const now = new Date();
  const cutoffs = {
    '1W':  new Date(now - 7  * 86400000),
    '1M':  new Date(now - 30 * 86400000),
    '3M':  new Date(now - 90 * 86400000),
    'YTD': new Date(now.getFullYear(), 0, 1),
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

function lastReturn(rawDict, rangeOpt) {
  const { values } = filterSeries(rawDict, rangeOpt);
  return values.length ? values[values.length - 1] : 0;
}

function metricCard(label, val, delta) {
  const vCls = val >= 0 ? 'pos' : 'neg';
  const vTxt = (val >= 0 ? '+' : '') + val.toFixed(1) + '%';
  const dCls = delta != null ? (delta >= 0 ? 'pos' : 'neg') : '';
  const dTxt = delta != null ? `${delta >= 0 ? '+' : ''}${delta.toFixed(1)}pp vs portfolio` : '';
  return `
    <div class="metric-card">
      <div class="metric-label">${label}</div>
      <div class="metric-val ${vCls}">${vTxt}</div>
      ${delta != null ? `<div class="metric-delta ${dCls}">${dTxt}</div>` : ''}
    </div>`;
}

function renderChart(perf, rangeOpt) {
  const ctx = document.getElementById('perf-chart').getContext('2d');
  if (chartInstance) { chartInstance.destroy(); chartInstance = null; }

  const datasets = [
    { key: 'portfolio', label: 'Portfolio', color: '#06b6d4', width: 2.5, dash: [] },
    { key: 'spy',       label: 'SPY',       color: '#7a6e5c', width: 1.5, dash: [3,3] },
    { key: 'vgt',       label: 'VGT',       color: '#d4a027', width: 1.5, dash: [6,3] },
    { key: 'vti',       label: 'VTI',       color: '#8b7355', width: 1.5, dash: [2,4] },
  ].filter(d => perf[d.key]);

  chartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: datasets.map(d => {
        const { labels, values } = filterSeries(perf[d.key].series, rangeOpt);
        return {
          label: d.label,
          data: labels.map((x, i) => ({ x, y: values[i] })),
          borderColor: d.color,
          borderWidth: d.width,
          borderDash: d.dash,
          pointRadius: 0,
          tension: 0.3,
        };
      }),
    },
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

export async function initPerformance(run) {
  const view = document.getElementById('view-performance');

  if (!run) {
    view.innerHTML = `<div class="empty-state">Run the committee first to see performance data.</div>`;
    return;
  }

  let rangeOpt = '1Y';
  let perf = null;

  view.innerHTML = `
    <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:24px;">
      Performance
    </div>
    <div style="display:flex;gap:4px;margin-bottom:24px;" id="range-pills">
      ${['1W','1M','3M','YTD','1Y'].map(r => `
        <button class="range-pill${r === rangeOpt ? ' active' : ''}" data-range="${r}"
          style="font-family:var(--font-mono);font-size:0.7rem;letter-spacing:0.06em;padding:5px 12px;border-radius:5px;border:1px solid var(--border);color:var(--text-3);background:transparent;cursor:pointer;transition:all 0.15s;"
        >${r}</button>`).join('')}
    </div>
    <div id="metric-cards" style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;margin-bottom:28px;"></div>
    <div style="height:400px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;">
      <canvas id="perf-chart"></canvas>
    </div>
    <div style="font-family:var(--font-mono);font-size:0.65rem;color:var(--text-4);margin-top:12px;">
      Past performance does not predict future results.
    </div>`;

  const PILL_BASE   = 'font-family:var(--font-mono);font-size:0.7rem;letter-spacing:0.06em;padding:5px 12px;border-radius:5px;border:1px solid var(--border);color:var(--text-3);background:transparent;cursor:pointer;transition:all 0.15s;';
  const PILL_ACTIVE = 'color:var(--bg);background:var(--amber);border-color:var(--amber);';
  view.querySelectorAll('.range-pill').forEach(btn => {
    if (btn.dataset.range === rangeOpt) btn.style.cssText = PILL_BASE + PILL_ACTIVE;
    btn.addEventListener('click', () => {
      rangeOpt = btn.dataset.range;
      view.querySelectorAll('.range-pill').forEach(b => { b.style.cssText = PILL_BASE; });
      btn.style.cssText = PILL_BASE + PILL_ACTIVE;
      if (perf) updateView();
    });
  });

  function updateView() {
    const pRet   = perf.portfolio ? lastReturn(perf.portfolio.series, rangeOpt) : 0;
    const spyRet = perf.spy       ? lastReturn(perf.spy.series, rangeOpt)       : null;
    const vgtRet = perf.vgt       ? lastReturn(perf.vgt.series, rangeOpt)       : null;
    const vtiRet = perf.vti       ? lastReturn(perf.vti.series, rangeOpt)       : null;

    document.getElementById('metric-cards').innerHTML =
      metricCard('Portfolio', pRet, null) +
      (spyRet != null ? metricCard('SPY', spyRet, spyRet - pRet) : '') +
      (vgtRet != null ? metricCard('VGT', vgtRet, vgtRet - pRet) : '') +
      (vtiRet != null ? metricCard('VTI', vtiRet, vtiRet - pRet) : '');

    renderChart(perf, rangeOpt);
  }

  document.getElementById('metric-cards').innerHTML = `<div class="empty-state" style="padding:20px 0;">Loading price data…</div>`;
  try {
    const tickers = run.portfolio.map(h => h.ticker).join(',');
    const weights = run.portfolio.map(h => h.weight).join(',');
    perf = await api.getPerformance(tickers, weights);
    updateView();
  } catch (e) {
    showToast(e.message, 'error');
    document.getElementById('metric-cards').innerHTML = `<div class="empty-state">Could not load performance data.</div>`;
  }
}
