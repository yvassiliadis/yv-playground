import os

# server.py reads these at import time, so they must be set before any test
# module imports it. conftest is loaded before test modules, making the env
# independent of test collection order.
os.environ.setdefault("FOOTBALL_DATA_API_KEY", "test-key")
os.environ["TRIVIA_TRIGGER_TOKEN"] = "test-token"
os.environ.setdefault("ZAFRONIX_WC_API_KEY", "")
os.environ.pop("SLACK_WEBHOOK_URL", None)
