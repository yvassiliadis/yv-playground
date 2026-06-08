import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.main import app


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_game(client):
    resp = await client.post("/games", json={"player_names": ["Alice", "Bot"], "human_count": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert "game_id" in data
    assert data["phase"] in ("playing", "round_over", "game_over")
    assert len(data["players"]) == 2


@pytest.mark.asyncio
async def test_get_game(client):
    resp = await client.post("/games", json={"player_names": ["Alice", "Bot"], "human_count": 1})
    game_id = resp.json()["game_id"]
    resp2 = await client.get(f"/games/{game_id}")
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_unknown_game_returns_404(client):
    resp = await client.get("/games/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_player_can_stay(client):
    resp = await client.post("/games", json={"player_names": ["Alice", "Bot"], "human_count": 1})
    body = resp.json()
    if body["phase"] != "playing":
        return  # round already over due to action cards; skip
    game_id = body["game_id"]
    resp2 = await client.post(f"/games/{game_id}/players/player_0/stay")
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_player_can_hit(client):
    resp = await client.post("/games", json={"player_names": ["Alice", "Bot"], "human_count": 1})
    body = resp.json()
    if body["phase"] != "playing":
        return
    game_id = body["game_id"]
    resp2 = await client.post(f"/games/{game_id}/players/player_0/hit")
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_round_scores_populated_after_round_over(client):
    resp = await client.post("/games", json={"player_names": ["Alice", "Bot"], "human_count": 1})
    body = resp.json()
    game_id = body["game_id"]

    # Play until round over
    for _ in range(50):  # safety limit
        body = (await client.get(f"/games/{game_id}")).json()
        if body["phase"] != "playing":
            break
        if body["pending_action"]:
            # Resolve pending action targeting first active player
            active = next(p for p in body["players"] if p["is_active"])
            body = (await client.post(f"/games/{game_id}/resolve-action/{active['id']}")).json()
            continue
        human = next(p for p in body["players"] if not p["is_ai"])
        if body["players"][body["current_player_index"]]["id"] == human["id"]:
            body = (await client.post(f"/games/{game_id}/players/{human['id']}/stay")).json()
        else:
            break

    # If round is over, all scores should be computed
    if body["phase"] in ("round_over", "game_over"):
        for p in body["players"]:
            if not p["has_busted"]:
                assert p["round_score"] >= 0
