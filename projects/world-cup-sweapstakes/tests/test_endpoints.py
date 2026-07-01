import hashlib
import hmac
import json
import os
import time
import urllib.parse

from fastapi.testclient import TestClient

os.environ["TRIVIA_TRIGGER_TOKEN"] = "test-token"
os.environ.setdefault("ZAFRONIX_WC_API_KEY", "")
os.environ.pop("SLACK_WEBHOOK_URL", None)
os.environ.pop("SLACK_BOT_TOKEN", None)
os.environ.pop("SLACK_CHANNEL_ID", None)

import server  # noqa: E402
import slack_poll

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
    # server.py's load_dotenv() repopulates the Slack creds from .env, so force
    # the module constants empty rather than relying on os.environ isolation.
    monkeypatch.setattr(server, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(server, "SLACK_CHANNEL_ID", "")
    monkeypatch.setattr(server, "_todays_poll_fixtures", _no_fixtures)
    resp = client.post("/api/trivia/post", headers={"X-Trigger-Token": "test-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["posted"] is False
    texts = " ".join(b["text"]["text"] for b in body["blocks"] if b.get("type") == "section")
    assert "Did You Know?" in texts


def test_post_sends_separate_message_per_game_plus_trivia(monkeypatch):
    monkeypatch.setattr(server, "SLACK_BOT_TOKEN", "tok")
    monkeypatch.setattr(server, "SLACK_CHANNEL_ID", "C1")

    async def _two_fixtures(now):
        return [
            {"game_id": "1", "home": "Mexico", "away": "Ecuador",
             "kickoff_utc": "2099-01-01T00:00:00Z", "stage": "LAST_32"},
            {"game_id": "2", "home": "Spain", "away": "Japan",
             "kickoff_utc": "2099-01-01T00:00:00Z", "stage": "LAST_32"},
        ]

    calls = []

    async def fake_post(token, channel, blocks, text, metadata):
        calls.append({"text": text, "metadata": metadata})
        return {"channel": channel, "ts": "1.2"}

    monkeypatch.setattr(server, "_todays_poll_fixtures", _two_fixtures)
    monkeypatch.setattr(slack_poll, "post_message", fake_post)

    resp = client.post("/api/trivia/post", headers={"X-Trigger-Token": "test-token"})
    assert resp.status_code == 200
    body = resp.json()
    # 1 trivia + 2 polls = 3 independent messages
    assert body["posted_count"] == 3
    assert len(calls) == 3
    # trivia message carries no poll state; each poll message carries exactly one game
    assert calls[0]["metadata"] is None
    poll_states = [c["metadata"]["event_payload"] for c in calls[1:]]
    assert all(len(s["games"]) == 1 for s in poll_states)


def _signed(body: str, secret: str):
    ts = str(int(time.time()))
    base = f"v0:{ts}:{body}".encode()
    sig = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def test_interactions_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(server, "SLACK_SIGNING_SECRET", "shhh")
    resp = client.post(
        "/api/slack/interactions",
        content="payload=%7B%7D",
        headers={"X-Slack-Request-Timestamp": "1", "X-Slack-Signature": "v0=nope"},
    )
    assert resp.status_code == 401


def test_interactions_acks_and_updates(monkeypatch):
    monkeypatch.setattr(server, "SLACK_SIGNING_SECRET", "shhh")
    monkeypatch.setattr(server, "SLACK_BOT_TOKEN", "tok")
    captured = {}

    async def fake_update(token, channel, ts, blocks, text, metadata):
        captured["blocks"] = blocks
        captured["state"] = metadata["event_payload"]

    monkeypatch.setattr(slack_poll, "update_message", fake_update)

    state = {
        "header_blocks": [],
        "games": {"1": {"home": "Mexico", "away": "Ecuador",
                        "kickoff_utc": "2099-01-01T00:00:00Z", "votes": {}}},
    }
    payload = {
        "user": {"id": "U1"},
        "channel": {"id": "C1"},
        "message": {"ts": "1.2", "metadata": {"event_type": "wc_prediction_poll", "event_payload": state}},
        "actions": [{"action_id": "vote_1_home", "value": json.dumps({"game_id": "1", "pick": "home"})}],
        "response_url": "https://hooks.slack.test/x",
    }
    body = "payload=" + urllib.parse.quote(json.dumps(payload))
    resp = client.post("/api/slack/interactions", content=body, headers=_signed(body, "shhh"))
    assert resp.status_code == 200
    assert captured["state"]["games"]["1"]["votes"] == {"U1": "home"}
