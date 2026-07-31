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


def _month_to_season(month: int, calendar: dict | None = None) -> str:
    """Map month number (1-based) to season name.

    Config-driven mapping: [[3,5,"春"],[6,8,"夏"],[9,11,"秋"],[12,2,"冬"]]
    A worldview calendar may override via manifest `calendar.month_to_season`.
    """
    mapping = calendar.get("month_to_season") if calendar else None
    if not mapping:
        mapping = MONTH_TO_SEASON
    seasons = calendar.get("seasons") if calendar else None
    if not seasons:
        seasons = SEASONS
    for start, end, name in mapping:
        if start <= end:
            if start <= month <= end:
                return name
        else:  # wraps around (e.g. 12-2)
            if month >= start or month <= end:
                return name
    return seasons[0]  # fallback


def _worldview_calendar(worldview: str | None) -> dict | None:
    """Return a worldview's calendar override (seasons/times_of_day/…), or None."""
    if not worldview:
        return None
    from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
    wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
    cal = (wv.manifest or {}).get("calendar")
    return cal if isinstance(cal, dict) and cal else None


def _resolve_seasons(calendar: dict | None) -> list[str]:
    return calendar.get("seasons") if calendar and calendar.get("seasons") else SEASONS


def _resolve_times_of_day(calendar: dict | None) -> list[str]:
    return calendar.get("times_of_day") if calendar and calendar.get("times_of_day") else TIMES_OF_DAY


class TimeManager:
    """Manages world time advancement and NPC state progression."""

    # ── Time advance ──────────────────────────────────────────

    def calc_delta(self, intent: str, worldview: str | None = None) -> int:
        """Return tick delta for a given intent.

        A worldview pack may override TIME_PER_INTENT via manifest
        `time_per_intent` (merged over the global config).
        """
        if worldview:
            from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
            wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
            wv_override = (wv.manifest or {}).get("time_per_intent")
            if isinstance(wv_override, dict) and intent in wv_override:
                try:
                    return max(1, int(wv_override[intent]))
                except (TypeError, ValueError):
                    pass
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

    def format_world_time(self, epoch: int, worldview: str | None = None) -> str:
        """Format tick count into human-readable world time label.

        Calendar:
          - 1 year = 360 days = 12 months × 30 days
          - Season mapping: 3-5=春 6-8=夏 9-11=秋 12-2=冬
          - 1 day = TICKS_PER_DAY (24) ticks
          - Time of day: 清晨/正午/黄昏/深夜, each TICKS_PER_TIME_OF_DAY (6) ticks

        A worldview pack may override seasons/times_of_day/month_to_season via
        manifest `calendar`. The label format itself is fixed (engine-owned).
        """
        calendar = _worldview_calendar(worldview)
        seasons = _resolve_seasons(calendar)
        times_of_day = _resolve_times_of_day(calendar)

        total_days = epoch // TICKS_PER_DAY
        years = 1 + total_days // DAYS_PER_YEAR
        day_of_year = total_days % DAYS_PER_YEAR
        month = 1 + day_of_year // DAYS_PER_MONTH
        day_of_month = day_of_year % DAYS_PER_MONTH + 1
        season = _month_to_season(month, calendar)

        # Time of day within current day
        ticks_in_day = epoch % TICKS_PER_DAY
        time_idx = (ticks_in_day * len(times_of_day)) // TICKS_PER_DAY
        time_idx = min(time_idx, len(times_of_day) - 1)
        tod = times_of_day[time_idx]

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

        delta = self.calc_delta(intent, getattr(session, "worldview", None))
        session.time_epoch += delta
        session.world_time = self.format_world_time(session.time_epoch, getattr(session, "worldview", None))
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
        session.world_time = self.format_world_time(session.time_epoch, getattr(session, "worldview", None))
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
        """After time advances, update important NPC states."""
        # Resolve the session's worldview to load its event pool.
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        sess = await db.get(WorldSession, session_id)
        worldview = getattr(sess, "worldview", None) or DEFAULT_WORLDVIEW_ID
        wv = get_worldview(worldview)

        result = await db.execute(
            select(NPC).where(
                NPC.session_id == session_id,
                NPC.is_important == True,
                NPC.is_alive == True,
            )
        )
        important_npcs = result.scalars().all()

        changes: list[dict] = []

        for npc in important_npcs:
            change = await self._progress_npc(db, npc, ticks, wv)
            if change:
                changes.append(change)

        if changes:
            logger.info(f"Scheduler: {len(changes)} NPC state changes for session {session_id}")

        return changes

    async def _progress_npc(
        self, db: AsyncSession, npc: NPC, ticks: int, wv=None,
    ) -> dict | None:
        """Progress a single NPC's state by the given tick count.

        Event pool comes from the worldview pack's events.json (falling back
        to the legacy inline xianxia behavior).
        """
        if wv is None:
            from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
            wv = get_worldview(DEFAULT_WORLDVIEW_ID)

        events = wv.events or {}
        seclusion_threshold = int(events.get("seclusion_threshold", 2160))
        idle_threshold = int(events.get("idle_threshold", 2160))
        idle_probability = float(events.get("idle_probability", 0.2))
        seclusion_event = events.get("seclusion_event", {})
        idle_events = events.get("idle_events", []) or []

        state = dict(npc.long_term_state or {})
        activity = state.get("activity", "idle")

        if activity == "seclusion":
            progress = state.get("seclusion_progress", 0) + ticks
            state["seclusion_progress"] = progress

            if progress >= seclusion_threshold:
                state["seclusion_progress"] = progress % seclusion_threshold
                npc.long_term_state = state
                await db.flush()
                desc = (seclusion_event.get("description", "") or "").replace("{npc_name}", npc.name)
                if not desc:
                    desc = f"{npc.name}在闭关中有所精进。"
                return {
                    "npc_id": npc.id,
                    "npc_name": npc.name,
                    "type": seclusion_event.get("type", "cultivation_progress"),
                    "description": desc,
                }

            npc.long_term_state = state
            await db.flush()
            return None

        if ticks >= idle_threshold and random.random() < idle_probability:
            if idle_events:
                evt = random.choice(idle_events)
                desc = (evt.get("description", "") or "").replace("{npc_name}", npc.name)
                return {
                    "npc_id": npc.id,
                    "npc_name": npc.name,
                    "type": evt.get("type", "random_encounter"),
                    "description": desc,
                }

        return None


# Singleton
time_manager = TimeManager()
