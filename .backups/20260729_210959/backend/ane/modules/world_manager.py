"""World Manager — static world data CRUD + initial generation."""

import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ane.database.models import WorldRegion

logger = logging.getLogger(__name__)


class WorldManager:

    async def create_region(
        self,
        db: AsyncSession,
        session_id: str,
        name: str,
        region_type: str = "area",
        description: str = "",
        parent_id: str | None = None,
        attributes: dict | None = None,
    ) -> WorldRegion:
        region = WorldRegion(
            session_id=session_id,
            name=name,
            region_type=region_type,
            description=description,
            parent_id=parent_id,
            attributes=attributes or {},
        )
        db.add(region)
        await db.flush()
        return region

    async def generate_initial_world(self, db: AsyncSession, session_id: str) -> list[WorldRegion]:
        """Generate world regions from templates (sects + settlements)."""
        all_regions: list[WorldRegion] = []

        from ane.content.world_templates import SECTS, SETTLEMENTS
        for entry in SECTS + SETTLEMENTS:
            region = await self.create_region(
                db, session_id, entry["name"], entry["type"], entry["description"],
                attributes=entry.get("attributes", {}),
            )
            all_regions.append(region)

        await db.flush()
        logger.info(f"Generated world with {len(all_regions)} regions for session {session_id}")
        return all_regions

    # ── Queries ───────────────────────────────────────────────

    async def get_all(self, db: AsyncSession, session_id: str) -> list[WorldRegion]:
        result = await db.execute(
            select(WorldRegion).where(WorldRegion.session_id == session_id)
        )
        return list(result.scalars().all())

    async def get_by_name(
        self, db: AsyncSession, session_id: str, name: str
    ) -> WorldRegion | None:
        result = await db.execute(
            select(WorldRegion).where(
                WorldRegion.session_id == session_id,
                WorldRegion.name == name,
            )
        )
        return result.scalar_one_or_none()

    async def get_children(
        self, db: AsyncSession, parent_id: str
    ) -> list[WorldRegion]:
        result = await db.execute(
            select(WorldRegion).where(WorldRegion.parent_id == parent_id)
        )
        return list(result.scalars().all())

    async def get_location_context(
        self, db: AsyncSession, session_id: str, location_name: str
    ) -> dict:
        """Return a context dict for a location — the location itself + parent chain."""
        loc = await self.get_by_name(db, session_id, location_name)
        if not loc:
            return {"current": None, "parents": []}

        parents = []
        current_parent_id = loc.parent_id
        # Walk up the parent chain (max depth 5 to avoid infinite loops)
        for _ in range(5):
            if not current_parent_id:
                break
            result = await db.execute(
                select(WorldRegion).where(WorldRegion.id == current_parent_id)
            )
            parent = result.scalar_one_or_none()
            if parent:
                parents.insert(0, {"name": parent.name, "type": parent.region_type, "attributes": parent.attributes})
                current_parent_id = parent.parent_id
            else:
                break

        return {
            "current": {"name": loc.name, "type": loc.region_type, "description": loc.description, "attributes": loc.attributes},
            "parents": parents,
        }


# Singleton
world_manager = WorldManager()
