import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
_REQUIRED_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")


def is_demo_mode() -> bool:
    return not all(os.environ.get(k) for k in _REQUIRED_KEYS)


def ensure_demo_data() -> None:
    if not is_demo_mode():
        return
    for example in sorted(_DATA_DIR.rglob("*.example.*")):
        real = example.parent / example.name.replace(".example", "")
        if not real.exists():
            real.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(example, real)
            logger.info("Demo mode: seeded %s", real.relative_to(_DATA_DIR.parent))
    logger.info(
        "Running in demo mode — add %s to .env to enable live AI runs",
        ", ".join(_REQUIRED_KEYS),
    )
