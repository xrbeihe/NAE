"""Game Engine — core orchestrator for the turn pipeline.

The single entry point for all game logic. Routes API requests through:
  validate → time → constraints → retrieve → memory → prompt → LLM →
  parse → event → DB → respond.
"""

import json
import logging
import random
from datetime import datetime
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from ane.database.models import WorldSession, Player, NPC, Memory, NPC_Relationship
from ane.modules.model_adapter import model_adapter
from ane.modules.input_validator import validate, ValidationResult
from ane.modules.time_manager import time_manager as tm
from ane.modules.npc_manager import npc_manager
from ane.modules.player_manager import player_manager as pm
from ane.modules.world_manager import world_manager
from ane.modules.memory_manager import memory_manager
from ane.modules.retrieval_engine import retrieval_engine
from ane.modules.npc_modeler import (
    parse_modeling_response,
    render_model_for_prompt as render_npc_model,
)
from ane.modules.prompt_builder import (
    prompt_builder,
    PromptContext,
    PlayerContext,
    NPCContext,
    SceneContext,
    AgenticContext,
    WorldContext,
    npc_to_context,
    player_to_context,
)
from ane.modules.narrative_constraints import constraints
from ane.modules.output_parser import parse, ParsedOutput
from ane.modules.event_bus import bus
from ane.config import (
    CONVERSATION_WINDOW_SIZE,
    DEFAULT_MODEL,
)
from ane.content.json_loader import nsfw_data, underage_data, ntr_data

logger = logging.getLogger(__name__)

# ── llm_modeling template (used by pre-llm_main modeling) ──
_MODEL_TEMPLATE = {
    "basic": {"name": "", "race": "", "gender": "", "age": 0, "height": 0,
              "cultivation": "", "identity": "", "faction": "", "position": ""},
    "appearance": {
        "overall_impression": "", "body_proportion": "", "aura": "",
        "face": {"features": ""},
        "skin": {"color": "", "luster": "", "fineness": ""},
        "hair": {"length": "", "style": "", "color": "", "ornament": ""},
        "torso": "",
        "chest": {"size": "", "shape": "", "fullness": ""},
        "waist": {"muscle_line": "", "slimness": "", "softness": ""},
        "buttocks": {"size": "", "curve": ""},
        "legs": {"length": "", "muscle_tone": "", "thighs": ""},
        "feet": {"shape": "", "size": "", "barefoot": False},
        "hands": {"fingers": "", "back": ""}
    },
    "voice": {"characteristics": ""},
    "attire": {"clothing": "", "accessories": ""},
    "behavior": {"expression": "", "habits": ""},
    "speech_style": {"word_habits": "", "particles": "", "speech_rhythm": "", "catchphrase": "", "battle_cry": "",
                     "address_player": "", "address_others": "", "when_angry": ""},
    "combat_style": {"preference": "", "weapon_usage": "",
                     "spirit_power_signature": ""},
    "personality": {"core": "", "fears": "", "likes": ""},
    "background": {"history": "", "major_events": "", "faction_affiliation": "", "family": ""},
    "cultivation": {"spiritual_root": "", "special_constitution": "", "techniques": "",
                    "divine_powers": "", "ring_storage": "", "wealth": ""},
    "knowledge_bounds": {"knows": [], "does_not_know": [], "suspicious_of": []},
    "attitude_to_player": {"surface": "", "true_feelings": "", "relationship_trend": ""},
    "relationships": {"father": "", "mother": "", "spouse": "", "master": "", "senior_brother": "",
                       "senior_sister": "", "junior_brother": "", "junior_sister": "",
                       "teacher": "", "superior": "", "subordinate": "",
                       "friends": [], "enemies": [],
                       "lover": "", "fiance": "", "beloved": "", "rival": "", "pursuer": ""},
    "nsfw": {"is_virgin": True, "fertility": "",
             "desire_toward_target": "", "rejection_toward_target": "",
             "male_genital": "", "female_genital": ""},
}

# ── Per-worldview NPC model schema ──
# 建模 prompt / _llm_cover / 渲染器 统一读取当前世界观的 modeler/schema.json；
# 包未提供时降级到 xianxia 默认模板（_MODEL_TEMPLATE），引擎行为保持不变。
_DEFAULT_WORLDVIEW_SCHEMA_ID = "xianxia_v1"


def _resolve_model_schema(worldview: str | None = None) -> dict:
    """Resolve the NPC model field-tree schema for a worldview.

    Returns the pack's modeler/schema.json when present, otherwise the
    hardcoded xianxia default template. Never raises — falls back to {}.
    """
    from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
    wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
    schema = wv.modeler_schema
    if isinstance(schema, dict) and schema:
        return schema
    return _MODEL_TEMPLATE


def _resolve_world_facts_for_timeline(world_facts: dict | None, timeline_id: str) -> dict | None:
    """Pick a timeline variant of world_facts.json for the session's timeline.

    world_facts.json may declare `timelines`: [{id, label, description,
    must_follow[], forbidden[], characters[]}]. When the session chose a
    timeline, its variant overrides the base must_follow/forbidden/characters
    (base knowledge_mode / label remain). Without timelines, returns the base
    dict unchanged — behavior is identical to before.
    """
    if not world_facts or not timeline_id:
        return world_facts
    timelines = world_facts.get("timelines") or []
    variant = next((t for t in timelines if isinstance(t, dict) and t.get("id") == timeline_id), None)
    if not variant:
        return world_facts
    merged = dict(world_facts)
    for key in ("must_follow", "forbidden", "characters"):
        if variant.get(key):
            merged[key] = variant[key]
    # Expose the chosen timeline's description so the block can state it.
    if variant.get("description"):
        merged["timeline_label"] = variant.get("label") or timeline_id
        merged["timeline_description"] = variant["description"]
    # Expose the timeline's opening scene (预填充开场文案)
    if variant.get("opening"):
        merged["timeline_opening"] = variant["opening"]
    return merged


@dataclass
class TurnResult:
    """Result of a single player turn."""
    narrative: str = ""
    state_changes: list[dict] = field(default_factory=list)
    world_time: str = ""
    time_delta: int = 0
    npc_updates: list[dict] = field(default_factory=list)
    nearby_characters: list[dict] = field(default_factory=list)
    is_system_command: bool = False
    system_response: str | None = None
    player_panel: str = ""
    important_npcs_panel: str = ""
    modeled_npcs: list[dict] = field(default_factory=list)
    prompt: str = ""
    recommendations: list[str] = field(default_factory=list)
    info_panel: str = ""  # 独立信息区：主角信息 + 附近人物，一整个区别于正文的文本区域
    usage: dict | None = None  # 本轮 llm_main 的 token/耗时（供前端可视化）


# ── GameEngine ─────────────────────────────────────────────────

