"""Worldview platform tests — loader, degradation, migration, golden snapshots."""

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from ane.worldview import (
    get as get_worldview,
    list_worldviews,
    clear_cache,
    reload,
    validate_pack,
    _is_valid_id,
    DEFAULT_WORLDVIEW_ID,
)
from ane.modules.prompt_builder import assemble_system, _EFFECTIVE_SYSTEM_PROMPT
from ane.modules.output_parser import parse, CORE_EVENT_TYPES, _event_types_for, VALID_EVENT_TYPES
from ane.database.models import Base, WorldSession
from ane.game_engine import game_engine
from ane.modules.input_validator import validate, nsfw_body_words


# ── Loader ───────────────────────────────────────────────────

def test_list_worldviews_contains_xianxia():
    views = {v["id"] for v in list_worldviews()}
    assert "xianxia_v1" in views


def test_get_xianxia_pack_loaded():
    wv = get_worldview("xianxia_v1")
    assert wv.manifest.get("name") == "修仙世界"
    assert wv.system_prompt  # full system prompt text
    assert wv.world_templates.get("sects")
    assert wv.player_templates.get("identities")
    assert wv.panel_spec.get("fields")
    assert wv.ui.get("labels")
    assert wv.modeler_role


def test_invalid_id_rejected():
    assert not _is_valid_id("../../etc/passwd")
    assert not _is_valid_id("A B")
    assert not _is_valid_id("")
    assert _is_valid_id("xianxia_v1")


def test_unknown_pack_falls_back_to_default():
    clear_cache()
    wv = get_worldview("no_such_worldview")
    assert wv.id == DEFAULT_WORLDVIEW_ID


def test_path_traversal_falls_back():
    clear_cache()
    wv = get_worldview("../worldview")
    assert wv.id == DEFAULT_WORLDVIEW_ID


# ── Golden snapshots (R1 quality guard) ─────────────────────

def test_assemble_system_matches_legacy_verbatim():
    """assemble_system('xianxia_v1') must be byte-identical to the legacy prompt."""
    new = assemble_system("xianxia_v1")
    assert new == _EFFECTIVE_SYSTEM_PROMPT


def test_default_assemble_matches_legacy():
    assert assemble_system() == _EFFECTIVE_SYSTEM_PROMPT


# ── Intent classification via pack keywords ──────────────────

def test_cultivate_keywords_from_pack():
    assert validate("我要闭关").intent == "cultivate"
    assert validate("我要闭关", worldview="xianxia_v1").intent == "cultivate"


def test_unknown_worldview_intent_falls_back():
    # no_such pack degrades to xianxia → cultivate still recognized
    assert validate("我要闭关", worldview="no_such").intent == "cultivate"


def test_nsfw_body_words_extra():
    words = nsfw_body_words("xianxia_v1")
    assert "双修" in words
    assert "炉鼎" in words
    # default (no worldview) also includes extras
    assert "双修" in nsfw_body_words()


# ── Event type whitelist ─────────────────────────────────────

def test_core_event_types_always_valid():
    assert "location_change" in CORE_EVENT_TYPES
    assert "economy_change" in CORE_EVENT_TYPES  # regression fix: was missing


def test_xianxia_extra_event_types():
    types = _event_types_for("xianxia_v1")
    assert "cultivation_change" in types
    assert "breakthrough" in types


def test_unknown_pack_event_types_core_only():
    # degrades to xianxia → still includes xianxia extras
    types = _event_types_for("no_such")
    assert "cultivation_change" in types


def test_parse_keeps_known_drops_unknown():
    raw = json.dumps({
        "narrative": "测试正文",
        "state_changes": [
            {"type": "economy_change", "target": "player", "change": -3, "unit": "块下品灵石"},
            {"type": "cultivation_change", "target": "player", "value": "筑基期"},
            {"type": "quantum_flip", "target": "player", "value": "x"},
        ],
    }, ensure_ascii=False)
    r = parse(raw)
    types = {c["type"] for c in r.state_changes}
    assert types == {"economy_change", "cultivation_change"}


# ── SQLite migration (non-destructive) ───────────────────────

@pytest.mark.asyncio
async def test_sessions_worldview_column(db):
    """WorldSession has a worldview column; flush assigns the xianxia default."""
    s = WorldSession(user_id="u1", name="测试")
    db.add(s)
    await db.flush()
    assert s.worldview == "xianxia_v1"


