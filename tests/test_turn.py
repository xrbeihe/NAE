"""Integration test: full turn loop with mock LLM.

Run: python -m pytest tests/ -v
"""

import pytest
from unittest.mock import AsyncMock, patch

from ane.game_engine import game_engine, TurnResult
from ane.modules.model_adapter import ModelAdapter
from ane.modules.memory_manager import memory_manager

# ── Mock LLM response ────────────────────────────────────────

MOCK_LLM_RESPONSE = """```json
{
  "narrative": "山风拂过青云山的松林，发出沙沙的声响。你站在山门前，望着远处云雾缭绕的峰峦，心中涌起一股豪情。\\n\\n一位青衫修士从山门内走出，向你抱拳道：\\"这位道友，可是初来青云宗？\\"",
  "state_changes": [
    {"type": "location_change", "target": "player", "field": "location", "value": "青云宗·山门"}
  ]
}
```"""


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def mock_llm():
    """Replace model_adapter.generate with a mock."""
    with patch.object(ModelAdapter, 'generate', new_callable=AsyncMock) as mock:
        mock.return_value = MOCK_LLM_RESPONSE
        yield mock


# ── Tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_session(db):
    """Creating a session should return valid metadata."""
    result = await game_engine.create_session(db, user_id='test_user', name="测试世界")

    assert result["session_id"]
    assert result["name"] == "测试世界"
    assert result["region_count"] > 0
    assert result["player_name"] == "无名修士"
    assert result["player_location"]  # random from player start locations


@pytest.mark.asyncio
async def test_full_turn_loop(db, mock_llm):
    """A complete turn should return narrative + state_changes + world_time."""
    session_result = await game_engine.create_session(db, user_id='test_user', name="测试世界")
    session_id = session_result["session_id"]

    result = await game_engine.process_turn(
        db, session_id, "你好，请问阁下是？", turn_number=1
    )

    assert isinstance(result, TurnResult)
    assert len(result.narrative) > 0
    assert "山风" in result.narrative
    assert len(result.state_changes) == 1
    assert result.state_changes[0]["type"] == "location_change"
    assert result.world_time
    assert result.time_delta > 0
    assert result.is_system_command is False

    assert mock_llm.call_count >= 1
    # HTEM has been removed — NPC_MODELING replaces it only on marking turns
    assert result.htem_directory == ""


@pytest.mark.asyncio
async def test_system_commands(db):
    """System commands should not trigger LLM calls."""
    result = await game_engine.create_session(db, user_id='test_user', name="测试世界")
    session_id = result["session_id"]

    turn = await game_engine.process_turn(db, session_id, "/status")
    assert turn.is_system_command is True
    assert "无名修士" in (turn.system_response or "")

    turn = await game_engine.process_turn(db, session_id, "/help")
    assert turn.is_system_command is True
    assert "/status" in (turn.system_response or "")


@pytest.mark.asyncio
async def test_conversation_memory(db, mock_llm):
    """Conversation turns should be stored in memory."""
    result = await game_engine.create_session(db, user_id='test_user', name="测试世界")
    session_id = result["session_id"]

    await game_engine.process_turn(db, session_id, "你好", turn_number=1)
    await game_engine.process_turn(db, session_id, "这里是什么地方？", turn_number=2)

    conv = await memory_manager.get_full_conversation(db, session_id)
    assert len(conv) == 2
    assert "你好" in conv[0].content
    assert "这里是什么地方" in conv[1].content


@pytest.mark.asyncio
async def test_facts_management(db):
    """Facts should be addable and retrievable."""
    result = await game_engine.create_session(db, user_id='test_user', name="测试世界")
    session_id = result["session_id"]

    await memory_manager.add_fact(db, session_id, "测试事实", category="test")
    await db.commit()

    facts = await memory_manager.get_facts(db, session_id)
    assert len(facts) >= 1
    assert any(f.content == "测试事实" for f in facts)


@pytest.mark.asyncio
async def test_time_advances_with_intent(db, mock_llm):
    """Time should advance more for 'cultivate' than 'dialogue'."""
    result = await game_engine.create_session(db, user_id='test_user', name="测试世界")
    session_id = result["session_id"]

    turn1 = await game_engine.process_turn(db, session_id, "你好", turn_number=1)
    dialogue_ticks = turn1.time_delta

    turn2 = await game_engine.process_turn(db, session_id, "我要闭关修炼", turn_number=2)
    cultivate_ticks = turn2.time_delta

    assert cultivate_ticks > dialogue_ticks
    # Time label may or may not differ depending on epoch values,
    # but the delta must be significantly larger for cultivation.
    assert cultivate_ticks >= 720  # cultivation = long duration
