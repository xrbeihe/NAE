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


def _pick_start_location(wv, timeline_id: str = "") -> str:
    """Player starts with no fixed location — the first turn's LLM decides.

    Returning "" means the player has no anchor until narrative sets one
    (via location_change). This avoids mismatches like a 砂忍 character
    being spawned in 木叶 by the template's first-city rule.
    """
    return ""

# Load player creation templates
_templates_path = Path(__file__).resolve().parent.parent / "content" / "player_templates.json"
with open(_templates_path, "r", encoding="utf-8") as f:
    PLAYER_TEMPLATES = json.load(f)


class PlayerManager:
    """Manages player data. Does NOT handle narrative logic."""

    def get_templates(self, worldview: str | None = None) -> dict:
        """Return the player creation template data for the frontend."""
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        if wv.player_templates:
            return wv.player_templates
        return PLAYER_TEMPLATES

    async def create(
        self, db: AsyncSession, session_id: str, worldview: str | None = None,
        timeline: str = "",
    ) -> Player:
        """Create a bare-minimum player stub. Character details come from apply_character()."""
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        defaults = wv.player_defaults or {}

        # Prefer the pack's own geography for the start location, so an IP world
        # (e.g. naruto) starts in its own village rather than a xianxia city.
        # A selected timeline's `start_location` anchors the player to its town.
        location = _pick_start_location(wv, timeline)

        player = Player(
            session_id=session_id,
            name=defaults.get("name", "无名修士"),
            cultivation=defaults.get("cultivation", "凡人"),
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
        worldview: str | None = None,
    ) -> Player | None:
        """Apply player-chosen character details. Writes all choices into player attributes
        and updates core fields so the prompt reflects what the player actually chose."""
        player = await self.get_by_session(db, session_id)
        if not player:
            return None

        templates = self.get_templates(worldview)
        identities = templates.get("identities", {}) if isinstance(templates, dict) else {}

        id_data = identities.get(identity, {})
        if not id_data:
            logger.warning(f"Unknown identity '{identity}' — using raw value")
            id_data = {"label": identity, "desc": "", "clothing": "", "monthly_income": "", "background": "", "spiritual_root": "", "talent_note": ""}

        bg_data = {}
        if isinstance(templates, dict) and templates.get("backgrounds"):
            bg_data = next((b for b in templates["backgrounds"] if b["value"] == background), {})
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
            attrs["identity_desc"] = ""  # 自定义身份：无预设 desc，不留「自定义身份」标签
            attrs["identity_custom"] = identity_custom
            # 注意：不覆写 background_summary —— 出身是独立选择，不能被身份文本覆盖
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
            gf_templates = templates.get("golden_fingers", []) if isinstance(templates, dict) else []
            gf_info = next((g for g in gf_templates if g["id"] == golden_finger_id), None)
            if golden_finger_id == "custom":
                attrs["golden_finger_id"] = "custom"
                # 自定义能力：name 直接用自定义内容，避免"自定义"占位
                attrs["golden_finger_name"] = golden_finger_custom or "自定义"
                attrs["golden_finger_tagline"] = ""
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

    async def apply_character_from_form(
        self,
        db: AsyncSession,
        session_id: str,
        values: dict,
        worldview: str | None = None,
    ) -> Player | None:
        """Generic character creation driven by the worldview's form.json.

        `values` is a flat {field_key: value} map. Custom inputs send their
        text under "{key}_custom". Field specs from form.json decide where each
        value lands (player column vs attributes), what derived fields are
        copied from the selected option, and how card-grid options map.
        """
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        form = wv.form or {}
        fields = form.get("fields", [])
        templates = self.get_templates(worldview)

        player = await self.get_by_session(db, session_id)
        if not player:
            return None

        attrs = dict(player.attributes or {})
        sect_chosen = ""

        for f in fields:
            key = f.get("key", "")
            kind = f.get("kind", "text")
            store = f.get("store", f"attrs.{key}")
            raw = values.get(key)
            is_custom = (raw == "__custom__")

            if kind == "select" and raw:
                # Resolve the selected option dict from the template source
                opt = self._find_option(templates, f.get("options_from"), raw)
                custom_val = values.get(key + "_custom", "")
                # Write the store value — 自定义时用自定义文本替换 __custom__ 标记，
                # 否则存 __custom__ 字面量会泄漏到主角面板
                if is_custom:
                    self._write_field(player, attrs, store, custom_val or raw)
                else:
                    self._write_field(player, attrs, store, raw)
                # Derived fields from the option (e.g. identity → clothing/月入)
                if opt:
                    for derive_key in f.get("derive", []) or []:
                        if derive_key in opt:
                            attrs[derive_key] = opt[derive_key]
                # Custom-field handling: store the custom text, flag it
                if is_custom:
                    attrs[key + "_custom"] = custom_val
                    if key == "identity":
                        attrs["identity_desc"] = ""  # 自定义身份：无预设 desc，不留「自定义身份」标签
                        # 注意：不覆写 background_summary —— 出身是独立选择，不能被身份文本覆盖
                if key == "sect":
                    sect_chosen = raw
            elif kind == "card_grid":
                opt = self._find_option(templates, f.get("options_from"), raw) if raw else None
                omap = f.get("option_map", {}) or {}
                # Map option fields to attributes (e.g. id→golden_finger_id)
                if opt:
                    for spec_key, attr_key in omap.items():
                        if spec_key in opt:
                            attrs[attr_key] = opt[spec_key]
                        else:
                            attrs[attr_key] = ""
                else:
                    for attr_key in omap.values():
                        attrs[attr_key] = ""
                if is_custom:
                    custom_val = values.get(key + "_custom", "")
                    attrs["golden_finger_id"] = "custom"
                    # 自定义能力：name 直接用自定义内容，避免"自定义"占位 + desc 双重展示
                    attrs["golden_finger_name"] = custom_val or "自定义"
                    attrs["golden_finger_tagline"] = ""
                    attrs["golden_finger_desc"] = custom_val
                    attrs["golden_finger_custom"] = custom_val
            elif kind == "number":
                self._write_field(player, attrs, store, raw)
            else:  # text
                self._write_field(player, attrs, store, raw)

        player.attributes = attrs
        await db.flush()
        logger.info(f"Character applied via form.json for session {session_id} ({worldview})")
        # Return the chosen sect so the route can assign a city.
        setattr(player, "_form_sect", sect_chosen)
        return player

    @staticmethod
    def _find_option(templates: dict, options_from: str, value):
        """Locate the selected option dict in a template source.

        identities is a dict-of-dicts keyed by value; list sources use
        either `value` or `id` as the matching key (golden_fingers use id).
        """
        if not value:
            return None
        src = templates.get(options_from) if isinstance(templates, dict) else None
        if not src:
            return None
        if isinstance(src, dict):  # identities: {key: {...}}
            return src.get(value)
        if isinstance(src, list):  # cultivations/backgrounds/golden_fingers
            for o in src:
                if not isinstance(o, dict):
                    continue
                if o.get("value") == value or o.get("id") == value:
                    return o
        return None

    @staticmethod
    def _write_field(player, attrs: dict, store: str, value):
        """Write a value to a player column or an attributes key per store spec."""
        if value is None:
            return
        if store == "player.name":
            player.name = str(value)
        elif store == "player.cultivation":
            player.cultivation = str(value)
        elif store == "player.location":
            player.location = str(value)
        elif store.startswith("attrs."):
            attrs[store[len("attrs."):]] = value
        else:
            attrs[store] = value

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
