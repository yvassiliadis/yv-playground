import os

from fastapi.testclient import TestClient

os.environ["TRIVIA_TRIGGER_TOKEN"] = "test-token"
os.environ.setdefault("ZAFRONIX_WC_API_KEY", "")
os.environ.pop("SLACK_WEBHOOK_URL", None)

import server  # noqa: E402

client = TestClient(server.app)


def test_preview_returns_message():
    resp = client.get("/api/trivia/preview")
    assert resp.status_code == 200
    assert "minutes until the 2026 World Cup Final" in resp.json()["message"]


def test_post_without_token_is_unauthorized():
    resp = client.post("/api/trivia/post")
    assert resp.status_code == 401


def test_post_with_token_composes_but_does_not_post_without_webhook():
    resp = client.post("/api/trivia/post", headers={"X-Trigger-Token": "test-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["posted"] is False
    assert "Did You Know" in body["message"]
