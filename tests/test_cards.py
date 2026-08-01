"""Integration test: character card maker (UserCard + card_routes + companion).

Covers: /cards CRUD, /cards/schema, create_companion_session_from_card,
opening-line via card greeting, clinginess → nudge threshold mapping.
Uses in-memory DB + mock LLM.

Run: python -m pytest tests/ -v
"""

import pytest
from unittest.mock import AsyncMock, patch
import pytest_asyncio

from ane.companion_engine import companion_engine
from ane.database.models import User, UserCard, Memory
from ane.modules.card_schema import (
    CLINGINESS_IDLE_SECONDS,
    CARD_SCHEMA,
    normalize_card,
    render_card_preview,
)
from ane.modules.model_adapter import ModelAdapter

USER_ID = "test_card_user"


# ── Mock LLM ────────────────────────────────────────────────────

MOCK_CHAT_RESPONSE = (
    '{"reply": "他垂眸一笑：\\"青竹，你来了。\\"", '
    '"emotion": "温和", "relationship_note": ""}'
)


@pytest.fixture
def mock_llm():
    async def _fake(prompt, model=None, **kwargs):
        return MOCK_CHAT_RESPONSE
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake) as mock:
        yield mock


@pytest_asyncio.fixture
async def card_fixture(db):
    """Create a User + a role card, return {card_id, card_data}."""
    u = User(id=USER_ID, username="card_user", password_hash="x", display_name="card", is_adult=True)
    db.add(u)
    await db.flush()
    card_data = normalize_card({
        "identity": {"name": "沈之澜", "gender": "男", "age": "24",
                     "occupation": "世子", "persona": "外冷内热的温润世子"},
        "speech_style": {"address_you": "青竹"},
        "initial_relationship": {"type": "青梅竹马", "history": "自幼一起长大"},
        "clinginess": {"level": "粘人"},
        "opening": {"greeting": "青竹，好些日子没见你来了。"},
    })
    c = UserCard(user_id=USER_ID, name="沈之澜", card_data=card_data, tags=["古风"])
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return {"card_id": c.id, "card_data": card_data}


# ── card_schema 单元 ───────────────────────────────────────────

def test_normalize_card_fills_defaults():
    """Normalize fills missing nested fields."""
    d = normalize_card({"identity": {"name": "X"}})
    assert d["identity"]["name"] == "X"
    assert d["identity"]["gender"] == ""
    assert d["initial_relationship"]["type"] == "陌生人"
    assert isinstance(d["relationship_behavior"]["intimate_terms"], list)


def test_render_card_preview():
    """Preview contains initial relationship + greeting."""
    d = normalize_card({
        "identity": {"name": "沈之澜"},
        "initial_relationship": {"type": "青梅竹马", "history": "自幼一起长大"},
        "opening": {"greeting": "你来了。"},
    })
    text = render_card_preview(d)
    assert "青梅竹马" in text
    assert "自幼一起长大" in text
    assert "开场白" in text


def test_clinginess_mapping():
    """Clinginess levels map to nudge intervals."""
    assert CLINGINESS_IDLE_SECONDS["粘人"] == 600
    assert CLINGINESS_IDLE_SECONDS["适中"] == 1800
    assert CLINGINESS_IDLE_SECONDS["高冷"] == 21600


# ── create_companion_session_from_card ─────────────────────────

@pytest.mark.asyncio
async def test_create_session_from_card(db, card_fixture):
    """Creates session bound to the card, clinginess maps to nudge threshold."""
    info = await companion_engine.create_companion_session_from_card(
        db, USER_ID, card_fixture["card_id"], name="沈之澜 的对话"
    )
    assert info["session_id"]
    assert info["worldview"] == "companion_v1"
    assert info["npc_name"] == "沈之澜"

    # 粘人 → 主动搭话间隔 600 秒
    s = await companion_engine.get_nudge_settings(db, info["session_id"])
    assert s["idle_seconds"] == 600


