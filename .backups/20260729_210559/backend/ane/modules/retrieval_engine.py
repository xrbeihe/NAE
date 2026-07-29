"""Retrieval Engine — loads relevant data for the current turn.

Phase 1+: Active Set = all NPCs at the player's location.
Also retrieves "related but absent" characters via
No embedding / RAG yet.
"""

import logging
from dataclasses import dataclass, field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ane.database.models import NPC as NPCModel

logger = logging.getLogger(__name__)


@dataclass
class ActiveSet:
    present_npcs: list[NPCModel] = field(default_factory=list)
    location_context: dict = field(default_factory=dict)
    related_absent: list[NPCModel] = field(default_factory=list)


class RetrievalEngine:
    """Loads only what's relevant to the current turn."""

    async def get_active_set(
        self,
        db: AsyncSession,
        session_id: str,
        player_location: str,
    ) -> ActiveSet:
        """Build the Active Set for this turn.

        Loads all NPCs at the player's location (hierarchical match).
        Also retrieves related-but-absent characters 
        """
        from ane.modules.npc_manager import npc_manager
        from ane.modules.world_manager import world_manager

        # All NPCs — filter by location hierarchy
        all_npcs = await npc_manager.get_by_session(db, session_id)
        player_loc_parts = set(player_location.replace("·", " ").split())

        present_npcs: list[NPCModel] = []
        for npc in all_npcs:
            if not npc.location:
                # NPC with no location set — include (likely just created)
                present_npcs.append(npc)
            elif npc.location == player_location:
                present_npcs.append(npc)
            else:
                npc_loc_parts = set((npc.location or "").replace("·", " ").split())
                if player_loc_parts & npc_loc_parts:
                    present_npcs.append(npc)

        # Location context — the place itself + parent chain
        location_context = await world_manager.get_location_context(
            db, session_id, player_location
        )

        # Related but absent — disabled (Fact table removed)
        related_absent = []

        logger.info(
            f"Active Set: {len(present_npcs)} present + "
            f"{len(related_absent)} related-absent @ {player_location}"
        )

        return ActiveSet(
            present_npcs=present_npcs,
            location_context=location_context,
            related_absent=related_absent,
        )

# Singleton
retrieval_engine = RetrievalEngine()
