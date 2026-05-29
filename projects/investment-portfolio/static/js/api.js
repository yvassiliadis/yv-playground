const BASE = '';

async function request(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

export const api = {
  getLatestRun:     ()       => request('GET',  '/api/runs/latest'),
  getAllRuns:        ()       => request('GET',  '/api/runs'),
  triggerRun:       ()       => request('POST', '/api/runs'),
  getPerformance:   (t, w)   => request('GET',  `/api/performance?tickers=${t}&weights=${w}`),
  getAdvisorLog:    ()       => request('GET',  '/api/advisor/log'),
  askAdvisor:       (ticker) => request('POST', '/api/advisor', { ticker }),
  getSettings:      ()       => request('GET',  '/api/settings'),
  updateSettings:   (data)   => request('PUT',  '/api/settings', data),
};
