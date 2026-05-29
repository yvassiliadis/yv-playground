import { squarify }   from '../treemap.js';
import { openDrawer } from '../drawer.js';

const CONTAINER_ID = 'view-portfolio';
const GAP = 3;
let resizeHandler = null;
let _lastRun = null;

// ── Section header helper ──────────────────────────────────────────────────
function sectionHeader(label, right = '') {
  return `
    <div class="section-header">
      <span class="section-label">${label}</span>
      <div class="section-rule"></div>
      ${right}
    </div>`;
}

// ── Treemap ────────────────────────────────────────────────────────────────
function renderTreemap(container, holdings) {
  const W = container.clientWidth;
  if (W === 0) return;
  const BASE_H = 480;
  const TARGET_MIN_TILE_H = 96;
  const MAX_H = 700;

  const MIN_TILE_H = 44;

  let H = BASE_H;
  let rects = squarify(holdings, 0, 0, W, H);

  if (rects.length > 1) {
    const heights = rects.map(r => r.h).sort((a, b) => a - b);
    const p10TileH = heights[Math.floor(heights.length * 0.1)];
    if (p10TileH < TARGET_MIN_TILE_H) {
      H = Math.min(MAX_H, Math.ceil(BASE_H * (TARGET_MIN_TILE_H / p10TileH)));
      rects = squarify(holdings, 0, 0, W, H);
    }
  }

  // Drop tiles too short to be useful; re-run squarify on the visible set
  const tinyTickers = new Set(rects.filter(r => r.h < MIN_TILE_H).map(r => r.data.ticker));
  let overflow = [];
  if (tinyTickers.size > 0) {
    overflow = holdings.filter(h => tinyTickers.has(h.ticker));
    rects = squarify(holdings.filter(h => !tinyTickers.has(h.ticker)), 0, 0, W, H);
  }

  container.innerHTML = '';
  container.style.height = `${Math.ceil(H + GAP)}px`;

  rects.forEach((r, idx) => {
    const d    = r.data;
    const area = r.w * r.h;

    const tile = document.createElement('div');
    tile.className = `tile ${d.conviction} c${(d.nominated_by || []).length}`;
    if (area < 7000)  tile.classList.add('sz-sm');
    if (area < 3500)  tile.classList.add('sz-xs');
    if (r.h < 70)     tile.classList.add('tile-compact');
    if (r.h < 28)     tile.classList.add('h-sm');
    if (r.h < 14)     tile.classList.add('h-xs');

    // Scale the gap down proportionally for thin/narrow tiles so they stay visible
    const gapV = Math.min(GAP, Math.floor(r.h / 4));
    const gapH = Math.min(GAP, Math.floor(r.w / 4));

    tile.style.cssText = `
      position:absolute;
      left:${r.x + gapH / 2}px; top:${r.y + gapV / 2}px;
      width:${r.w - gapH}px; height:${r.h - gapV}px;
      animation-delay:${idx * 25}ms;
    `;

    const fs   = Math.min(1.15, Math.max(0.78, r.w / 90)) + 'rem';
    const wfs  = Math.min(1.0,  Math.max(0.72, r.w / 120)) + 'rem';
    const up   = d.mean_upside_pct;
    const uCls = up != null && up >= 0 ? 'pos' : 'neg';
    const uTxt = up != null ? (up >= 0 ? `+${up.toFixed(1)}%` : `${up.toFixed(1)}%`) : '';

    tile.innerHTML = `
      <div class="tile-inner">
        <div class="tile-top">
          <div class="tile-ticker" style="font-size:${fs}">${d.ticker}</div>
          <div class="tile-company">${d.company_name}</div>
          <div class="tile-dots">${(d.nominated_by || []).map(m => `<img src="/static/${m.toLowerCase()}.png" class="member-thumb" alt="${m}">`).join('')}</div>
        </div>
        <div class="tile-bottom">
          <span class="tile-weight" style="font-size:${wfs}">${d.weight}%</span>
          ${uTxt ? `<span class="tile-upside ${uCls}">${uTxt}</span>` : ''}
        </div>
      </div>`;

    tile.addEventListener('click', () => openDrawer(d, _lastRun));
    container.appendChild(tile);
  });

}

