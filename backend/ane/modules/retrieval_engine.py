"""Retrieval Engine — loads relevant data for the current turn.

Phase 1+: Active Set = core NPCs (always) + secondary NPCs in same location.
Also retrieves "related but absent" characters via Fact-based lookup.
No embedding / RAG yet.
"""

import logging
from dataclasses import dataclass, field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ane.database.models import NPC as NPCModel, Fact

logger = logging.getLogger(__name__)


@dataclass
class ActiveSet:
    core_npcs: list[NPCModel] = field(default_factory=list)
    nearby_npcs: list[NPCModel] = field(default_factory=list)
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

        Phase 1+: core NPCs filtered by location. Secondary NPCs loaded
        only if they share the player's location hierarchy.
        Also retrieves related-but-absent characters via Fact lookup.
        """
        from ane.modules.npc_manager import npc_manager
        from ane.modules.world_manager import world_manager

        # 1. Core NPCs — filtered by player's location
        all_core = await npc_manager.get_core(db, session_id)
        player_loc_parts = set(player_location.replace("·", " ").split())
        core_npcs: list[NPCModel] = []
        for npc in all_core:
            if not npc.location:
                # Core NPC with no location set — include (likely just created)
                core_npcs.append(npc)
            elif npc.location == player_location:
                core_npcs.append(npc)
            else:
                npc_loc_parts = set(npc.location.replace("·", " ").split())
                if player_loc_parts & npc_loc_parts:
                    core_npcs.append(npc)

        # 2. Nearby NPCs — hierarchical location matching
        all_npcs = await npc_manager.get_by_session(db, session_id)
        core_ids = {n.id for n in core_npcs}
        player_loc_parts = set(player_location.replace("·", " ").split())

        nearby_npcs: list[NPCModel] = []
        for npc in all_npcs:
            if npc.id in core_ids:
                continue
            npc_loc_parts = set((npc.location or "").replace("·", " ").split())
            if npc.location == player_location or player_loc_parts & npc_loc_parts:
                nearby_npcs.append(npc)

        # 3. Location context — the place itself + parent chain
        location_context = await world_manager.get_location_context(
            db, session_id, player_location
        )

        # 4. Related but absent — characters mentioned in Facts
        #    but NOT in the Active Set (they exist in the world but aren't here)
        related_absent = await self._get_related_absent(
            db, session_id, core_npcs, nearby_npcs
        )

        logger.info(
            f"Active Set: {len(core_npcs)} core + {len(nearby_npcs)} nearby "
            f"+ {len(related_absent)} related-absent @ {player_location}"
        )

        return ActiveSet(
            core_npcs=core_npcs,
            nearby_npcs=nearby_npcs,
            location_context=location_context,
            related_absent=related_absent,
        )

    async def _get_related_absent(
        self,
        db: AsyncSession,
        session_id: str,
        core_npcs: list[NPCModel],
        nearby_npcs: list[NPCModel],
    ) -> list[NPCModel]:
        """Find characters mentioned in Facts who are NOT in the Active Set.

        Strategy:
          1. Query all character-category facts (category = 'character')
          2. Extract NPC names mentioned in fact content
          3. Cross-reference with all NPCs in the session
          4. Exclude NPCs already in the Active Set (core + nearby)
          5. Return up to 5, sorted by fact priority desc
        """
        from ane.modules.npc_manager import npc_manager

        # Get active set IDs as a fast lookup
        active_ids = {n.id for n in core_npcs} | {n.id for n in nearby_npcs}

        # Get character facts
        result = await db.execute(
            select(Fact)
            .where(
                Fact.session_id == session_id,
                Fact.category == "character",
            )
            .order_by(Fact.priority.desc())
            .limit(20)
        )
        character_facts = result.scalars().all()

        if not character_facts:
            return []

        # Get all NPCs in session for name matching
        all_npcs = await npc_manager.get_by_session(db, session_id)
        name_to_npc = {n.name: n for n in all_npcs if n.id not in active_ids}

        # Extract NPC names from fact content and match
        related: list[NPCModel] = []
        seen_ids: set[str] = set()

        for fact in character_facts:
            content = fact.content or ""
            for name, npc in name_to_npc.items():
                if name in content and npc.id not in seen_ids:
                    related.append(npc)
                    seen_ids.add(npc.id)
                    if len(related) >= 5:
                        return related

        return related


# Singleton
retrieval_engine = RetrievalEngine()
