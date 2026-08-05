"""ANE configuration — loaded from config.json, overridden by env vars."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root (ane/ now lives under backend/ane/)
ROOT_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env file
load_dotenv(ROOT_DIR / ".env")

# Load JSON config
_config_path = Path(__file__).resolve().parent / "config.json"
with open(_config_path, "r", encoding="utf-8") as f:
    _cfg = json.load(f)

# ── Server ───────────────────────────────────────────────────

HOST = os.getenv("ANE_HOST", _cfg["server"]["host"])
PORT = int(os.getenv("ANE_PORT", _cfg["server"]["port"]))

# ── JWT Secret ──────────────────────────────────────────────

SECRET_KEY = os.getenv("ANE_SECRET_KEY", _cfg.get("secret_key", "default-dev-key"))

# ── Database ─────────────────────────────────────────────────

_raw_db_url = os.getenv("ANE_DATABASE_URL", _cfg["database"]["url"])
# Expand relative paths: "sqlite+aiosqlite:///data/ane.db" -> absolute
if ":///" in _raw_db_url and not _raw_db_url.startswith("sqlite+aiosqlite:///"):
    pass  # non-sqlite absolute URL
elif _raw_db_url.startswith("sqlite+aiosqlite:///") and ":///" in _raw_db_url:
    _, path_part = _raw_db_url.split(":///", 1)
    if not os.path.isabs(path_part):
        abs_path = str(ROOT_DIR / path_part)
        DATABASE_URL = f"sqlite+aiosqlite:///{abs_path}"
    else:
        DATABASE_URL = _raw_db_url
else:
    DATABASE_URL = _raw_db_url

# Ensure data directory exists
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Image library storage (开源共享图片库)
IMAGE_DIR = DATA_DIR / "images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# ── LLM ──────────────────────────────────────────────────────

_llm = _cfg["llm"]
DEFAULT_MODEL = os.getenv("ANE_DEFAULT_MODEL", _llm["default_model"])
LLM_TEMPERATURE = float(os.getenv("ANE_LLM_TEMPERATURE", _llm["temperature"]))
LLM_MAX_TOKENS = int(os.getenv("ANE_LLM_MAX_TOKENS", _llm["max_tokens"]))
SYSTEM_PROMPT_SUFFIX = os.getenv("ANE_SYSTEM_PROMPT_SUFFIX", _llm.get("system_prompt_suffix", ""))

# Provider API keys (env vars only, never from JSON)
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
DEEPSEEK_API_KEY   = os.getenv("DEEPSEEK_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")
SENSENOVA_API_KEY  = os.getenv("SENSENOVA_API_KEY", "")

# Provider base URLs (JSON default, env override)
_providers = _llm["providers"]
OPENAI_BASE_URL      = os.getenv("OPENAI_BASE_URL", _providers["openai"]["base_url"])
DEEPSEEK_BASE_URL    = os.getenv("DEEPSEEK_BASE_URL", _providers["deepseek"]["base_url"])
SENSENOVA_BASE_URL   = os.getenv("SENSENOVA_BASE_URL", _providers["sensenova"]["base_url"])
OLLAMA_BASE_URL      = os.getenv("OLLAMA_BASE_URL", _providers["ollama"]["base_url"])
GEMINI_BASE_URL      = os.getenv("GEMINI_BASE_URL", _providers["gemini"]["base_url"])
CLAUDE_BASE_URL      = os.getenv("CLAUDE_BASE_URL", _providers["claude"]["base_url"])

# ── Phase 1 constants ────────────────────────────────────────

_p1 = _cfg["phase1"]
CONVERSATION_WINDOW_SIZE = _p1["conversation_window_size"]
SHORTMEMORY_WINDOW_SIZE = 5  # 短期记忆窗口（compact 摘要）
TIME_PER_INTENT: dict[str, int] = _p1["time_per_intent"]
SEASONS: list[str]       = _p1["seasons"]
TIMES_OF_DAY: list[str]  = _p1["times_of_day"]

# ── Time constants (configurable) ──────────────────────────────

_time_cfg = _p1.get("time", {})
TICKS_PER_YEAR: int          = _time_cfg.get("ticks_per_year", 8640)
DAYS_PER_YEAR: int           = _time_cfg.get("days_per_year", 360)
DAYS_PER_SEASON: int         = DAYS_PER_YEAR // 4  # 90
MONTHS_PER_YEAR: int         = _time_cfg.get("months_per_year", 12)
DAYS_PER_MONTH: int          = _time_cfg.get("days_per_month", 30)
TICKS_PER_DAY: int           = _time_cfg.get("ticks_per_day", 24)
TICKS_PER_TIME_OF_DAY: int   = _time_cfg.get("ticks_per_time_of_day", 6)
MONTH_TO_SEASON: list        = _time_cfg.get("month_to_season", [])
MAP_WIDTH_LOGICAL: int       = _time_cfg.get("map_width_logical", 800)
TRAVEL_DAYS_ACROSS_MAP: int  = _time_cfg.get("travel_days_across_map", 30)
