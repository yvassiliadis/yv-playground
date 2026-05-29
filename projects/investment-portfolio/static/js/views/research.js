import { api } from '../api.js';
import { showToast } from '../app.js';

function recBadge(rec) {
  const cls = {
    'strong buy': 'strong-buy',
    'buy': 'buy',
    'watch': 'watch',
    'pass': 'pass',
    'already in portfolio': 'portfolio',
    'not enough opinions': 'uncertain',
  }[rec] ?? 'uncertain';
  return `<span class="rec-badge ${cls}">${rec.toUpperCase()}</span>`;
}

function allocCell(e, portfolioByTicker) {
  const inPort = portfolioByTicker[e.ticker];
  if (e.recommendation === 'already in portfolio' && inPort?.weight != null) {
    return '$' + Math.round(10000 * inPort.weight / 100).toLocaleString();
  }
  return e.suggested_allocation_pct != null
    ? '$' + Math.round(10000 * e.suggested_allocation_pct / 100).toLocaleString()
    : '—';
}

const REC_CHIPS = [
  { rec: 'already in portfolio', cls: 'portfolio',   label: 'In Portfolio' },
  { rec: 'strong buy',           cls: 'strong-buy',  label: 'Strong Buy'   },
  { rec: 'buy',                  cls: 'buy',          label: 'Buy'          },
  { rec: 'watch',                cls: 'watch',        label: 'Watch'        },
  { rec: 'not enough opinions',  cls: 'uncertain',    label: 'Uncertain'    },
  { rec: 'pass',                 cls: 'pass',         label: 'Pass'         },
];

const REC_ORDER = REC_CHIPS.map(c => c.rec);

const DEFAULT_ACTIVE_RECS = new Set([
  'already in portfolio', 'strong buy', 'buy', 'watch',
]);

// Persisted across re-inits so filters survive "Ask" calls
const activeRecs = new Set(DEFAULT_ACTIVE_RECS);
let fitsOnly = true;
let sortCol = null;
let sortDir = 'asc';

