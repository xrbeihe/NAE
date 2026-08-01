"""Integration test: 1v1 companion chat (companion_engine + chat routes).

Covers: create_companion_session / process_chat / relationship memory /
nudge (opening line + active nudge + suppression). Uses in-memory DB + mock LLM.

Run: python -m pytest tests/ -v
"""

import pytest
from unittest.mock import AsyncMock, patch
import pytest_asyncio

from ane.companion_engine import companion_engine
from ane.database.models import User, UserNPC, Memory
from ane.modules.model_adapter import ModelAdapter

USER_ID = "test_companion_user"


# ── Mock LLM responses ────────────────────────────────────────

MOCK_CHAT_RESPONSE = (
    '{"reply": "他垂眸一笑，声音低缓：\\"你来了。\\"", '
    '"emotion": "平和", "relationship_note": "与你约定明日同游"}'
)

MOCK_NUDGE_RESPONSE = (
    '{"reply": "他倚着窗，望向门口方向，轻声道：\\"这么久没来，还以为你忘了这里。\\"", '
    '"emotion": "淡淡失落", "relationship_note": ""}'
)


@pytest.fixture
def mock_llm():
    """Replace model_adapter.generate with a mock."""
    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "companion_nudge":
            return MOCK_NUDGE_RESPONSE
        return MOCK_CHAT_RESPONSE
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake) as mock:
        yield mock


@pytest_asyncio.fixture
async def companion_session(db):
    """Create a UserNPC + companion session, return {session_id, npc_name}."""
    u = User(id=USER_ID, username="test_companion", password_hash="x",
             display_name="test", is_adult=True)
    db.add(u)
    await db.flush()
    card = {
        "basic": {"name": "沈之澜", "gender": "男", "age": 24,
                  "identity": "温润世子", "cultivation": "炼气",
                  "faction": "镇北王府", "position": "世子"},
        "personality": {"core": "外冷内热，克制隐忍"},
        "speech_style": {"address_player": "称呼你为「阿青」"},
    }
    u_npc = UserNPC(user_id=USER_ID, name="沈之澜", model_data=card, tags=["温柔"])
    db.add(u_npc)
    await db.commit()
    await db.refresh(u_npc)
    info = await companion_engine.create_companion_session(db, USER_ID, u_npc.id, name="测试对话")
    return info


# ── create_companion_session ─────────────────────────────────

@pytest.mark.asyncio
async def test_create_companion_session(db, companion_session):
    """Creating a companion session: worldview pinned, NPC bound, no world regions."""
    info = companion_session
    assert info["session_id"]
    assert info["worldview"] == "companion_v1"
    assert info["npc_name"] == "沈之澜"

    # 校验：不生成世界区域（区别于世界会话）
    from ane.database.models import WorldRegion
    regions = (await db.execute(
        __import__("sqlalchemy").select(WorldRegion).where(WorldRegion.session_id == info["session_id"])
    )).scalars().all()
    assert len(regions) == 0


@pytest.mark.asyncio
async def test_create_companion_session_missing_npc(db):
    """Missing character card should raise ValueError."""
    import pytest as _pt
    with _pt.raises(ValueError):
        await companion_engine.create_companion_session(db, USER_ID, "nonexistent_npc")


# ── process_chat ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_chat(db, companion_session, mock_llm):
    """A message returns reply + emotion, saves conversation + relationship note."""
    result = await companion_engine.process_chat(
        db, companion_session["session_id"], "早呀，沈世子。", user_id=USER_ID,
    )
    assert result["reply"]
    assert "你来了" in result["reply"]
    assert result["emotion"] == "平和"
    assert result["relationship_note"]  # relationship note persisted

    # 关系记忆落库
    from ane.companion_engine import COMPANION_MEMORY_TYPE
    mems = (await db.execute(
        __import__("sqlalchemy").select(Memory).where(
            Memory.session_id == companion_session["session_id"],
            Memory.memory_type == COMPANION_MEMORY_TYPE,
        )
    )).scalars().all()
    assert len(mems) == 1
    assert "约定" in mems[0].content

    # 对话记录落库（user + ai）
    history = await companion_engine.get_history(db, companion_session["session_id"])
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_process_chat_wrong_worldview(db, mock_llm):
    """process_chat on a non-companion session should raise."""
    from ane.database.models import WorldSession
    s = WorldSession(user_id=USER_ID, name="普通世界", worldview="xianxia_v1")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    import pytest as _pt
    with _pt.raises(ValueError):
        await companion_engine.process_chat(db, s.id, "你好")


# ── Relationship memory ───────────────────────────────────────

@pytest.mark.asyncio
async def test_get_relationship_memory(db, companion_session):
    """get_relationship_memory strips [第N轮] prefix."""
    from ane.companion_engine import COMPANION_MEMORY_TYPE
    db.add(Memory(session_id=companion_session["session_id"],
                  memory_type=COMPANION_MEMORY_TYPE,
                  content="[第1轮] 阿青怕黑", turn_number=1))
    db.add(Memory(session_id=companion_session["session_id"],
                  memory_type=COMPANION_MEMORY_TYPE,
                  content="[第3轮] 阿青靠在我肩上", turn_number=3))
    await db.commit()

    mems = await companion_engine.get_relationship_memory(db, companion_session["session_id"])
    assert len(mems) == 2
    assert mems[0]["turn"] == 1
    assert mems[0]["content"] == "阿青怕黑"  # prefix stripped
    assert mems[1]["content"] == "阿青靠在我肩上"


