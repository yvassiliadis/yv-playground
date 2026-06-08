const BASE = '';

async function request(method, path, body) {
  const resp = await fetch(BASE + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) throw new Error(`${method} ${path} → ${resp.status}`);
  return resp.json();
}

export const api = {
  createGame: (playerNames, humanCount) =>
    request('POST', '/games', { player_names: playerNames, human_count: humanCount }),
  getGame: (gameId) => request('GET', `/games/${gameId}`),
  hit:  (gameId, playerId) => request('POST', `/games/${gameId}/players/${playerId}/hit`),
  stay: (gameId, playerId) => request('POST', `/games/${gameId}/players/${playerId}/stay`),
  resolveAction: (gameId, targetPlayerId) =>
    request('POST', `/games/${gameId}/resolve-action/${targetPlayerId}`),
  nextRound: (gameId) => request('POST', `/games/${gameId}/next-round`),
};