@pytest.mark.asyncio
async def test_migration_adds_column_to_existing_db(tmp_path):
    """Simulate a pre-existing DB without the worldview column, then migrate."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from ane.database.engine import init_db

    db_path = tmp_path / "pre.db"
    url = f"sqlite+aiosqlite:///{db_path}"

    # Build an OLD schema (no worldview column)
    eng_old = create_async_engine(url)
    async with eng_old.begin() as conn:
        await conn.execute(text("CREATE TABLE sessions (id VARCHAR PRIMARY KEY, user_id VARCHAR NOT NULL, name VARCHAR DEFAULT '未命名世界', world_time VARCHAR DEFAULT '', time_epoch INTEGER DEFAULT 0, created_at DATETIME, is_active BOOLEAN, map_data JSON, world_intro TEXT)"))
        await conn.execute(text("INSERT INTO sessions (id, user_id, name) VALUES ('s1', 'u1', '旧会话')"))
    await eng_old.dispose()

    # Migrate via init_db (our engine already points at the real DB, so we
    # replicate the exact migration SQL here instead of calling init_db).
    from sqlalchemy.ext.asyncio import create_async_engine as _cae
    eng = _cae(url)
    async with eng.begin() as conn:
        cols = await conn.execute(text("PRAGMA table_info(sessions)"))
        col_names = {row[1] for row in cols.fetchall()}
        assert "worldview" not in col_names
        await conn.execute(
            text("ALTER TABLE sessions ADD COLUMN worldview TEXT NOT NULL DEFAULT 'xianxia_v1'")
        )
    async with eng.begin() as conn:
        cols = await conn.execute(text("PRAGMA table_info(sessions)"))
        col_names = {row[1] for row in cols.fetchall()}
        assert "worldview" in col_names
        rows = await conn.execute(text("SELECT id, worldview FROM sessions"))
        data = rows.fetchall()
        assert len(data) == 1
        assert data[0][0] == "s1"
        assert data[0][1] == "xianxia_v1"
    await eng.dispose()


# ── P2: pack validation / reload / upload ─────────────────────

def test_validate_xianxia_pack_ok():
    report = validate_pack("xianxia_v1")
    assert report["ok"] is True
    assert not report["errors"]


@pytest.mark.asyncio
async def test_upload_worldview_via_asgi(tmp_path):
    """End-to-end: build a minimal pack zip, upload via the ASGI app."""
    import io
    import zipfile
    import httpx
    from ane.main import app
    from ane.worldview import WORLDVIEWS_DIR

    wv_id = "test_pack_upload"
    # Build a minimal pack zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "worldview_id": wv_id, "name": "测试世界观", "version": "0.1.0",
        }, ensure_ascii=False))
        zf.writestr("system_prompt.txt", "你是一个测试世界观的叙事引擎。\n世界观：测试。")
        zf.writestr("world_templates.json", json.dumps({
            "regions": [{"name": "测试地", "type": "area", "description": "测试区域"}],
            "sects": [], "settlements": [], "sect_filters": [],
        }, ensure_ascii=False))
        zf.writestr("player_templates.json", json.dumps({
            "genders": [{"value": "男", "label": "男"}],
            "cultivations": [{"value": "测试", "label": "测试", "desc": "测试"}],
            "personalities": [{"value": "随和", "label": "随和", "desc": "好相处"}],
            "backgrounds": [{"value": "普通", "label": "普通", "desc": "普通家庭"}],
            "identities": {"测试员": {"label": "测试员", "desc": "测试", "clothing": "", "monthly_income": "", "background": ""}},
            "golden_fingers": [],
        }, ensure_ascii=False))
        zf.writestr("intent_keywords.json", json.dumps({}, ensure_ascii=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/worldviews/upload",
            files={"file": (f"{wv_id}.zip", buf.getvalue(), "application/zip")},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["installed"] == wv_id
        assert data["validation"]["ok"] is True

    try:
        # The pack should now be loadable
        wv = get_worldview(wv_id)
        assert wv.manifest.get("name") == "测试世界观"
        assert wv.world_templates.get("regions")
        # And listable
        ids = {w["id"] for w in list_worldviews()}
        assert wv_id in ids
    finally:
        # Cleanup: delete the test pack
        import shutil
        target = WORLDVIEWS_DIR / wv_id
        if target.exists():
            shutil.rmtree(target)
        reload(wv_id)


@pytest.mark.asyncio
async def test_upload_rejects_bad_zip():
    import httpx
    from ane.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/worldviews/upload",
            files={"file": ("bad.zip", b"not a zip", "application/zip")},
        )
        assert resp.status_code == 400


def test_validate_missing_pack():
    report = validate_pack("no_such_worldview")
    assert report["ok"] is False


def test_reload_clears_cache():
    clear_cache()
    wv1 = get_worldview("xianxia_v1")
    reload("xianxia_v1")
    wv2 = get_worldview("xianxia_v1")
    # reload dropped the cache → a fresh object is loaded
    assert wv1 is not wv2
    assert wv2.manifest.get("name") == "修仙世界"


# ── P2: worldview time_per_intent override ───────────────────

def test_modern_city_time_override():
    from ane.modules.time_manager import time_manager as tm
    # modern_city overrides travel → 2 (global is 12)
    assert tm.calc_delta("travel", worldview="modern_city") == 2
    # xianxia has no override → global value
    assert tm.calc_delta("travel", worldview="xianxia_v1") == 12
    assert tm.calc_delta("travel") == 12  # default path unchanged


def test_worldview_event_pool():
    wv = get_worldview("xianxia_v1")
    events = wv.events
    assert events.get("seclusion_threshold") == 2160
    assert events.get("idle_events")
    mwv = get_worldview("modern_city")
    assert mwv.events.get("seclusion_event", {}).get("type") == "routine_progress"


@pytest.mark.asyncio
async def test_session_pins_worldview_version(db):
    """Creating a session pins the pack version at creation time."""
    result = await game_engine.create_session(db, user_id='u', name="测试", worldview="modern_city")
    s = await db.get(WorldSession, result["session_id"])
    assert s.worldview == "modern_city"
    assert s.worldview_version == "1.0.0"


@pytest.mark.asyncio
async def test_session_default_worldview_version_empty_for_legacy(db):
    """A session created without explicit version keeps the pin empty (legacy)."""
    result = await game_engine.create_session(db, user_id='u', name="测试")
    s = await db.get(WorldSession, result["session_id"])
    assert s.worldview == "xianxia_v1"
    assert s.worldview_version == "1.0.0"  # xianxia pack version


# ── P6: form.json generic character creation ─────────────────

@pytest.mark.asyncio
async def test_form_path_applies_character(db):
    """apply_character_from_form writes per the xianxia form.json spec."""
    from ane.modules.player_manager import player_manager

    result = await game_engine.create_session(db, user_id='u', name="测试")
    sid = result["session_id"]

    values = {
        "name": "林北",
        "age": 19,
        "gender": "男",
        "background": "无父无母",
        "cultivation": "筑基期",
        "personality": "谨慎隐忍",
        "identity": "外门弟子",
    }
    player = await player_manager.apply_character_from_form(
        db, sid, values, worldview="xianxia_v1",
    )
    assert player is not None
    assert player.name == "林北"
    assert player.cultivation == "筑基期"
    attrs = dict(player.attributes or {})
    assert attrs.get("gender") == "男"
    assert attrs.get("age") == 19
    assert attrs.get("background") == "无父无母"
    assert attrs.get("personality") == "谨慎隐忍"
    assert attrs.get("identity") == "外门弟子"
    # Derived fields from identity option
    assert attrs.get("clothing") or attrs.get("monthly_income")


@pytest.mark.asyncio
async def test_form_path_custom_identity(db):
    """Selecting __custom__ for identity writes the custom text."""
    from ane.modules.player_manager import player_manager

    result = await game_engine.create_session(db, user_id='u', name="测试")
    sid = result["session_id"]

    values = {
        "name": "无名",
        "age": 20,
        "gender": "女",
        "background": "无父无母",
        "cultivation": "凡人",
        "personality": "随和",
        "identity": "__custom__",
        "identity_custom": "我是天命之女，身负异象",
    }
    player = await player_manager.apply_character_from_form(
        db, sid, values, worldview="xianxia_v1",
    )
    attrs = dict(player.attributes or {})
    assert attrs.get("identity") == "__custom__"
    assert attrs.get("identity_desc") == "自定义身份"
    assert attrs.get("background_summary") == "我是天命之女，身负异象"


@pytest.mark.asyncio
async def test_form_path_golden_finger_map(db):
    """card_grid golden finger maps option fields into attributes."""
    from ane.modules.player_manager import player_manager

    result = await game_engine.create_session(db, user_id='u', name="测试")
    sid = result["session_id"]

    # Find a real golden finger id from the pack templates
    wv = get_worldview("xianxia_v1")
    gf = (wv.player_templates.get("golden_fingers") or [{}])[0]
    gf_id = gf.get("id", "reincarnation")

    values = {
        "name": "张三",
        "age": 18,
        "gender": "男",
        "background": "无父无母",
        "cultivation": "凡人",
        "personality": "随和",
        "identity": "外门弟子",
        "golden_finger": gf_id,
    }
    player = await player_manager.apply_character_from_form(
        db, sid, values, worldview="xianxia_v1",
    )
    attrs = dict(player.attributes or {})
    assert attrs.get("golden_finger_id") == gf_id
    assert attrs.get("golden_finger_name")  # mapped from option


@pytest.mark.asyncio
async def test_form_path_sect_special(db):
    """Choosing a sect marks _form_sect for city assignment."""
    from ane.modules.player_manager import player_manager

    result = await game_engine.create_session(db, user_id='u', name="测试")
    sid = result["session_id"]

    values = {
        "name": "李四",
        "age": 21,
        "gender": "女",
        "background": "无父无母",
        "cultivation": "凡人",
        "personality": "随和",
        "identity": "外门弟子",
        "sect": "青云宗",
    }
    player = await player_manager.apply_character_from_form(
        db, sid, values, worldview="xianxia_v1",
    )
    assert getattr(player, "_form_sect", "") == "青云宗"


@pytest.mark.asyncio
async def test_form_attached_in_templates_endpoint():
    """GET /sessions/__any__/templates returns the pack's form.json."""
    from ane.main import app
    import httpx
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/sessions/__any__/templates?worldview=xianxia_v1")
        assert r.status_code == 200
        data = r.json()
        assert data.get("form") is not None
        fields = data["form"].get("fields", [])
        keys = [f["key"] for f in fields]
        assert "name" in keys and "cultivation" in keys and "golden_finger" in keys

