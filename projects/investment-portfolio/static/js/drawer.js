import { api } from './api.js';

const MEMBER_NAMES = { claude: 'Claude', gpt: 'GPT', gemini: 'Gemini' };

let _openTicker = null;

function applyUpsideClass(el, val) {
  el.classList.remove('pos', 'neg');
  if (val != null) el.classList.add(val >= 0 ? 'pos' : 'neg');
}

export function openDrawer(holding, run = null) {
  const d = holding;

  document.getElementById('d-bar').className = `drawer-top-bar ${d.conviction}`;
  document.getElementById('d-ticker').textContent  = d.ticker;
  document.getElementById('d-company').textContent = d.company_name;

  const conLabel = d.conviction === 'moonshot' ? '🌙 Moonshot' : 'Core';
  const consPill = d.nominated_by?.length === 3
    ? `<span class="pill consensus">3-way consensus</span>` : '';
  document.getElementById('d-pills').innerHTML = `
    <span class="pill ${d.conviction}">${conLabel}</span>
    <span class="pill weight">${d.weight}% weight</span>
    ${consPill}`;

  _openTicker = d.ticker;

  const frozenPriceStr = d.current_price != null
    ? '$' + d.current_price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '—';
  const frozenMeanCls    = d.mean_upside_pct != null   ? (d.mean_upside_pct >= 0 ? 'pos' : 'neg')   : '';
  const frozenMedianCls  = d.median_upside_pct != null ? (d.median_upside_pct >= 0 ? 'pos' : 'neg') : '';
  const frozenMeanStr    = d.mean_upside_pct != null   ? (d.mean_upside_pct >= 0 ? '+' : '')   + d.mean_upside_pct.toFixed(1)   + '%' : '—';
  const frozenMedianStr  = d.median_upside_pct != null ? (d.median_upside_pct >= 0 ? '+' : '') + d.median_upside_pct.toFixed(1) + '%' : '—';

  document.getElementById('d-upside').innerHTML = `
    <div class="upside-card">
      <div class="upside-card-label">Current Price</div>
      <div class="upside-card-val" id="d-price-live">…</div>
      <div class="upside-card-sub">${frozenPriceStr}</div>
    </div>
    <div class="upside-card">
      <div class="upside-card-label">Mean Target</div>
      <div class="upside-card-val" id="d-mean-live">…</div>
      <div class="upside-card-sub ${frozenMeanCls}">${frozenMeanStr}</div>
    </div>
    <div class="upside-card">
      <div class="upside-card-label">Median Target</div>
      <div class="upside-card-val" id="d-median-live">…</div>
      <div class="upside-card-sub ${frozenMedianCls}">${frozenMedianStr}</div>
    </div>`;

  fetchLiveQuote(d.ticker);

  document.getElementById('d-members').innerHTML = (d.nominated_by || []).map(m => `
    <div class="member-chip"><img src="/static/${m.toLowerCase()}.png" class="member-thumb" alt="${m.toLowerCase()}">${MEMBER_NAMES[m.toLowerCase()] || m}</div>`).join('');

  const totalAmount = run?.investment_amount ?? 10000;
  const investAmt = Math.round(totalAmount * (d.weight / 100));
  document.getElementById('d-invest').textContent = `Recommended investment: $${investAmt.toLocaleString()} of $${totalAmount.toLocaleString()}`;

  document.getElementById('d-rationale').textContent = d.rationale;

  const analystSection = document.getElementById('d-analyst-section');
  const analystTakes   = document.getElementById('d-analyst-takes');
  if (run && (d.nominated_by || []).length) {
    const picksMap = { claude: run.claude_picks, gpt: run.gpt_picks, gemini: run.gemini_picks };
    analystTakes.innerHTML = (d.nominated_by || []).map(m => {
      const key  = m.toLowerCase();
      const pick = (picksMap[key] || []).find(p => p.ticker === d.ticker);
      if (!pick) return '';
      return `
        <div style="display:flex;flex-direction:column;gap:6px;padding:14px 0;border-bottom:1px solid var(--border);">
          <div style="display:flex;align-items:center;gap:8px;">
            <img src="/static/${key}.png" class="member-thumb" alt="${key}" style="width:20px;height:20px;">
            <span style="font-family:var(--font-mono);font-size:0.78rem;font-weight:600;color:var(--text);">${MEMBER_NAMES[key] || m}</span>
            <span class="pill ${pick.conviction}" style="font-size:0.62rem;">${pick.conviction}</span>
          </div>
          <div style="font-size:0.82rem;line-height:1.65;color:var(--text-2);">${pick.rationale}</div>
        </div>`;
    }).join('');
    analystSection.style.display = '';
  } else {
    analystSection.style.display = 'none';
  }

  document.getElementById('backdrop').classList.add('open');
  document.getElementById('drawer').classList.add('open');
}

export function closeDrawer() {
  document.getElementById('backdrop').classList.remove('open');
  document.getElementById('drawer').classList.remove('open');
}

export function initDrawer() {
  document.getElementById('d-close').addEventListener('click', closeDrawer);
  document.getElementById('backdrop').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
}

async function fetchLiveQuote(ticker) {
  let quote;
  try {
    quote = await api.getQuote(ticker);
  } catch (e) {
    quote = { current_price: null, mean_upside_pct: null, median_upside_pct: null };
  }
  if (_openTicker !== ticker) return;

  const priceEl  = document.getElementById('d-price-live');
  const meanEl   = document.getElementById('d-mean-live');
  const medianEl = document.getElementById('d-median-live');
  if (!priceEl) return;

  priceEl.textContent = quote.current_price != null
    ? '$' + quote.current_price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : '—';

  meanEl.textContent = quote.mean_upside_pct != null
    ? (quote.mean_upside_pct >= 0 ? '+' : '') + quote.mean_upside_pct.toFixed(1) + '%'
    : '—';
  applyUpsideClass(meanEl, quote.mean_upside_pct);

  medianEl.textContent = quote.median_upside_pct != null
    ? (quote.median_upside_pct >= 0 ? '+' : '') + quote.median_upside_pct.toFixed(1) + '%'
    : '—';
  applyUpsideClass(medianEl, quote.median_upside_pct);
}
