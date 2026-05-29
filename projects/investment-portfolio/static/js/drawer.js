const MEMBER_NAMES = { claude: 'Claude', gpt: 'GPT-4o', gemini: 'Gemini' };

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

  const uCls  = (d.mean_upside_pct ?? 0) >= 0 ? 'pos' : 'neg';
  const uSign = (d.mean_upside_pct ?? 0) >= 0 ? '+' : '';
  const mSign = (d.median_upside_pct ?? 0) >= 0 ? '+' : '';
  const mCls  = (d.median_upside_pct ?? 0) >= 0 ? 'pos' : 'neg';
  const priceStr = d.current_price != null ? '$' + d.current_price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
  document.getElementById('d-upside').innerHTML = `
    <div class="upside-card">
      <div class="upside-card-label">Current Price</div>
      <div class="upside-card-val">${priceStr}</div>
    </div>
    <div class="upside-card">
      <div class="upside-card-label">Mean Target</div>
      <div class="upside-card-val ${uCls}">${d.mean_upside_pct != null ? uSign + d.mean_upside_pct.toFixed(1) + '%' : '—'}</div>
    </div>
    <div class="upside-card">
      <div class="upside-card-label">Median Target</div>
      <div class="upside-card-val ${mCls}">${d.median_upside_pct != null ? mSign + d.median_upside_pct.toFixed(1) + '%' : '—'}</div>
    </div>`;

  document.getElementById('d-members').innerHTML = (d.nominated_by || []).map(m => `
    <div class="member-chip"><img src="/static/${m.toLowerCase()}.png" class="member-thumb" alt="${m.toLowerCase()}">${MEMBER_NAMES[m.toLowerCase()] || m}</div>`).join('');

  const investAmt = Math.round(10000 * (d.weight / 100));
  document.getElementById('d-invest').textContent = `Recommended investment: $${investAmt.toLocaleString()} of $10,000`;

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
