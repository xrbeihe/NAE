"""Route-level integration tests for the NPC modeling chain (HTTP layer).

Uses the real FastAPI app with overridden DB + auth dependencies (in-memory
DB via the `db` fixture), so no real database file is touched.

Covers:
  - POST /npcs/library (create with worldview provenance)
  - POST /{session}/npcs/import/{name} (library → session)
  - POST /{session}/npc-modeling/confirm (⭐ path, legacy retained)
  - list /npcs/library returns worldview

Run: python -m pytest tests/ -v
"""

import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

import httpx

from ane.main import app
from ane.database.models import User, UserNPC, NPC
from ane.modules.model_adapter import ModelAdapter
from ane.database.engine import get_db
from ane.auth import create_access_token, get_current_user

USER_ID = "route_modeling_user"

MODEL_JSON = {
    "model_version": "1.1",
    "basic": {"name": "沈知微", "gender": "女", "age": 22,
              "identity": "炼器师", "cultivation": "筑基期",
              "faction": "万宝楼", "position": "执事"},
    "personality": {"core": "外热内冷，对器物有执念"},
    "lifestyle": {"habit": "喜欢熬夜炼器", "diet": "嗜辣"},
}


@pytest.fixture
def mock_llm():
    """Label-dispatching mock for model_adapter.generate."""
    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "_llm_nameget":
            return "沈知微\n"
        return json.dumps(MODEL_JSON, ensure_ascii=False)
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake) as m:
        yield m


@pytest_asyncio.fixture
async def auth_client(db, mock_llm):
    """HTTP client with overridden DB + auth deps, real JWT header."""
    u = User(id=USER_ID, username="route_model", password_hash="x",
             display_name="路由", is_adult=True)
    db.add(u)
    await db.commit()
    token = create_access_token({"sub": USER_ID})

    async def _db_override():
        yield db

    async def _user_override():
        return u

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_current_user] = _user_override
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.headers["Authorization"] = f"Bearer {token}"
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


