"""ANE — AI Narrative Engine.

Logging is configured on first import so ALL code paths
(tests, CLI, server) write to the same backend.log.
"""

import logging
import sys
from pathlib import Path

_LOG_INITIALIZED = False


def _init_logging():
    global _LOG_INITIALIZED
    if _LOG_INITIALIZED:
        return
    _LOG_INITIALIZED = True

    LOG_DIR = Path(__file__).resolve().parent.parent.parent  # project root
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    BACKEND_LOG = LOG_DIR / "backend.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # File handler
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(str(BACKEND_LOG), encoding="utf-8", mode="a")  # append, not overwrite
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(file_fmt)
    root.addHandler(fh)

    # Console handler
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(console_fmt)
    root.addHandler(ch)

    # Also capture uvicorn's own output (startup banner, requests, etc.)
    for name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
        uv = logging.getLogger(name)
        uv.addHandler(fh)
        uv.propagate = False  # avoid double-logging via root

    # Quiet external libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Logging initialized: %s", BACKEND_LOG)


_init_logging()
