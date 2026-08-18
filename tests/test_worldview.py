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
    assert wv.modeler_schema  # modeler/schema.json loaded
    assert wv.modeler_schema.get("basic")


def test_packs_ship_their_own_modeler_schema():
    # Each pack's modeler schema differs from the xianxia default
    fk = get_worldview("fantasy_kingdom")
    mc = get_worldview("modern_city")
    assert "knighthood" in fk.modeler_schema          # western-fantasy specific
    assert "magic" in fk.modeler_schema
    assert "lifestyle" in mc.modeler_schema           # modern-life specific
    assert "wardrobe" in mc.modeler_schema
    # Generic renderer surfaces these custom fields without hardcoded knowledge
    from ane.modules.npc_modeler import render_model_for_prompt
    md = {
        "basic": {"name": "Sir Aldric", "rank": "勋爵"},
        "magic": {"school": "火焰术", "level": "学徒"},
        "knighthood": {"liege": "亚瑟王"},
        "model_version": "1.1",
    }
    rendered = render_model_for_prompt(md, include_nsfw=False)
    assert "火焰术" in rendered      # custom field rendered
    assert "亚瑟王" in rendered      # nested custom field rendered
    assert "Sir Aldric" in rendered


def test_model_relationships_parse_generically():
    """Relationship parsing must not hardcode xianxia keys."""
    from ane.game_engine import GameEngine
    # fantasy-style relationships
    fk = GameEngine._model_rels_to_entries(
        {"liege_lord": "亚瑟王", "family": ["凯尔", "艾琳娜"], "enemies": ["黑骑士莫甘"]}
    )
    types = {e["target"]: e["type"] for e in fk}
    assert types["亚瑟王"] == "领主"
    assert types["凯尔"] == "家人"
    assert types["黑骑士莫甘"] == "敌人"
    # xianxia-style relationships still map to Chinese labels
    xi = GameEngine._model_rels_to_entries({"master": "玄真道人", "friends": ["林晚"]})
    xi_types = {e["target"]: e["type"] for e in xi}
    assert xi_types["玄真道人"] == "师父"
    assert xi_types["林晚"] == "朋友"


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
    """xianxia now uses shell+kernel. Its system prompt = worldview shell + generic kernel."""
    new = assemble_system("xianxia_v1")
    # Worldview-specific shell content present
    assert "东方玄幻修仙世界" in new
    assert "不要让角色表现出超出其修为的能力" in new
    assert "cultivation_change" in new          # xianxia state_change type
    assert "藏经阁" in new                       # xianxia sect recommendation
    # Generic kernel content present (engine-owned, now shared)
    assert "严禁向玩家反问" in new
    assert "state_changes 用于记录数据库需要持久化的状态变更" in new
    # The worldview shell itself is much slimmer than the old full prompt
    from ane.worldview import get as get_worldview
    shell = get_worldview("xianxia_v1").system_prompt or ""
    assert len(shell) < 2500


def test_default_assemble_matches_legacy():
    # Default (no pack / xianxia) still resolves to a valid system prompt
    assert assemble_system()
    assert "东方玄幻修仙世界" in assemble_system()


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


# ── Structural validators (name pools / panel sources / timelines) ──

def test_name_pool_duplicates_detected():
    from ane.worldview import _check_name_pools
    errors, warnings = [], []
    _check_name_pools({"surnames": ["林", "林"], "given_names_male": [], "given_names_female": []},
                      errors, warnings)
    assert any("重复" in w for w in warnings)


def test_name_pool_gender_overlap_detected():
    from ane.worldview import _check_name_pools
    errors, warnings = [], []
    _check_name_pools({"surnames": [], "given_names_male": ["红"], "given_names_female": ["红"]},
                      errors, warnings)
    assert any("重叠" in w for w in warnings)


def test_name_pool_surname_as_given_detected():
    from ane.worldview import _check_name_pools
    errors, warnings = [], []
    _check_name_pools({"surnames": ["宇智波"], "given_names_male": ["宇智波"], "given_names_female": []},
                      errors, warnings)
    assert any("完整姓名" in w for w in warnings)


