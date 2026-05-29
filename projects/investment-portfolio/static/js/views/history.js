function fmtDate(ts) {
  return new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function diffView(runA, runB) {
  const curr = Object.fromEntries(runA.portfolio.map(h => [h.ticker, h]));
  const prev = Object.fromEntries(runB.portfolio.map(h => [h.ticker, h]));

  const added   = runA.portfolio.filter(h => !prev[h.ticker]);
  const removed = runB.portfolio.filter(h => !curr[h.ticker]);
  const weightChanges = runA.portfolio
    .filter(h => prev[h.ticker] && Math.abs(h.weight - prev[h.ticker].weight) >= 0.5)
    .map(h => ({ prev: prev[h.ticker], curr: h }));

  function diffBlock(title, rows, color) {
    if (!rows.length) return '';
    return `
      <div style="margin-bottom:20px;">
        <div style="font-family:var(--font-mono);font-size:0.62rem;letter-spacing:0.12em;text-transform:uppercase;color:${color};margin-bottom:10px;">${title} (${rows.length})</div>
        ${rows.map(h => `
          <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
            <span style="font-family:var(--font-mono);font-size:0.85rem;font-weight:600;color:${color};width:56px;">${h.ticker}</span>
            <span style="font-size:0.78rem;color:var(--text-3);flex:1;">${h.company_name}</span>
            <span style="font-family:var(--font-mono);font-size:0.78rem;color:var(--text-3);">${h.weight}%</span>
            ${h.nominated_by?.length > 1 ? '<span style="font-size:0.68rem;color:var(--green);">consensus</span>' : ''}
          </div>`).join('')}
      </div>`;
  }

  function wchangeBlock() {
    if (!weightChanges.length) return '';
    return `
      <div style="margin-bottom:20px;">
        <div style="font-family:var(--font-mono);font-size:0.62rem;letter-spacing:0.12em;text-transform:uppercase;color:var(--amber);margin-bottom:10px;">Weight Changes (${weightChanges.length})</div>
        ${weightChanges.map(({ prev: p, curr: c }) => {
          const delta = c.weight - p.weight;
          const arrow = delta > 0 ? '▲' : '▼';
          const col   = delta > 0 ? 'var(--green)' : 'var(--red)';
          return `
            <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
              <span style="font-family:var(--font-mono);font-size:0.85rem;font-weight:600;color:var(--text);width:56px;">${c.ticker}</span>
              <span style="font-size:0.78rem;color:var(--text-3);flex:1;">${c.company_name}</span>
              <span style="font-family:var(--font-mono);font-size:0.78rem;color:var(--text-3);">${p.weight}% → ${c.weight}%</span>
              <span style="font-family:var(--font-mono);font-size:0.78rem;color:${col};">${arrow} ${Math.abs(delta).toFixed(1)}pp</span>
            </div>`;
        }).join('')}
      </div>`;
  }

  if (!added.length && !removed.length && !weightChanges.length) {
    return `<div class="empty-state" style="padding:32px 0;">Portfolio unchanged between selected runs.</div>`;
  }

  return diffBlock('New Positions', added, 'var(--green)')
    + diffBlock('Exited Positions', removed, 'var(--red)')
    + wchangeBlock();
}

export function initHistory(allRuns) {
  const view = document.getElementById('view-history');

  if (!allRuns?.length) {
    view.innerHTML = `<div class="empty-state">No run history yet.</div>`;
    return;
  }

  const runLabels = allRuns.map(r => fmtDate(r.timestamp));

  view.innerHTML = `
    <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:24px;">
      Run <em style="font-style:italic;color:var(--amber)">History</em>
    </div>

    <table class="atlas-table" style="margin-bottom:40px;">
      <thead><tr>
        <th>Date</th><th>Core</th><th>Moonshots</th><th>Consensus</th>
      </tr></thead>
      <tbody>
        ${allRuns.map(r => `
          <tr>
            <td class="mono">${fmtDate(r.timestamp)}</td>
            <td class="mono">${r.portfolio.filter(h => h.conviction === 'core').length}</td>
            <td class="mono">${r.portfolio.filter(h => h.conviction === 'moonshot').length}</td>
            <td class="mono">${r.portfolio.filter(h => (h.nominated_by?.length ?? 0) > 1).length}</td>
          </tr>`).join('')}
      </tbody>
    </table>

    ${allRuns.length < 2 ? '<div class="empty-state">Run the committee again to enable comparison.</div>' : `
      <div style="font-family:var(--font-serif);font-size:1.2rem;font-weight:600;font-style:italic;color:var(--text-2);margin-bottom:16px;">Compare Runs</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:28px;">
        <div>
          <div class="section-label" style="margin-bottom:6px;">Current run</div>
          <select id="sel-a" style="font-family:var(--font-mono);font-size:0.78rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--text-2);padding:8px 12px;width:100%;">
            ${runLabels.map((l, i) => `<option value="${i}">${l}</option>`).join('')}
          </select>
        </div>
        <div>
          <div class="section-label" style="margin-bottom:6px;">Compare against</div>
          <select id="sel-b" style="font-family:var(--font-mono);font-size:0.78rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--text-2);padding:8px 12px;width:100%;">
            ${runLabels.map((l, i) => `<option value="${i}"${i===1?' selected':''}>${l}</option>`).join('')}
          </select>
        </div>
      </div>
      <div id="diff-output"></div>
    `}`;

  function renderDiff() {
    const a = parseInt(document.getElementById('sel-a').value);
    const b = parseInt(document.getElementById('sel-b').value);
    const out = document.getElementById('diff-output');
    if (!out) return;
    if (a === b) { out.innerHTML = `<div class="empty-state">Select two different runs to compare.</div>`; return; }
    out.innerHTML = diffView(allRuns[a], allRuns[b]);
  }

  if (allRuns.length >= 2) {
    document.getElementById('sel-a').addEventListener('change', renderDiff);
    document.getElementById('sel-b').addEventListener('change', renderDiff);
    renderDiff();
  }
}