@pytest.mark.asyncio
async def test_create_session_from_card_missing(db):
    """Missing card should raise ValueError."""
    import pytest as _pt
    with _pt.raises(ValueError):
        await companion_engine.create_companion_session_from_card(db, USER_ID, "nonexistent")


# ── process_chat with card (renders new schema) ────────────────

@pytest.mark.asyncio
async def test_process_chat_from_card(db, card_fixture, mock_llm):
    """Chat with a card-bound session: reply uses card address term."""
    info = await companion_engine.create_companion_session_from_card(
        db, USER_ID, card_fixture["card_id"]
    )
    result = await companion_engine.process_chat(
        db, info["session_id"], "青竹，今天去哪儿？", user_id=USER_ID,
    )
    assert result["reply"]
    assert "青竹" in result["reply"]  # 角色卡的称呼生效


# ── Opening line uses card greeting ────────────────────────────

@pytest.mark.asyncio
async def test_nudge_opening_uses_card_greeting(db, card_fixture, mock_llm):
    """New session nudge: card's opening.greeting drives the prompt hint."""
    info = await companion_engine.create_companion_session_from_card(
        db, USER_ID, card_fixture["card_id"]
    )
    result = await companion_engine.nudge(db, info["session_id"], user_id=USER_ID)
    assert result is not None
    assert result["kind"] == "开场白"
    # mock 返回的是固定回复，但验证 LLM prompt 收到了开场白提示
    assert mock_llm.call_count >= 1
    call_kwargs = mock_llm.call_args
    prompt = call_kwargs.args[0]
    assert "青梅竹马" in prompt        # 初始关系注入
    assert "好些日子没见你来了" in prompt  # greeting 注入


# ── card_routes（HTTP 层）─────────────────────────────────────

@pytest.mark.asyncio
async def test_cards_crud_http(db):
    """/cards CRUD via FastAPI TestClient-style calls."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from ane.api.card_routes import router as card_router
    from ane.auth import get_current_user
    from ane.database.engine import get_db
    from sqlalchemy.ext.asyncio import AsyncSession

    # 构造 app + 依赖覆盖（db 用传入 fixture）
    app = FastAPI()
    app.include_router(card_router)

    async def _override_db():
        yield db
    async def _override_user():
        from sqlalchemy import select as _sel
        u = (await db.execute(_sel(User).where(User.username == "card_http_user"))).scalar_one_or_none()
        if u is None:
            u = User(id=USER_ID, username="card_http_user", password_hash="x", display_name="h", is_adult=True)
            db.add(u)
            await db.flush()
        return u

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # schema
        r = await client.get("/cards/schema")
        assert r.status_code == 200
        d = r.json()
        assert "schema" in d and "labels" in d and "selects" in d
        assert "initial_relationship" in d["schema"]

        # create
        r = await client.post("/cards", json={
            "name": "测试卡",
            "card_data": {"identity": {"name": "小竹"}},
            "tags": ["测试"],
        })
        assert r.status_code == 200
        cid = r.json()["id"]

        # get
        r = await client.get(f"/cards/{cid}")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "测试卡"
        assert data["card_data"]["identity"]["name"] == "小竹"
        assert data["card_data"]["initial_relationship"]["type"] == "陌生人"  # 默认补全

        # list
        r = await client.get("/cards")
        assert r.status_code == 200
        assert len(r.json()["cards"]) == 1

        # update
        r = await client.put(f"/cards/{cid}", json={
            "name": "测试卡2",
            "card_data": {"identity": {"name": "小竹"}, "initial_relationship": {"type": "恋人"}},
            "tags": ["测试", "更新"],
        })
        assert r.status_code == 200
        r = await client.get(f"/cards/{cid}")
        assert r.json()["card_data"]["initial_relationship"]["type"] == "恋人"

        # duplicate name → 409
        r = await client.post("/cards", json={"name": "测试卡2", "card_data": {}})
        assert r.status_code == 409

        # delete
        r = await client.delete(f"/cards/{cid}")
        assert r.status_code == 200
        r = await client.get(f"/cards/{cid}")
        assert r.status_code == 404
