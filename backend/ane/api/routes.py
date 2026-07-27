"""FastAPI routes for the ANE API — all require authentication."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ane.auth import get_current_user
from ane.database.engine import get_db
from ane.database.models import WorldSession, Player, NPC, Memory
from ane.game_engine import game_engine
from ane.modules.memory_manager import memory_manager
from ane.modules.player_manager import player_manager as pm
from ane.api.schemas import (
    CreateSessionRequest, CreateSessionResponse,
    SessionSummary, TurnRequest, TurnResponse,
    DeleteSessionResponse, ApplyCharacterRequest,
    MoveRequest,
    NpcModelingRequest, NpcModelingResponse,
    NpcModelingConfirmRequest, NpcModelingConfirmResponse,
    SummaryEntry, SummariesResponse,
)

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ── Static data routes (registered before /{session_id} to avoid path capture) ──

_NON_XIANXIA_KEYWORDS = [
    "魔法少女", "契约兽", "魔女",
    "地精", "精灵", "矮人", "兽人",
    "魔法科技", "高科技碗", "高科技",
    "便利店", "学校", "中学",
    "触手", "灾兽",
    "公国", "王国", "公主",
    "变身系统", "心形宝石",
]


def _is_reject_by_keywords(name: str, entry: dict) -> bool:
    all_text = name
    all_text += entry.get("description", "")
    for k, v in entry.get("attributes", {}).items():
        if isinstance(v, str):
            all_text += v
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    all_text += item
                elif isinstance(item, dict):
                    all_text += item.get("name", "") + " " + item.get("summary", "")
    for kw in _NON_XIANXIA_KEYWORDS:
        if kw in all_text:
            return True
    return False


@router.get("/models/sects")
async def list_sects():
    from ane.content.json_loader import load_json
    data = load_json("world_templates.json")
    filtered = []
    for e in data.get("sects", []):
        name = e["name"]
        if not (name.endswith("圣地") or name.endswith("宗") or name.endswith("门")
                or name.endswith("宫") or name.endswith("阁") or name.endswith("殿")
                or name.endswith("谷") or name.endswith("观") or name.endswith("派")):
            continue
        if _is_reject_by_keywords(name, e):
            continue
        filtered.append(name)
    return {"sects": filtered}


@router.get("/models/sects/detail")
async def list_sects_detail():
    """返回宗门名 + 描述，供地图保存时展示设定用"""
    from ane.content.json_loader import load_json
    data = load_json("world_templates.json")
    result = []
    for e in data.get("sects", []):
        name = e["name"]
        if not (name.endswith("圣地") or name.endswith("宗") or name.endswith("门")
                or name.endswith("宫") or name.endswith("阁") or name.endswith("殿")
                or name.endswith("谷") or name.endswith("观") or name.endswith("派")):
            continue
        if _is_reject_by_keywords(name, e):
            continue
        desc = e.get("description", "")
        attrs = e.get("attributes", {})
        # Collect key details: spiritual_rules, law_description, atmosphere
        extra = []
        for k in ["spiritual_rules", "law_description", "atmosphere"]:
            v = attrs.get(k, "")
            if v and len(v) > 5:
                extra.append(v)
        result.append({
            "name": name,
            "description": desc[:200] if desc else "",
            "details": extra[:3],
        })
    return {"sects": result}


@router.get("/models/cities")
async def list_cities():
    from ane.content.json_loader import load_json
    data = load_json("world_templates.json")
    filtered = []
    for e in data.get("settlements", []):
        name = e["name"]
        if not name.endswith("城"):
            continue
        if _is_reject_by_keywords(name, e):
            continue
        filtered.append(name)
    return {"cities": filtered}


# ── helpers ─────────────────────────────────────────────────

async def _get_users_session(db: AsyncSession, session_id: str, user_id: str) -> WorldSession:
    """Fetch a session and verify it belongs to the current user."""
    result = await db.execute(
        select(WorldSession).where(
            WorldSession.id == session_id,
            WorldSession.user_id == user_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


# ── Session CRUD ────────────────────────────────────────────

@router.post("", response_model=CreateSessionResponse, status_code=201)
async def create_session(
    req: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await game_engine.create_session(db, user_id=user.id, name=req.name)
    return CreateSessionResponse(**result)


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """List all sessions belonging to the current user."""
    result = await db.execute(
        select(WorldSession)
        .where(WorldSession.user_id == user.id)
        .order_by(WorldSession.created_at.desc())
    )
    sessions = result.scalars().all()
    summaries = []
    for s in sessions:
        player_result = await db.execute(
            select(Player).where(Player.session_id == s.id)
        )
        player = player_result.scalar_one_or_none()
        summaries.append(SessionSummary(
            session_id=s.id,
            name=s.name,
            world_time=s.world_time,
            is_active=s.is_active,
            created_at=s.created_at.isoformat() if s.created_at else None,
            player_name=player.name if player else "",
            player_cultivation=player.cultivation if player else "",
            map_data=s.map_data,
        ))
    return summaries


@router.delete("/{session_id}", response_model=DeleteSessionResponse)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from ane.database.models import Player as P, NPC as N, WorldRegion, EventLog, Fact, Memory as M
    session = await _get_users_session(db, session_id, user.id)
    for model in [P, N, WorldRegion, EventLog, Fact, M]:
        await db.execute(delete(model).where(model.session_id == session_id))
    await db.delete(session)
    await db.commit()
    logger.info(f"Session deleted: {session_id}")
    return DeleteSessionResponse(session_id=session_id, deleted=True)


@router.get("/{session_id}", response_model=SessionSummary)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    # Edge case: this route also catches '/models/sects'
    if session_id == 'models':
        raise HTTPException(status_code=404, detail="Not found")
    from ane.database.models import NPC
    import json as _json
    session = await _get_users_session(db, session_id, user.id)
    player_result = await db.execute(
        select(Player).where(Player.session_id == session.id)
    )
    player = player_result.scalar_one_or_none()
    full_convs = await memory_manager.get_full_conversation(db, session_id)
    conversations = [
        {"turn_number": m.turn_number, "content": m.content}
        for m in full_convs
    ]
    npc_result = await db.execute(
        select(NPC).where(NPC.session_id == session_id, NPC.is_core == True)
    )
    npc_names = [n.name for n in npc_result.scalars().all()]
    prompt_entries = await memory_manager.get_prompts(db, session_id)
    prompts_list = [
        {"turn_number": m.turn_number, "content": m.content}
        for m in prompt_entries
    ]

    # Load recommendations for session reload
    recommendations: list[str] = []
    try:
        rec_mem = await db.execute(
            select(Memory).where(
                Memory.session_id == session_id,
                Memory.memory_type == "recommendations",
            ).limit(1)
        )
        rec_entry = rec_mem.scalar_one_or_none()
        if rec_entry:
            recommendations = _json.loads(rec_entry.content)
    except Exception:
        pass

    return SessionSummary(
        session_id=session.id,
        name=session.name,
        world_time=session.world_time,
        is_active=session.is_active,
        created_at=session.created_at.isoformat() if session.created_at else None,
        player_name=player.name if player else "",
        player_cultivation=player.cultivation if player else "",
        player_location=player.location if player else "",
        player_gender=dict(player.attributes or {}).get('gender', '男') if player else "男",
        travel_log=dict(player.attributes or {}).get('travel_log', []) if player else [],
        conversation=conversations,
        npc_names=npc_names,
        htem_directory=(await memory_manager.get_htem_directory(db, session_id)) or "",
        map_data=session.map_data,
        world_intro=session.world_intro or "",
        prompts=prompts_list,
        recommendations=recommendations,
    )


# ── Map ─────────────────────────────────────────────────────

@router.post("/{session_id}/map", status_code=200)
async def save_map(
    session_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    logger.info(f"save_map called for session {session_id} with {len(req)} keys")
    session = await _get_users_session(db, session_id, user.id)
    session.map_data = req
    session.world_intro = req.get("world_intro", "")

    # ── Relocate player and NPCs to match the saved map ──
    locations = req.get("locations", [])
    city_locations = req.get("cityLocations", [])
    chosen_sect = req.get("chosen_sect", "")
    chosen_city = req.get("chosen_city", "")
    sect_names = [loc["name"] for loc in locations if isinstance(loc, dict)]
    city_names = [cl["name"] for cl in city_locations if isinstance(cl, dict)]
    all_places = list(dict.fromkeys(sect_names + city_names))

    if all_places:
        import random as _rnd
        player = await pm.get_by_session(db, session_id)

        # 1. Determine player spawn city
        # Frontend already matched city to sect by coordinates — use directly
        if not chosen_city:
            # Fallback: coordinate matching (legacy)
            if chosen_sect and city_locations:
                for loc in locations:
                    if isinstance(loc, dict) and loc.get("name") == chosen_sect:
                        for cl in city_locations:
                            if isinstance(cl, dict) and cl.get("x") == loc.get("x") and cl.get("y") == loc.get("y"):
                                chosen_city = cl.get("name", "")
                                break
                        break
        if not chosen_city:
            chosen_city = _rnd.choice(city_names) if city_names else _rnd.choice(all_places)

        if player:
            player.location = chosen_city
            attrs = dict(player.attributes or {})
            hierarchy_parts = [chosen_city]
            for loc in locations:
                if isinstance(loc, dict) and loc.get("name") == chosen_sect:
                    hierarchy_parts.insert(0, loc["name"])
                    break
            attrs["location_hierarchy"] = " → ".join(hierarchy_parts)
            player.attributes = attrs


    # Save default recommendations if provided
    default_recs = req.get("default_recommendations", [])
    if default_recs:
        from ane.database.models import Memory
        import json as _json
        await db.execute(
            delete(Memory).where(
                Memory.session_id == session_id,
                Memory.memory_type == "recommendations",
            )
        )
        db.add(Memory(
            session_id=session_id,
            memory_type="recommendations",
            content=_json.dumps(default_recs, ensure_ascii=False),
            turn_number=0,
        ))
    await db.commit()
    return {"ok": True, "relocated": len(all_places)}


# ── Turn ────────────────────────────────────────────────────

@router.post("/{session_id}/turn", response_model=TurnResponse)
async def process_turn(
    session_id: str,
    req: TurnRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    session = await _get_users_session(db, session_id, user.id)
    from sqlalchemy import func
    from ane.database.models import Memory as Mem
    count_result = await db.execute(
        select(func.count()).where(
            Mem.session_id == session_id,
            Mem.memory_type == "conversation",
        )
    )
    turn_count = count_result.scalar() or 0
    turn_result = await game_engine.process_turn(
        db, session_id, req.input, turn_number=turn_count + 1, model=req.model,
        mark_important_npc=req.mark_important_npc,
        load_model_data=req.load_model_data,
        user_id=user.id,
    )
    return TurnResponse(
        narrative=turn_result.narrative,
        state_changes=turn_result.state_changes,
        world_time=turn_result.world_time,
        time_delta=turn_result.time_delta,
        npc_updates=turn_result.npc_updates,
        nearby_characters=turn_result.nearby_characters,
        htem_directory=turn_result.htem_directory,
        is_system_command=turn_result.is_system_command,
        system_response=turn_result.system_response,
        prompt=turn_result.prompt,
        player_panel=turn_result.player_panel,
        important_npcs_panel=turn_result.important_npcs_panel,
        modeled_npcs=turn_result.modeled_npcs,
        recommendations=turn_result.recommendations,
    )

@router.get("/{session_id}/templates")
async def get_character_templates():
    from ane.modules.player_manager import player_manager
    return player_manager.get_templates()


@router.post("/{session_id}/character")
async def apply_character(
    session_id: str,
    req: ApplyCharacterRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    session = await _get_users_session(db, session_id, user.id)
    player = await game_engine.apply_character(
        db, session_id,
        name=req.name, age=req.age, gender=req.gender,
        background=req.background,
        cultivation=req.cultivation,
        personality=req.personality,
        identity=req.identity,
        golden_finger_id=req.golden_finger_id,
        golden_finger_custom=req.golden_finger_custom,
        identity_custom=req.identity_custom,
    )
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    await db.commit()
    # Include golden finger info in response
    attrs = player.attributes or {}
    return {
        "session_id": session_id,
        "player_name": player.name,
        "cultivation": player.cultivation,
        "identity": req.identity,
        "golden_finger_name": attrs.get("golden_finger_name", ""),
        "golden_finger_tagline": attrs.get("golden_finger_tagline", ""),
    }


@router.post("/{session_id}/npc-modeling")
async def npc_modeling(
    session_id: str,
    req: NpcModelingRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Step 1: Create or model an important NPC.

    Extracts name from player input via llm_nameget, then runs llm_modeling
    to generate a full 90+ field character profile. Returns the model data.
    """
    session = await _get_users_session(db, session_id, user.id)
    result = await game_engine.do_npc_modeling(
        db, session_id, req.input, user_id=user.id,
    )
    return result


