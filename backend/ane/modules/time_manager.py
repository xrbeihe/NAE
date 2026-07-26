"""Time Manager — time advance calculation + inline NPC state progression (Phase 1 Scheduler)."""

import math
import logging
import random
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ane.database.models import WorldSession, NPC
from ane.config import (
    TIME_PER_INTENT, SEASONS, TIMES_OF_DAY,
    TICKS_PER_YEAR, DAYS_PER_YEAR, MONTHS_PER_YEAR,
    DAYS_PER_MONTH, TICKS_PER_DAY, TICKS_PER_TIME_OF_DAY,
    MONTH_TO_SEASON, MAP_WIDTH_LOGICAL, TRAVEL_DAYS_ACROSS_MAP,
)

logger = logging.getLogger(__name__)


def _month_to_season(month: int) -> str:
    """Map month number (1-based) to season name.

    Config-driven mapping: [[3,5,"春"],[6,8,"夏"],[9,11,"秋"],[12,2,"冬"]]
    """
    for start, end, name in MONTH_TO_SEASON:
        if start <= end:
            if start <= month <= end:
                return name
        else:  # wraps around (e.g. 12-2)
            if month >= start or month <= end:
                return name
    return SEASONS[0]  # fallback


class TimeManager:
    """Manages world time advancement and NPC state progression."""

    # ── Time advance ──────────────────────────────────────────

    def calc_delta(self, intent: str) -> int:
        """Return tick delta for a given intent."""
        return TIME_PER_INTENT.get(intent, 1)

    def calc_travel_delta(self, from_x: float, from_y: float, to_x: float, to_y: float) -> int:
        """Calculate travel time in ticks based on map distance.

        Map width (800 logical px) = TRAVEL_DAYS_ACROSS_MAP (30) days of travel.
        Returns ticks = days * TICKS_PER_DAY.
        """
        dist = math.hypot(to_x - from_x, to_y - from_y)
        days = dist / MAP_WIDTH_LOGICAL * TRAVEL_DAYS_ACROSS_MAP
        ticks = max(1, round(days * TICKS_PER_DAY))
        return ticks

    def format_world_time(self, epoch: int) -> str:
        """Format tick count into human-readable world time label.

        Calendar:
          - 1 year = 360 days = 12 months × 30 days
          - Season mapping: 3-5=春 6-8=夏 9-11=秋 12-2=冬
          - 1 day = TICKS_PER_DAY (24) ticks
          - Time of day: 清晨/正午/黄昏/深夜, each TICKS_PER_TIME_OF_DAY (6) ticks
        """
        total_days = epoch // TICKS_PER_DAY
        years = 1 + total_days // DAYS_PER_YEAR
        day_of_year = total_days % DAYS_PER_YEAR
        month = 1 + day_of_year // DAYS_PER_MONTH
        day_of_month = day_of_year % DAYS_PER_MONTH + 1
        season = _month_to_season(month)

        # Time of day within current day
        ticks_in_day = epoch % TICKS_PER_DAY
        time_idx = (ticks_in_day * len(TIMES_OF_DAY)) // TICKS_PER_DAY
        time_idx = min(time_idx, len(TIMES_OF_DAY) - 1)
        tod = TIMES_OF_DAY[time_idx]

        return f"第{years}年·{month}月·{day_of_month}日·{season}季·{tod}"

    async def advance(
        self,
        db: AsyncSession,
        session_id: str,
        intent: str,
    ) -> tuple[int, str]:
        """Advance world time by the amount indicated by intent.

        Returns (ticks_added, new_world_time_label).
        """
        result = await db.execute(
            select(WorldSession).where(WorldSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if not session:
            return 0, ""

        delta = self.calc_delta(intent)
        session.time_epoch += delta
        session.world_time = self.format_world_time(session.time_epoch)
        await db.flush()

        logger.info(
            f"Time advance: +{delta} ticks ({intent}) → {session.world_time}"
        )
        return delta, session.world_time

    async def advance_ticks(
        self,
        db: AsyncSession,
        session_id: str,
        ticks: int,
        reason: str = "manual",
    ) -> tuple[int, str]:
        """Advance world time by a specific tick count.

        Returns (ticks_added, new_world_time_label).
        """
        result = await db.execute(
            select(WorldSession).where(WorldSession.id == session_id)
        )
        session = result.scalar_one_or_none()
        if not session:
            return 0, ""

        session.time_epoch += ticks
        session.world_time = self.format_world_time(session.time_epoch)
        await db.flush()

        logger.info(
            f"Time advance: +{ticks} ticks ({reason}) → {session.world_time}"
        )
        return ticks, session.world_time

    # ── Inline Scheduler (Phase 1) ────────────────────────────

    async def update_active_npcs(
        self,
        db: AsyncSession,
        session_id: str,
        ticks: int,
    ) -> list[dict]:
        """After time advances, update Active Set NPC states."""
        result = await db.execute(
            select(NPC).where(
                NPC.session_id == session_id,
                NPC.is_core == True,
                NPC.is_alive == True,
            )
        )
        core_npcs = result.scalars().all()

        changes: list[dict] = []

        for npc in core_npcs:
            change = await self._progress_npc(db, npc, ticks)
            if change:
                changes.append(change)

        if changes:
            logger.info(f"Scheduler: {len(changes)} NPC state changes for session {session_id}")

        return changes

    async def _progress_npc(
        self, db: AsyncSession, npc: NPC, ticks: int
    ) -> dict | None:
        """Progress a single NPC's state by the given tick count."""
        state = dict(npc.long_term_state or {})
        activity = state.get("activity", "idle")

        if activity == "seclusion":
            progress = state.get("seclusion_progress", 0) + ticks
            state["seclusion_progress"] = progress

            threshold = 2160  # ~3 months at 24 ticks/day
            if progress >= threshold:
                state["seclusion_progress"] = progress % threshold
                npc.long_term_state = state
                await db.flush()
                return {
                    "npc_id": npc.id,
                    "npc_name": npc.name,
                    "type": "cultivation_progress",
                    "description": f"{npc.name}在闭关中修为有所精进。",
                }

            npc.long_term_state = state
            await db.flush()
            return None

        if ticks >= 2160 and random.random() < 0.2:
            event_type = random.choice(["minor_cultivation", "minor_encounter"])
            if event_type == "minor_cultivation":
                return {
                    "npc_id": npc.id,
                    "npc_name": npc.name,
                    "type": "cultivation_progress",
                    "description": f"听闻{npc.name}近日修为略有精进。",
                }
            else:
                return {
                    "npc_id": npc.id,
                    "npc_name": npc.name,
                    "type": "random_encounter",
                    "description": f"{npc.name}似乎经历了一些事，但详情不明。",
                }

        return None


# Singleton
time_manager = TimeManager()