# ── POST /npcs/library ────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_npc_library_via_http(auth_client, db):
    """Create a library NPC via HTTP — returns model + worldview, persists."""
    r = await auth_client.post(
        "/npcs/library?worldview=modern_city",
        json={"input": "沈知微是万宝楼的炼器师，外热内冷", "tags": ["炼器"]},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "沈知微"
    assert data["worldview"] == "modern_city"
    assert data["model_data"]["basic"]["name"] == "沈知微"

    # Persisted with provenance worldview
    from sqlalchemy import select
    row = (await db.execute(select(UserNPC).where(UserNPC.name == "沈知微"))).scalar_one_or_none()
    assert row is not None
    assert row.worldview == "modern_city"
    assert row.model_data.get("model_version")


@pytest.mark.asyncio
async def test_create_npc_library_default_worldview(auth_client, db):
    """No worldview param → stored as xianxia_v1."""
    r = await auth_client.post("/npcs/library", json={"input": "沈知微", "tags": []})
    assert r.status_code == 200, r.text
    from sqlalchemy import select
    row = (await db.execute(select(UserNPC).where(UserNPC.name == "沈知微"))).scalar_one_or_none()
    assert row is not None
    assert row.worldview == "xianxia_v1"


@pytest.mark.asyncio
async def test_list_npc_library_returns_worldview(auth_client, db):
    """GET /npcs/library includes each NPC's provenance worldview."""
    db.add(UserNPC(user_id=USER_ID, name="沈知微", model_data=dict(MODEL_JSON),
                   tags=[], worldview="modern_city"))
    await db.commit()

    r = await auth_client.get("/npcs/library")
    assert r.status_code == 200
    npcs = r.json()["npcs"]
    assert any(n["name"] == "沈知微" and n["worldview"] == "modern_city" for n in npcs)


# ── POST /{session}/npcs/import/{name} ────────────────────────

@pytest.mark.asyncio
async def test_import_library_npc_to_session(auth_client, db):
    """Import a library NPC into a session — becomes important + model + debut."""
    from ane.game_engine import game_engine
    info = await game_engine.create_session(db, user_id=USER_ID, name="导入测试")
    session_id = info["session_id"]
    db.add(UserNPC(user_id=USER_ID, name="沈知微", model_data=dict(MODEL_JSON),
                   tags=[], worldview="modern_city"))
    await db.commit()

    r = await auth_client.post(f"/sessions/{session_id}/npcs/import/沈知微")
    assert r.status_code == 200, r.text
    assert r.json()["imported"] is True

    from sqlalchemy import select
    npc = (await db.execute(
        select(NPC).where(NPC.session_id == session_id, NPC.name == "沈知微")
    )).scalar_one_or_none()
    assert npc is not None
    assert npc.is_important is True
    assert npc.identity == "炼器师"
    assert npc.cultivation == "筑基期"
    lts = dict(npc.long_term_state or {})
    assert lts.get("pending_debut") is True
    assert lts.get("model", {}).get("model_version") == "1.1"
    # cross-worldview schema fields preserved on import
    assert lts["model"]["lifestyle"]["habit"] == "喜欢熬夜炼器"


@pytest.mark.asyncio
async def test_import_duplicate_rejected(auth_client, db):
    """Importing an NPC already in the session → 409."""
    from ane.game_engine import game_engine
    info = await game_engine.create_session(db, user_id=USER_ID, name="导入测试")
    session_id = info["session_id"]
    db.add(UserNPC(user_id=USER_ID, name="沈知微", model_data=dict(MODEL_JSON),
                   tags=[], worldview="modern_city"))
    db.add(NPC(session_id=session_id, name="沈知微", is_important=True))
    await db.commit()

    r = await auth_client.post(f"/sessions/{session_id}/npcs/import/沈知微")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_update_npc_library_uses_provenance_worldview(auth_client, db):
    """Incremental update threads the stored worldview into _llm_cover."""
    db.add(UserNPC(user_id=USER_ID, name="沈知微", model_data=dict(MODEL_JSON),
                   tags=["炼器"], worldview="modern_city"))
    await db.commit()

    # Patch _llm_cover to capture the worldview argument (and return {} so
    # the merge is a no-op for data but we still verify the threading).
    import ane.api.routes as routes_mod
    from unittest.mock import AsyncMock as _AM
    captured = {}

    async def _fake_cover(name, user_input, existing_model, user_id="", session_id="", worldview=None):
        captured["worldview"] = worldview
        return {"personality": {"core": "外热内冷，对器物有执念"}}

    with patch.object(routes_mod.game_engine, "_llm_cover", new=_fake_cover):
        r = await auth_client.put(
            "/npcs/library/沈知微",
            json={"input": "她其实很恋旧"},
        )
    assert r.status_code == 200, r.text
    assert captured.get("worldview") == "modern_city"
    # merge applied + version bumped
    assert r.json()["model_data"]["model_version"] == "1.1"


# ── POST /{session}/npc-modeling/confirm (⭐ retained path) ────

@pytest.mark.asyncio
async def test_npc_modeling_confirm_new_npc(auth_client, db):
    """⭐ confirm path: creates important NPC + full model."""
    from ane.game_engine import game_engine
    info = await game_engine.create_session(db, user_id=USER_ID, name="建模测试")
    session_id = info["session_id"]

    r = await auth_client.post(
        f"/sessions/{session_id}/npc-modeling/confirm",
        json={"input": "沈知微是万宝楼的炼器师", "name": "沈知微"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["npc_name"] == "沈知微"
    assert data["model_data"]["model_version"]

    from sqlalchemy import select
    npc = (await db.execute(
        select(NPC).where(NPC.session_id == session_id, NPC.name == "沈知微")
    )).scalar_one_or_none()
    assert npc is not None
    assert npc.is_important is True
    assert dict(npc.long_term_state or {}).get("pending_debut") is True
