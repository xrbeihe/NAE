"""Load content from JSON files adjacent to this loader."""

import json
from pathlib import Path

_CONTENT_DIR = Path(__file__).resolve().parent


def load_json(filename: str) -> dict:
    """Load and parse a JSON file from the content directory."""
    path = _CONTENT_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Lazy-loaded module-level data ─────────────────────────────

def _load_npc() -> dict:
    return load_json("npc_templates.json")


def _load_world() -> dict:
    return load_json("world_templates.json")


def _load_nsfw() -> dict:
    return load_json("nsfw_templates.json")


def _load_underage() -> dict:
    return load_json("underage_templates.json")


def _load_portrait() -> dict:
    return load_json("portrait_templates.json")


def _load_ntr() -> dict:
    return load_json("ntr_templates.json")


# Expose data as module-level variables (loaded once on first import)
_npc_data: dict | None = None
_world_data: dict | None = None
_nsfw_data: dict | None = None
_underage_data: dict | None = None
_portrait_data: dict | None = None
_ntr_data: dict | None = None


def npc_data() -> dict:
    global _npc_data
    if _npc_data is None:
        _npc_data = _load_npc()
    return _npc_data


def world_data() -> dict:
    global _world_data
    if _world_data is None:
        _world_data = _load_world()
    return _world_data


def nsfw_data() -> dict:
    global _nsfw_data
    if _nsfw_data is None:
        _nsfw_data = _load_nsfw()
    return _nsfw_data


def portrait_data() -> dict:
    global _portrait_data
    if _portrait_data is None:
        _portrait_data = _load_portrait()
    return _portrait_data


def underage_data() -> dict:
    global _underage_data
    if _underage_data is None:
        _underage_data = _load_underage()
    return _underage_data


def ntr_data() -> dict:
    global _ntr_data
    if _ntr_data is None:
        _ntr_data = _load_ntr()
    return _ntr_data