// ── Signal strip ──────────────────────────────────────────────────────────
function signalList(items, type, maxU) {
  return items
    .slice()
    .sort((a, b) => (b.mean_upside_pct ?? 0) - (a.mean_upside_pct ?? 0))
    .map((h, i) => {
      const u    = h.mean_upside_pct ?? 0;
      const uCls = u >= 0 ? 'pos' : 'neg';
      const uTxt = u >= 0 ? `+${u.toFixed(1)}%` : `${u.toFixed(1)}%`;
      const pct  = (Math.max(0, u) / maxU * 100).toFixed(1);
      return `
        <div class="signal-item" data-ticker="${h.ticker}">
          <span class="signal-rank">${i + 1}</span>
          <span class="signal-ticker">${h.ticker}</span>
          <div class="signal-bar-track">
            <div class="signal-bar-fill ${type}" data-pct="${pct}"></div>
          </div>
          <span class="signal-pct ${uCls}">${uTxt}</span>
          <span class="signal-weight">${h.weight}%</span>
        </div>`;
    }).join('');
}

function animateBars(container) {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    container.querySelectorAll('.signal-bar-fill').forEach(b => { b.style.width = b.dataset.pct + '%'; });
  }));
}

// ── Public API ─────────────────────────────────────────────────────────────
export function onPortfolioActivated() {
  if (!_lastRun) return;
  setTimeout(() => {
    const wrap = document.getElementById('treemap-wrap');
    if (wrap) renderTreemap(wrap, _lastRun.portfolio);
  }, 50);
}