class GameEngine:
    """Central orchestrator. All game logic entry points live here."""

    def __init__(self):
        self._register_event_handlers()

    def _register_event_handlers(self):
        """Register all state-change event handlers on the bus."""
        for event_type in [
            "location_change", "cultivation_change", "status_change",
            "npc_status", "item_added", "item_removed",
            "relationship_change", "quest_accepted", "quest_completed",
            "player_name_change", "character_status",
        ]:
            bus.subscribe(event_type, self._handle_state_change)
        bus.subscribe("npc_nearby", self._handle_npc_nearby_event)
        bus.subscribe("npc_important", self._handle_npc_important_event)
        logger.info("Event bus handlers registered")

    async def _handle_state_change(self, session_id: str, change: dict):
        """Event handler for state changes — logging only.

        Actual DB writes happen in the main turn pipeline (process_turn).
        The Event Bus fires asynchronously; handlers should not open DB sessions
        to avoid locking conflicts.
        """
        logger.debug(f"State change received: {change.get('type')} -> session={session_id[:12]}")

    async def _handle_npc_nearby_event(self, session_id: str, change: dict):
        pass

    async def _handle_npc_important_event(self, session_id: str, change: dict):
        """When an NPC is marked as important via state_change, log it.
        DB writing is handled in the main process_turn loop.
        """
        npc_name = change.get("value", "") or change.get("field", "")
        if npc_name:
            logger.info(f"Important NPC event received: {npc_name}")

    async def _handle_npc_nearby(self, db, session_id: str, change: dict):
        name = change.get("field", "")
        act = change.get("value", "")
        if not name:
            return
        existing = await db.execute(
            select(NPC).where(NPC.session_id == session_id, NPC.name == name)
        )
        if existing.scalar_one_or_none():
            return
        await npc_manager.create(db, session_id, name=name, npc_type="background", behavior=act)

    # ── Session lifecycle ──────────────────────────────────────

    async def create_session(self, db: AsyncSession, user_id: str, name: str = "未命名世界",
                             worldview: str | None = None, timeline: str | None = None) -> dict:
        """Create a new world session: DB record → world → player stub → NPCs."""
        from ane.modules.time_manager import TimeManager as _tm
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        wv_id = worldview or DEFAULT_WORLDVIEW_ID
        wv = get_worldview(wv_id)  # validate id — falls back to default if invalid/missing
        wv_version = (wv.manifest or {}).get("version", "")
        session = WorldSession(user_id=user_id, name=name, worldview=wv_id, worldview_version=wv_version,
                               timeline_id=(timeline or "").strip())
        session.time_epoch = 0
        session.world_time = _tm().format_world_time(0, worldview=wv_id)
        db.add(session)
        await db.flush()

        # Generate world regions
        regions = await world_manager.generate_initial_world(db, session.id, worldview=wv_id)

        # Create player stub — timeline's start_location anchors the player's town
        player = await pm.create(db, session.id, worldview=wv_id, timeline=(timeline or "").strip())

        logger.info(f"Session created: {session.id} — {name}")
        await db.commit()

        return {
            "session_id": session.id,
            "name": session.name,
            "world_time": session.world_time,
            "player_name": player.name,
            "player_location": player.location,
            "player_cultivation": player.cultivation,
            "region_count": len(regions),
        }

    async def apply_character(
        self, db: AsyncSession, session_id: str,
        name: str, age: int, gender: str, background: str, cultivation: str,
        personality: str, identity: str,
        golden_finger_id: str = "",
        golden_finger_custom: str = "",
        identity_custom: str = "",
        personality_custom: str = "",
        worldview: str | None = None,
    ) -> Player | None:
        """Apply player-chosen character details.
        Delegates to PlayerManager.
        """
        return await pm.apply_character(
            db, session_id, name, age, gender, background, cultivation, personality, identity,
            golden_finger_id=golden_finger_id,
            golden_finger_custom=golden_finger_custom,
            identity_custom=identity_custom,
            personality_custom=personality_custom,
            worldview=worldview,
        )

    # ── Turn pipeline ──────────────────────────────────────────

    async def process_turn(
        self,
        db: AsyncSession,
        session_id: str,
        user_input: str,
        turn_number: int = 1,
        model: str | None = None,
        mark_important_npc: bool = False,
        load_model_data: bool = True,
        user_id: str = "",
        word_count_min: int = 500,
        word_count_max: int = 1200,
        prompt_ids: list[str] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> TurnResult:
        """Run the full turn pipeline.

        Steps:
          1. Validate & classify input
          2. Handle system commands (bypass LLM)
          3. Apply player info extraction (name/cultivation from input)
          4. Advance time based on intent
          5. Build constraints
          6. Retrieve Active Set (core + nearby NPCs)
          7. Load memory (facts, summary, conversation)
          8. Build prompt via PromptBuilder
          9. Call LLM (narrative + optional character_model for new important NPCs)
          10. Parse LLM output, extract character_model if present
          11. Apply state changes via Event Bus
          12. Save conversation to memory
          13. Auto-summarize if suggested
          14. Save character_model to NPC DB (if modeling needed)
          15. Record fact from important NPC markers
          16. Commit & return TurnResult
        """
        # Step 1: Validate
        from ane.database.models import User as user_model
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        user_obj = await db.get(user_model, user_id) if user_id else None
        is_adult = bool(user_obj and user_obj.is_adult) if user_obj else False
        # Resolve the session's worldview (before Step 4 needs it too)
        session_row = await db.get(WorldSession, session_id)
        worldview = getattr(session_row, "worldview", None) or DEFAULT_WORLDVIEW_ID

        # Detect worldview pack upgrades since the session was created.
        pinned_ver = getattr(session_row, "worldview_version", "") or ""
        current_ver = (get_worldview(worldview).manifest or {}).get("version", "")
        if pinned_ver and current_ver and pinned_ver != current_ver:
            logger.info(
                f"Worldview upgrade detected: session {session_id} pinned {worldview}@{pinned_ver}, "
                f"pack now {current_ver} — session keeps pin (no auto-migration)"
            )

        validation = validate(user_input, mark_important_npc, is_adult=is_adult, worldview=worldview)

        # Step 2: System commands
        if validation.is_system_command:
            return await self._handle_system_command(
                db, session_id, validation.system_command, user_input, worldview=worldview,
            )

        if not validation.is_safe:
            return TurnResult(
                narrative="你的输入包含不被允许的内容，请重新表述。",
                world_time="",
                time_delta=0,
            )

        # Step 3: Load player reference for later steps
        player = await pm.get_by_session(db, session_id)

        # Step 3a: Handle mark_important_npc from turn (not stand-alone /npc-modeling)
        if validation.mark_important_npc:
            from ane.modules.npc_manager import npc_manager as _npc_mgr
            names = await self._llm_nameget_multi(
                user_input, user_id=user_id, session_id=session_id, worldview=worldview,
            )
            if names:
                for npc_name in names:
                    existing = await db.execute(
                        select(NPC).where(
                            NPC.session_id == session_id,
                            NPC.name == npc_name,
                        )
                    )
                    db_npc = existing.scalar_one_or_none()
                    if db_npc:
                        db_npc.is_important = True
                    else:
                        player_loc = player.location if player else "未知"
                        await _npc_mgr.create(
                            db, session_id, name=npc_name,
                            location=player_loc, is_important=True,
                        )
                await db.flush()
                logger.info(f"mark_important_npc (turn): {npc_name}")
            else:
                logger.warning("mark_important_npc: _llm_nameget returned empty")

        # Step 4: Advance time
        intent = validation.intent
        # HO marker overrides intent to nsfw
        if validation.nsfw_confirmed:
            intent = "nsfw"
            logger.info("HO confirmed — intent overridden to nsfw")
        time_delta, world_time_label = await tm.advance(db, session_id, intent)
        npc_updates = await tm.update_active_npcs(db, session_id, time_delta)

        # Re-fetch updated session for world_time (session_row was fetched before time advance)
        session = await db.get(WorldSession, session_id)
        world_time_str = session.world_time if session else world_time_label

        # Step 5: Build constraints
        player = await pm.get_by_session(db, session_id)
        player_cultivation = player.cultivation if player else "凡人"
        player_location = player.location if player else "未知"
        player_name = player.name if player else "无名修士"

        # Step 6: Retrieve Active Set
        active_set = await retrieval_engine.get_active_set(
            db, session_id, player_location,
        )

        # Build NPC name list for constraints
        npc_names = [n.name for n in active_set.present_npcs]

        constraint_set = constraints.get_context_constraints(
            player_cultivation=player_cultivation,
            player_location=player_location,
            active_npc_names=npc_names[:12],
            intent=intent,
            worldview=worldview,
        )

        # Step 7: Load memory
        conversation = await memory_manager.get_conversation(db, session_id)

        # Step 8: Build prompt context
        ctx = PromptContext()
        ctx.user_input = validation.cleaned_input
        ctx.word_count_min = word_count_min
        ctx.word_count_max = word_count_max

        # ── User custom prompts (提示词库): load the enabled prompt by prompt_ids.
        # Only this user's prompts are eligible (data isolation). Multi-prompt
        # content is supported: all selected prompts' pre/post are injected.
        if user_id and prompt_ids:
            from ane.database.models import UserPrompt
            wanted = set(prompt_ids)
            if wanted:
                _pr = await db.execute(
                    select(UserPrompt).where(
                        UserPrompt.user_id == user_id,
                        UserPrompt.id.in_(wanted),
                    )
                )
                for _p in _pr.scalars().all():
                    if _p.pre_prompt and _p.pre_prompt.strip():
                        ctx.custom_pre_prompts.append(_p.pre_prompt)
                    if _p.post_prompt and _p.post_prompt.strip():
                        ctx.custom_post_prompts.append(_p.post_prompt)
                if ctx.custom_pre_prompts or ctx.custom_post_prompts:
                    logger.info(
                        f"Custom prompts injected: {len(ctx.custom_pre_prompts)} pre, "
                        f"{len(ctx.custom_post_prompts)} post"
                    )

        # ── Info panel 持续化：把上一轮信息栏整块回喂，让 LLM 基于它持续更新 ──
        prev_panel = await memory_manager.get_latest_info_panel(db, session_id)
        if prev_panel:
            ctx.custom_pre_prompts.append(
                "【上一轮信息栏】按 info_panel 规则重新输出，仅保留主角状态、正在交互人物、玩家要求的栏目：\n" + prev_panel
            )
            logger.info("Info panel persisted: previous turn panel fed back to LLM")

        # Authoritative canon (IP worldviews) — injected from world_facts.json
        _wv_obj = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        ctx.world_facts = _resolve_world_facts_for_timeline(
            _wv_obj.world_facts,
            getattr(session_row, "timeline_id", "") or "",
        )

        # World context — name comes from the pack (never hardcoded, so
        # non-xianxia worldviews don't render the wrong world name). The
        # other dimensions (calendar/era/law/factions/spiritual_rules) are
        # intentionally left empty: they render only when a data source exists.
        ctx.world = WorldContext(name=_wv_obj.name or "青云界")

        # Player context
        if player:
            ctx.player = player_to_context(player)

        # NPC contexts: convert ORM models to NPCContext
        ctx.core_npcs = [npc_to_context(n) for n in active_set.present_npcs]
        # nearby_npcs intentionally left empty — the active set already
        # provides present NPCs via core_npcs; the field stays for future use.

        # 📦 Load model data: if load_model_data is on, scan input for names of
        # already-modeled important NPCs and pull their full data into context.
        if load_model_data:
            named_npcs_in_input = set()
            # Check if any DB modeled NPC name appears in user input
            all_db_npcs = await db.execute(
                select(NPC).where(
                    NPC.session_id == session_id,
                    NPC.is_important == True,
                )
            )
            for db_npc in all_db_npcs.scalars().all():
                if db_npc.name and db_npc.name in user_input:
                    named_npcs_in_input.add(db_npc.name)
                    lts = dict(db_npc.long_term_state or {}) if isinstance(db_npc.long_term_state, dict) else {}
                    # ⬇ Handle pending_debut for load_model_data NPCs not in active_set
                    if lts.pop("pending_debut", False):
                        ctx.is_modeling_turn = True
                        db_npc.long_term_state = lts
                    existing_model = lts.get("model", {})
                    if existing_model and isinstance(existing_model, dict) and existing_model.get("model_version"):
                        # Already has model — ensure it's in core NPCs for full detail
                        if db_npc.name not in {n.name for n in ctx.core_npcs}:
                            ctx.core_npcs.append(npc_to_context(db_npc))
                            logger.info(f"load_model_data: added {db_npc.name} to core NPCs")
            if named_npcs_in_input:
                logger.info(f"load_model_data: names found in input: {named_npcs_in_input}")

        # Debut check: if any important NPC has pending_debut, this is its first appearance
        # Clear the flag after marking so it only fires once
        # Check both active_set NPCs AND any load_model_data-injected NPCs
        for npc in active_set.present_npcs:
            lts = dict(npc.long_term_state or {}) if isinstance(npc.long_term_state, dict) else {}
            if lts.pop("pending_debut", False):
                ctx.is_modeling_turn = True
                npc.long_term_state = lts

        # Scene from location context
        loc_ctx = active_set.location_context
        current = loc_ctx.get("current", {}) or {}
        parents = loc_ctx.get("parents", [])
        hierarchy = " → ".join(p["name"] for p in parents)
        if hierarchy and current.get("name"):
            hierarchy += " → " + current["name"]
        elif current.get("name"):
            hierarchy = current["name"]

        ctx.scene = SceneContext(
            location_hierarchy=hierarchy,
            location_name=current.get("name", player_location),
            location_description=current.get("description", ""),
            time_label=world_time_str,
        )

        # Constraints
        ctx.constraints = constraint_set

        # Agentic state
        actionable = [p.name for p in active_set.present_npcs]
        ctx.agentic = AgenticContext(
            pov_character=player_name,
            actionable_characters=actionable,
        )

        # Set NSFW flag for context-level control of NPC model NSFW block
        ctx.nsfw_active = (intent == "nsfw")

        # Conversation
        ctx.conversation = conversation

        # Latest turn full narrative (raw conversation memory) for the next prompt
        ctx.last_narratives = await memory_manager.get_last_narratives(
            db, session_id, limit=1
        )

        # NOTE: recommendations are NOT injected into the next prompt —
        # they are only shown to the player in the UI (see frontend).

        # Era entries: inject only the most recent 3 records for long-term
        # context — older eras are superseded by newer summaries, so keeping
        # them all would make the prompt grow unboundedly.
        ctx.longmemory_entries = await memory_manager.get_longmemory_entries(db, session_id, limit=3)

        # NSFW material injection
        if intent == "nsfw":
            nsfw_prompt = await self._build_nsfw_material(db, session_id, active_set.present_npcs)
            if nsfw_prompt:
                ctx.nsfw_material = nsfw_prompt

        # NTR material injection
        ntr_prompt = self._build_ntr_material(validation)
        if ntr_prompt:
            existing = ctx.nsfw_material or ""
            ctx.nsfw_material = existing + ("\n\n" if existing else "") + ntr_prompt

        # ── Relationship character extraction ──
        related_chars = await self._extract_related_characters(db, session_id, user_input)
        if related_chars:
            ctx.scene.absent_related = related_chars
            ctx.constraints.hard.append(
                "当前场景中的关键关系人物已标注（⚠ 不在场但相关人物）。"
                "这些角色不在当前场景中但影响剧情——"
                "可能随时回来、被发现的风险等。"
                "叙事中应体现这种张力，但不要让他们突然出现。"
            )

        # ── Inject known player relationships so LLM can output complete player_relationships ──
        from ane.database.models import NPC_Relationship as _NPCRel
        if player_name:
            rel_result = await db.execute(
                select(_NPCRel).where(
                    _NPCRel.session_id == session_id,
                    _NPCRel.source_name == player_name,
                )
            )
            known_rels = rel_result.scalars().all()
            if known_rels:
                rel_lines = ["【已有关系记录 - 供player_relationships参考】"]
                for r in known_rels:
                    rel_lines.append(
                        f"  - {r.target_name}: {r.description} (type={r.rel_type}, affinity={r.affinity})"
                    )
                ctx.constraints.soft.append("\n".join(rel_lines))

            # ── 注入全部已登记 NPC 名字，让 LLM 输出关系时用全名（避免简称导致重复）──
            _all_npcs = await db.execute(
                select(NPC).where(NPC.session_id == session_id)
            )
            _registered = [n.name for n in _all_npcs.scalars().all() if n.name]
            if _registered:
                ctx.constraints.soft.append(
                    "【已登记人物名单】以下人物已登记在册，player_relationships 中提及他们时"
                    "必须使用这里登记的全名（如已有「蒙奇·D·路飞」就写「蒙奇·D·路飞」，不要写简称「路飞」），"
                    "避免把同一人物登记成两个名字：\n"
                    + "、".join(_registered)
                )

        # Assemble system prompt per worldview (defaults to the pack text)
        from ane.modules.prompt_builder import assemble_system as _assemble_system
        ctx.system = _assemble_system(worldview)

        prompt = prompt_builder.build(ctx)
        prompt = prompt_builder.simplify_prompt(prompt)

        # Step 8a: Save player input immediately before LLM call
        # This ensures user input survives a refresh during LLM generation.
        await memory_manager.save_user_input(
            db, session_id, turn_number, validation.cleaned_input,
        )
        await db.flush()

        # Step 9: Call llm_main — narrative
        raw_response = ""
        _mt_kwargs = {}
        if max_tokens:
            _mt_kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            _mt_kwargs["temperature"] = temperature
        try:
            raw_response = await model_adapter.generate(
                prompt, model=model or DEFAULT_MODEL,
                user_id=user_id, session_id=session_id, label="llm_main",
                **_mt_kwargs,
            )
        except Exception as e:
            logger.exception(f"llm_main generation failed: {e}")

        # Ensure parsed is always initialized, even if llm_main fails
        try:
            parsed: ParsedOutput = parse(raw_response, worldview=worldview) if raw_response else ParsedOutput(
                narrative="（AI叙事生成失败，请重试）",
                state_changes=[],
                is_valid_json=False,
                parse_error="llm_main returned empty response",
            )
            # Retry once if parse failed (truncated / malformed JSON)
            if not parsed.narrative or not parsed.is_valid_json:
                logger.warning("llm_main parse failed — retrying with format reminder")
                retry_prompt = prompt + (
                    "\n\n⚠️ 注意：你上次的输出格式有误，请严格按照以下要求重新输出：\n"
                    "1. 只输出纯 JSON，不加任何文字说明\n"
                    "2. 所有 key 用英文双引号包裹\n"
                    "3. JSON 结构必须完整（括号闭合、无截断）\n"
                    "4. 不要添加 ```json 标记"
                )
                try:
                    retry_raw = await model_adapter.generate(
                        retry_prompt, model=model or DEFAULT_MODEL,
                        user_id=user_id, session_id=session_id, label="llm_main",
                        **_mt_kwargs,
                    )
                    if retry_raw:
                        parsed = parse(retry_raw, worldview=worldview)
                except Exception:
                    logger.exception("llm_main retry also failed — using first attempt's result")

            # ── 短输出重试：narrative 低于字数下限 → 重试一次写充分 ──
            # 计算实际中文字数（排除标点/空格/换行）
            import re as _re
            _narr = parsed.narrative or ""
            _han_count = len(_re.findall(r'[一-鿿]', _narr))
            _min = max(100, word_count_min)  # 下限至少 100，避免误重试
            if parsed.is_valid_json and _narr and _han_count < _min and _han_count > 0:
                logger.warning(
                    f"Narrative too short ({_han_count} hanzi < {_min}) — retrying to reach word count"
                )
                retry_prompt2 = (
                    f"你上一轮输出的正文只有约 {_han_count} 个汉字，远低于要求的 {word_count_min}-{word_count_max} 字。\n"
                    f"请重新输出本轮叙事：在已有内容基础上扩展，充分描写环境、动作、心理、对话，"
                    f"达到 {word_count_min}-{word_count_max} 汉字的完整篇幅。\n"
                    f"要求：仍然只输出纯 JSON，结构完整。"
                )
                # 追加到原 prompt 尾部，保留全部上下文
                retry_prompt2_full = prompt + "\n\n" + retry_prompt2
                try:
                    retry_raw2 = await model_adapter.generate(
                        retry_prompt2_full, model=model or DEFAULT_MODEL,
                        user_id=user_id, session_id=session_id, label="llm_main",
                        **_mt_kwargs,
                    )
                    if retry_raw2:
                        parsed2 = parse(retry_raw2, worldview=worldview)
                        if parsed2.is_valid_json and (parsed2.narrative or ""):
                            parsed = parsed2
                except Exception:
                    logger.exception("llm_main short-output retry failed — keeping first attempt")
        except Exception as e:
            logger.exception(f"Output parsing failed: {e}")
            parsed = ParsedOutput(
                narrative="（AI叙事生成失败，请重试）",
                state_changes=[],
                is_valid_json=False,
                parse_error=str(e),
            )

        # Step 10: Note the data needed for bg summary (will fire AFTER commit)
        _bg_sid = session_id
        _bg_tn = turn_number
        _bg_narrative = parsed.narrative
        _bg_changes = parsed.state_changes
        _bg_user_id = user_id
        _bg_input = validation.cleaned_input

        # Step 11: Apply state changes via Event Bus
        await bus.publish_state_changes(session_id, parsed.state_changes)

        # Step 12: Save conversation + recommendations to memory
        recommendations = await memory_manager.add_conversation_turn(
            db, session_id, turn_number, validation.cleaned_input,
            parsed.narrative, parsed.nearby_characters, prompt=prompt,
            user_id=user_id,
        )
        # llm_main may output recommendations in JSON — save to DB
        llm_recs = parsed.recommendations
        if llm_recs and isinstance(llm_recs, list) and len(llm_recs) > 0:
            recommendations = llm_recs
            import json as _json
            # Replace previous recommendations
            await db.execute(
                delete(Memory).where(
                    Memory.session_id == session_id,
                    Memory.memory_type == "recommendations",
                )
            )
            db.add(Memory(
                session_id=session_id,
                memory_type="recommendations",
                content=_json.dumps(llm_recs, ensure_ascii=False),
                turn_number=turn_number,
            ))
            await db.flush()

        # Step 14: (removed — llm_modeling replaces HTEM entirely)

        # Step 15: Apply state_changes — write back to Player/NPC tables
        for change in parsed.state_changes:
            change_type = change.get("type", "")
            change_target = change.get("target", "")
            change_field = change.get("field", "")
            change_value = change.get("value", "")

            if change_type == "npc_important":
                if change_target:
                    npc_obj = await db.get(NPC, change_target)
                    if npc_obj and npc_obj.session_id == session_id:
                        await npc_manager.mark_important(db, change_target, session_id=session_id)
                        logger.info(f"step15: npc_important {change_target}")
                    else:
                        logger.warning(f"step15: npc_important target {change_target} not found or wrong session")

            # ── Player cultivation ──
            elif change_type == "cultivation_change":
                if change_target == "player" and change_value:
                    logger.info(f"step15: {player.name} cultivation {player.cultivation}→{change_value}")
                    player.cultivation = change_value

            # ── Player location ──
            elif change_type == "location_change":
                if change_target == "player" and change_value:
                    logger.info(f"step15: {player.name} location {player.location}→{change_value}")
                    player.location = change_value
                    # 同步 location_hierarchy，避免 prompt 里【用户扮演角色】块
                    # 仍显示旧层级（与 /move 接口行为对齐）
                    p_attrs = dict(player.attributes or {})
                    p_attrs["location_hierarchy"] = change_value
                    player.attributes = p_attrs

            # ── Player inventory ──
            elif change_type == "item_added":
                if change_target == "player" and change_value:
                    inv = list(player.inventory or [])
                    inv.append({
                        "name": change_value,
                        "description": change.get("description", ""),
                    })
                    player.inventory = inv
                    logger.info(f"step15: item_added {change_value}")

            elif change_type == "item_removed":
                if change_target == "player" and change_value:
                    inv = list(player.inventory or [])
                    player.inventory = [i for i in inv if i.get("name") != change_value]
                    logger.info(f"step15: item_removed {change_value}")

            # ── Player name ──
            elif change_type == "player_name_change":
                if change_target == "player" and change_value:
                    logger.info(f"step15: player name {player.name}→{change_value}")
                    player.name = change_value

            # ── Player attributes (personality, clothing, special_constitution, etc.) ──
            elif change_type == "status_change":
                if change_target == "player" and change_field and change_value:
                    attrs = dict(player.attributes or {})
                    if change_field == "_extensions":
                        import ast
                        if isinstance(change_value, str):
                            try:
                                attrs["_extensions"] = json.loads(change_value)
                            except json.JSONDecodeError:
                                try:
                                    attrs["_extensions"] = ast.literal_eval(change_value)
                                except (ValueError, SyntaxError):
                                    attrs["_extensions"] = {}
                        else:
                            attrs["_extensions"] = change_value
                        logger.info(f"step15: _extensions updated: {change_value}")
                    elif change_field.startswith("attributes."):
                        attr_key = change_field[len("attributes."):]
                        attrs[attr_key] = change_value
                        logger.info(f"step15: attributes.{attr_key} → {change_value}")
                    else:
                        attrs[change_field] = change_value
                        logger.info(f"step15: player.{change_field} → {change_value}")
                    player.attributes = attrs

            # ── NPC status changes ──
            elif change_type in ("npc_status", "character_status"):
                if change_target and change_field and change_value:
                    # Look up NPC by name (LLM outputs names, not UUIDs)
                    npc_obj = await db.execute(
                        select(NPC).where(
                            NPC.session_id == session_id,
                            NPC.name == change_target,
                        )
                    )
                    db_npc = npc_obj.scalar_one_or_none()
                    if db_npc:
                        old = getattr(db_npc, change_field, "?")
                        setattr(db_npc, change_field, change_value)
                        logger.info(f"step15: npc {db_npc.name}/{change_field}: {old}→{change_value}")
                    else:
                        logger.warning(f"step15: npc_status target '{change_target}' not found in DB")

            # ── Relationship (update target NPC's relation to player) ──
            elif change_type == "relationship_change":
                if change_target and change_value:
                    existing_r = await db.execute(
                        select(NPC_Relationship).where(
                            NPC_Relationship.session_id == session_id,
                            NPC_Relationship.source_name == change_target,
                            NPC_Relationship.target_name == player_name,
                        )
                    )
                    db_rel = existing_r.scalar_one_or_none()
                    if db_rel:
                        db_rel.rel_type = str(change_value)
                        db_rel.updated_at = datetime.utcnow()
                        logger.info(f"step15: relationship {change_target}→{player_name}: {change_value}")

            # ── Generic catch-all for worldview-specific event types ──
            # Unknown-but-validated event types with target=player and a
            # field write into player.attributes (e.g. modern_city 职业).
            # This lets new worldviews extend state without engine changes.
            elif change_type not in ("npc_nearby",):
                if change_target == "player" and change_field and change_value is not None:
                    attrs = dict(player.attributes or {})
                    if change_field.startswith("attributes."):
                        attr_key = change_field[len("attributes."):]
                        attrs[attr_key] = change_value
                    else:
                        attrs[change_field] = change_value
                    player.attributes = attrs
                    logger.info(f"step15: generic {change_type} → attributes.{change_field} = {change_value}")

        # Apply nearby character seeding
        for change in parsed.state_changes:
            if change.get("type") == "npc_nearby":
                await self._handle_npc_nearby(db, session_id, change)

        # ── Process player_relationships: write/update NPC_Relationship table ──
        player_rels_added = 0
        for rel in (parsed.player_relationships or []):
            rel_name = (rel.get("name") or "").strip()
            rel_desc = (rel.get("description") or "").strip()
            if not rel_name or not rel_desc:
                continue
            # Ensure NPC exists in DB (create stub if not)
            from ane.modules.npc_manager import npc_manager as _npc_mgr
            existing_npc = await db.execute(
                select(NPC).where(
                    NPC.session_id == session_id,
                    NPC.name == rel_name,
                )
            )
            db_npc = existing_npc.scalar_one_or_none()
            # ── 兜底：精确匹配失败时，尝试"包含匹配"归一化简称到已登记全名 ──
            #   如 LLM 写「路飞」但已登记「蒙奇·D·路飞」→ 用全名，避免重复建档
            if not db_npc:
                _all_reg = await db.execute(
                    select(NPC).where(NPC.session_id == session_id)
                )
                for _n in _all_reg.scalars().all():
                    if not _n.name:
                        continue
                    # 归一化判断：去掉分隔符后，若"简称"是"全名"的**后缀**（且简称≥2字）
                    # 则视为同一人（如「路飞」是「蒙奇·D·路飞」的后缀）。
                    # 用后缀而非子串，避免「林星」误并入「林星如」（那是前缀，不是简称）。
                    _a = rel_name.replace("·", "").replace("D·", "").replace(" ", "").strip()
                    _b = _n.name.replace("·", "").replace("D·", "").replace(" ", "").strip()
                    if not _a or not _b:
                        continue
                    if _a == _b:
                        rel_name = _n.name; db_npc = _n; break
                    if len(_a) < len(_b):
                        short, long = _a, _b
                    else:
                        short, long = _b, _a
                    if len(short) >= 2 and long.endswith(short):
                        rel_name = _n.name
                        db_npc = _n
                        logger.info(f"Relationship name normalized: '{rel_name}' matched registered '{_n.name}'")
                        break
            if not db_npc:
                db_npc = await _npc_mgr.create(
                    db, session_id, name=rel_name,
                    location=player_location,
                )
            # Find existing edge (player → NPC)
            existing_r = await db.execute(
                select(NPC_Relationship).where(
                    NPC_Relationship.session_id == session_id,
                    NPC_Relationship.source_name == player_name,
                    NPC_Relationship.target_name == rel_name,
                )
            )
            db_rel = existing_r.scalar_one_or_none()
            if db_rel:
                # UPDATE existing edge with current state (覆盖关系类型/描述/亲密度)
                db_rel.rel_type = rel.get("type", "关系")
                db_rel.description = rel_desc
                db_rel.affinity = rel.get("affinity", 0)
                db_rel.updated_at = datetime.utcnow()
            else:
                # INSERT new edge
                db.add(NPC_Relationship(
                    session_id=session_id,
                    source_name=player_name,
                    target_name=rel_name,
                    rel_type=rel.get("type", "关系"),
                    description=rel_desc,
                    affinity=rel.get("affinity", 0),
                ))
                player_rels_added += 1
        if player_rels_added:
            await db.flush()
            logger.info(f"Player relationships added: {player_rels_added}")

        # Step 16: Build player and important NPC panels for frontend display
        from ane.panels import render_player_panel
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        _wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        _panel_spec = _wv.panel_spec or {}
        if _panel_spec:
            player_panel_str = render_player_panel(player, _panel_spec)
        else:
            # No panel spec (missing pack) — minimal fallback
            player_panel_str = "【主角面板】\n"
            if player:
                p_attrs = dict(player.attributes or {}) if isinstance(player.attributes, dict) else {}
                player_panel_str += " ｜ ".join([
                    f"姓名：{player.name}",
                    f"位置：{player.location or '未知'}",
                ])
            else:
                player_panel_str += "（无玩家数据）\n"

        # Step 16: Build player and important NPC panels for frontend display
        from ane.panels import render_player_panel
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        _wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        _panel_spec = _wv.panel_spec or {}
        if _panel_spec:
            player_panel_str = render_player_panel(player, _panel_spec)
        else:
            # No panel spec (missing pack) — minimal fallback
            player_panel_str = "【主角面板】\n"
            if player:
                p_attrs = dict(player.attributes or {}) if isinstance(player.attributes, dict) else {}
                player_panel_str += " ｜ ".join([
                    f"姓名：{player.name}",
                    f"位置：{player.location or '未知'}",
                ])
            else:
                player_panel_str += "（无玩家数据）\n"

        # ── Build modeled_npcs: NPCs with model data mentioned in this turn's input ──
        all_npcs = await npc_manager.get_by_session(db, session_id)
        modeled_npcs = []
        for n in all_npcs:
            if n.name and n.name in user_input:
                n_attrs = dict(n.long_term_state or {})
                model_data = n_attrs.get("model", {})
                if model_data and isinstance(model_data, dict) and model_data.get("model_version"):
                    gender = n.gender or model_data.get("basic", {}).get("gender", "")
                    modeled_npcs.append({
                        "name": n.name,
                        "gender": gender,
                        "model_data": model_data,
                    })

        # Step 17: Commit and return
        # 持久化本轮 info_panel（供下一轮回喂持续更新）
        await memory_manager.save_info_panel(db, session_id, turn_number, parsed.info_panel)
        await db.commit()

        # Fire background summary AFTER commit — avoids concurrent SQLite write
        # 后台 llm_summary 生成短/长记忆（叙事辅助核心）；推荐栏不受其影响
        # （add_summary_entry 已不再写入 recommendations memory，见 memory_manager）
        if _bg_narrative:
            import asyncio as _asyncio
            _asyncio.ensure_future(self._run_bg_llm_summary(
                _bg_sid, _bg_tn, _bg_narrative, _bg_changes,
                _bg_user_id, _bg_input,
            ))

        return TurnResult(
            narrative=parsed.narrative,
            state_changes=parsed.state_changes,
            world_time=world_time_str,
            time_delta=time_delta,
            npc_updates=npc_updates,
            nearby_characters=parsed.nearby_characters,
            prompt=prompt,
            player_panel=player_panel_str,
            important_npcs_panel="",
            modeled_npcs=modeled_npcs,
            recommendations=recommendations,
            info_panel=parsed.info_panel,
            usage=model_adapter.get_last_usage(label="llm_main"),
        )

    # ── Background llm_summary (fires after commit to avoid SQLite lock) ──

    async def _run_bg_llm_summary(
        self, sid, tn, narrative, state_changes, user_id, user_input,
    ):
        """Run llm_summary in background, its own session, after main commit."""
        from ane.database.engine import async_session_factory as _sf_
        async with _sf_() as _db:
            try:
                sc_lines = []
                for sc in (state_changes or []):
                    if sc.get("type") in ("location_change", "cultivation_change", "item_added", "item_removed"):
                        sc_lines.append(f"  [{sc['type']}] {sc.get('target', '?')}: {sc.get('value', '')}")
                sc_block = "\n".join(sc_lines) if sc_lines else "（无关键状态变更）"

                # 上一轮 shortmemory（供"行动/目标"描述进展，避免每轮从零概括）
                prev_sm = ""
                try:
                    _prev = await _db.execute(
                        select(Memory).where(
                            Memory.session_id == sid,
                            Memory.memory_type == "shortmemory",
                            Memory.turn_number < tn,
                        ).order_by(Memory.turn_number.desc()).limit(1)
                    )
                    _prev_row = _prev.scalar_one_or_none()
                    if _prev_row:
                        prev_sm = _prev_row.content.strip()
                except Exception:
                    pass
                prev_block = (
                    f"上一轮的行动/目标：\n{prev_sm}\n\n"
                    if prev_sm else ""
                )

                prompt = (
                    "你是一个场景摘要和行动顾问。输出给玩家看的场景记忆摘要，同时给下一轮叙事引擎提供上下文。\n\n"
                    "输出格式（纯文本，不要JSON标记）：\n"
                    "当前地点：地名/场所 | 时间\n"
                    "行动/目标：一句话概括玩家位置和当前意图。注意对比上一轮，体现进展（若延续上轮行动，写明'继续/推进'而非重复描述）\n"
                    "推荐行动：\n1.\n2.\n3.\n\n"
                    "注意：各字段之间不要留空行，紧凑排列。\n\n"
                    + prev_block
                    + f"玩家输入：{user_input}\n"
                    f"叙事内容：\n{narrative}\n\n"
                    f"本轮状态变更：\n{sc_block}\n\n"
                    "请按格式输出摘要："
                )
                from ane.modules.model_adapter import model_adapter as _ma
                output = await _ma.generate(prompt, user_id=user_id, session_id=sid, label="llm_summary")
                if output:
                    await memory_manager.add_summary_entry(
                        _db, sid, tn, output.strip(),
                    )
                    await _db.commit()
                    logger.info(f"Background llm_summary saved for session={sid[:12]} turn={tn}")

                # ── Era generation: every 5n+1 turns (6, 11, 16, ...) ──
                # Take the 5 most recent compact entries (tn-5 ~ tn-1)
                if tn >= 6 and (tn - 1) % 5 == 0:
                    try:
                        era_turn_start = tn - 5
                        era_turn_end = tn - 1

                        # Fetch the 5 compact entries for this era (tn-5 ~ tn-1)
                        compact_entries = await _db.execute(
                            select(Memory)
                            .where(
                                Memory.session_id == sid,
                                Memory.memory_type == "shortmemory",
                                Memory.turn_number.between(era_turn_start, era_turn_end),
                            )
                            .order_by(Memory.turn_number.asc())
                        )
                        compact_list = list(compact_entries.scalars().all())
                        if compact_list:
                            # Build raw material from the 5 compact entries
                            turn_summaries = []
                            for ce in compact_list:
                                turn_summaries.append(f"[第{ce.turn_number}轮]\n{ce.content.strip()}")
                            raw_era = "\n\n".join(turn_summaries)

                            # LLM-compress the 5 turns into one coherent era summary
                            era_prompt = (
                                "你负责把连续数轮的游戏记忆压缩成一段精炼的纪元总结，供长期记忆使用。\n"
                                "不要逐轮罗列流水账，要提炼这期间的【实质进展与因果】。\n\n"
                                "以下是这段时期的游戏记忆：\n"
                                f"{raw_era}\n\n"
                                "输出要求（纯文本，简洁，不要JSON）：\n"
                                "进展：这段时期真正发生了什么、主角做了什么关键决定、与核心人物关系的实质变化，"
                                "以及由此对后续的影响——用几句话讲清因果链，不要分条罗列。\n"
                                "只写对这之后仍重要的因果与影响，删掉过程细节和重复内容。控制在200字以内。\n"
                                "有什么写什么，没有的维度可以不写，不必硬凑字数。"
                            )
                            from ane.modules.model_adapter import model_adapter as _ma
                            era_text = (await _ma.generate(era_prompt, user_id=user_id, session_id=sid, label="llm_era")).strip()
                            if not era_text:
                                era_text = raw_era[:800]  # fallback: raw truncated
                            logger.info(f"Era summary generated: turns {tn-5}-{tn-1} ({len(era_text)} chars)")

                            time_range = f"第{era_turn_start}轮—第{era_turn_end}轮"
                            await memory_manager.add_longmemory_entry(
                                _db, sid, era_turn_start, era_turn_end, time_range, era_text,
                            )
                            await _db.commit()
                            logger.info(f"Era saved: turns {era_turn_start}-{era_turn_end}")
                    except Exception:
                        logger.exception(f"Era generation failed for session={sid[:12]} turns={tn-5}-{tn-1}")
            except Exception:
                logger.exception(f"Background llm_summary failed for session={sid[:12]} turn={tn}")

    # ── NPC Modeling (standalone endpoint) ─────────────────────────

    async def do_npc_modeling(
        self, db: AsyncSession, session_id: str, user_input: str,
        user_id: str = "", worldview: str | None = None,
    ) -> dict:
        """Pre-check: extract ALL names, classify known vs new.
        Does NOT call LLM or write DB — all modeling deferred to confirm step.
        Returns:
            updated: list of {npc_name} for known NPCs that COULD be updated
            new_names: list of names not yet modeled (need creation)
        """
        from ane.modules.npc_manager import npc_manager as npc_mgr
        from ane.database.models import NPC

        names = await self._llm_nameget_multi(
            user_input, user_id=user_id, session_id=session_id, worldview=worldview,
        )
        if not names:
            logger.warning(
                f"do_npc_modeling: no names extracted "
                f"(session={session_id[:12]}, worldview={worldview})"
            )
            return {"updated": [], "new_names": []}

        updated = []
        new_names = []

        for npc_name in names:
            existing = await db.execute(
                select(NPC).where(
                    NPC.session_id == session_id,
                    NPC.name == npc_name,
                )
            )
            db_npc = existing.scalar_one_or_none()

            if db_npc and db_npc.is_important:
                # Has model_data → prompt user to confirm update
                lts = dict(db_npc.long_term_state or {}) if isinstance(db_npc.long_term_state, dict) else {}
                existing_model = lts.get("model", {})
                if existing_model and isinstance(existing_model, dict) and existing_model.get("model_version"):
                    updated.append({"npc_name": npc_name})
                else:
                    # Important but no model yet → treat as new candidate
                    new_names.append(npc_name)
            elif db_npc and not db_npc.is_important:
                new_names.append(npc_name)
            else:
                new_names.append(npc_name)

        return {"updated": updated, "new_names": new_names}

    @staticmethod
    def _deep_merge(base: dict, updates: dict) -> None:
        """Recursively merge updates dict into base dict (in-place)."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                GameEngine._deep_merge(base[key], value)
            else:
                base[key] = value

    @staticmethod
    def _model_rels_to_entries(model_rels: dict) -> list[dict]:
        """Convert a model's relationships dict to NPC.relations entries list.

        Worldview-generic: iterates every field in `relationships`. String
        fields become a paired relationship (type = Chinese label from
        npc_modeler's field map, falling back to the raw field name); list
        fields become one relationship per item. No hardcoded xianxia keys.
        """
        from ane.modules.npc_modeler import _FIELD_LABELS as _RELABELS
        entries = []
        for key, val in (model_rels or {}).items():
            if not val:
                continue
            label = _RELABELS.get(key, key)
            if isinstance(val, str):
                if val.strip():
                    entries.append({"target": val.strip(), "type": label, "nature": "", "external_note": ""})
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and item.strip():
                        entries.append({"target": item.strip(), "type": label, "nature": "", "external_note": ""})
        return entries

    # ── Full NPC modeling (called after user confirms) ──────────

    async def _run_npc_modeling(
        self, db, npc_name, user_input, npc_model, session_id, user_id, player_name, player_location, is_new_npc,
        worldview: str | None = None,
    ):
        """Full modeling for a confirmed NPC."""
        from ane.modules.model_adapter import model_adapter
        from ane.modules.npc_modeler import parse_modeling_response as _pmr
        from ane.database.models import Memory
        from ane.modules.input_validator import nsfw_body_words
        from ane.config import SYSTEM_PROMPT_SUFFIX
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID

        wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
        world_name = wv.name or "修仙世界"
        model_schema = _resolve_model_schema(worldview)

        safe_input = user_input
        for w in nsfw_body_words(worldview):
            if w in safe_input:
                safe_input = safe_input.replace(w, "【已修饰】")
        safe_input = safe_input.strip() or "一个NPC"

        # Worldview role line + world blurb from the pack's modeler/role.txt.
        # Fallback: the original xianxia modeling prompt header.
        role_template = wv.modeler_role
        if role_template and "{npc_name}" in role_template:
            world_blurb = (wv.constraints or {}).get("modeler_blurb", "")
            if not world_blurb:
                # derive a one-line blurb from the system prompt's world section
                sysp = wv.system_prompt or ""
                for line in sysp.splitlines():
                    if line.strip().startswith("世界观"):
                        world_blurb = line.strip()[3:].strip()
                        break
            if not world_blurb:
                world_blurb = "东方玄幻修仙世界，有宗门、境界、灵根、法宝、丹药、灵石等设定。"
            model_prompt = (
                f"{SYSTEM_PROMPT_SUFFIX}\n\n"
                + role_template.format(worldview_name=world_name, npc_name=npc_name, worldview_blurb=world_blurb)
                + f"\n\n人物性别：{npc_model.gender or '待确定'}\n\n"
                f"玩家输入：{safe_input}（玩家原文中部分敏感描述已替换为【已修饰】，"
                f"你须根据上下文推演补全这些描述的合理设定，不要忽略）\n\n"
                f"请输出一个完整的 JSON 人物档案（结构如下）。\n"
                f"【核心要求】\n"
                f"1. 玩家输入中提到什么就填什么，不要忽略玩家给的任何信息\n"
                f"2. 没有明确依据的字段，根据已有信息（身份、修为、背景）合理推演补全\n"
                f"3. 补全的内容不能与玩家明确说的事实矛盾\n"
                f"4. 外貌身体穿着等描写要具体有画面感\n"
                f"5. 大胆补全，不要留空——玩家不满意后续可以发指令修改\n"
                f"5b. attire（衣着）与 jewelry（首饰）必须填写，与外貌同等重要——"
                f"即使玩家没有明说穿着，也要根据身份/修为/气质推演出一套符合角色的衣着和配饰。"
                f"严禁将 attire 留空或输出空对象。\n"
                f"5c. 严格按下方 JSON schema 的结构输出，不要把 appearance 的子字段"
                f"（face/skin/hair/chest 等）平铺到顶层——它们必须嵌套在 appearance 内。\n"
                f"{wv.modeler_age_rules or ''}"
                f"7. 身世限制：如果玩家没有主动描述该人物的身世背景（包括过去的经历、"
                f"家族、历史等），则background.history保持为空字符串，不要自行编造。\n"
                f"   只有在玩家输入中明确提到身世相关内容时再填写。\n"
                f"8. 关系限制：如果玩家没有主动描述该人物与谁有仇、与谁亲近、"
                f"是某人的徒弟/配偶/师兄等关系，则relationships中对应的字段保持为空或空数组。\n"
                f"   即使玩家提到了{player_name}以外的其他NPC名字，也不代表他们之间有具体关系——"
                f"只有玩家明确说了「XXX是YYY的XX」时才填写。\n"
                f"9. 关系理解：玩家说的「我」指{player_name}本人。"
                f"如果玩家说「XXX是我妈/我爸/我师尊/我师兄/我道侣/我的XX」，"
                f"则表示该NPC与{player_name}有该称谓所对应的关系，"
                f"而不是与NPC彼此之间有该关系。"
                f"请根据玩家输入正确理解「我」指代{player_name}，正确填写NPC与{player_name}的关系，"
                f"以及NPC之间的关系（如「张海是张大强的妻子」）。\n"
                f"注意区分：spouse（配偶/丈夫/妻子）指已婚夫妻关系，"
                f"lover（恋人）指恋爱中未结婚的对象。\n\n"
                f'{json.dumps(model_schema, ensure_ascii=False, indent=2)}\n\n'
                f"请输出 JSON："
            )
        else:
            # Legacy xianxia modeling prompt (unchanged) — used when the pack
            # provides no role.txt.
            model_prompt = (
                f"{SYSTEM_PROMPT_SUFFIX}\n\n"
                f"你是一个修仙世界角色建模师。玩家在修仙世界中标记了一个重要人物「{npc_name}」。\n\n"
                f"世界观：东方玄幻修仙世界，有宗门、境界（炼气期→筑基期→金丹期→元婴期→化神期→炼虚期→合体期→大乘期→渡劫期）、灵根（金木水火土/变异/天灵根等）、法宝、丹药、灵石等设定。\n"
                f"注意：世界观是背景参考而非限制。玩家的意愿凌驾于世界观之上。\n"
                f"如果玩家要求的设定看起来不符合修仙世界，直接照做即可——玩家说怎么穿就怎么穿，说长什么样就长什么样。\n"
                f"玩家没有明确说的一律按修仙世界惯例推演补全。\n\n"
                f"人物性别：{npc_model.gender or '待确定'}\n\n"
                f"玩家输入：{safe_input}（玩家原文中部分敏感描述已替换为【已修饰】，"
                f"你须根据上下文推演补全这些描述的合理设定，不要忽略）\n\n"
                f"请输出一个完整的 JSON 人物档案（结构如下）。\n"
                f"【核心要求】\n"
                f"1. 玩家输入中提到什么就填什么，不要忽略玩家给的任何信息\n"
                f"2. 没有明确依据的字段，根据已有信息（身份、修为、背景）合理推演补全\n"
                f"3. 补全的内容不能与玩家明确说的事实矛盾\n"
                f"4. 外貌身体穿着等描写要具体有画面感\n"
                f"5. 大胆补全，不要留空——玩家不满意后续可以发指令修改\n"
                f"5b. attire（衣着）与 jewelry（首饰）必须填写，与外貌同等重要——"
                f"即使玩家没有明说穿着，也要根据身份/修为/气质推演出一套符合角色的衣着和配饰。"
                f"严禁将 attire 留空或输出空对象。\n"
                f"5c. 严格按下方 JSON schema 的结构输出，不要把 appearance 的子字段"
                f"（face/skin/hair/chest 等）平铺到顶层——它们必须嵌套在 appearance 内。\n"
                f"6. 年龄限制：对年轻俊美的角色（无论男女），除非玩家明确给出年龄，"
                f"一律设定在40岁以下。外貌应符合实际年龄印象——年轻人应有年轻人的样貌，\n"
                f"   可以有超乎常人的美貌，但不应毫无根据地呈现老年人特征（沧桑、皱纹等）。\n"
                f"   如果玩家没有提及年龄，按以下规则推断：筑基以下≤25岁，\n"
                f"   金丹约30-80岁（外貌二十多岁），元婴约50-200岁（外貌三十多岁），\n"
                f"   化神以上可保持青春但容貌仍符合年龄气质。"
                f"   总之，年轻俊美的帅哥美女统一在40岁以下外貌。\n"
                f"7. 身世限制：如果玩家没有主动描述该人物的身世背景（包括过去的经历、"
                f"家族、历史等），则background.history保持为空字符串，不要自行编造。\n"
                f"   只有在玩家输入中明确提到身世相关内容时再填写。\n"
                f"8. 关系限制：如果玩家没有主动描述该人物与谁有仇、与谁亲近、"
                f"是某人的徒弟/配偶/师兄等关系，则relationships中对应的字段保持为空或空数组。\n"
                f"   即使玩家提到了{player_name}以外的其他NPC名字，也不代表他们之间有具体关系——"
                f"只有玩家明确说了「XXX是YYY的XX」时才填写。\n"
                f"9. 关系理解：玩家说的「我」指{player_name}本人。"
                f"如果玩家说「XXX是我妈/我爸/我师尊/我师兄/我道侣/我的XX」，"
                f"则表示该NPC与{player_name}有该称谓所对应的关系，"
                f"而不是与NPC彼此之间有该关系。"
                f"请根据玩家输入正确理解「我」指代{player_name}，正确填写NPC与{player_name}的关系，"
                f"以及NPC之间的关系（如「张海是张大强的妻子」）。\n"
                f"注意区分：spouse（配偶/丈夫/妻子）指已婚夫妻关系，"
                f"lover（恋人）指恋爱中未结婚的对象。\n\n"
                f'{json.dumps(model_schema, ensure_ascii=False, indent=2)}\n\n'
                f"请输出 JSON："
            )

        # 建模需要输出 90+ 字段完整 JSON，单独提高 max_tokens 防止
        # 输出撞满默认 8192 被截断（曾致 model 只有 basic+appearance）。
        raw = await model_adapter.generate(
            model_prompt, user_id=user_id, session_id=session_id, label="llm_modeling",
            max_tokens=16000,
        )
        if session_id:
            try:
                db.add(Memory(
                    session_id=session_id, memory_type="llm_log",
                    content=f"【llm_modeling】\n输入（摘要）：{model_prompt[:500]}...\n\n输出：{raw[:2000]}\n",
                    turn_number=0,
                ))
                await db.flush()
            except Exception:
                pass

        model_data = _pmr(raw)
        if model_data:
            lts = dict(npc_model.long_term_state or {})
            lts["model"] = model_data
            lts["pending_debut"] = True
            npc_model.long_term_state = lts
            basic = model_data.get("basic", {})
            # Schema-generic: xianxia basic.identity/cultivation; other schemas
            # (fantasy → title/rank, modern → occupation/level) map onto the
            # NPC table columns so the panel/list stays populated.
            if basic.get("identity") or basic.get("title") or basic.get("occupation"):
                npc_model.identity = basic.get("identity") or basic.get("title") or basic.get("occupation")
            if basic.get("cultivation") or basic.get("level") or basic.get("rank"):
                npc_model.cultivation = basic.get("cultivation") or basic.get("level") or basic.get("rank")
            if basic.get("gender"): npc_model.gender = basic["gender"]
            if basic.get("age"): npc_model.age = int(basic["age"])
            pers_core = model_data.get("personality", {}).get("core", "")
            if pers_core: npc_model.personality = pers_core
            # Sync relationships from model to NPC.relations
            model_rels = model_data.get("relationships", {})
            if model_rels and isinstance(model_rels, dict):
                npc_model.relations = {
                    "entries": GameEngine._model_rels_to_entries(model_rels),
                }
            await db.commit()
            logger.info(f"llm_modeling saved for {npc_name}")
        else:
            await db.commit()
            logger.warning(f"llm_modeling returned no valid model for {npc_name}")

        return model_data or {}

    # ── NSFW Material ──────────────────────────────────────────

    async def _build_nsfw_material(
        self, db: AsyncSession, session_id: str, core_npcs: list[NPC],
    ) -> str:
        """Build NSFW reference material for the prompt.

        Detects underage participants and loads from underage_templates.json
        instead of the standard nsfw_templates.json when minors are involved.
        """
        templates = nsfw_data()
        _rnd = random.Random()

        # Detect underage participants
        underage_npcs = []
        for n in core_npcs:
            try:
                n_age = int(n.age)
            except (TypeError, ValueError):
                n_age = 0
            if n_age and n_age < 18:
                underage_npcs.append(n)

        # Also check player age
        player_underage = False
        from ane.modules.player_manager import player_manager as pm
        player = await pm.get_by_session(db, session_id)
        player_age = (player.attributes or {}).get("age", 0) if player else 0
        # age 可能存成字符串（表单 number 字段），比较前转 int
        try:
            player_age = int(player_age)
        except (TypeError, ValueError):
            player_age = 0
        if player_age and player_age < 18:
            player_underage = True

        # If minors are involved, swap to underage template library
        if underage_npcs or player_underage:
            templates = underage_data()
            _rnd = random.Random()
            names = [n.name for n in underage_npcs]
            if player_underage:
                names.append("玩家")
            names_str = "、".join(set(names))
            underage_flag = True
        else:
            underage_flag = False

        positions = templates.get("positions", [])
        chosen = _rnd.choice(positions) if positions else {"name": "传教士体位", "description": ""}

        foreplay = templates.get("foreplay_actions", [])
        chosen_fp = _rnd.choice(foreplay) if foreplay else {"details": ""}

        entry_lines = templates.get("sensory_bundles", {}).get("entry", [])
        entry_line = _rnd.choice(entry_lines) if entry_lines else ""
        wet_lines = templates.get("sensory_bundles", {}).get("wet", [])
        wet_line = _rnd.choice(wet_lines) if wet_lines else ""
        climax_lines_f = templates.get("sensory_bundles", {}).get("climax_female", [])
        climax_line_f = _rnd.choice(climax_lines_f) if climax_lines_f else ""
        climax_lines_m = templates.get("sensory_bundles", {}).get("climax_male", [])
        climax_line_m = _rnd.choice(climax_lines_m) if climax_lines_m else ""
        aftermath_lines = templates.get("sensory_bundles", {}).get("aftermath", [])
        aftermath_line = _rnd.choice(aftermath_lines) if aftermath_lines else ""

        dialogue = templates.get("dialogue", {})
        dom_dlg = _rnd.choice(dialogue.get("dominant_male", [""])) if dialogue.get("dominant_male") else ""
        sub_dlg = _rnd.choice(dialogue.get("submissive_female", [""])) if dialogue.get("submissive_female") else ""

        def _safe_choice(lst):
            return _rnd.choice(lst) if lst else "无"

        nsfw_block = f"""【性爱参考素材】
推荐体位：{chosen.get('name', '传教士体位')}
{chosen.get('description', '')}

进入描写参考：{entry_line}
湿润描写参考：{wet_line}
高潮描写参考（女）：{climax_line_f}
高潮描写参考（男）：{climax_line_m}
事后描写参考：{aftermath_line}

前戏参考：{chosen_fp.get('details', '')}

对话参考：
主导方：{dom_dlg}
顺从方：{sub_dlg}

女性状态参考：
精神状态：{_safe_choice(templates.get('female_states', {}).get('mental', []))}
表情神态：{_safe_choice(templates.get('female_states', {}).get('expression', []))}
语言语气：{_safe_choice(templates.get('female_states', {}).get('speech_style', []))}
身体反应：{_safe_choice(templates.get('female_states', {}).get('body_reaction', []))}

外貌描写参考：
衣物状态：{_safe_choice(templates.get('appearance_highlights', {}).get('clothing_state', []))}
情动迹象：{_safe_choice(templates.get('appearance_highlights', {}).get('arousal_signs', []))}
凌乱美感：{_safe_choice(templates.get('appearance_highlights', {}).get('disheveled', []))}
裸露细节：{_safe_choice(templates.get('appearance_highlights', {}).get('nude_details', []))}
事后模样：{_safe_choice(templates.get('appearance_highlights', {}).get('afterglow', []))}

节奏建议：根据场景性质选择——Type 1（刺激插曲）在一轮内闭环，给出完整的挑逗→前戏→进入→多次高潮/射精→事后的刺激过程；Type 2（情节性性爱）可跨轮次推进，每轮有阶段性闭环和推进结果。无论哪种模式，每轮都必须有推进感。"""

        if underage_npcs or player_underage:
            nsfw_block += f"\n\n⚠ 涉及未成年角色（{names_str}）。请用未成年角色的视角和生理特征来描写，保持生动性与代入感，避免用成年人的身体特征来套用。"
        return nsfw_block

    # ── NTR Material ──────────────────────────────

    def _build_ntr_material(self, validation: "ValidationResult") -> str:
        """Build NTR reference material for the prompt.
        Injected when intent is "ntr" or is_ntr flag is set.
        """
        if validation.intent != "ntr" and not validation.is_ntr:
            return ""

        ut = ntr_data()
        _rnd = __import__("random").Random()

        dynamics = ut.get("relationship_dynamics", [])
        chosen_dyn = _rnd.choice(dynamics) if dynamics else {"name": "", "subtypes": []}

        psy_arcs = ut.get("psychological_arcs", [])
        chosen_arc = _rnd.choice(psy_arcs) if psy_arcs else {"role": "", "change_chain": "", "typical_inner_monologue": []}

        scenes = ut.get("ntr_scene_templates", [])
        chosen_scene = _rnd.choice(scenes) if scenes else {"name": "", "description": "", "tension_elements": []}

        contrasts = ut.get("humiliation_contrasts", {})
        all_c = []
        for k in ["size_comparison", "skill_comparison", "ownership_declaration", "boundary_violation"]:
            all_c.extend(contrasts.get(k, []))
        chosen_contrast = _rnd.choice(all_c) if all_c else ""

        dialogue = ut.get("dialogue_examples", {})

        def _safe_choice(lst):
            return _rnd.choice(lst) if lst else "（暂无）"

        tension_items = chosen_scene.get("tension_elements", [])
        tension_block = ""
        for e in tension_items:
            tension_block += "- " + e + "\n"
        if not tension_block:
            tension_block = "（暂无）"

        parts = [
            "【NTR场景参考】",
            "",
            "关系类型：" + chosen_dyn.get("name", ""),
            chosen_dyn.get("description", ""),
            "子类型：" + "、".join(chosen_dyn.get("subtypes", [])),
            "",
            "心理变化链（" + chosen_arc.get("role", "") + "视角）：",
            chosen_arc.get("change_chain", ""),
            "",
            "内心独白示例：",
            _safe_choice(chosen_arc.get("typical_inner_monologue", [])),
            "",
            "场景模板：" + chosen_scene.get("name", ""),
            chosen_scene.get("description", ""),
            "张力要素：",
            tension_block.strip(),
            "",
            "对比/羞辱对话：",
            chosen_contrast,
            "",
            "对话示例：",
            "抵抗阶段：" + _safe_choice(dialogue.get("resistance_phase", [])),
            "动摇阶段：" + _safe_choice(dialogue.get("ambivalence_phase", [])),
            "沉沦阶段：" + _safe_choice(dialogue.get("surrender_phase", [])),
            "原配方：" + _safe_choice(dialogue.get("betrayed_lines", [])),
        ]
        return "\n".join(parts)

    # ── Multi-name extraction via LLM ───────────────────

    async def _llm_nameget_multi(
        self, user_input, user_id="", session_id="", worldview: str | None = None,
    ) -> list[str]:
        """Extract ALL character names from user input.

        Names are always returned in Chinese: when a worldview is supplied
        (e.g. an IP pack like naruto_shippuden), the prompt requires foreign
        names to be transliterated into their common Chinese translation
        (Naruto → 鸣人, うずまきナルト → 漩涡鸣人). The validator accepts
        pure-CJK 1-4 char names — single-char IP names like 白 (Haku in
        Naruto) are valid — while still rejecting English/kana output.
        Returns a list of proper name strings, or empty list.
        """
        from ane.modules.model_adapter import model_adapter

        # Words that are never names — guards the 1-char relaxation from
        # admitting generic tokens the model might emit.
        _NAME_NOISE = {
            "男", "女", "无", "未知", "谁", "他", "她", "我", "你", "它",
            "姓名", "名字", "角色", "主角", "路人", "少年", "少女", "小孩",
            "忍者", "武士", "村民", "npc", "NPC", "name", "Name",
        }

        # ── Language rule: names must come back in Chinese ──
        # IP/western worldviews contain foreign (kana/romaji/English) names;
        # the model must translate them to the standard Chinese reading.
        lang_rule = ""
        if worldview:
            lang_rule = (
                f"3. 当前世界观为「{worldview}」。如果角色名是外文（日文/英文等），"
                "必须输出其常用中文译名（如 Naruto → 鸣人，うずまきナルト → 漩涡鸣人），"
                "禁止输出外文原名或罗马音\n"
            )

        prompt = (
            "从以下文本中列出所有被提到的人物姓名（中文名）。\n"
            "规则：\n"
            "1. 如果文本中明确说出了姓名（如'我叫陆青棠'、'她是白慕彩'）→ 输出那个姓名\n"
            "2. 如果文本中没有姓名只有描述（如'佩剑少女'、'卖糖葫芦的'），"
            "则根据其身份和场景赋予一个符合世界观的中文姓名（姓+名，2-3字）\n"
            + lang_rule +
            "4. 原作角色的单字名要保留原样（如『白』『蝎』），不要添加姓氏或前缀\n"
            "5. 每行只输出一个中文姓名，不要输出任何其他文字\n"
            "6. 如果没有任何人物姓名也输出空\n\n"
            f"文本：\n{user_input}"
        )

        def _is_valid_chinese_name(name: str) -> bool:
            if not name:
                return False
            if name in _NAME_NOISE:
                return False
            cjk_count = sum(1 for c in name if '一' <= c <= '鿿')
            # 1-4 chars: allows single-char IP names (e.g. 白 in Naruto) up
            # to 4-char translations (漩涡鸣人); still rejects foreign/romaji.
            return cjk_count == len(name) and 1 <= len(name) <= 4

        def _parse_names(text: str) -> list[str]:
            if not text or not text.strip():
                return []
            names = set()
            rejected = []
            for line in text.strip().split('\n'):
                raw = line.strip().rstrip("。，, .!\n、；：:；，、 ")
                if not raw:
                    continue
                # Split by common delimiters in case LLM put multiple per line
                for token in raw.replace('、', '\n').replace('，', '\n').replace(',', '\n').split('\n'):
                    token = token.strip()
                    if _is_valid_chinese_name(token):
                        names.add(token)
                    else:
                        rejected.append(token)
            if rejected:
                # Diagnosability: which candidates were dropped and why
                logger.debug(f"_llm_nameget rejected[{len(rejected)}]: {rejected[:10]}")
            return list(names)

        for attempt in range(2):
            try:
                result = await model_adapter.generate(
                    prompt,
                    user_id=user_id, session_id=session_id, label="_llm_nameget",
                )
                # Diagnosability: always record what the model actually returned
                # (truncated) so a later 400 can be traced to the raw output.
                logger.info(
                    f"_llm_nameget raw[{attempt + 1}]: {result[:200]!r} "
                    f"(worldview={worldview})" if result else
                    f"_llm_nameget raw[{attempt + 1}]: (empty) (worldview={worldview})"
                )
                names = _parse_names(result) if result else []
                if names:
                    logger.info(f"_llm_nameget_multi: {names}")
                    return names
                if attempt == 0:
                    # Retry with stronger instruction
                    retry = (
                        "你是一个姓名提取助手。\n"
                        "从文本中提取所有人物姓名（中文名，1-4个字；单字名如『白』『蝎』要保留原样）。\n"
                    )
                    if worldview:
                        retry += (
                            f"当前世界观为「{worldview}」，外文角色名必须输出常用中文译名"
                            "（如 Naruto → 鸣人，うずまきナルト → 漩涡鸣人），禁止输出英文/日文。\n"
                        )
                    retry += (
                        "每行只输出一个中文姓名，不要其他文字。\n"
                        f"文本：\n{user_input}"
                    )
                    prompt = retry
            except Exception as e:
                logger.warning(f"_llm_nameget_multi attempt {attempt + 1} failed: {e}")

        logger.warning(
            f"_llm_nameget_multi FAILED: empty after 2 attempts; "
            f"input={user_input[:80]!r} worldview={worldview}"
        )
        return []

    async def _llm_cover(
        self, npc_name, user_input, existing_model, user_id="", session_id="", worldview: str | None = None,
    ) -> dict | None:
        """Partial update to an existing NPC model.
        Only fills fields that the user's new input touches, doesn't touch others.
        Returns a dict of only the changed sub-tree, or None on failure.
        """
        from ane.modules.model_adapter import model_adapter
        from ane.config import SYSTEM_PROMPT_SUFFIX

        # Build a minimal template showing only the structure (per-worldview schema)
        template_copy = json.loads(json.dumps(_resolve_model_schema(worldview)))

        # Serialize existing model (abbreviated for context window)
        existing_summary = json.dumps(existing_model, ensure_ascii=False, indent=2)
        if len(existing_summary) > 3000:
            existing_summary = existing_summary[:3000] + "\n  ... (截断)"

        prompt = (
            f"{SYSTEM_PROMPT_SUFFIX}\n\n"
            f"你是一个角色档案更新助手。人物「{npc_name}」已有以下完整档案。\n"
            f"现在玩家提供了新的信息，请对照已有档案，更新玩家本次涉及的任何字段。\n\n"
            f"【已有档案】\n{existing_summary}\n\n"
            f"【玩家新输入】\n{user_input}\n\n"
            f"【执行规则】\n"
            f"1. 玩家输入中提到的所有新信息/修改/补充，都必须在输出中体现。\n"
            f"2. 如果玩家输入涉及人物关系（夫妻/兄妹/师徒/仇敌等）或对玩家的态度变化，"
            f"务必更新 relationships 和 attitude_to_player。\n"
            f"3. 严格按照以下 JSON 结构输出，只包含有内容的字段。\n"
            f"4. 已有档案中未受本次输入影响的部分不要重复输出。\n"
            f"5. 即使只有关系变化也输出完整的子结构，不要省略。\n\n"
            f"参考输出结构：\n"
            f'{json.dumps(template_copy, ensure_ascii=False, indent=2)}\n\n'
            f"输出 JSON（只包含本次需更新的字段和值，不要输出空字段）："
        )

        for attempt in range(2):
            try:
                raw = await model_adapter.generate(
                    prompt, user_id=user_id, session_id=session_id, label="llm_cover",
                )
                # Parse JSON
                import re
                brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
                if not brace_match:
                    logger.warning(f"_llm_cover: no JSON in response for {npc_name}")
                    continue
                updates = json.loads(brace_match.group())
                if updates and isinstance(updates, dict) and len(updates) > 0:
                    logger.info(f"_llm_cover: {npc_name} got {len(updates)} top-level fields updated")
                    return updates
            except Exception as e:
                logger.warning(f"_llm_cover attempt {attempt + 1} failed for {npc_name}: {e}")

        return None

    # ── Relationship character extraction ─────────────────────

    async def _extract_related_characters(
        self, db, session_id, user_input,
    ) -> list[str]:
        import re
        items = []
        m = re.search(
            r'(\S{1,4})的(丈夫|老婆|妻子|老公|女友|男友|未婚夫|未婚妻|夫君|娘子|道侣)是(\S{2,4})',
            user_input,
        )
        if m:
            name_or_role = m.group(3)
            target = m.group(1)
            relation = m.group(2)
            items.append(f"{name_or_role}（{target}的{relation}，不在现场）")
            return items
        m = re.search(r'(\S{2,4})的(丈夫|老婆|妻子|老公|女友|男友|未婚夫|未婚妻|夫君|娘子|道侣)', user_input)
        if m:
            target = m.group(1)
            relation = m.group(2)
            items.append(f"{target}的{relation}（不在现场）")
            return items
        return items

    # ── NPC name extraction helper ──────────────────────────────

    async def _random_npc_name(self, db, session_id) -> str:
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        result = await db.execute(
            select(NPC.name).where(NPC.session_id == session_id)
        )
        existing_names = {row[0] for row in result.fetchall()}
        sess = await db.get(WorldSession, session_id)
        worldview = getattr(sess, "worldview", None) or DEFAULT_WORLDVIEW_ID
        return npc_manager._random_name(existing_names, worldview=worldview)

    # ── System commands ─────────────────────────────────────────

    async def _handle_system_command(
        self, db, session_id, cmd, user_input, worldview: str | None = None,
    ) -> TurnResult:
        """Handle system commands."""
        from ane.modules.player_manager import player_manager as pm
        if cmd == "system_help":
            return self._cmd_help()
        if cmd == "system_status":
            from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
            player = await pm.get_by_session(db, session_id)
            name = player.name if player else "未知"
            loc = player.location if player else "未知"
            cult = player.cultivation if player else "未知"
            wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
            status_label = (wv.player_defaults or {}).get("status_label", "修士")
            return TurnResult(
                is_system_command=True,
                system_response=f"{status_label}：{name} | 位置：{loc} | 修为：{cult}",
            )
        return TurnResult(
            is_system_command=True,
            system_response="未知命令。可用命令：/help、/status",
        )


    def _cmd_help(self) -> TurnResult:
        return TurnResult(
            is_system_command=True,
            system_response=(
                "【帮助】\n"
                "/help - 显示帮助\n"
                "描述这个世界 - 查看世界概况"
            ),
        )

# Singleton
game_engine = GameEngine()
