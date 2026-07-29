"""Player Manager — CRUD operations for the Player entity."""

import json
import logging
import random
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ane.database.models import Player

logger = logging.getLogger(__name__)

# Pick a random location from world templates for player start
# Loaded dynamically from world_templates.json to avoid hardcoded name lists.
from ane.content.json_loader import load_json
_WORLD_DATA = load_json("world_templates.json")
_START_LOCATIONS: list[str] = [s["name"] for s in _WORLD_DATA.get("settlements", [])]

# Load player creation templates
_templates_path = Path(__file__).resolve().parent.parent / "content" / "player_templates.json"
with open(_templates_path, "r", encoding="utf-8") as f:
    PLAYER_TEMPLATES = json.load(f)


class PlayerManager:
    """Manages player data. Does NOT handle narrative logic."""

    def get_templates(self) -> dict:
        """Return the player creation template data for the frontend."""
        return PLAYER_TEMPLATES

    async def create(self, db: AsyncSession, session_id: str) -> Player:
        """Create a bare-minimum player stub. Character details come from apply_character()."""
        location = random.choice(_START_LOCATIONS)
        player = Player(
            session_id=session_id,
            name="无名修士",
            cultivation="凡人",
            location=location,
            attributes={"location_hierarchy": location},
        )
        db.add(player)
        await db.flush()
        logger.info(f"Player stub created: {player.id} in session {session_id} @ {location}")
        return player

    async def apply_character(
        self,
        db: AsyncSession,
        session_id: str,
        name: str,
        age: int,
        gender: str,
        background: str,
        cultivation: str,
        personality: str,
        identity: str,
        golden_finger_id: str = "",
        golden_finger_custom: str = "",
        identity_custom: str = "",
        personality_custom: str = "",
    ) -> Player | None:
        """Apply player-chosen character details. Writes all choices into player attributes
        and updates core fields so the prompt reflects what the player actually chose."""
        player = await self.get_by_session(db, session_id)
        if not player:
            return None

        id_data = PLAYER_TEMPLATES["identities"].get(identity, {})
        if not id_data:
            logger.warning(f"Unknown identity '{identity}' — using raw value")
            id_data = {"label": identity, "desc": "", "clothing": "", "monthly_income": "", "background": "", "spiritual_root": "", "talent_note": ""}

        bg_data = {}
        if PLAYER_TEMPLATES.get("backgrounds"):
            bg_data = next((b for b in PLAYER_TEMPLATES["backgrounds"] if b["value"] == background), {})
        if not bg_data:
            bg_data = {"label": background, "desc": "", "initial_resource": "", "personality_tendency": "", "typical_sect_path": ""}

        attrs = dict(player.attributes or {})

        # ── Core fields ──
        player.name = name
        player.cultivation = cultivation

        # ── Identity-derived attributes ──
        attrs["age"] = age
        attrs["gender"] = gender
        attrs["background"] = background
        attrs["identity"] = identity

        if identity == "custom" and identity_custom:
            # Custom identity: use player-provided text as the identity description
            attrs["clothing"] = ""
            attrs["monthly_income"] = ""
            attrs["spiritual_root"] = ""
            attrs["talent_note"] = ""
            attrs["background_summary"] = identity_custom
            attrs["identity_desc"] = "自定义身份"
            attrs["identity_custom"] = identity_custom
        else:
            attrs["clothing"] = id_data.get("clothing", "")
            attrs["monthly_income"] = id_data.get("monthly_income", "")
            attrs["spiritual_root"] = id_data.get("spiritual_root", "")
            attrs["talent_note"] = id_data.get("talent_note", "")
            attrs["background_summary"] = id_data.get("background", "")
            attrs["identity_desc"] = id_data.get("desc", "")
            attrs.pop("identity_custom", None)

        # ── Player-chosen attributes ──
        if personality == "custom" and personality_custom:
            attrs["personality"] = personality_custom
            attrs["personality_custom"] = personality_custom
        else:
            attrs["personality"] = personality
            attrs.pop("personality_custom", None)

        # ── Defaults for fields the player doesn't select ──
        attrs.setdefault("height", 175)
        attrs.setdefault("weight", 65)
        attrs.setdefault("appearance_brief", "相貌平平")
        attrs.setdefault("appearance_summary", "")
        attrs.setdefault("moral_character", "节操正常")
        attrs.setdefault("sexual_knowledge", "粗浅")
        attrs.setdefault("fertility", "正常")
        attrs.setdefault("savings", "10块下品灵石")
        attrs.setdefault("lifestyle_summary", "")
        attrs.setdefault("special_constitution", "")
        attrs.setdefault("current_action", "")
        attrs.setdefault("current_pose", "")
        attrs.setdefault("visible_state", "")
        attrs.setdefault("relations", [])
        attrs.setdefault("location_hierarchy", player.location or "")

        # ── Golden Finger ──
        if golden_finger_id:
            gf_templates = PLAYER_TEMPLATES.get("golden_fingers", [])
            gf_info = next((g for g in gf_templates if g["id"] == golden_finger_id), None)
            if golden_finger_id == "custom":
                attrs["golden_finger_id"] = "custom"
                attrs["golden_finger_name"] = "自定义"
                attrs["golden_finger_tagline"] = "你命由你不由天"
                attrs["golden_finger_desc"] = golden_finger_custom or "未填写"
            elif gf_info:
                attrs["golden_finger_id"] = golden_finger_id
                attrs["golden_finger_name"] = gf_info["name"]
                attrs["golden_finger_tagline"] = gf_info["tagline"]
                attrs["golden_finger_desc"] = golden_finger_custom or gf_info["desc"]
            else:
                attrs["golden_finger_id"] = golden_finger_id
                attrs["golden_finger_name"] = "未知机缘"
                attrs["golden_finger_tagline"] = ""
                attrs["golden_finger_desc"] = golden_finger_custom or ""
        else:
            attrs["golden_finger_id"] = ""
            attrs["golden_finger_name"] = ""
            attrs["golden_finger_tagline"] = ""
            attrs["golden_finger_desc"] = ""

        player.attributes = attrs
        await db.flush()
        logger.info(f"Character applied: {name} ({cultivation}) — {identity} — {personality}")
        if golden_finger_id:
            gf_name = attrs.get("golden_finger_name", "?")
            logger.info(f"  Golden finger: {gf_name}")
        return player

    async def get_by_session(self, db: AsyncSession, session_id: str) -> Player | None:
        result = await db.execute(
            select(Player).where(Player.session_id == session_id)
        )
        return result.scalar_one_or_none()

    async def update_name(
        self, db: AsyncSession, session_id: str, new_name: str
    ) -> Player | None:
        player = await self.get_by_session(db, session_id)
        if player:
            old = player.name
            player.name = new_name
            await db.flush()
            logger.info(f"Player name: {old} → {new_name}")
        return player

    async def update_location(
        self, db: AsyncSession, session_id: str, new_location: str
    ) -> Player | None:
        player = await self.get_by_session(db, session_id)
        if player:
            old = player.location
            player.location = new_location
            await db.flush()
            logger.info(f"Player location: {old} → {new_location}")
        return player

    async def update_cultivation(
        self, db: AsyncSession, session_id: str, new_cultivation: str
    ) -> Player | None:
        player = await self.get_by_session(db, session_id)
        if player:
            player.cultivation = new_cultivation
            await db.flush()
        return player

    async def update_status(
        self, db: AsyncSession, session_id: str, status: dict
    ) -> Player | None:
        player = await self.get_by_session(db, session_id)
        if player:
            player.status = {**(player.status or {}), **status}
            await db.flush()
        return player

    async def add_to_inventory(
        self, db: AsyncSession, session_id: str, item: dict
    ) -> Player | None:
        player = await self.get_by_session(db, session_id)
        if player:
            inv = list(player.inventory or [])
            inv.append(item)
            player.inventory = inv
            await db.flush()
        return player


# Singleton
player_manager = PlayerManager()