export function initPortfolio(run) {
  _lastRun = run;
  const view = document.getElementById(CONTAINER_ID);

  if (!run) {
    view.innerHTML = `<div class="empty-state">No portfolio yet — click Run Committee to begin.</div>`;
    return;
  }

  const ts   = new Date(run.timestamp).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
  const core = run.portfolio.filter(h => h.conviction === 'core');
  const moon = run.portfolio.filter(h => h.conviction === 'moonshot');

  view.innerHTML = `
    <div style="margin-bottom:24px;">
      <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:6px;">
        My Big Fat Greek Portfolio<br><em style="font-style:italic;color:var(--amber);font-size:calc(1.9rem - 2pt)">by Feta Beta Capital</em>
      </div>
      <div style="font-family:var(--font-mono);font-size:0.68rem;color:var(--text-4);">
        ${run.portfolio.length} suggested positions · Run ${ts}
      </div>
    </div>

    ${sectionHeader('Allocation Map', `
      <div style="display:flex;gap:16px;align-items:center;font-family:var(--font-mono);font-size:0.62rem;color:var(--text-4);">
        <span style="display:flex;align-items:center;gap:5px;">
          <span style="width:10px;height:10px;border-radius:2px;background:#272114;border:1px solid #d4a027;display:inline-block;"></span>Core
        </span>
        <span style="display:flex;align-items:center;gap:5px;">
          <span style="width:10px;height:10px;border-radius:2px;background:#1e1530;border:1px solid #9d7ff5;display:inline-block;"></span>Moonshot
        </span>
        <span style="display:flex;align-items:center;gap:4px;">
          <span class="dot claude" style="width:6px;height:6px;"></span>
          <span class="dot gpt" style="width:6px;height:6px;"></span>
          <span class="dot gemini" style="width:6px;height:6px;"></span>
          &nbsp;3-way = green border
        </span>
      </div>`)}

    <div id="treemap-wrap" style="position:relative;background:var(--surface);margin-bottom:48px;"></div>

    ${sectionHeader('Upside Opportunity', '<span class="section-label" style="white-space:nowrap;">analyst consensus vs. current price</span>')}

    <div style="display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--border);border-radius:6px;overflow:hidden;">
      <div style="padding:24px 28px;border-right:1px solid var(--border);">
        <div style="font-family:var(--font-serif);font-size:1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:20px;display:flex;align-items:center;gap:10px;">
          <div style="width:3px;height:16px;border-radius:1.5px;background:var(--amber);flex-shrink:0;"></div>Core Positions
        </div>
        <div id="core-signal"></div>
      </div>
      <div style="padding:24px 28px;">
        <div style="font-family:var(--font-serif);font-size:1rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:20px;display:flex;align-items:center;gap:10px;">
          <div style="width:3px;height:16px;border-radius:1.5px;background:var(--purple);flex-shrink:0;"></div>Moonshot Positions
        </div>
        <div id="moon-signal"></div>
      </div>
    </div>
  `;

  // Treemap tile styles (injected once since they're dynamic/animation-based)
  if (!document.getElementById('tile-styles')) {
    const s = document.createElement('style');
    s.id = 'tile-styles';
    s.textContent = `
      #treemap-wrap::after { content:''; position:absolute; inset:0; border:1px solid var(--border); border-radius:6px; pointer-events:none; }
      .tile { position:absolute; padding:2px; animation: tileIn 0.32s cubic-bezier(0.22,1,0.36,1) both; }
      @keyframes tileIn { from { opacity:0; transform:scale(0.94); } to { opacity:1; transform:scale(1); } }
      .tile-inner {
        width:100%; height:100%; border-radius:4px; padding:6px 8px; overflow:hidden;
        cursor:pointer; display:flex; flex-direction:column; justify-content:space-between;
        overflow:hidden; position:relative; transition: filter 0.15s;
      }
      .tile-inner::after { content:''; position:absolute; inset:0; border-radius:4px; opacity:0; transition:opacity 0.2s; background:rgba(255,255,255,0.04); pointer-events:none; }
      .tile:hover .tile-inner::after { opacity:1; }
      .tile:hover .tile-inner { filter:brightness(1.12); }
      .tile.core .tile-inner     { background:linear-gradient(145deg,#1e1a12,#272114); border:1px solid #3a2e1a; }
      .tile.moonshot .tile-inner { background:linear-gradient(145deg,#14101e,#1e1530); border:1px solid #2e1e56; }
      .tile.c1 .tile-inner  { box-shadow:inset 0 0 0 1px rgba(212,160,39,0.1); }
      .tile.c2 .tile-inner  { box-shadow:inset 0 0 0 1px rgba(212,160,39,0.35), 0 0 14px rgba(212,160,39,0.06); }
      .tile.c3 .tile-inner  { box-shadow:inset 0 0 0 1.5px rgba(34,197,94,0.55), 0 0 20px rgba(34,197,94,0.10); }
      .tile.moonshot.c2 .tile-inner { box-shadow:inset 0 0 0 1px rgba(157,127,245,0.45), 0 0 14px rgba(157,127,245,0.08); }
      .tile-ticker { font-family:var(--font-mono); font-weight:600; line-height:1; letter-spacing:0.04em; color:var(--text); }
      .tile-company { font-size:0.67rem; color:var(--text-3); line-height:1.35; margin-top:3px; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
      .tile-dots { display:flex; gap:3px; margin-top:5px; }
      .tile-bottom { display:flex; justify-content:space-between; align-items:flex-end; margin-top:6px; }
      .tile-weight { font-family:var(--font-mono); font-weight:600; color:var(--amber); line-height:1; }
      .tile.moonshot .tile-weight { color:var(--purple); }
      .tile-upside { font-family:var(--font-mono); font-size:0.75rem; line-height:1; }
      .tile-upside.pos { color:var(--green); }
      .tile-upside.neg { color:var(--red); }
      .tile.sz-sm .tile-company, .tile.sz-sm .tile-dots { display:none; }
      .tile.sz-xs .tile-company, .tile.sz-xs .tile-dots, .tile.sz-xs .tile-bottom { display:none; }
      .tile.sz-xs .tile-ticker { font-size:0.78rem !important; }
      .tile.tile-compact .tile-inner { padding:4px 6px; }
      .tile.tile-compact .tile-company, .tile.tile-compact .tile-dots { display:none; }
      .tile.h-sm .tile-inner { padding:4px 8px; }
      .tile.h-sm .tile-company, .tile.h-sm .tile-dots, .tile.h-sm .tile-bottom { display:none; }
      .tile.h-sm .tile-ticker { font-size:0.72rem !important; line-height:1; }
      .tile.h-xs .tile-inner { padding:2px 6px; }
      .tile.h-xs .tile-company, .tile.h-xs .tile-dots, .tile.h-xs .tile-bottom, .tile.h-xs .tile-ticker { display:none; }
      .signal-item { display:flex; align-items:center; gap:10px; padding:9px 0; border-bottom:1px solid var(--border); cursor:pointer; transition:padding 0.1s, background 0.1s; border-radius:0; margin:0; }
      .signal-item:last-child { border-bottom:none; }
      .signal-item:hover { padding:9px 10px; margin:0 -10px; background:var(--surface-2); border-radius:4px; }
      .signal-rank { font-family:var(--font-mono); font-size:0.62rem; color:var(--text-4); width:14px; text-align:right; flex-shrink:0; }
      .signal-ticker { font-family:var(--font-mono); font-size:0.8rem; font-weight:600; color:var(--text); width:48px; flex-shrink:0; letter-spacing:0.03em; }
      .signal-bar-track { flex:1; height:3px; background:var(--border); border-radius:1.5px; overflow:hidden; }
      .signal-bar-fill { height:100%; border-radius:1.5px; width:0; transition:width 0.7s cubic-bezier(0.22,1,0.36,1); }
      .signal-bar-fill.core     { background:linear-gradient(90deg, var(--amber-dim), var(--amber)); }
      .signal-bar-fill.moonshot { background:linear-gradient(90deg, var(--purple-dim), var(--purple)); }
      .signal-pct { font-family:var(--font-mono); font-size:0.72rem; font-weight:500; width:52px; text-align:right; flex-shrink:0; }
      .signal-pct.pos { color:var(--green); }
      .signal-pct.neg { color:var(--red); }
      .signal-weight { font-family:var(--font-mono); font-size:0.68rem; color:var(--text-4); width:28px; text-align:right; flex-shrink:0; }
    `;
    document.head.appendChild(s);
  }

  const wrap = document.getElementById('treemap-wrap');
  requestAnimationFrame(() => renderTreemap(wrap, run.portfolio));

  // Shared scale so core and moonshot bars are comparable
  const maxU = Math.max(...run.portfolio.map(h => h.mean_upside_pct ?? 0), 1);

  // Render signal strips
  document.getElementById('core-signal').innerHTML = signalList(core, 'core', maxU);
  document.getElementById('moon-signal').innerHTML = signalList(moon, 'moonshot', maxU);

  // Wire signal item clicks
  view.querySelectorAll('.signal-item').forEach(el => {
    el.addEventListener('click', () => {
      const h = run.portfolio.find(x => x.ticker === el.dataset.ticker);
      if (h) openDrawer(h, run);
    });
  });

  // Animate bars
  animateBars(view);

  // Re-render treemap on resize (remove previous handler to avoid accumulation)
  if (resizeHandler) window.removeEventListener('resize', resizeHandler);
  let resizeTimer;
  resizeHandler = () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => renderTreemap(wrap, run.portfolio), 120);
  };
  window.addEventListener('resize', resizeHandler);
}

export function refreshPortfolio(run) {
  initPortfolio(run);
}
