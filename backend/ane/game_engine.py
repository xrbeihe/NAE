"""Game Engine — core orchestrator for the turn pipeline.

The single entry point for all game logic. Routes API requests through:
  validate → time → constraints → retrieve → memory → prompt → LLM →
  parse → event → DB → respond.
"""

import json
import logging
import random
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from ane.database.models import WorldSession, Player, NPC, Memory, Fact
from ane.modules.model_adapter import model_adapter
from ane.modules.input_validator import validate, extract_player_info, ValidationResult
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
        "face": {"shape": "", "features": "", "eyes": "", "lashes": "", "eyebrows": "",
                 "nose": "", "lips": "", "teeth": "", "dimples": "",
                 "tear_mole": "", "expression_habit": ""},
        "skin": {"color": "", "luster": "", "fineness": ""},
        "hair": {"length": "", "style": "", "color": "", "ornament": ""},
        "neck": "", "collarbone": "", "shoulders": "",
        "chest": {"size": "", "shape": "", "fullness": ""},
        "waist": {"muscle_line": "", "slimness": "", "softness": ""},
        "belly": "",
        "buttocks": {"size": "", "curve": ""}, "hips": "",
        "legs": {"length": "", "muscle_tone": "", "thighs": ""},
        "feet": {"shape": "", "size": "", "barefoot": False},
        "hands": {"fingers": "", "back": ""}
    },
    "voice": {"timbre": "", "speed": "", "volume": ""},
    "clothing": {"type": "", "color": "", "material": "", "pattern": "", "collar": "",
                 "outerwear": "", "belt": "", "hosiery": "", "shoes": ""},
    "jewelry": {"earrings": "", "necklace": "", "rings": "", "bracelets": ""},
    "equipment": [{"name": "", "description": "", "position": ""}],
    "behavior": {"stance": "", "sitting": "", "gait": "", "smile": "",
                 "mannerisms": "", "speech_rhythm": "", "catchphrase": ""},
    "speech_style": {"word_habits": "", "particles": "",
                     "address_player": "", "address_others": "", "when_angry": ""},
    "combat_style": {"preference": "", "weapon_usage": "", "battle_cry": "",
                     "spirit_power_signature": ""},
    "personality": {"core": "", "values": "", "principles": "", "bottom_line": "",
                    "interests": "", "fears": "", "aversions": "", "likes": "", "obsession": ""},
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