export async function initResearch() {
  const view = document.getElementById('view-research');
  let allEntries = [];
  let portfolioByTicker = {};

  try {
    [allEntries] = await Promise.all([
      api.getAdvisorLog(),
      api.getLatestRun().then(r => {
        if (r?.portfolio) r.portfolio.forEach(h => { portfolioByTicker[h.ticker] = h; });
      }).catch(() => {}),
    ]);
  } catch (e) { showToast(e.message, 'error'); }

  const heading = `
    <div style="font-family:var(--font-serif);font-size:1.9rem;font-weight:700;letter-spacing:-0.02em;color:var(--text);margin-bottom:24px;">
      Research <em style="font-style:italic;color:var(--amber)">Log</em>
    </div>`;

  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 30);

  const seen = new Set();
  const entries = [...allEntries].reverse().filter(e => {
    if (seen.has(e.ticker)) return false;
    seen.add(e.ticker);
    return !e.timestamp || new Date(e.timestamp) >= cutoff;
  });

  if (!entries.length) {
    view.innerHTML = heading + `<div class="empty-state">No research in the last 30 days.</div>`;
    return;
  }

  let expandedTicker = null;

  function getFiltered() {
    let data = entries.filter(e =>
      (activeRecs.size === 0 || activeRecs.has(e.recommendation)) && (!fitsOnly || e.fits_philosophy)
    );

    if (sortCol) {
      const dir = sortDir === 'asc' ? 1 : -1;
      data = [...data].sort((a, b) => {
        let av, bv;
        switch (sortCol) {
          case 'date':    av = a.timestamp ?? '';                bv = b.timestamp ?? '';                break;
          case 'ticker':  av = a.ticker ?? '';                   bv = b.ticker ?? '';                   break;
          case 'company': av = a.company_name ?? '';             bv = b.company_name ?? '';             break;
          case 'rec':     av = REC_ORDER.indexOf(a.recommendation ?? ''); bv = REC_ORDER.indexOf(b.recommendation ?? ''); break;
          case 'alloc':   av = a.suggested_allocation_pct ?? -1; bv = b.suggested_allocation_pct ?? -1; break;
          case 'upside':  av = a.mean_upside_pct ?? -999;       bv = b.mean_upside_pct ?? -999;       break;
          case 'fits':    av = a.fits_philosophy ? 1 : 0;       bv = b.fits_philosophy ? 1 : 0;       break;
        }
        if (av < bv) return -dir;
        if (av > bv) return dir;
        return 0;
      });
    }

    return data;
  }

  function renderRows() {
    const tbody = document.getElementById('research-tbody');
    if (!tbody) return;

    document.querySelectorAll('.research-th').forEach(th => {
      const indicator = th.querySelector('.sort-indicator');
      if (!indicator) return;
      const active = sortCol === th.dataset.col;
      indicator.textContent = active ? (sortDir === 'asc' ? '↑' : '↓') : '↕';
      indicator.style.opacity = active ? '1' : '0.3';
    });

    const filtered = getFiltered();

    if (!filtered.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="research-empty-row">No entries match the current filters.</td></tr>`;
      return;
    }

    tbody.innerHTML = filtered.flatMap(e => {
      const u = e.mean_upside_pct;
      const upside = u != null ? `${u >= 0 ? '+' : ''}${u}%` : '—';
      const uColor = u != null ? (u >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--text-3)';
      const isExpanded = expandedTicker === e.ticker;

      const mainRow = `
        <tr class="research-row${isExpanded ? ' expanded' : ''}" data-ticker="${e.ticker}">
          <td class="mono">${e.timestamp?.slice(0,10) ?? '—'}</td>
          <td class="mono research-ticker-cell">${e.ticker}</td>
          <td>${e.company_name}</td>
          <td>${recBadge(e.recommendation)}</td>
          <td class="mono">${allocCell(e, portfolioByTicker)}</td>
          <td class="mono" style="color:${uColor};">${upside}</td>
          <td class="mono" style="color:${e.fits_philosophy ? 'var(--green)' : 'var(--text-3)'};">${e.fits_philosophy ? 'Yes' : 'No'}</td>
        </tr>`;

      if (!isExpanded) return [mainRow];

      const expandedRow = `
        <tr class="research-expanded-row">
          <td colspan="7">
            <div class="research-member-grid">
              ${['claude', 'gpt', 'gemini'].map((m, i) => `
                <div class="research-member-col${i < 2 ? ' bordered' : ''}">
                  <div class="research-member-header">
                    <div style="display:flex;align-items:center;gap:6px;">
                      <img src="/static/${m}.png" class="member-thumb" alt="${m}" style="width:18px;height:18px;">
                      <span style="font-family:var(--font-mono);font-size:0.65rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--text-4);">${m}</span>
                    </div>
                    ${e[`${m}_rec`] ? recBadge(e[`${m}_rec`]) : ''}
                  </div>
                  <div class="research-member-take">${e[`${m}_take`] || '—'}</div>
                </div>`).join('')}
            </div>
          </td>
        </tr>`;

      return [mainRow, expandedRow];
    }).join('');

    tbody.querySelectorAll('.research-row').forEach(row => {
      row.addEventListener('click', () => {
        const ticker = row.dataset.ticker;
        expandedTicker = expandedTicker === ticker ? null : ticker;
        renderRows();
      });
    });
  }

  view.innerHTML = heading + `
    <div class="research-filter-bar">
      <span class="filter-label">Opinion</span>
      ${REC_CHIPS.map(c => `
        <button class="filter-chip ${c.cls}${activeRecs.has(c.rec) ? ' active' : ''}" data-rec="${c.rec}">${c.label}</button>
      `).join('')}
      <div class="filter-divider"></div>
      <button class="filter-chip fits-chip${fitsOnly ? ' active' : ''}" id="fits-chip">Fits ✓</button>
    </div>

    <table class="atlas-table">
      <thead><tr>
        <th class="research-th" data-col="date">Date <span class="sort-indicator">↕</span></th>
        <th class="research-th" data-col="ticker">Ticker <span class="sort-indicator">↕</span></th>
        <th class="research-th" data-col="company">Company <span class="sort-indicator">↕</span></th>
        <th class="research-th" data-col="rec">Opinion <span class="sort-indicator">↕</span></th>
        <th class="research-th" data-col="alloc">Suggested $ <span class="sort-indicator">↕</span></th>
        <th class="research-th" data-col="upside">Upside <span class="sort-indicator">↕</span></th>
        <th class="research-th" data-col="fits">Fits? <span class="sort-indicator">↕</span></th>
      </tr></thead>
      <tbody id="research-tbody"></tbody>
    </table>`;

  view.querySelectorAll('.filter-chip[data-rec]').forEach(chip => {
    chip.addEventListener('click', () => {
      const rec = chip.dataset.rec;
      if (activeRecs.has(rec)) activeRecs.delete(rec);
      else activeRecs.add(rec);
      chip.classList.toggle('active', activeRecs.has(rec));
      expandedTicker = null;
      renderRows();
    });
  });

  document.getElementById('fits-chip').addEventListener('click', function () {
    fitsOnly = !fitsOnly;
    this.classList.toggle('active', fitsOnly);
    expandedTicker = null;
    renderRows();
  });

  view.querySelectorAll('.research-th').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (sortCol !== col) {
        sortCol = col;
        sortDir = 'asc';
      } else if (sortDir === 'asc') {
        sortDir = 'desc';
      } else {
        sortCol = null;
        sortDir = 'asc';
      }
      renderRows();
    });
  });

  renderRows();
}
