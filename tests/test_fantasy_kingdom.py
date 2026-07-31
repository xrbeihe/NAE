"""fantasy_kingdom worldview smoke tests — third worldview proves generality."""

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
  "narrative": "铁橡堡的酒馆里飘着麦酒和烤肉的香气。你推开厚重的木门，炉火的光映在粗糙的木墙上。柜台后的老板娘擦着杯子，抬眼打量了你一眼。\\n\\n角落里坐着几个冒险者，一个穿着旧链甲的雇佣兵正往杯里倒麦酒。",
  "state_changes": [
    {"type": "location_change", "target": "player", "field": "location", "value": "铁橡堡·酒馆"}
  ]
}
```"""


@pytest.fixture
def mock_llm():
    with patch.object(ModelAdapter, 'generate', new_callable=AsyncMock) as mock:
        mock.return_value = MOCK_LLM_RESPONSE
        yield mock


@pytest.mark.asyncio
async def test_create_session_with_fantasy_kingdom(db):
    result = await game_engine.create_session(db, user_id='u', name="西幻", worldview="fantasy_kingdom")
    assert result["session_id"]
    s = await db.get(WorldSession, result["session_id"])
    assert s.worldview == "fantasy_kingdom"


@pytest.mark.asyncio
async def test_fantasy_regions_from_pack(db):
    result = await game_engine.create_session(db, user_id='u', name="西幻", worldview="fantasy_kingdom")
    regions = await world_manager.get_all(db, result["session_id"])
    names = [r.name for r in regions]
    assert "铁橡堡" in names
    assert "影牙森林" in names
    assert not any("宗" in n for n in names)  # no xianxia sects


@pytest.mark.asyncio
async def test_fantasy_default_player_name(db):
    result = await game_engine.create_session(db, user_id='u', name="西幻", worldview="fantasy_kingdom")
    p = await player_manager.get_by_session(db, result["session_id"])
    assert p.name == "无名旅人"


@pytest.mark.asyncio
async def test_fantasy_no_cultivate_intent(db):
    assert validate("我要闭关修炼", worldview="fantasy_kingdom").intent == "dialogue"
    assert validate("我骑马去城堡", worldview="fantasy_kingdom").intent == "travel"
    assert validate("我要去地下城探险", worldview="fantasy_kingdom").intent == "travel"


@pytest.mark.asyncio
async def test_fantasy_turn_and_panel(db, mock_llm):
    result = await game_engine.create_session(db, user_id='u', name="西幻", worldview="fantasy_kingdom")
    sid = result["session_id"]
    turn = await game_engine.process_turn(db, sid, "走进酒馆要一杯麦酒", turn_number=1)
    assert isinstance(turn, TurnResult)
    assert "铁橡堡" in turn.narrative
    # Panel from fantasy spec — no xianxia fields
    assert "职业" in turn.player_panel or "姓名" in turn.player_panel
    assert "修为" not in turn.player_panel
    assert "灵石" not in turn.player_panel


def test_fantasy_extra_event_types():
    from ane.modules.output_parser import _event_types_for
    types = _event_types_for("fantasy_kingdom")
    assert "magic_progress" in types
    assert "cultivation_change" not in types  # fantasy does not extend cultivation


def test_fantasy_time_override():
    from ane.modules.time_manager import time_manager as tm
    # fantasy overrides travel → 48 (global is 12)
    assert tm.calc_delta("travel", worldview="fantasy_kingdom") == 48


def test_fantasy_calendar_override():
    from ane.modules.time_manager import time_manager as tm
    # fantasy calendar uses 黎明/黄昏 as time-of-day names (not xianxia 清晨/夜晚)
    # epoch 0 = day 1 dawn
    t = tm.format_world_time(0, worldview="fantasy_kingdom")
    assert "黎明" in t
    # xianxia (no calendar override) keeps its own labels
    t_x = tm.format_world_time(0, worldview="xianxia_v1")
    assert "清晨" in t_x
    # default (no worldview) unchanged
    t_def = tm.format_world_time(0)
    assert t_def == t_x
