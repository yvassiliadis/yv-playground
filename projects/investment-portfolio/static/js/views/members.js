const MEMBER_CONFIG = {
  claude: { label: 'Claude',  color: '#f59e0b', cls: 'claude' },
  gpt:    { label: 'GPT-4o',  color: '#10b981', cls: 'gpt'    },
  gemini: { label: 'Gemini',  color: '#3b82f6', cls: 'gemini' },
};

function pickEntryHtml(pick, color) {
  const convCls   = pick.conviction === 'moonshot' ? 'moonshot' : 'core';
  const convLabel = pick.conviction === 'moonshot' ? '🌙 Moonshot' : 'Core';
  const up     = pick.mean_upside_pct;
  const uSign  = up != null && up >= 0 ? '+' : '';
  const uCls   = up != null && up >= 0 ? 'pos' : 'neg';
  const uTxt   = up != null ? `<span class="signal-pct ${uCls}" style="width:auto">${uSign}${up.toFixed(1)}%</span>` : '';
  const edge   = pick.variant_perception
    ? `<p style="font-size:0.78rem;color:var(--text-3);margin-top:8px;line-height:1.65;">
         <strong style="color:var(--text-2);">Edge:</strong> ${pick.variant_perception}</p>` : '';
  const rationale = pick.rationale
    ? `<p style="font-size:0.83rem;line-height:1.75;color:var(--text-2);">${pick.rationale}</p>` : '';

  return `
    <details style="border-bottom:1px solid var(--border);padding:12px 0;" class="pick-entry">
      <summary style="list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">
        <div style="flex:1;min-width:0;">
          <div style="font-family:var(--font-mono);font-size:1.02rem;font-weight:700;letter-spacing:0.04em;color:${color};line-height:1;">${pick.ticker}</div>
          <div style="font-size:0.72rem;color:var(--text-3);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${pick.company_name}</div>
          <div style="display:flex;align-items:center;gap:6px;margin-top:5px;flex-wrap:wrap;">
            <span class="pill ${convCls}" style="font-size:0.6rem;">${convLabel}</span>
            ${uTxt}
          </div>
        </div>
        <span style="font-family:var(--font-mono);font-size:0.8rem;color:var(--text-4);flex-shrink:0;padding-top:2px;">+</span>
      </summary>
      <div style="padding:10px 0 4px;">${rationale}${edge}</div>
    </details>`;
}

function memberColHtml(memberKey, run, sources) {
  const cfg  = MEMBER_CONFIG[memberKey];
  const picks = run[`${memberKey}_picks`] || [];
  const core  = picks.filter(p => p.conviction === 'core');
  const moon  = picks.filter(p => p.conviction === 'moonshot');

  const entries = core.map(p => pickEntryHtml(p, cfg.color)).join('')
    + (moon.length ? `<div style="font-size:0.65rem;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;color:var(--purple);margin:20px 0 2px;padding-top:16px;border-top:1px solid var(--border);">🌙 Moonshots</div>` : '')
    + moon.map(p => pickEntryHtml(p, cfg.color)).join('');

  const sourcesHtml = sources?.length ? `
    <details style="margin-top:16px;">
      <summary style="font-family:var(--font-mono);font-size:0.65rem;letter-spacing:0.08em;text-transform:uppercase;color:var(--text-4);cursor:pointer;">
        Sources consulted (${sources.length})
      </summary>
      <ul style="margin-top:8px;padding-left:16px;font-size:0.75rem;color:var(--text-3);line-height:2;">
        ${sources.map(s => `<li><a href="${s.url}" target="_blank" style="color:var(--amber);text-decoration:none;">${s.title}</a></li>`).join('')}
      </ul>
    </details>` : '';

  return `
    <div>
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;padding-bottom:16px;border-bottom:2px solid ${cfg.color};">
        <img src="/static/${memberKey}.png" class="member-thumb member-thumb-lg" alt="${cfg.label}">
        <span style="font-family:var(--font-mono);font-size:0.95rem;font-weight:600;color:${cfg.color};">${cfg.label}</span>
        <span style="font-family:var(--font-mono);font-size:0.65rem;color:var(--text-4);">${picks.length} picks</span>
      </div>
      ${entries}
      ${sourcesHtml}
    </div>`;
}

export function initMembers(run) {
  const view = document.getElementById('view-members');

  if (!run) {
    view.innerHTML = `<div class="empty-state">No committee data yet — run the committee first.</div>`;
    return;
  }

  view.innerHTML = `
    <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:24px;">
      Member <em style="font-style:italic;color:var(--amber)">Picks</em>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:32px;">
      <div id="col-claude"></div>
      <div id="col-gpt"></div>
      <div id="col-gemini"></div>
    </div>`;

  document.getElementById('col-claude').innerHTML  = memberColHtml('claude',  run, run.claude_sources);
  document.getElementById('col-gpt').innerHTML     = memberColHtml('gpt',     run, null);
  document.getElementById('col-gemini').innerHTML  = memberColHtml('gemini',  run, null);

  view.querySelectorAll('.pick-entry').forEach(el => {
    const toggle = el.querySelector('summary span:last-child');
    el.addEventListener('toggle', () => { if (toggle) toggle.textContent = el.open ? '−' : '+'; });
  });
}