@router.post("/{session_id}/npc-modeling/confirm", response_model=NpcModelingConfirmResponse)
async def npc_modeling_confirm(
    session_id: str,
    req: NpcModelingConfirmRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Step 2: Confirm and run full modeling for one new NPC."""
    from sqlalchemy import select
    from ane.database.models import NPC
    from ane.modules.npc_manager import npc_manager as npc_mgr
    from ane.modules.player_manager import player_manager as pm

    session = await _get_users_session(db, session_id, user.id)
    player = await pm.get_by_session(db, session_id)
    player_location = player.location if player else "未知"

    existing = await db.execute(
        select(NPC).where(
            NPC.session_id == session_id,
            NPC.name == req.name,
        )
    )
    db_npc = existing.scalar_one_or_none()
    if not db_npc:
        # Create NPC now (deferred from pre-check step)
        db_npc = await npc_mgr.create(
            db, session_id, name=req.name, location=player_location, is_core=True,
        )
        if not db_npc:
            raise HTTPException(status_code=500, detail=f"Failed to create NPC {req.name}")
    # Mark as important — this is a player-approved modeling action
    db_npc.is_important = True

    player_name = player.name if player else "玩家"
    model_data = await game_engine._run_npc_modeling(
        db, req.name, req.input, db_npc,
        session_id, user.id, player_name, player_location, is_new_npc=True,
    )
    return {"npc_name": req.name, "model_data": model_data}


@router.post("/{session_id}/move", status_code=200)
async def move_player(
    session_id: str,
    req: MoveRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Move player to a new city/sect on the map.

    Calculates travel time from map coordinates, advances world time,
    updates player location, and logs a fact.
    """
    from ane.modules.time_manager import time_manager as tm
    from ane.database.models import Player as P
    session = await _get_users_session(db, session_id, user.id)

    player = await pm.get_by_session(db, session_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    # Use old location's map coords if available (stored in attributes), else assume 0,0
    p_attrs = dict(player.attributes or {}) if player else {}
    from_x = float(p_attrs.get('_map_x', 0.0))
    from_y = float(p_attrs.get('_map_y', 0.0))

    ticks = tm.calc_travel_delta(from_x, from_y, req.dest_x, req.dest_y)
    session.time_epoch += ticks
    session.world_time = tm.format_world_time(session.time_epoch)

    # Update player location
    old_loc = player.location
    player.location = req.destination
    attrs = dict(player.attributes or {})
    attrs["location_hierarchy"] = req.destination
    attrs["_map_x"] = req.dest_x
    attrs["_map_y"] = req.dest_y

    # ── Travel log ──
    travel_log = attrs.get("travel_log", [])
    if not isinstance(travel_log, list):
        travel_log = []
    travel_log.append({
        "from": old_loc,
        "to": req.destination,
        "ticks": ticks,
        "world_time": session.world_time,
    })
    # Keep last 20 entries
    attrs["travel_log"] = travel_log[-20:]
    player.attributes = attrs

    # Update NPC locations based on new context
    npc_updates = await tm.update_active_npcs(db, session_id, ticks)

    await db.commit()
    logger.info(f"Move: {old_loc} → {req.destination} (+{ticks} ticks, {session.world_time})")

    return {
        "session_id": session_id,
        "player_location": req.destination,
        "old_location": old_loc,
        "world_time": session.world_time,
        "ticks": ticks,
        "npc_updates": len(npc_updates),
    }

@router.get("/{session_id}/summaries", response_model=SummariesResponse)
async def get_summaries(
    session_id: str,
    from_turn: int = 0,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Get compact summaries for 📕 hover display. Returns up to 3 turns."""
    session = await _get_users_session(db, session_id, user.id)
    entries = await memory_manager.get_summaries_since(db, session_id, from_turn)
    return SummariesResponse(
        session_id=session_id,
        from_turn=from_turn,
        summaries=[
            SummaryEntry(
                turn_number=e.turn_number,
                content=e.content,
                created_at=str(e.created_at) if e.created_at else None,
            )
            for e in entries
        ],
    )


# ── Relationship Graph (🚻) ──────────────────────────────────

@router.get("/{session_id}/relationship-graph")
async def get_relationship_graph(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Get the structured relationship graph for the session."""
    from ane.database.models import NPC_Relationship as _Rel
    session = await _get_users_session(db, session_id, user.id)
    result = await db.execute(
        select(_Rel).where(_Rel.session_id == session_id)
    )
    edges = []
    for rel in result.scalars().all():
        edges.append({
            "source": rel.source_name,
            "target": rel.target_name,
            "type": rel.rel_type,
            "description": rel.description,
            "affinity": rel.affinity,
        })
    return {"session_id": session_id, "edges": edges}