def test_panel_source_alignment():
    from ane.worldview import _check_panel_sources
    panel = {"fields": [{"label": "血继限界", "key": "golden_finger_name", "source": "attrs"},
                        {"label": "未知字段", "key": "nonexistent_key", "source": "attrs"}]}
    pt = {"golden_fingers": [{"id": "x", "name": "写轮眼"}]}
    errors, warnings = [], []
    _check_panel_sources(panel, pt, errors, warnings)
    # golden_finger_name is backed by option_map → no warning; nonexistent_key warns
    assert not any("golden_finger_name" in w for w in warnings)
    assert any("nonexistent_key" in w for w in warnings)


def test_timeline_completeness():
    from ane.worldview import _check_timelines
    wf = {"timelines": [
        {"id": "t1", "label": "第七班成立", "description": "…", "must_follow": [], "forbidden": [], "characters": []},
        {"id": "t1", "label": "", "description": "", "must_follow": [], "forbidden": [], "characters": []},
        {"id": "t3", "label": "ok", "description": "…", "forbidden": []},  # missing must_follow
    ]}
    errors, warnings = [], []
    _check_timelines(wf, errors, warnings)
    assert any("重复" in w for w in warnings)       # t1 dup id
    assert any("缺少 label" in w for w in warnings) # t1 missing label
    assert any("缺少 must_follow" in w for w in warnings)  # t3
    assert not errors


def test_naruto_timelines_pass_validator():
    """The naruto pack's 19 timelines satisfy the completeness rules."""
    from ane.worldview import validate_pack
    report = validate_pack("naruto_shippuden")
    assert report["ok"] is True
    # no duplicate/missing timeline warnings
    assert not any("timeline" in w or "timelines" in w for w in report["warnings"])


@pytest.mark.asyncio
async def test_list_shared_worldviews_includes_lore(db):
    """Shared list must attach the pack's lore (world_facts.json) per card."""
    from ane.database.models import WorldviewShare
    db.add(WorldviewShare(user_id="u1", worldview_id="naruto_shippuden", title="火影忍者"))
    await db.commit()

    from ane.api.worldview_routes import list_shared_worldviews
    result = await list_shared_worldviews(db=db, user=None)
    wvs = {w["worldview_id"]: w for w in result["worldviews"]}
    assert "naruto_shippuden" in wvs
    lore = wvs["naruto_shippuden"].get("lore") or ""
    assert len(lore) > 100  # 火影包的完整 lore 已写入


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
    # 自定义身份 → identity 存自定义文本（不再泄漏 __custom__ 标记）
    assert attrs.get("identity") == "我是天命之女，身负异象"
    # identity_desc 不再硬编码「自定义身份」
    assert attrs.get("identity_desc") == ""
    # background_summary 不被身份文本覆写（保持用户选的出身）
    assert attrs.get("background_summary") != "我是天命之女，身负异象"


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
async def test_form_path_custom_golden_finger(db):
    """自定义金手指：__custom__ 标记 + custom 文本 → desc 正确落库。"""
    from ane.modules.player_manager import player_manager

    result = await game_engine.create_session(db, user_id='u', name="测试")
    sid = result["session_id"]

    values = {
        "name": "王五",
        "age": 19,
        "gender": "男",
        "background": "孤儿",
        "cultivation": "凡人",
        "personality": "谨慎",
        "identity": "杂役弟子",
        "golden_finger": "__custom__",
        "golden_finger_custom": "系统面板：可查看自己与周围人的因果线",
    }
    player = await player_manager.apply_character_from_form(
        db, sid, values, worldview="xianxia_v1",
    )
    attrs = dict(player.attributes or {})
    assert attrs.get("golden_finger_id") == "custom"
    # 自定义能力：name 直接用自定义内容（不再用"自定义"占位）
    assert attrs.get("golden_finger_name") == "系统面板：可查看自己与周围人的因果线"
    assert attrs.get("golden_finger_desc") == "系统面板：可查看自己与周围人的因果线"
    assert attrs.get("golden_finger_custom") == "系统面板：可查看自己与周围人的因果线"


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

