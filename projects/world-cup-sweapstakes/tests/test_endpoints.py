import os

from fastapi.testclient import TestClient

os.environ["TRIVIA_TRIGGER_TOKEN"] = "test-token"
os.environ.setdefault("ZAFRONIX_WC_API_KEY", "")
os.environ.pop("SLACK_WEBHOOK_URL", None)
os.environ.pop("SLACK_BOT_TOKEN", None)
os.environ.pop("SLACK_CHANNEL_ID", None)

import server  # noqa: E402

client = TestClient(server.app)


async def _no_fixtures(now):
    return []


def test_preview_returns_blocks(monkeypatch):
    monkeypatch.setattr(server, "_todays_poll_fixtures", _no_fixtures)
    resp = client.get("/api/trivia/preview")
    assert resp.status_code == 200
    blocks = resp.json()["blocks"]
    texts = " ".join(b["text"]["text"] for b in blocks if b.get("type") == "section")
    assert "Did You Know?" in texts
    assert "days until the 2026 World Cup Final" in texts


def test_post_without_token_is_unauthorized():
    resp = client.post("/api/trivia/post")
    assert resp.status_code == 401


def test_post_with_token_composes_but_does_not_post_without_bot_token(monkeypatch):
    monkeypatch.setattr(server, "_todays_poll_fixtures", _no_fixtures)
    resp = client.post("/api/trivia/post", headers={"X-Trigger-Token": "test-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["posted"] is False
    texts = " ".join(b["text"]["text"] for b in body["blocks"] if b.get("type") == "section")
    assert "Did You Know?" in texts
