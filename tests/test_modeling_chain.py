"""Modeling-chain tests — the important-NPC modeling pipeline.

Covers the methods that had no direct test coverage:
  - parse_modeling_response / render_model_for_prompt (npc_modeler)
  - _llm_nameget_multi, _run_npc_modeling, _llm_cover, _deep_merge (game_engine)
  - do_npc_modeling pre-check classification (updated vs new)
  - full-turn integration: modeled NPC's data loads into prompt when named

Run: python -m pytest tests/ -v
"""

import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from ane.game_engine import game_engine, GameEngine
from ane.modules.model_adapter import ModelAdapter
from ane.database.models import NPC, User

USER_ID = "modeling_chain_user"

MODEL_JSON = {
    "model_version": "1.1",  # real modeled NPCs always carry this (set by parse_modeling_response)
    "basic": {"name": "白慕彩", "gender": "女", "age": 25, "identity": "长老", "cultivation": "金丹期"},
    "personality": {"core": "外冷内热，执念于剑道"},
    "relationships": {"master": "玄真道人", "friends": ["林晚"]},
    "nsfw": {"is_virgin": True, "fertility": "正常"},
}


@pytest.fixture
def mock_llm():
    """Replace model_adapter.generate with a label-dispatching mock.

    - _llm_nameget → a single name line (used by do_npc_modeling)
    - llm_modeling / llm_cover → a full modeling JSON (used by _run/_cover)
    """
    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "_llm_nameget":
            return "白慕彩\n"
        return json.dumps(MODEL_JSON, ensure_ascii=False)
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake) as mock:
        yield mock


@pytest_asyncio.fixture
async def session_with_player(db):
    """A world session with a player, for turn-level integration."""
    u = User(id=USER_ID, username="modeling_user", password_hash="x",
             display_name="建模", is_adult=True)
    db.add(u)
    await db.flush()
    info = await game_engine.create_session(db, user_id=USER_ID, name="建模测试")
    return info["session_id"]


def _mk_npc(db, session_id, name, is_important=True, model=None, pending_debut=False):
    lts = {}
    if model is not None:
        lts["model"] = model
    if pending_debut:
        lts["pending_debut"] = True
    return NPC(
        session_id=session_id, name=name, is_important=is_important,
        long_term_state=lts, location="青云山·山门", npc_type="named",
    )


# ── npc_modeler.parse_modeling_response ──────────────────────

@pytest.mark.asyncio
async def test_parse_modeling_response_injects_version():
    from ane.modules.npc_modeler import parse_modeling_response, NPC_MODEL_VERSION
    data = parse_modeling_response(json.dumps(MODEL_JSON, ensure_ascii=False))
    assert data is not None
    assert data["model_version"] == NPC_MODEL_VERSION
    assert data["basic"]["name"] == "白慕彩"


@pytest.mark.asyncio
async def test_parse_modeling_response_missing_name_rejected():
    from ane.modules.npc_modeler import parse_modeling_response
    bad = json.dumps({"basic": {"gender": "女"}}, ensure_ascii=False)
    assert parse_modeling_response(bad) is None


@pytest.mark.asyncio
async def test_parse_modeling_response_code_fence_stripped():
    from ane.modules.npc_modeler import parse_modeling_response
    raw = "```json\n" + json.dumps(MODEL_JSON, ensure_ascii=False) + "\n```"
    data = parse_modeling_response(raw)
    assert data is not None
    assert data["basic"]["name"] == "白慕彩"


# ── render_model_for_prompt ──────────────────────────────────

def test_render_model_for_prompt_nsfw_omitted_by_default():
    from ane.modules.npc_modeler import render_model_for_prompt
    md = dict(MODEL_JSON)
    md["model_version"] = "1.1"
    rendered = render_model_for_prompt(md, include_nsfw=False)
    assert "白慕彩" in rendered
    assert "剑道" in rendered
    assert "身体特征" not in rendered          # nsfw block omitted
    assert "是否处子" not in rendered


def test_render_model_for_prompt_nsfw_included_when_requested():
    from ane.modules.npc_modeler import render_model_for_prompt
    md = dict(MODEL_JSON)
    md["model_version"] = "1.1"
    rendered = render_model_for_prompt(md, include_nsfw=True)
    assert "身体特征" in rendered
    assert "是否处子" in rendered