# ── nudge ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nudge_opening_line(db, companion_session, mock_llm):
    """New session (no conversation) -> character speaks first (opening line)."""
    result = await companion_engine.nudge(db, companion_session["session_id"], user_id=USER_ID)
    assert result is not None
    assert result["kind"] == "开场白"
    assert "这么久没来" in result["reply"]

    # 开场白已存为一条 AI 消息（1 轮 = user占位 + assistant 两条记录）
    history = await companion_engine.get_history(db, companion_session["session_id"])
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "（角色主动开口）"
    assert history[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_nudge_idle_no_nudge(db, companion_session, mock_llm):
    """Active conversation (recent) -> no nudge."""
    # 先发一条消息（建立最后对话时间 = now）
    await companion_engine.process_chat(db, companion_session["session_id"], "我在。", user_id=USER_ID)
    result = await companion_engine.nudge(db, companion_session["session_id"], user_id=USER_ID)
    assert result is None


@pytest.mark.asyncio
async def test_nudge_active_after_idle(db, companion_session, mock_llm):
    """After long idle (conversation older than threshold) -> nudge fires."""
    from datetime import datetime, timedelta
    await companion_engine.process_chat(db, companion_session["session_id"], "我回来了。", user_id=USER_ID)

    # 把对话时间改到很久以前
    old = datetime.utcnow() - timedelta(hours=5)
    await db.execute(
        Memory.__table__.update().where(
            Memory.session_id == companion_session["session_id"],
            Memory.memory_type == "conversation",
        ).values(created_at=old)
    )
    # 清掉开场白可能设置的 _last_nudge_ts（新会话没 nudge 过，本来就没有）
    await db.commit()

    result = await companion_engine.nudge(db, companion_session["session_id"], user_id=USER_ID)
    assert result is not None
    assert result["kind"] == "主动搭话"
    assert "这么久没来" in result["reply"]


@pytest.mark.asyncio
async def test_nudge_suppression(db, companion_session, mock_llm):
    """After a nudge just fired, a second nudge is suppressed (cooldown)."""
    from datetime import datetime, timedelta
    from ane.database.models import NPC
    from ane.companion_engine import _NUDGE_TS_KEY

    # 模拟空闲 5 小时
    old = datetime.utcnow() - timedelta(hours=5)
    await db.execute(
        Memory.__table__.update().where(
            Memory.session_id == companion_session["session_id"],
            Memory.memory_type == "conversation",
        ).values(created_at=old)
    )
    # 首次 nudge 触发
    r1 = await companion_engine.nudge(db, companion_session["session_id"], user_id=USER_ID)
    assert r1 is not None

    # 再次 nudge：_last_nudge_ts 刚更新 → 抑制
    r2 = await companion_engine.nudge(db, companion_session["session_id"], user_id=USER_ID)
    assert r2 is None


# ── 主动搭话阈值配置化 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_nudge_settings_default(db, companion_session):
    """Default threshold is the module constant (30 min)."""
    s = await companion_engine.get_nudge_settings(db, companion_session["session_id"])
    from ane.companion_engine import NUDGE_IDLE_SECONDS
    assert s["idle_seconds"] == NUDGE_IDLE_SECONDS


@pytest.mark.asyncio
async def test_nudge_settings_custom(db, companion_session, mock_llm):
    """Set a custom threshold (very clingy) -> idle beyond it triggers nudge."""
    from datetime import datetime, timedelta

    # 设阈值为 60 秒（粘人）
    s = await companion_engine.set_nudge_settings(db, companion_session["session_id"], 60)
    assert s["idle_seconds"] == 60

    # 发一条消息（最后对话时间 = now）
    await companion_engine.process_chat(db, companion_session["session_id"], "我回来了。", user_id=USER_ID)

    # 模拟空闲 2 分钟（> 60s 阈值）
    old = datetime.utcnow() - timedelta(minutes=2)
    await db.execute(
        Memory.__table__.update().where(
            Memory.session_id == companion_session["session_id"],
            Memory.memory_type == "conversation",
        ).values(created_at=old)
    )
    await db.commit()

    # 阈值 60s：空闲 2 分钟应触发
    r = await companion_engine.nudge(db, companion_session["session_id"], user_id=USER_ID)
    assert r is not None
    assert r["kind"] == "主动搭话"

    # 读回确认
    s2 = await companion_engine.get_nudge_settings(db, companion_session["session_id"])
    assert s2["idle_seconds"] == 60


@pytest.mark.asyncio
async def test_nudge_settings_clamp(db, companion_session):
    """Out-of-range values are clamped to [0, 86400]."""
    s = await companion_engine.set_nudge_settings(db, companion_session["session_id"], 999999)
    assert s["idle_seconds"] == 86400
    s2 = await companion_engine.set_nudge_settings(db, companion_session["session_id"], -5)
    assert s2["idle_seconds"] == 0
