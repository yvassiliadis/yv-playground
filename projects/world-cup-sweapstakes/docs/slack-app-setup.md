# Slack App Setup — Daily Prediction Poll

The daily post now uses a Slack **app bot token** (not the old incoming webhook)
so button votes can be recorded.

## One-time setup (workspace admin)
1. Create a Slack app at https://api.slack.com/apps → "From scratch".
2. **OAuth & Permissions** → Bot Token Scopes: add `chat:write`. Install to the
   workspace and copy the **Bot User OAuth Token** (`xoxb-…`).
3. **Interactivity & Shortcuts** → turn on, set Request URL to
   `https://<cloud-run-url>/api/slack/interactions`.
4. **Basic Information** → copy the **Signing Secret**.
5. Invite the bot to the target channel and copy the channel ID.

## Cloud Run env vars
- `SLACK_BOT_TOKEN` — the `xoxb-…` bot token (scope `chat:write`).
- `SLACK_SIGNING_SECRET` — from Basic Information.
- `SLACK_CHANNEL_ID` — target channel ID.
- `TRIVIA_TRIGGER_TOKEN` — unchanged (protects `POST /api/trivia/post`).

`SLACK_WEBHOOK_URL` is no longer used by the daily post and can be removed.

## Fallback
If admin access to create the app is unavailable, revert to emoji-reaction
voting on the incoming webhook (see the design doc). This is a documented pivot,
not implemented here.