def test_render_model_for_prompt_migrates_v1():
    """v1.0 model (un-merged structure) renders without error."""
    from ane.modules.npc_modeler import render_model_for_prompt
    v1 = {
        "model_version": "1.0",
        "basic": {"name": "旧角色", "gender": "男"},
        "appearance": {
            "face": {"eyes": "丹凤眼", "lashes": "长睫"},
            "neck": "修长", "collarbone": "分明",
        },
        "clothing": {"type": "长袍", "color": "青"},
        "jewelry": {"ring": "玉戒"},
    }
    rendered = render_model_for_prompt(v1, include_nsfw=False)
    assert "旧角色" in rendered
    assert "长睫" in rendered       # lashes merged into eyes
    assert "修长" in rendered       # neck merged into torso


# ── game_engine._llm_nameget_multi ───────────────────────────

@pytest.mark.asyncio
async def test_llm_nameget_extracts_names():
    async def _fake(prompt, model=None, **kwargs):
        return "张海\n张大强\n"
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        names = await game_engine._llm_nameget_multi("张海是我妈，张大强是我爸", user_id=USER_ID)
    assert set(names) == {"张海", "张大强"}


@pytest.mark.asyncio
async def test_llm_nameget_retries_then_empty():
    """Invalid/non-name output on both attempts → empty list, no crash."""
    async def _fake(prompt, model=None, **kwargs):
        return "这些不是姓名"   # fails the 2-3 char CJK check
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        names = await game_engine._llm_nameget_multi("随便一句话", user_id=USER_ID)
    assert names == []


# ── game_engine._run_npc_modeling ─────────────────────────────

@pytest.mark.asyncio
async def test_run_npc_modeling_saves_model(db, mock_llm):
    npc = _mk_npc(db, session_id="sess", name="白慕彩", is_important=True)
    db.add(npc)
    await db.flush()

    model_data = await game_engine._run_npc_modeling(
        db, "白慕彩", "白慕彩是我师姐，外冷内热", npc,
        session_id="sess", user_id=USER_ID,
        player_name="陆青棠", player_location="青云山·山门",
        is_new_npc=True, worldview="xianxia_v1",
    )

    assert model_data
    assert model_data.get("model_version")
    # NPC columns synced from basic
    assert npc.identity == "长老"
    assert npc.cultivation == "金丹期"
    assert npc.gender == "女"
    assert npc.age == 25
    assert npc.personality == "外冷内热，执念于剑道"
    # model + debut flag persisted in long_term_state
    lts = dict(npc.long_term_state or {})
    assert lts.get("model", {}).get("basic", {}).get("name") == "白慕彩"
    assert lts.get("pending_debut") is True
    # relationships mapped to NPC.relations entries
    rels = dict(npc.relations or {})
    targets = {e["target"] for e in rels.get("entries", [])}
    assert targets == {"玄真道人", "林晚"}


@pytest.mark.asyncio
async def test_run_npc_modeling_invalid_response_keeps_npc(db):
    """Failed modeling → no model stored, no crash."""
    async def _fake(prompt, model=None, **kwargs):
        return "这不是JSON"
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        npc = _mk_npc(db, session_id="sess", name="白慕彩")
        db.add(npc)
        await db.flush()
        model_data = await game_engine._run_npc_modeling(
            db, "白慕彩", "白慕彩是我师姐", npc,
            session_id="sess", user_id=USER_ID,
            player_name="陆青棠", player_location="青云山·山门",
            is_new_npc=True, worldview="xianxia_v1",
        )
    assert model_data == {}
    lts = dict(npc.long_term_state or {})
    assert "model" not in lts
    assert npc.identity == ""


# ── game_engine._llm_cover + _deep_merge ──────────────────────

@pytest.mark.asyncio
async def test_llm_cover_partial_update(db, mock_llm):
    existing = dict(MODEL_JSON)
    existing["model_version"] = "1.1"
    updates = await game_engine._llm_cover(
        "白慕彩", "她其实怕黑", existing, user_id=USER_ID,
        session_id="sess", worldview="xianxia_v1",
    )
    # mock returns the full MODEL_JSON — merge must not lose existing fields
    assert updates
    game_engine._deep_merge(existing, updates)
    assert existing["basic"]["name"] == "白慕彩"


@pytest.mark.asyncio
async def test_llm_cover_no_json_returns_none(db):
    async def _fake(prompt, model=None, **kwargs):
        return "无法解析的内容"
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        updates = await game_engine._llm_cover(
            "白慕彩", "她其实怕黑", dict(MODEL_JSON),
            user_id=USER_ID, session_id="sess",
        )
    assert updates is None


