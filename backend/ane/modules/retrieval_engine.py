"""Retrieval Engine — loads relevant data for the current turn.

Phase 1+: Active Set = 与场景相关、且**被叙事提到**的 NPC。判定**不看位置字符串**：
  ① 玩家标记的重要人物 → 始终在场（档案一直可用）
  ② 最近的对话 / 本轮玩家输入 / 上一轮信息栏里出现名字的 NPC → 在场（含后缀匹配：「路飞」↔「蒙奇·D·路飞」）
  ③ 其余 NPC（含位置为空的一次性路人）不再自动在场；死亡的 NPC 一律不注入
位置只作为【当前场景】的参考信息注入，不参与在场判定。
"""

import logging
import re
from dataclasses import dataclass, field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ane.database.models import NPC as NPCModel

logger = logging.getLogger(__name__)

# 在场 NPC 上限：重要人物优先，其余按提到顺序补足（保护 prompt 体积）
MAX_PRESENT_NPCS = 8


@dataclass
class ActiveSet:
    present_npcs: list[NPCModel] = field(default_factory=list)
    location_context: dict = field(default_factory=dict)


def is_name_mentioned(name: str, text: str) -> bool:
    """名字（或它的后缀简称）是否出现在文本里。

    「蒙奇·D·路飞」 → 文本里有「路飞」也算提到（取名字末尾 2-3 字）；
    反向不成立（「林星」不会命中「林星如」，避免前缀误伤）。
    """
    if not name or not text:
        return False
    if name in text:
        return True
    for n in (3, 2):
        if len(name) > n and name[-n:] in text:
            return True
    return False


class RetrievalEngine:
    """Loads only what's relevant to the current turn."""

    async def get_active_set(
        self,
        db: AsyncSession,
        session_id: str,
        player_location: str = "",
        mentioned_text: str = "",
        max_present: int = MAX_PRESENT_NPCS,
    ) -> ActiveSet:
        """Build the Active Set for this turn.

        在场判定不看位置（位置只影响【当前场景】参考信息）：
          重要人物始终在场；其余 NPC 需要名字出现在 mentioned_text 里。
        """
        from ane.modules.npc_manager import npc_manager
        from ane.modules.world_manager import world_manager

        all_npcs = await npc_manager.get_by_session(db, session_id)

        important: list[NPCModel] = []
        mentioned: list[NPCModel] = []
        for npc in all_npcs:
            if not npc.is_alive:            # 死亡 NPC 不再注入档案
                continue
            if npc.is_important:
                important.append(npc)
            elif is_name_mentioned(npc.name, mentioned_text):
                mentioned.append(npc)

        present_npcs = important + mentioned
        if len(present_npcs) > max_present:
            logger.info(
                f"Active Set truncated: {len(present_npcs)} → {max_present} "
                f"(important {len(important)} kept first)"
            )
            present_npcs = present_npcs[:max_present]

        # Location context — the place itself + parent chain（仅作场景参考，不决定谁在场）
        location_context = await world_manager.get_location_context(
            db, session_id, player_location
        )

        logger.info(
            f"Active Set: {len(present_npcs)} present (important={len(important)}, "
            f"mentioned={len(mentioned)}) @ {player_location or '未设定'}"
        )

        return ActiveSet(
            present_npcs=present_npcs,
            location_context=location_context,
        )

# Singleton
retrieval_engine = RetrievalEngine()