@dataclass
class TurnResult:
    """Result of a single player turn."""
    narrative: str = ""
    state_changes: list[dict] = field(default_factory=list)
    world_time: str = ""
    time_delta: int = 0
    npc_updates: list[dict] = field(default_factory=list)
    nearby_characters: list[dict] = field(default_factory=list)
    htem_directory: str = ""
    is_system_command: bool = False
    system_response: str | None = None
    player_panel: str = ""
    important_npcs_panel: str = ""
    modeled_npcs: list[dict] = field(default_factory=list)
    prompt: str = ""
    recommendations: list[str] = field(default_factory=list)


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

    async def create_session(self, db: AsyncSession, user_id: str, name: str = "未命名世界") -> dict:
        """Create a new world session: DB record → world → player stub → NPCs."""
        from ane.modules.time_manager import TimeManager as _tm
        session = WorldSession(user_id=user_id, name=name)
        session.time_epoch = 0
        session.world_time = _tm().format_world_time(0)
        db.add(session)
        await db.flush()

        # Generate world regions
        regions = await world_manager.generate_initial_world(db, session.id)

        # Create player stub
        player = await pm.create(db, session.id)

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
    ) -> Player | None:
        """Apply player-chosen character details.
        Delegates to PlayerManager.
        """
        return await pm.apply_character(
            db, session_id, name, age, gender, background, cultivation, personality, identity,
            golden_finger_id=golden_finger_id,
            golden_finger_custom=golden_finger_custom,
            identity_custom=identity_custom,
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
        user_obj = await db.get(user_model, user_id) if user_id else None
        is_adult = bool(user_obj and user_obj.is_adult) if user_obj else False
        validation = validate(user_input, mark_important_npc, is_adult=is_adult)

        # Step 2: System commands
        if validation.is_system_command:
            return await self._handle_system_command(
                db, session_id, validation.system_command, user_input,
            )

        if not validation.is_safe:
            return TurnResult(
                narrative="你的输入包含不被允许的内容，请重新表述。",
                world_time="",
                time_delta=0,
            )

        # Step 3: Extract player info from input
        player = await pm.get_by_session(db, session_id)
        if player:
            extracted = extract_player_info(user_input)
            if extracted.get("name"):
                await pm.update_name(db, session_id, extracted["name"])
            if extracted.get("cultivation"):
                await pm.update_cultivation(db, session_id, extracted["cultivation"])
            if extracted.get("master_name"):
                # Store as a fact — the LLM will integrate it
                await memory_manager.add_fact(
                    db, session_id,
                    f"玩家的师尊是{extracted['master_name']}",
                    category="character", priority=10,
                )

        # Step 3a: Handle mark_important_npc from turn (not stand-alone /npc-modeling)
        if validation.mark_important_npc:
            from ane.modules.npc_manager import npc_manager as _npc_mgr
            names = await self._llm_nameget_multi(user_input, user_id=user_id, session_id=session_id)
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

        # Re-fetch updated session for world_time
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
        npc_names = [n.name for n in active_set.core_npcs]
        npc_names += [n.name for n in active_set.nearby_npcs]

        constraint_set = constraints.get_context_constraints(
            player_cultivation=player_cultivation,
            player_location=player_location,
            active_npc_names=npc_names[:12],
            intent=intent,
        )

        # Step 7: Load memory
        facts = await memory_manager.get_facts(db, session_id)
        conversation = await memory_manager.get_conversation(db, session_id)

        # Step 8: Build prompt context
        ctx = PromptContext()
        ctx.user_input = validation.cleaned_input

        # World context
        ctx.world = WorldContext(name="青云界")

        # Player context
        if player:
            ctx.player = player_to_context(player)

        # NPC contexts: convert ORM models to NPCContext
        ctx.core_npcs = [npc_to_context(n) for n in active_set.core_npcs]
        ctx.nearby_npcs = [npc_to_context(n) for n in active_set.nearby_npcs]

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
        for npc in active_set.core_npcs:
            lts = dict(npc.long_term_state or {}) if isinstance(npc.long_term_state, dict) else {}
            if lts.pop("pending_debut", False):
                ctx.is_modeling_turn = True
                npc.long_term_state = lts
        for npc in active_set.nearby_npcs:
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
        actionable = [p.name for p in active_set.core_npcs]
        ctx.agentic = AgenticContext(
            pov_character=player_name,
            actionable_characters=actionable,
        )

        # Set NSFW flag for context-level control of NPC model NSFW block
        ctx.nsfw_active = (intent == "nsfw")

        # Facts
        ctx.facts = facts

        # Conversation
        ctx.conversation = conversation

        # Era entries: inject all longmemory records for long-term context
        ctx.longmemory_entries = await memory_manager.get_longmemory_entries(db, session_id)

        # Related absent characters
        ctx.related_absent = [npc_to_context(n) for n in active_set.related_absent]

        # NSFW material injection
        if intent == "nsfw":
            nsfw_prompt = await self._build_nsfw_material(db, session_id, active_set.core_npcs)
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

        prompt = prompt_builder.build(ctx)
        prompt = prompt_builder.simplify_prompt(prompt)

        # Step 9: Call llm_main — narrative
        raw_response = ""
        try:
            raw_response = await model_adapter.generate(
                prompt, model=model or DEFAULT_MODEL,
                user_id=user_id, session_id=session_id, label="llm_main",
            )
        except Exception as e:
            logger.exception(f"llm_main generation failed: {e}")

        # Ensure parsed is always initialized, even if llm_main fails
        try:
            parsed: ParsedOutput = parse(raw_response) if raw_response else ParsedOutput(
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
                    )
                    if retry_raw:
                        parsed = parse(retry_raw)
                except Exception:
                    logger.exception("llm_main retry also failed — using first attempt's result")
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
                        await npc_manager.mark_important(db, change_target)
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
                    npc_obj = await db.get(NPC, change_target)
                    if npc_obj and npc_obj.session_id == session_id:
                        old = getattr(npc_obj, change_field, "?")
                        setattr(npc_obj, change_field, change_value)
                        logger.info(f"step15: npc {npc_obj.name}/{change_field}: {old}→{change_value}")
                    else:
                        logger.warning(f"step15: npc_status target {change_target} not found or wrong session")

            # ── Relationship (written as NPC_Relationship edge) ──
            elif change_type == "relationship_change":
                if change_target and change_value:
                    pass  # handled by _run_bg_relationship

            # ── Economy (numeric savings) ──
            elif change_type == "economy_change":
                if change_target == "player":
                    delta = change.get("change", 0)
                    unit = change.get("unit", "")
                    if delta != 0:
                        attrs = dict(player.attributes or {})
                        cur = attrs.get("_savings_amount", 0)
                        if not isinstance(cur, (int, float)):
                            cur = 0
                        cur += delta
                        attrs["_savings_amount"] = cur
                        if unit:
                            attrs["_savings_unit"] = unit
                        player.attributes = attrs
                        logger.info(f"step15: economy {cur} ({delta:+d}) {attrs.get('_savings_unit', '')}")

        # Apply nearby character seeding
        for change in parsed.state_changes:
            if change.get("type") == "npc_nearby":
                await self._handle_npc_nearby(db, session_id, change)

        # ── Process offstage_npcs (named NPCs in narrative, not revealed to player) ──
        offstage_npcs_added = 0
        for offn in (parsed.offstage_npcs or []):
            if not isinstance(offn, dict):
                continue
            off_name = (offn.get("name") or "").strip()
            if not off_name:
                continue
            # Check if already exists in DB
            existing = await db.execute(
                select(NPC).where(
                    NPC.session_id == session_id,
                    NPC.name == off_name,
                )
            )
            if existing.scalar_one_or_none():
                continue  # already known
            # Create basic NPC record
            new_npc = NPC(
                session_id=session_id,
                name=off_name,
                identity=(offn.get("identity") or "").strip(),
                gender=(offn.get("gender") or "").strip(),
                location=player.location or "",
                npc_type="named",
                is_core=True,
                relations={
                    "entries": [{
                        "target": player.name if player else "玩家",
                        "type": (offn.get("relation") or "未知").strip(),
                        "nature": "",
                        "external_note": (offn.get("attitude") or "").strip(),
                    }],
                },
            )
            db.add(new_npc)
            offstage_npcs_added += 1
        if offstage_npcs_added:
            await db.flush()
            logger.info(f"Offstage NPCs created: {offstage_npcs_added} for session {session_id[:12]}")

        # Step 16: Build player and important NPC panels for frontend display
        player_panel_str = "【主角面板】\n"
        if player:
            p_attrs = dict(player.attributes or {})
            line_parts = [
                f"姓名：{player.name} ｜ {p_attrs.get('gender', '?')} ｜ {p_attrs.get('age', '?')}岁",
                f"修为：{player.cultivation}",
                f"性格：{p_attrs.get('personality', '未知')}",
                f"身份：{p_attrs.get('identity', '未知')}",
                f"位置：{player.location or '未知'}",
            ]
            sr = p_attrs.get("spiritual_root", "未知")
            line_parts.append(f"灵根：{sr}")
            sc = p_attrs.get("special_constitution", "")
            if sc:
                line_parts.append(f"体质：{sc}")
            line_parts.append(f"衣物：{p_attrs.get('clothing', '未设定')}")
            inv = player.inventory or []
            if inv:
                items = "、".join(i.get("name", "?") for i in inv)
                line_parts.append(f"物品：{items}")
            p_gf = p_attrs.get("golden_finger_name", "")
            if p_gf:
                line_parts.append(f"金手指：{p_gf}")
            p_gf_desc = p_attrs.get("golden_finger_desc", "")
            if p_gf_desc:
                line_parts.append(f"设定：{p_gf_desc}")
            # Savings display
            savings_amount = p_attrs.get("_savings_amount", 0)
            if savings_amount:
                savings_unit = p_attrs.get("_savings_unit", "块下品灵石")
                line_parts.append(f"灵石：{savings_amount}{savings_unit}")
            exts = p_attrs.get("_extensions", {})
            if exts and isinstance(exts, dict):
                ext_parts = []
                for ek, ev in exts.items():
                    if ek and ev:
                        if isinstance(ev, dict):
                            sub = " | ".join(f"{sk}:{sv}" for sk, sv in ev.items() if sk and sv)
                            ext_parts.append(f"{ek}→{sub}" if sub else f"{ek}→{ev}")
                        else:
                            ext_parts.append(f"{ek}→{ev}")
                if ext_parts:
                    line_parts.append(f"扩展：{' / '.join(ext_parts)}")
            player_panel_str += " ｜ ".join(line_parts)
        else:
            player_panel_str += "（无玩家数据）\n"

        # Important NPCs panel
        all_npcs = await npc_manager.get_by_session(db, session_id)
        important_npcs = [n for n in all_npcs if n.is_important]
        if important_npcs:
            panel_parts = ["【重要人物】"]
            for n in important_npcs:
                n_attrs = dict(n.long_term_state or {})
                # Check for NPC model data
                model_data = n_attrs.get("model", {})
                panel_parts.append(
                    f"⭐ {n.name} ｜ {n.identity or '散修'} ｜ {n.cultivation}\n"
                )
                if n.personality:
                    panel_parts.append(f"  性格：{n.personality}")
                if model_data:
                    bg = model_data.get("background", {})
                    if bg.get("family"):
                        panel_parts.append(f"  家族：{bg['family']}")
                    pers = model_data.get("personality", {})
                    if pers.get("obsession"):
                        panel_parts.append(f"  执念：{pers['obsession']}")
                if n_attrs.get("summary"):
                    panel_parts.append(f"  备注：{n_attrs['summary']}")
                panel_parts.append("")
            panel_str = "\n".join(panel_parts).strip()
        else:
            panel_str = "【重要人物】\n（无）"

        # ── Build modeled_npcs: NPCs with model data mentioned in this turn's input ──
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
        await db.commit()

        # Fire background relationship processing AFTER commit (every turn)
        import asyncio as _asyncio
        # Collect NPC names that were modeled this turn
        _recently_modeled = []
        for _n in (all_npcs or []):
            _n_lts = dict(_n.long_term_state or {})
            if _n_lts.get("pending_debut"):
                _recently_modeled.append(_n.name)
        _asyncio.ensure_future(self._run_bg_relationship(
                session_id, parsed.state_changes, parsed.offstage_npcs,
                _recently_modeled, user_id,
            ))

        # Fire background summary AFTER commit — avoids concurrent SQLite write
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
            htem_directory="",  # HTEM removed
            prompt=prompt,
            player_panel=player_panel_str,
            important_npcs_panel=panel_str,
            modeled_npcs=modeled_npcs,
            recommendations=recommendations,
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
                prompt = (
                    "你是一个场景摘要和行动顾问。输出给玩家看的场景记忆摘要，同时给下一轮叙事引擎提供上下文。\n\n"
                    "输出格式（纯文本，不要JSON标记）：\n"
                    "当前地点：地名/场所 | 时间\n"
                    "氛围/环境：气味、光线、天气、声音中最有特色的2-3项\n"
                    "行动/目标：一句话概括玩家位置和当前意图\n"
                    "持有物品中重要的变化：有则写，无则写无\n"
                    "交互npc：姓名 | 身份/修为 | 当前行为 | 互动态度\n"
                    "（每行一个，只列出有交互的NPC。本轮无交互NPC写无）\n"
                    "世界事件：\n"
                    "之前的世界事件：（无）\n"
                    f"本轮：[第{tn}轮]本轮叙事里发生的事（只写一条）\n"
                    "推荐行动：\n1.\n2.\n3.\n\n"
                    f"玩家输入：{user_input}\n"
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
                            # Build shortened content for each turn (strip header lines)
                            turn_summaries = []
                            for ce in compact_list:
                                lines = ce.content.strip().split("\n")
                                # Take first 3 substantial lines per turn
                                keep = [l for l in lines if l.strip() and not l.startswith("当前地点") and not l.startswith("推荐行动")]
                                turn_summaries.append(f"Turn {ce.turn_number}: {' | '.join(keep[:3])}")

                            era_text = "\n".join(turn_summaries)
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
        user_id: str = "",
    ) -> dict:
        """Handle POST /npc-modeling: extract names, classify known vs new,
        run llm_cover for known NPCs, return new names for frontend confirmation.

        Returns:
            updated: list of {npc_name, model_data} for NPCs that got updated
            new_names: list of names not yet in the database
        """
        from ane.modules.npc_manager import npc_manager as npc_mgr
        from ane.database.models import NPC, Memory

        # 1. Extract ALL names
        names = await self._llm_nameget_multi(user_input, user_id=user_id, session_id=session_id)
        if not names:
            return {"updated": [], "new_names": []}

        from ane.modules.player_manager import player_manager as pm
        player = await pm.get_by_session(db, session_id)
        player_location = player.location if player else "未知"

        updated = []
        new_names = []

        for npc_name in names:
            # 2. Check DB for each name
            existing = await db.execute(
                select(NPC).where(
                    NPC.session_id == session_id,
                    NPC.name == npc_name,
                )
            )
            db_npc = existing.scalar_one_or_none()

            if db_npc and db_npc.is_important:
                # 3. Known NPC with existing model — run llm_cover for partial update
                lts = dict(db_npc.long_term_state or {}) if isinstance(db_npc.long_term_state, dict) else {}
                existing_model = lts.get("model", {})
                if existing_model and isinstance(existing_model, dict) and existing_model.get("model_version"):
                    updates = await self._llm_cover(
                        npc_name, user_input, existing_model,
                        user_id=user_id, session_id=session_id,
                    )
                    if updates:
                        self._deep_merge(existing_model, updates)
                        existing_model["model_version"] = "1.0"
                        lts["model"] = existing_model
                        db_npc.long_term_state = lts
                        db_npc.is_important = True
                        basic = existing_model.get("basic", {})
                        if basic.get("identity"): db_npc.identity = basic["identity"]
                        if basic.get("cultivation"): db_npc.cultivation = basic["cultivation"]
                        if basic.get("gender"): db_npc.gender = basic["gender"]
                        if basic.get("age"): db_npc.age = int(basic["age"])
                        pers_core = existing_model.get("personality", {}).get("core", "")
                        if pers_core: db_npc.personality = pers_core
                        # Sync relationships from model to NPC.relations
                        model_rels = existing_model.get("relationships", {})
                        if model_rels and isinstance(model_rels, dict):
                            db_npc.relations = {
                                "entries": GameEngine._model_rels_to_entries(model_rels),
                            }
                        await db.commit()
                        logger.info(f"llm_cover updated for {npc_name}")
                        updated.append({"npc_name": npc_name, "model_data": existing_model})
                    else:
                        logger.warning(f"llm_cover failed for {npc_name} — keeping existing data")
                    # cover success or fail: don't add to new_names, already in DB
                else:
                    # NPC exists but no model_data yet (orphan) — treat as new candidate
                    new_names.append(npc_name)
            elif db_npc and not db_npc.is_important:
                # NPC exists in DB but wasn't important before — treat as new candidate
                new_names.append(npc_name)
            else:
                # 4. Not in DB at all — return as new name candidate (no DB creation yet)
                new_names.append(npc_name)

        await db.commit()
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
        """Convert _MODEL_TEMPLATE relationships dict to NPC.relations entries list."""
        entries = []
        # Direct string fields → paired relationship
        for key, label in [("father", "父亲"), ("mother", "母亲"), ("spouse", "配偶"),
                           ("master", "师父"), ("senior_brother", "师兄"), ("senior_sister", "师姐"),
                           ("junior_brother", "师弟"), ("junior_sister", "师妹"),
                           ("teacher", "师尊"), ("superior", "上级"), ("subordinate", "下属"),
                           ("lover", "恋人"), ("fiance", "婚约对象"), ("beloved", "爱人"),
                           ("rival", "竞争者"), ("pursuer", "追求者")]:
            v = model_rels.get(key)
            if v and isinstance(v, str) and v.strip():
                entries.append({"target": v.strip(), "type": label, "nature": "", "external_note": ""})
        # List fields
        for key, label in [("friends", "朋友"), ("enemies", "敌人")]:
            for v in (model_rels.get(key) or []):
                if isinstance(v, str) and v.strip():
                    entries.append({"target": v.strip(), "type": label, "nature": "", "external_note": ""})
        return entries

    # ── Full NPC modeling (called after user confirms) ──────────

    async def _run_npc_modeling(
        self, db, npc_name, user_input, npc_model, session_id, user_id, player_name, player_location, is_new_npc,
    ):
        """Full modeling for a confirmed NPC."""
        from ane.modules.model_adapter import model_adapter
        from ane.modules.npc_modeler import parse_modeling_response as _pmr
        from ane.database.models import Memory
        from ane.modules.input_validator import _NSFW_BODY_WORDS
        from ane.config import SYSTEM_PROMPT_SUFFIX

        safe_input = user_input
        for w in _NSFW_BODY_WORDS:
            if w in safe_input:
                safe_input = safe_input.replace(w, "【已修饰】")
        safe_input = safe_input.strip() or "一个NPC"

        model_prompt = (
            f"{SYSTEM_PROMPT_SUFFIX}\n\n"
            f"你是一个角色建模师。玩家在游戏中标记重要人物「{npc_name}」。\n\n"
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
            f"8. 关系理解：玩家说的「我」指{player_name}本人。"
            f"如果玩家说「XXX是我妈/我爸/我师尊/我师兄/我道侣/我的XX」，"
            f"则表示该NPC与{player_name}有该称谓所对应的关系，"
            f"而不是与NPC彼此之间有该关系。"
            f"请根据玩家输入正确理解「我」指代{player_name}，正确填写NPC与{player_name}的关系，"
            f"以及NPC之间的关系（如「张海是张大强的妻子」）。\n"
            f"注意区分：spouse（配偶/丈夫/妻子）指已婚夫妻关系，"
            f"lover（恋人）指恋爱中未结婚的对象。\n\n"
            f'{json.dumps(_MODEL_TEMPLATE, ensure_ascii=False, indent=2)}\n\n'
            f"请输出 JSON："
        )

        raw = await model_adapter.generate(
            model_prompt, user_id=user_id, session_id=session_id, label="llm_modeling",
        )
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
            if basic.get("identity"): npc_model.identity = basic["identity"]
            if basic.get("cultivation"): npc_model.cultivation = basic["cultivation"]
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
        underage_npcs = [
            n for n in core_npcs
            if n.age is not None and n.age < 18
        ]

        # Also check player age
        player_underage = False
        from ane.modules.player_manager import player_manager as pm
        player = await pm.get_by_session(db, session_id)
        player_age = (player.attributes or {}).get("age", 0) if player else 0
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
        self, user_input, user_id="", session_id="",
    ) -> list[str]:
        """Extract ALL character names from user input.
        Returns a list of proper name strings, or empty list.
        """
        from ane.modules.model_adapter import model_adapter

        prompt = (
            "从以下文本中列出所有被提到的人物姓名（中文名）。\n"
            "规则：\n"
            "1. 如果文本中明确说出了姓名（如'我叫陆青棠'、'她是白慕彩'）→ 输出那个姓名\n"
            "2. 如果文本中没有姓名只有描述（如'佩剑少女'、'卖糖葫芦的'），"
            "则根据其身份和场景赋予一个符合世界观的中文姓名（姓+名，2-3字）\n"
            "3. 每行只输出一个名字，不要输出任何其他文字\n"
            "4. 如果没有任何人物姓名也输出空\n\n"
            f"文本：\n{user_input}"
        )

        def _is_valid_chinese_name(name: str) -> bool:
            if not name:
                return False
            cjk_count = sum(1 for c in name if '一' <= c <= '鿿')
            return cjk_count == len(name) and 2 <= len(name) <= 3

        def _parse_names(text: str) -> list[str]:
            if not text or not text.strip():
                return []
            names = set()
            for line in text.strip().split('\n'):
                raw = line.strip().rstrip("。，, .!\n、；：:；，、 ")
                if not raw:
                    continue
                # Split by common delimiters in case LLM put multiple per line
                for token in raw.replace('、', '\n').replace('，', '\n').replace(',', '\n').split('\n'):
                    token = token.strip()
                    if _is_valid_chinese_name(token):
                        names.add(token)
            return list(names)

        for attempt in range(2):
            try:
                result = await model_adapter.generate(
                    prompt,
                    user_id=user_id, session_id=session_id, label="_llm_nameget",
                )
                names = _parse_names(result) if result else []
                if names:
                    logger.info(f"_llm_nameget_multi: {names}")
                    return names
                if attempt == 0:
                    # Retry with stronger instruction
                    prompt = (
                        "你是一个姓名提取助手。\n"
                        "从文本中提取所有人物姓名（中文名，每个姓名2-3个字）。\n"
                        "每行只输出一个姓名，不要其他文字。\n"
                        f"文本：\n{user_input}"
                    )
            except Exception as e:
                logger.warning(f"_llm_nameget_multi attempt {attempt + 1} failed: {e}")

        return []

    async def _llm_cover(
        self, npc_name, user_input, existing_model, user_id="", session_id="",
    ) -> dict | None:
        """Partial update to an existing NPC model.
        Only fills fields that the user's new input touches, doesn't touch others.
        Returns a dict of only the changed sub-tree, or None on failure.
        """
        from ane.modules.model_adapter import model_adapter
        from ane.config import SYSTEM_PROMPT_SUFFIX

        # Build a minimal template showing only the structure
        template_copy = json.loads(json.dumps(_MODEL_TEMPLATE))

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

    # ── (duplicate removed — see the full implementations above) ──

    # ── NPC name extraction helper ──────────────────────────────

    async def _random_npc_name(self, db, session_id) -> str:
        result = await db.execute(
            select(NPC.name).where(NPC.session_id == session_id)
        )
        existing_names = {row[0] for row in result.fetchall()}
        return npc_manager._random_name(existing_names)

    # ── System commands ─────────────────────────────────────────

    async def _handle_system_command(
        self, db, session_id, cmd, user_input,
    ) -> TurnResult:
        """Handle system commands /help, /facts, /addfact."""
        if cmd == "system_help":
            return self._cmd_help()
        elif cmd == "system_list_facts":
            return await self._cmd_list_facts(db, session_id)
        return TurnResult(
            is_system_command=True,
            system_response="未知命令。可用命令：/help, /facts, /addfact <内容>",
        )


    def _cmd_help(self) -> TurnResult:
        return TurnResult(
            is_system_command=True,
            system_response=(
                "【帮助】\n"
                "/facts - 列出世界事实\n"
                "/addfact <内容> - 添加世界事实\n"
                "/help - 显示帮助\n"
                "描述这个世界 - 查看世界概况"
            ),
        )

    async def _cmd_list_facts(self, db, session_id) -> TurnResult:
        facts = await memory_manager.get_facts(db, session_id)
        if not facts:
            return TurnResult(is_system_command=True, system_response="【世界事实】\n（暂无事实）")
        lines = ["【世界事实】"]
        for f in facts:
            lines.append(f"[{f.category}] (P{f.priority}) {f.content}")
        return TurnResult(is_system_command=True, system_response="\n".join(lines))

    # ── Background relationship processing ─────────────────────

    async def _run_bg_relationship(
        self,
        session_id: str,
        state_changes: list[dict],
        offstage_npcs: list[dict],
        recently_modeled: list[str],  # NPC names that got modeled in this turn
        user_id: str = "",
    ):
        """Background: read existing relationship graph + new data → LLM increment → update + notify frontend."""
        from ane.database.engine import async_session_factory as _sf_
        from ane.database.models import NPC, NPC_Relationship, Memory
        from ane.config import SYSTEM_PROMPT_SUFFIX
        from datetime import datetime as _dt
        import json

        async with _sf_() as _db:
            try:
                # ── 1. Gather input for this increment ──
                increment_lines = []

                # 1a. This turn's llm_main narrative + offstage_npcs
                conv_result = await _db.execute(
                    select(Memory).where(
                        Memory.session_id == session_id,
                        Memory.memory_type == "conversation",
                    ).order_by(Memory.turn_number.desc()).limit(3)  # last 3 turns only
                )
                for entry in conv_result.scalars().all():
                    content = entry.content or ""
                    idx = content.find("【AI】")
                    if idx >= 0:
                        ai_text = content[idx + 4:].strip()
                        nearby_idx = ai_text.find("\n\n【附近人物】")
                        if nearby_idx >= 0:
                            ai_text = ai_text[:nearby_idx]
                        increment_lines.append(f"第{entry.turn_number}轮叙事：{ai_text[:2000]}")

                # 1b. This turn's offstage_npcs
                if offstage_npcs:
                    increment_lines.append("本轮幕后NPC：")
                    for offn in offstage_npcs:
                        increment_lines.append(
                            f"  {offn.get('name','?')} ({offn.get('identity','?')}) "
                            f"— 关系: {offn.get('relation','?')} — 态度: {offn.get('attitude','?')}"
                        )

                # 1c. Newly modeled NPC relationships
                if recently_modeled:
                    for npc_name in recently_modeled:
                        db_npc = await _db.execute(
                            select(NPC).where(
                                NPC.session_id == session_id,
                                NPC.name == npc_name,
                            )
                        )
                        npc = db_npc.scalar_one_or_none()
                        if not npc:
                            continue
                        lts = dict(npc.long_term_state or {})
                        model = lts.get("model", {})
                        if not isinstance(model, dict) or not model.get("model_version"):
                            continue
                        rels = model.get("relationships", {})
                        att = model.get("attitude_to_player", {})
                        parts = [f"【{npc.name}建模档案】"]
                        att_parts = []
                        for k, label in [("surface", "表层态度"), ("true_feelings", "真实想法"),
                                         ("relationship_trend", "关系变化倾向")]:
                            v = att.get(k)
                            if v:
                                att_parts.append(f"{label}：{v}")
                        if att_parts:
                            parts.append("对玩家态度：" + "，".join(att_parts))
                        rel_parts = []
                        for k, label in [("father", "父亲"), ("mother", "母亲"), ("spouse", "配偶"),
                                         ("master", "师父"), ("lover", "恋人"),
                                         ("beloved", "爱人"), ("rival", "竞争者")]:
                            v = rels.get(k)
                            if v and isinstance(v, str) and v.strip():
                                rel_parts.append(f"{label}：{v}")
                        for k, label in [("friends", "朋友"), ("enemies", "敌人")]:
                            v = rels.get(k)
                            if isinstance(v, list) and v:
                                rel_parts.append(f"{label}：" + "、".join(v[:5]))
                        if rel_parts:
                            parts.append("关系：" + "，".join(rel_parts))
                        if len(parts) > 1:
                            increment_lines.append("\n".join(parts))

                increment_block = "\n\n".join(increment_lines) if increment_lines else "（本轮无新关系数据）"

                # ── 2. Read existing relationship graph ──
                existing_rel_result = await _db.execute(
                    select(NPC_Relationship).where(
                        NPC_Relationship.session_id == session_id
                    )
                )
                existing_edges = list(existing_rel_result.scalars().all())
                existing_graph_lines = ["【当前关系网】"]
                for rel in existing_edges:
                    existing_graph_lines.append(
                        f"  {rel.source_name} → {rel.target_name} : {rel.rel_type} "
                        f"(affinity={rel.affinity}) {rel.description}"
                    )
                if not existing_edges:
                    existing_graph_lines.append("  （空）")
                existing_graph = "\n".join(existing_graph_lines)

                # ── 3. LLM call: incremental update ──
                prompt = (
                    f"{SYSTEM_PROMPT_SUFFIX}\n\n"
                    f"你是一个关系网增量更新助手。以下是当前已有的关系网和本轮新增的数据。\n"
                    f"请输出本轮需要作出的变更。\n\n"
                    f"{existing_graph}\n\n"
                    f"【本轮新增数据】\n{increment_block}\n\n"
                    f"请输出 JSON 对象，包含 add 和 update 两个数组：\n"
                    f'{{\n'
                    f'  "add": [\n'
                    f'    {{"source": "姓名A", "target": "姓名B", "type": "关系类型", '
                    f'"description": "描述", "affinity": 整数(-100~100)}}\n'
                    f'  ],\n'
                    f'  "update": [\n'
                    f'    {{"source": "姓名A", "target": "姓名B", "type": "关系类型（可选，不变可不填）", '
                    f'"description": "新描述", "affinity": 新亲密度}}\n'
                    f'  ]\n'
                    f'}}\n\n'
                    f"规则：\n"
                    f"1. 只输出 JSON，不要额外文字\n"
                    f"2. add 中一个 pair (source→target) 只输出一条——但可以同时有多种关系标签\n"
                    f"3. 关系可以多重：如某人是主角的母亲又是主角的性爱对象，type 用 / 分隔\n"
                    f"4. 关系冲突时，以本轮最新数据为准\n"
                    f"5. 建模档案中的关系（如'mother: 林慕萱'）是确定事实，应优先采用\n"
                    f"6. 已有关系中未在本轮变更的不要输出\n"
                    f"7. 关系类型使用中文，affinity -100~100\n"
                    f"8. 没有变更时输出 {{\"add\": [], \"update\": []}}"
                )

                from ane.modules.model_adapter import model_adapter as _ma
                raw = await _ma.generate(
                    prompt, user_id=user_id, session_id=session_id,
                    label="llm_relationshipprocess",
                )
                logger.info(f"_run_bg_relationship: raw output length={len(raw)}")

                # ── 4. Parse JSON ──
                import re
                brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
                if not brace_match:
                    logger.warning("_run_bg_relationship: no JSON object in response")
                    return
                changes = json.loads(brace_match.group())

                # ── 5. Build name→id mapping ──
                npc_result = await _db.execute(
                    select(NPC).where(NPC.session_id == session_id)
                )
                name_to_npc = {}
                for npc in npc_result.scalars().all():
                    name_to_npc[npc.name] = npc.id

                added = 0

                # 5a. Add new edges
                for edge in changes.get("add", []):
                    source = (edge.get("source") or "").strip()
                    target = (edge.get("target") or "").strip()
                    if not source or not target:
                        continue
                    new_rel = NPC_Relationship(
                        session_id=session_id,
                        source_name=source,
                        source_id=name_to_npc.get(source),
                        target_name=target,
                        target_id=name_to_npc.get(target),
                        rel_type=edge.get("type", "未知"),
                        description=edge.get("description", ""),
                        affinity=max(-100, min(100, edge.get("affinity", 0) if isinstance(edge.get("affinity"), int) else 0)),
                    )
                    _db.add(new_rel)
                    added += 1

                # 5b. Update existing edges
                for edge in changes.get("update", []):
                    source = (edge.get("source") or "").strip()
                    target = (edge.get("target") or "").strip()
                    if not source or not target:
                        continue
                    for rel in existing_edges:
                        if rel.source_name == source and rel.target_name == target:
                            if edge.get("type"):
                                rel.rel_type = edge["type"]
                            if edge.get("description"):
                                rel.description = edge["description"]
                            if "affinity" in edge:
                                rel.affinity = max(-100, min(100, edge["affinity"]))
                            rel.updated_at = _dt.utcnow()
                            break

                await _db.commit()
                logger.info(f"_run_bg_relationship: +{added} edges, ~{len(changes.get('update', []))} updates for session={session_id[:12]}")

            except Exception:
                logger.exception("_run_bg_relationship failed")


# Singleton
game_engine = GameEngine()
