"""modern_city worldview smoke tests — prove the platform is truly generic."""

import pytest
from unittest.mock import AsyncMock, patch

from ane.game_engine import game_engine, TurnResult
from ane.database.models import WorldSession
from ane.modules.model_adapter import ModelAdapter
from ane.modules.input_validator import validate
from ane.modules.world_manager import world_manager
from ane.modules.player_manager import player_manager

MOCK_LLM_RESPONSE = """```json
{
  "narrative": "清晨的阳光透过窗帘洒进出租屋，你揉了揉眼睛。手机屏幕亮起，是一条新消息。\\n\\n楼下的便利店里，店员正往货架上补货，看到你进来，笑着打了个招呼。",
  "state_changes": [
    {"type": "location_change", "target": "player", "field": "location", "value": "阳光花园小区·楼下便利店"}
  ]
}
```"""


@pytest.fixture
def mock_llm():
    with patch.object(ModelAdapter, 'generate', new_callable=AsyncMock) as mock:
        mock.return_value = MOCK_LLM_RESPONSE
        yield mock


@pytest.mark.asyncio
async def test_create_session_with_modern_city(db):
    """Creating a session with modern_city worldview must bind it."""
    result = await game_engine.create_session(db, user_id='u', name="都市", worldview="modern_city")
    assert result["session_id"]
    s = await db.get(WorldSession, result["session_id"])
    assert s.worldview == "modern_city"


@pytest.mark.asyncio
async def test_modern_city_world_regions_from_pack(db):
    """World regions come from the modern_city pack (no sects)."""
    result = await game_engine.create_session(db, user_id='u', name="都市", worldview="modern_city")
    regions = await world_manager.get_all(db, result["session_id"])
    names = [r.name for r in regions]
    assert "阳光花园小区" in names
    assert not any("宗" in n for n in names)  # no xianxia sects


@pytest.mark.asyncio
async def test_modern_city_default_player_name(db):
    """Player stub name comes from the pack's player_defaults."""
    result = await game_engine.create_session(db, user_id='u', name="都市", worldview="modern_city")
    p = await player_manager.get_by_session(db, result["session_id"])
    assert p.name == "无名市民"


@pytest.mark.asyncio
async def test_modern_city_no_cultivate_intent(db):
    """modern_city has no cultivate keywords — '闭关' falls back to dialogue."""
    assert validate("我要闭关修炼", worldview="modern_city").intent == "dialogue"
    assert validate("我坐地铁去上班", worldview="modern_city").intent == "travel"


@pytest.mark.asyncio
async def test_modern_city_turn_and_panel(db, mock_llm):
    """A full turn renders the modern panel (职业/存款, no 修为/灵石)."""
    result = await game_engine.create_session(db, user_id='u', name="都市", worldview="modern_city")
    sid = result["session_id"]
    turn = await game_engine.process_turn(db, sid, "今天去公司面试", turn_number=1)
    assert isinstance(turn, TurnResult)
    assert "地铁" not in turn.narrative  # narrative comes from mock
    assert "便利店" in turn.narrative
    # Panel from modern spec — no xianxia fields
    assert "职业" in turn.player_panel or "姓名" in turn.player_panel
    assert "修为" not in turn.player_panel
    assert "灵石" not in turn.player_panel
