"""NPC Manager — CRUD + initial generation (placeholder names only).
Full NPC profiles are created by NPC_MODELING when the player marks them important.
"""

import logging
import random
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ane.database.models import NPC
from ane.content.npc_templates import (
    SURNAMES,
    GIVEN_NAMES_MALE,
    GIVEN_NAMES_FEMALE,
)

logger = logging.getLogger(__name__)


class NPCManager:

    async def create(self, db: AsyncSession, session_id: str, **kwargs) -> NPC:
        npc = NPC(session_id=session_id, **kwargs)
        db.add(npc)
        await db.flush()
        return npc

    async def get_by_session(self, db: AsyncSession, session_id: str) -> list[NPC]:
        result = await db.execute(
            select(NPC).where(NPC.session_id == session_id)
        )
        return list(result.scalars().all())

    async def get_core(self, db: AsyncSession, session_id: str) -> list[NPC]:
        """Return all NPCs with is_core=True."""
        result = await db.execute(
            select(NPC).where(
                NPC.session_id == session_id,
                NPC.is_core == True,
                NPC.is_alive == True,
            )
        )
        return list(result.scalars().all())

    async def get_by_location(
        self, db: AsyncSession, session_id: str, location: str
    ) -> list[NPC]:
        result = await db.execute(
            select(NPC).where(
                NPC.session_id == session_id,
                NPC.location == location,
                NPC.is_alive == True,
            )
        )
        return list(result.scalars().all())

    async def get_by_id(self, db: AsyncSession, npc_id: str) -> NPC | None:
        result = await db.execute(select(NPC).where(NPC.id == npc_id))
        return result.scalar_one_or_none()

    async def get_important(
        self, db: AsyncSession, session_id: str
    ) -> list[NPC]:
        """Return all player-marked important NPCs for this session."""
        result = await db.execute(
            select(NPC).where(
                NPC.session_id == session_id,
                NPC.is_important == True,
            )
        )
        return list(result.scalars().all())

    async def mark_important(self, db: AsyncSession, npc_id: str) -> NPC | None:
        """Mark an NPC as important."""
        npc = await self.get_by_id(db, npc_id)
        if npc:
            npc.is_important = True
            await db.flush()
        return npc

    async def update_location(
        self, db: AsyncSession, npc_id: str, location: str
    ) -> NPC | None:
        npc = await self.get_by_id(db, npc_id)
        if npc:
            npc.location = location
            await db.flush()
        return npc

    async def update_state(
        self, db: AsyncSession, npc_id: str, key: str, value
    ) -> NPC | None:
        npc = await self.get_by_id(db, npc_id)
        if npc:
            state = dict(npc.long_term_state or {})
            state[key] = value
            npc.long_term_state = state
            await db.flush()
        return npc

    # ── Helpers ───────────────────────────────────────────────

    def _random_name(self, used: set[str]) -> str:
        """Generate a unique random xianxia-style name."""
        for _ in range(100):
            surname = random.choice(SURNAMES)
            pool = random.choice([GIVEN_NAMES_MALE, GIVEN_NAMES_FEMALE])
            given = random.choice(pool)
            name = surname + given
            if name not in used:
                return name
        return (
            random.choice(SURNAMES)
            + random.choice(GIVEN_NAMES_MALE)
            + str(random.randint(1, 99))
        )


# Singleton
npc_manager = NPCManager()