def test_deep_merge_nested():
    base = {"personality": {"core": "外冷", "likes": "剑"}, "basic": {"name": "白"}}
    upd = {"personality": {"fears": "黑暗"}, "attire": {"clothing": "白裙"}}
    GameEngine._deep_merge(base, upd)
    assert base["personality"]["core"] == "外冷"      # untouched
    assert base["personality"]["fears"] == "黑暗"      # added
    assert base["basic"]["name"] == "白"               # untouched
    assert base["attire"]["clothing"] == "白裙"        # added


# ── game_engine.do_npc_modeling (pre-check) ──────────────────

@pytest.mark.asyncio
async def test_do_npc_modeling_classifies_existing_modeled_as_updated(db, mock_llm):
    """An important NPC with model_version → lands in updated, not new_names."""
    npc = _mk_npc(db, session_id="sess", name="白慕彩", is_important=True, model=dict(MODEL_JSON))
    db.add(npc)
    await db.flush()

    result = await game_engine.do_npc_modeling(db, "sess", "白慕彩是我师姐", user_id=USER_ID)
    assert result["new_names"] == []
    assert "白慕彩" in [u["npc_name"] for u in result["updated"]]


@pytest.mark.asyncio
async def test_do_npc_modeling_unmodeled_important_is_new(db, mock_llm):
    """Important but no model_version → new candidate (needs confirm)."""
    npc = _mk_npc(db, session_id="sess", name="白慕彩", is_important=True, model={})
    db.add(npc)
    await db.flush()

    result = await game_engine.do_npc_modeling(db, "sess", "白慕彩", user_id=USER_ID)
    assert "白慕彩" in result["new_names"]
    assert result["updated"] == []


# ── Turn integration: load_model_data injects modeled NPC ─────

MOCK_TURN_RESPONSE = """```json
{
  "narrative": "白慕彩立于山门，衣袂在风中轻扬。她看了你一眼，淡然道：\\"你来了。\\"",
  "state_changes": []
}
```"""


@pytest.mark.asyncio
async def test_turn_loads_modeled_npc_data_into_prompt(db, session_with_player, mock_llm):
    """After modeling, naming the NPC in a turn injects full data into the prompt.

    The modeled NPC has pending_debut set → the turn's prompt must contain both
    the 【建模登场】 debut block and the rendered model data (剑道 from personality).
    """
    session_id = session_with_player

    # Re-create the modeled NPC through the real pipeline so the DB session
    # (the `db` fixture) holds the object — process_turn reads from it.
    npc = NPC(
        session_id=session_id, name="白慕彩", is_important=True,
        long_term_state={"model": dict(MODEL_JSON), "pending_debut": True},
        location="青云山·山门", npc_type="named",
        identity="长老", cultivation="金丹期", gender="女", age=25,
    )
    db.add(npc)
    await db.commit()

    # Mock llm_main to return a valid narrative (model data is only in the prompt,
    # the mock never inspects it — the prompt string is captured below).
    async def _fake(prompt, model=None, **kwargs):
        return MOCK_TURN_RESPONSE
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake) as m:
        result = await game_engine.process_turn(
            db, session_id, "白慕彩，你今天气色不错。", turn_number=1,
            user_id=USER_ID, load_model_data=True,
        )

    assert result.narrative and "你来了" in result.narrative
    # The prompt that reached llm_main carried the modeled NPC + debut block
    prompt = result.prompt
    assert "白慕彩" in prompt
    assert "剑道" in prompt                 # model data (personality.core) rendered
    assert "建模登场" in prompt              # pending_debut → debut block

    # pending_debut was consumed — a second turn should NOT re-trigger debut
    async def _fake2(prompt, model=None, **kwargs):
        return MOCK_TURN_RESPONSE
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake2) as m2:
        result2 = await game_engine.process_turn(
            db, session_id, "白慕彩，你回来了。", turn_number=2,
            user_id=USER_ID, load_model_data=True,
        )
    assert "建模登场" not in result2.prompt


@pytest.mark.asyncio
async def test_turn_without_model_data_no_debut(db, session_with_player, mock_llm):
    """An important NPC without a model gets legacy rendering, no debut block."""
    session_id = session_with_player
    npc = NPC(
        session_id=session_id, name="陆寒渊", is_important=True,
        long_term_state={}, location="青云山·山门",
        identity="挚友", cultivation="筑基期", npc_type="named",
    )
    db.add(npc)
    await db.commit()

    async def _fake(prompt, model=None, **kwargs):
        return MOCK_TURN_RESPONSE
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        result = await game_engine.process_turn(
            db, session_id, "陆寒渊，好久不见。", turn_number=1,
            user_id=USER_ID, load_model_data=True,
        )

    assert "建模登场" not in result.prompt       # no model → no debut
    assert "陆寒渊" in result.prompt
