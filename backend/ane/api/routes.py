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
    NpcLibraryCreateRequest, NpcLibraryUpdateRequest,
)

import logging
from datetime import datetime
logger = logging.getLogger(__name__)


router = APIRouter(prefix="/sessions", tags=["sessions"])


# ── Static data routes (registered before /{session_id} to avoid path capture) ──

# Legacy filter list — kept for backward compatibility. New worldviews'
# world_templates.json are already curated, so filtering is only applied
# when the pack supplies no sect_filters of its own.
_NON_XIANXIA_KEYWORDS = [
    "魔法少女", "契约兽", "魔女",
    "地精", "精灵", "矮人", "兽人",
    "魔法科技", "高科技碗", "高科技",
    "便利店", "学校", "中学",
    "触手", "灾兽",
    "公国", "王国", "公主",
    "变身系统", "心形宝石",
]


def _worldview_world_templates(worldview: str | None) -> dict:
    """Return the active pack's world_templates.json."""
    from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
    wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
    if wv.world_templates:
        return wv.world_templates
    from ane.content.json_loader import load_json
    return load_json("world_templates.json")


def _is_reject_by_keywords(name: str, entry: dict, filters: list[str] | None = None) -> bool:
    keywords = filters if filters is not None else _NON_XIANXIA_KEYWORDS
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
    for kw in keywords:
        if kw in all_text:
            return True
    return False


def _sect_suffix_ok(name: str, data: dict) -> bool:
    """xianxia-style suffix filter — only applied when pack data uses it."""
    suffixes = data.get("sect_suffixes")
    if suffixes:
        return any(name.endswith(s) for s in suffixes)
    return (name.endswith("圣地") or name.endswith("宗") or name.endswith("门")
            or name.endswith("宫") or name.endswith("阁") or name.endswith("殿")
            or name.endswith("谷") or name.endswith("观") or name.endswith("派"))


def _sect_filters(data: dict) -> list[str] | None:
    filters = data.get("sect_filters")
    return filters if isinstance(filters, list) else None


@router.get("/models/sects")
async def list_sects(worldview: str | None = None):
    data = _worldview_world_templates(worldview)
    filtered = []
    for e in data.get("sects", []):
        name = e["name"]
        if not _sect_suffix_ok(name, data):
            continue
        if _is_reject_by_keywords(name, e, _sect_filters(data)):
            continue
        filtered.append(name)
    return {"sects": filtered}


@router.get("/models/sects/detail")
async def list_sects_detail(worldview: str | None = None):
    """返回宗门名 + 描述，供地图保存时展示设定用"""
    data = _worldview_world_templates(worldview)
    result = []
    for e in data.get("sects", []):
        name = e["name"]
        if not _sect_suffix_ok(name, data):
            continue
        if _is_reject_by_keywords(name, e, _sect_filters(data)):
            continue
        desc = e.get("description", "")
        result.append({
            "name": name,
            "description": desc[:200] if desc else "",
            "details": [],
        })
    return {"sects": result}


@router.get("/models/cities")
async def list_cities(worldview: str | None = None):
    data = _worldview_world_templates(worldview)
    filtered = []
    for e in data.get("settlements", []):
        name = e["name"]
        if not name.endswith("城"):
            continue
        if _is_reject_by_keywords(name, e, _sect_filters(data)):
            continue
        filtered.append(name)
    return {"cities": filtered}


@router.get("/models/cities/detail")
async def list_cities_detail(worldview: str | None = None):
    """返回城市名 + 描述，供地图保存时展示设定用"""
    data = _worldview_world_templates(worldview)
    result = []
    for e in data.get("settlements", []):
        name = e["name"]
        if not name.endswith("城"):
            continue
        if _is_reject_by_keywords(name, e, _sect_filters(data)):
            continue
        result.append({
            "name": name,
            "description": (e.get("description") or "")[:200],
        })
    return {"cities": result}


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


async def _assign_city_for_sect(
    db: AsyncSession, player, sect_name: str, worldview: str | None,
) -> None:
    """Assign a random city from the worldview pack when a sect is chosen."""
    import random as _rnd
    world_data = _worldview_world_templates(worldview)
    settlements = world_data.get("settlements", [])
    city_names = [s["name"] for s in settlements
                  if s["name"].endswith("城") and not _is_reject_by_keywords(s["name"], s, _sect_filters(world_data))]
    if city_names:
        chosen_city = _rnd.choice(city_names)
        player.location = chosen_city
        attrs = dict(player.attributes or {})
        attrs["location_hierarchy"] = f"{sect_name} → {chosen_city}"
        attrs["sect"] = sect_name
        player.attributes = attrs


# ── Session CRUD ────────────────────────────────────────────

@router.post("", response_model=CreateSessionResponse, status_code=201)
async def create_session(
    req: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    from ane.worldview import _is_valid_id, list_worldviews, DEFAULT_WORLDVIEW_ID
    if not _is_valid_id(req.worldview):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {req.worldview!r}")
    # Validate the pack exists (falls back silently inside create_session; we
    # surface an explicit error for a wrong id so the frontend can recover).
    if req.worldview != DEFAULT_WORLDVIEW_ID:
        available = {w["id"] for w in list_worldviews()}
        if req.worldview not in available:
            raise HTTPException(
                status_code=400,
                detail=f"世界观 {req.worldview!r} 不存在。可用: {sorted(available) or '（无）'}",
            )
    result = await game_engine.create_session(db, user_id=user.id, name=req.name, worldview=req.worldview, timeline=req.timeline)
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
    from ane.database.models import Player as P, NPC as N, WorldRegion, EventLog, Memory as M
    session = await _get_users_session(db, session_id, user.id)
    for model in [P, N, WorldRegion, EventLog, M]:
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

    def _sanitize(s):
        """Remove surrogate characters that break JSON serialization."""
        if not s:
            return s
        if isinstance(s, str):
            return s.encode('utf-8', 'surrogatepass').decode('utf-8', 'replace')
        return s

    conversations = [
        {"turn_number": m.turn_number, "content": _sanitize(m.content)}
        for m in full_convs
    ]
    npc_result = await db.execute(
        select(NPC).where(NPC.session_id == session_id, NPC.is_important == True)
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
        # 主角面板：前端悬浮球显示用（渲染逻辑与 game_engine step16 一致）
        player_panel=_build_player_panel(db, session, player),
        # 最新信息栏：刷新/切换会话后前端重建显示
        info_panel=await memory_manager.get_latest_info_panel(db, session_id),
    )


def _build_player_panel(db: AsyncSession, session, player) -> str:
    """Render the player panel text for a session (悬浮球主角信息)。"""
    if not player:
        return ""
    try:
        from ane.panels import render_player_panel
        from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
        wv = get_worldview(getattr(session, "worldview", None) or DEFAULT_WORLDVIEW_ID)
        panel_spec = wv.panel_spec or {}
        if panel_spec:
            return render_player_panel(player, panel_spec)
        return "【主角面板】\n" + " ｜ ".join([
            f"姓名：{player.name}",
            f"位置：{player.location or '未知'}",
        ])
    except Exception:
        return ""


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


@router.post("/{session_id}/map/select", status_code=200)
async def save_map_select(
    session_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """After map is saved, store player's sect/city choice and relocate."""
    session = await _get_users_session(db, session_id, user.id)
    chosen_sect = req.get("chosen_sect", "")
    chosen_city = req.get("chosen_city", "")

    map_data = dict(session.map_data) if session.map_data else {}
    map_data["chosen_sect"] = chosen_sect
    map_data["chosen_city"] = chosen_city
    session.map_data = map_data

    if session.map_data:
        locations = map_data.get("locations", [])
        city_locations = map_data.get("cityLocations", [])
        player = await pm.get_by_session(db, session_id)
        if player:
            player.location = chosen_city or chosen_sect or player.location
            attrs = dict(player.attributes or {})
            hierarchy_parts = []
            if chosen_sect:
                hierarchy_parts.insert(0, chosen_sect)
            if chosen_city:
                hierarchy_parts.append(chosen_city)
            attrs["location_hierarchy"] = " → ".join(hierarchy_parts) if hierarchy_parts else chosen_city
            player.attributes = attrs
    await db.commit()
    return {"ok": True, "location": chosen_city or chosen_sect}


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
    # Count only actual player turns (排除 turn=0 的角色卡)
    count_result = await db.execute(
        select(func.count()).where(
            Mem.session_id == session_id,
            Mem.memory_type == "conversation",
            Mem.turn_number > 0,
        )
    )
    turn_count = count_result.scalar() or 0
    turn_result = await game_engine.process_turn(
        db, session_id, req.input, turn_number=turn_count + 1, model=req.model,
        mark_important_npc=req.mark_important_npc,
        load_model_data=req.load_model_data,
        user_id=user.id,
        word_count_min=req.word_count_min,
        word_count_max=req.word_count_max,
        prompt_ids=req.prompt_ids,
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
        info_panel=turn_result.info_panel,
    )

@router.get("/{session_id}/templates")
async def get_character_templates(
    session_id: str,
    worldview: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    from ane.modules.player_manager import player_manager
    from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
    # __any__ → generic templates; otherwise prefer the session's worldview,
    # falling back to the ?worldview= query param.
    wv_id = worldview
    if session_id and session_id != "__any__":
        s = await db.get(WorldSession, session_id)
        if s:
            wv_id = s.worldview
    templates = player_manager.get_templates(worldview=wv_id)
    # Attach the pack's ui.json so the frontend can render labels/buttons.
    wv = get_worldview(wv_id or DEFAULT_WORLDVIEW_ID)
    result = dict(templates)
    result["ui"] = wv.ui or {}
    result["player_defaults"] = wv.player_defaults or {}
    result["form"] = wv.form  # may be None → frontend falls back to legacy form
    result["world_templates"] = wv.world_templates or {}  # for has_sects visibility etc.
    result["npc_templates"] = wv.npc_templates or {}  # NPC modeling prompt-library data (name pools / archetypes / quick picks)
    result["modeler_schema"] = wv.modeler_schema or {}  # per-worldview NPC model field tree (edits the edit dialog)
    result["world_facts"] = wv.world_facts or {}  # IP canon (incl. timelines[] for the start-time picker)
    return result


@router.post("/{session_id}/character")
async def apply_character(
    session_id: str,
    req: ApplyCharacterRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    session = await _get_users_session(db, session_id, user.id)
    worldview = getattr(session, "worldview", None)
    from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
    wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)

    # ── Resolve chosen values ──
    # Form path: pack ships form.json + frontend sends `fields` map.
    # Legacy path: explicit request fields.
    chosen_sect = ""
    if wv.form and req.fields:
        player = await pm.apply_character_from_form(
            db, session_id, dict(req.fields), worldview=worldview,
        )
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        chosen_sect = getattr(player, "_form_sect", "") or ""
    else:
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
            personality_custom=req.personality_custom,
            worldview=worldview,
        )
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")
        chosen_sect = req.chosen_sect

    # ── If a sect was chosen, assign a random city from the worldview pack ──
    if chosen_sect:
        await _assign_city_for_sect(db, player, chosen_sect, worldview)

    await db.commit()
    # ── Persist a character-creation card into conversation (survives refresh) ──
    from ane.database.models import Memory as _Memory
    attrs = dict(player.attributes or {}) if player and player.attributes else {}
    card_parts = [
        "📋 **角色创建成功**",
        f"姓名：{player.name}",
        f"性别：{attrs.get('gender', '男')} ｜ 年龄：{attrs.get('age', 19)}岁",
        f"修为：{player.cultivation}",
        f"身份：{req.identity} — {attrs.get('identity_desc', '')}",
        f"性格：{attrs.get('personality', '')}",
        f"出身：{attrs.get('background_summary', '')}",
        f"灵根：{attrs.get('spiritual_root', '未知')}",
        f"衣物：{attrs.get('clothing', '未设定')}",
    ]
    if attrs.get("sect"):
        card_parts.append(f"宗门：{attrs['sect']}")
    if player.location:
        card_parts.append(f"初始位置：{player.location}")
    if attrs.get("golden_finger_name"):
        tag = attrs.get("golden_finger_tagline", "")
        # 自定义金手指：tagline 为空但 desc 有值（自定义内容），用 desc 兜底显示
        if not tag:
            tag = attrs.get("golden_finger_desc", "")
        card_parts.append(f"金手指：{attrs['golden_finger_name']}{' — ' + tag if tag else ''}")
    if attrs.get("monthly_income"):
        card_parts.append(f"月入：{attrs['monthly_income']}")
    card_content = "\n".join(card_parts)
    db.add(_Memory(
        session_id=session_id,
        memory_type="conversation",
        content=f"【系统】{card_content}",
        turn_number=0,
    ))

    # ── Write initial recommendations ──
    # Text comes from the worldview's ui.json (author-editable); falls back
    # to the legacy xianxia wording when the pack has none.
    import json as _json
    from ane.database.models import Memory as _Mem2
    from sqlalchemy import delete as _delete

    sect_name = attrs.get("sect", "")
    has_golden_finger = bool(attrs.get("golden_finger_name"))

    ui_recs = (wv.ui or {}).get("initial_recommendations", {}) or {}
    base = ui_recs.get("base") or ["四处走走，熟悉周围的环境", "找当地人打听本地的消息", "去坊市看看有没有合适的装备或丹药"]
    with_sect = ui_recs.get("with_sect") or ["前往宗门大殿报到，领取身份令牌", "拜访同门师兄弟，结交新朋友"]
    with_gf = ui_recs.get("with_golden_finger") or ["找个安静的地方探查自己的机缘"]
    without_gf = ui_recs.get("without_golden_finger") or ["尝试感应天地灵气，熟悉自己的根骨"]
    tail = ui_recs.get("tail") or ["检查随身物品，清点灵石", "向遇到的修士打听附近的风土人情", "去藏书阁或经楼翻阅本地志"]

    recs = list(base)
    if sect_name:
        recs += list(with_sect)
    recs.append(list(with_gf)[0] if has_golden_finger else list(without_gf)[0])
    recs += list(tail)

    await db.execute(
        _delete(_Mem2).where(
            _Mem2.session_id == session_id,
            _Mem2.memory_type == "recommendations",
        )
    )
    db.add(_Mem2(
        session_id=session_id,
        memory_type="recommendations",
        content=_json.dumps(recs, ensure_ascii=False),
        turn_number=0,
    ))

    await db.commit()
    # Include golden finger info + full player profile in response
    attrs = dict(player.attributes or {}) if player and player.attributes else {}

    # ── Build player_panel matching game_engine.py step 16 format ──
    from ane.panels import render_player_panel
    from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
    wv = get_worldview(getattr(session, "worldview", None) or DEFAULT_WORLDVIEW_ID)
    panel_spec = wv.panel_spec or {}
    player_panel_str = render_player_panel(player, panel_spec) if panel_spec else (
        "【主角面板】\n" + " ｜ ".join([
            f"姓名：{player.name}",
            f"位置：{player.location or '未知'}",
        ])
    )


    return {
        "session_id": session_id,
        "player_name": player.name if player else "",
        "cultivation": player.cultivation if player else "",
        "location": player.location if player else "",
        "identity": req.identity,
        "identity_desc": attrs.get("identity_desc", ""),
        "gender": attrs.get("gender", "男"),
        "age": attrs.get("age", 19),
        "personality": attrs.get("personality", ""),
        "background_summary": attrs.get("background_summary", ""),
        "spiritual_root": attrs.get("spiritual_root", ""),
        "clothing": attrs.get("clothing", ""),
        "golden_finger_name": attrs.get("golden_finger_name", ""),
        "golden_finger_tagline": attrs.get("golden_finger_tagline", ""),
        "golden_finger_desc": attrs.get("golden_finger_desc", ""),
        "sect": attrs.get("sect", ""),
        "monthly_income": attrs.get("monthly_income", ""),
        "player_panel": player_panel_str,
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
            db, session_id, name=req.name, location=player_location,
        )
        if not db_npc:
            raise HTTPException(status_code=500, detail=f"Failed to create NPC {req.name}")
    # Mark as important — this is a player-approved modeling action
    db_npc.is_important = True

    player_name = player.name if player else "玩家"

    # Check if this is an update to an already-modeled NPC
    lts = dict(db_npc.long_term_state or {}) if isinstance(db_npc.long_term_state, dict) else {}
    existing_model = lts.get("model", {})
    if existing_model and isinstance(existing_model, dict) and existing_model.get("model_version"):
        # Run llm_cover for incremental update
        model_data = await game_engine._llm_cover(
            req.name, req.input, existing_model,
            user_id=user.id, session_id=session_id,
            worldview=getattr(session, "worldview", None),
        )
        if model_data:
            game_engine._deep_merge(existing_model, model_data)
            # Preserve the model's current version — incremental updates never
            # downgrade structure (a 1.1 model must stay 1.1, not become 1.0).
            if not existing_model.get("model_version"):
                existing_model["model_version"] = "1.1"
            lts["model"] = existing_model
            db_npc.long_term_state = lts
            basic = existing_model.get("basic", {})
            if basic.get("identity") or basic.get("title") or basic.get("occupation"):
                db_npc.identity = basic.get("identity") or basic.get("title") or basic.get("occupation")
            if basic.get("cultivation") or basic.get("level") or basic.get("rank"):
                db_npc.cultivation = basic.get("cultivation") or basic.get("level") or basic.get("rank")
            if basic.get("gender"): db_npc.gender = basic["gender"]
            if basic.get("age"): db_npc.age = int(basic["age"])
            pers_core = existing_model.get("personality", {}).get("core", "")
            if pers_core: db_npc.personality = pers_core
            model_rels = existing_model.get("relationships", {})
            if model_rels and isinstance(model_rels, dict):
                db_npc.relations = {
                    "entries": game_engine._model_rels_to_entries(model_rels),
                }
            await db.commit()
            logger.info(f"llm_cover updated for {req.name}")
        else:
            await db.commit()
            logger.warning(f"llm_cover failed for {req.name} — keeping existing data")
    else:
        # New NPC — run full modeling
        player_location = player.location if player else "未知"
        model_data = await game_engine._run_npc_modeling(
            db, req.name, req.input, db_npc,
            session_id, user.id, player_name, player_location, is_new_npc=True,
            worldview=getattr(session, "worldview", None),
        )
    return {"npc_name": req.name, "model_data": model_data or existing_model}


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


# ── Memory (short + long) ──────────────────────────────────

@router.get("/{session_id}/memories")
async def get_memories(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Return shortmemory (recent era summary) and longmemory (era) for 📕 display."""
    from ane.api.schemas import MemoryResponse, SummaryEntry as SE
    session = await _get_users_session(db, session_id, user.id)
    short = await memory_manager.get_summaries_since(db, session_id, 0)
    long = await memory_manager.get_longmemory_entries(db, session_id)
    return MemoryResponse(
        short=[SE(turn_number=e.turn_number, content=e.content) for e in short],
        long=[SE(turn_number=e.turn_number, content=e.content) for e in long],
    )


# ── Relationship Graph (🚻) ──────────────────────────────────

@router.get("/{session_id}/relationship-graph")
async def get_relationship_graph(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Get the structured relationship graph for the session.
    Only returns relationships where at least one party is an important NPC
    or an NPC with a DB record. Background passersby are excluded."""
    from ane.database.models import NPC_Relationship as _Rel, NPC as _NPC, Player as _Player
    session = await _get_users_session(db, session_id, user.id)
    result = await db.execute(
        select(_Rel).where(_Rel.session_id == session_id)
    )
    # Get the set of NPC names + player name that actually exist in the DB
    npc_result = await db.execute(
        select(_NPC.name).where(_NPC.session_id == session_id)
    )
    known_names = {row[0] for row in npc_result.fetchall()}
    # Player name is also a valid entity in the relationship graph
    player_result = await db.execute(
        select(_Player.name).where(_Player.session_id == session_id)
    )
    player_name = player_result.scalar_one_or_none()
    if player_name:
        known_names.add(player_name)
    edges = []
    for rel in result.scalars().all():
        # Skip entries from/to names that don't exist in the NPC table
        # (these are background passersby that snuck into the relationship graph)
        if rel.source_name not in known_names or rel.target_name not in known_names:
            continue
        edges.append({
            "source": rel.source_name,
            "target": rel.target_name,
            "type": rel.rel_type,
            "description": rel.description,
            "affinity": rel.affinity,
        })
    # ── Merge model-declared relationships (NPC.relations from modeling/import) ──
    # Worldview-generic: each NPC's model relationships surface in the graph
    # immediately, without waiting for the background llm_relationshipprocess.
    npc_result2 = await db.execute(
        select(_NPC.name, _NPC.relations).where(_NPC.session_id == session_id)
    )
    existing_edges = {(e["source"], e["target"], e["type"]) for e in edges}
    for row in npc_result2.fetchall():
        npc_name, rel_json = row
        entries = []
        if isinstance(rel_json, dict):
            entries = rel_json.get("entries", []) or []
        elif isinstance(rel_json, list):
            entries = rel_json
        for ent in entries:
            if not isinstance(ent, dict):
                continue
            target = ent.get("target") or ""
            rel_type = ent.get("type") or ""
            if not target:
                continue
            if (npc_name, target, rel_type) in existing_edges:
                continue
            # Model-declared relationships are explicit author intent — show
            # them even if the target isn't a DB NPC yet.
            if npc_name not in known_names:
                continue
            existing_edges.add((npc_name, target, rel_type))
            edges.append({
                "source": npc_name,
                "target": target,
                "type": rel_type,
                "description": ent.get("description", "") or ent.get("nature", ""),
                "affinity": ent.get("affinity", 0),
            })
    return {"session_id": session_id, "edges": edges, "player_name": player_name or ""}


# ── Relationship Graph: DELETE edge ──
@router.delete("/{session_id}/relationship-graph/{target_name}")
async def delete_relationship_edge(
    session_id: str,
    target_name: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Delete a relationship edge where player is source and target_name is target."""
    from ane.database.models import NPC_Relationship as _Rel, Player as _Player
    session = await _get_users_session(db, session_id, user.id)
    player_result = await db.execute(
        select(_Player.name).where(_Player.session_id == session_id)
    )
    player_name = player_result.scalar_one_or_none()
    if not player_name:
        raise HTTPException(status_code=404, detail="Player not found")
    result = await db.execute(
        select(_Rel).where(
            _Rel.session_id == session_id,
            _Rel.source_name == player_name,
            _Rel.target_name == target_name,
        )
    )
    rel = result.scalar_one_or_none()
    if not rel:
        raise HTTPException(status_code=404, detail="Relationship not found")
    await db.delete(rel)
    await db.flush()
    return {"deleted": True, "target": target_name}


# ── Important NPCs Library (😘) ─────────────────────────────────

@router.get("/{session_id}/important-npcs")
async def get_important_npcs(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Return all important NPCs with full model_data for the session."""
    from ane.database.models import NPC as NPCModel
    session = await _get_users_session(db, session_id, user.id)
    result = await db.execute(
        select(NPCModel).where(
            NPCModel.session_id == session_id,
            NPCModel.is_important == True,
        )
    )
    npcs = []
    for n in result.scalars().all():
        lts = dict(n.long_term_state or {}) if isinstance(n.long_term_state, dict) else {}
        model_data = lts.get("model", {})
        _b = model_data.get("basic", {}) if isinstance(model_data, dict) else {}
        npcs.append({
            "name": n.name,
            "gender": n.gender or _b.get("gender", ""),
            "identity": n.identity or _b.get("identity", "") or _b.get("title", "") or _b.get("occupation", ""),
            "cultivation": n.cultivation or _b.get("cultivation", "") or _b.get("level", "") or _b.get("rank", ""),
            "model_data": model_data if isinstance(model_data, dict) and model_data.get("model_version") else {},
        })
    return {"session_id": session_id, "npcs": npcs}


# ── NPC Library (跨世界总库) ─────────────────────────────────

# New router for user-level (not session-level) operations
_lib_router = APIRouter(tags=["npc-library"])

@_lib_router.get("/npcs/library")
async def list_npc_library(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """List all NPCs in the user's library (跨世界总库)."""
    from ane.database.models import UserNPC
    result = await db.execute(
        select(UserNPC).where(UserNPC.user_id == user.id).order_by(UserNPC.updated_at.desc())
    )
    npcs = []
    for n in result.scalars().all():
        npcs.append({
            "name": n.name,
            "model_data": n.model_data if isinstance(n.model_data, dict) and n.model_data.get("model_version") else {},
            "tags": n.tags or [],
            "worldview": getattr(n, "worldview", None) or "xianxia_v1",
        })
    logger.info(f"[npc-lib] LIST: user={user.id[:12]} count={len(npcs)}")
    return {"npcs": npcs}


@_lib_router.post("/npcs/library")
async def create_npc_library(
    req: NpcLibraryCreateRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
    worldview: str | None = None,
):
    """Create a new NPC in the user's library with full modeling."""
    from ane.database.models import UserNPC, NPC as NPCModel
    from ane.modules.npc_manager import npc_manager as npc_mgr
    from ane.modules.player_manager import player_manager as pm

    # 1. Extract name from input
    names = await game_engine._llm_nameget_multi(req.input, user_id=user.id)
    if not names:
        raise HTTPException(status_code=400, detail="未能从输入中提取NPC姓名")
    npc_name = names[0]

    # 2. Check for duplicate in library
    existing = await db.execute(
        select(UserNPC).where(
            UserNPC.user_id == user.id,
            UserNPC.name == npc_name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"NPC「{npc_name}」已在总库中")

    # 3. Create a temporary NPC for modeling (needed by _run_npc_modeling)
    temp_npc = NPCModel(
        session_id="", name=npc_name, location="", npc_type="named",
    )
    player_name = "玩家"

    # 4. Run full modeling (worldview = the library NPC's provenance schema)
    model_data = await game_engine._run_npc_modeling(
        db, npc_name, req.input, temp_npc,
        session_id="", user_id=user.id,
        player_name=player_name, player_location="",
        is_new_npc=True,
        worldview=worldview,
    )

    # 5. Save to user_npcs table
    # If modeling failed (model_data is None or no model_version), still create
    # the record so the user can retry later
    final_model = model_data if isinstance(model_data, dict) else {}
    if not final_model.get("model_version"):
        logger.warning(f"[npc-lib] Modeling returned no valid data for {npc_name}, creating empty record")
    user_npc = UserNPC(
        user_id=user.id,
        name=npc_name,
        model_data=final_model,
        tags=req.tags or [],
        worldview=worldview or "xianxia_v1",
    )
    db.add(user_npc)
    await db.commit()
    logger.info(f"[npc-lib] CREATE: user={user.id[:12]} name={npc_name} worldview={worldview or 'xianxia_v1'}")
    return {"name": npc_name, "model_data": model_data, "tags": req.tags or [],
            "worldview": worldview or "xianxia_v1"}


@_lib_router.put("/npcs/library/{name}")
async def update_npc_library(
    name: str,
    req: NpcLibraryUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Update an NPC in the user's library: direct replace (model_data) or AI incremental (input)."""
    from ane.database.models import UserNPC
    result = await db.execute(
        select(UserNPC).where(
            UserNPC.user_id == user.id,
            UserNPC.name == name,
        )
    )
    user_npc = result.scalar_one_or_none()
    if not user_npc:
        raise HTTPException(status_code=404, detail=f"NPC「{name}」不在总库中")

    if req.model_data is not None:
        # Direct replace — from manual edit (full structure from the editor)
        req.model_data["model_version"] = "1.1"
        user_npc.model_data = req.model_data
        if req.tags is not None:
            user_npc.tags = req.tags
        user_npc.updated_at = datetime.utcnow()
        await db.commit()
        logger.info(f"[npc-lib] DIRECT_UPDATE: user={user.id[:12]} name={name}")
        return {"name": name, "model_data": req.model_data}

    existing_model = dict(user_npc.model_data or {})

    if not existing_model.get("model_version"):
        raise HTTPException(status_code=400, detail="NPC没有建模数据，请先建模")

    # Run incremental update (AI merge)
    # Library NPCs are cross-world; the provenance schema (stored at create
    # time) drives the merge structure — the existing model still wins.
    lib_wv = getattr(user_npc, "worldview", None) or "xianxia_v1"
    updates = await game_engine._llm_cover(
        name, req.input, existing_model,
        user_id=user.id, session_id="",
        worldview=lib_wv,
    )
    if updates:
        game_engine._deep_merge(existing_model, updates)
        # Preserve current version — incremental updates never downgrade.
        if not existing_model.get("model_version"):
            existing_model["model_version"] = "1.1"
        user_npc.model_data = existing_model

        # Sync to all session NPCs that came from this library entry
        from ane.database.models import NPC as NPCModel
        synced = await db.execute(
            select(NPCModel).where(
                NPCModel.source_user_npc_id == user_npc.id,
            )
        )
        for session_npc in synced.scalars().all():
            session_lts = dict(session_npc.long_term_state or {})
            session_lts["model"] = existing_model
            session_npc.long_term_state = session_lts
            # Sync top-level columns
            basic = existing_model.get("basic", {})
            if basic.get("identity") or basic.get("title") or basic.get("occupation"):
                session_npc.identity = basic.get("identity") or basic.get("title") or basic.get("occupation")
            if basic.get("cultivation") or basic.get("level") or basic.get("rank"):
                session_npc.cultivation = basic.get("cultivation") or basic.get("level") or basic.get("rank")
            if basic.get("gender"):
                session_npc.gender = basic["gender"]
            if basic.get("age"):
                session_npc.age = int(basic["age"])
            pers_core = existing_model.get("personality", {}).get("core", "")
            if pers_core:
                session_npc.personality = pers_core

    if req.tags is not None:
        user_npc.tags = req.tags
    user_npc.updated_at = datetime.utcnow()
    await db.commit()
    logger.info(f"[npc-lib] UPDATE: user={user.id[:12]} name={name}")
    return {"name": name, "model_data": existing_model}


@_lib_router.delete("/npcs/library/{name}")
async def delete_npc_library(
    name: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Delete an NPC from the user's library."""
    from ane.database.models import UserNPC
    result = await db.execute(
        select(UserNPC).where(
            UserNPC.user_id == user.id,
            UserNPC.name == name,
        )
    )
    user_npc = result.scalar_one_or_none()
    if not user_npc:
        raise HTTPException(status_code=404, detail=f"NPC「{name}」不在总库中")
    await db.delete(user_npc)
    await db.commit()
    logger.info(f"[npc-lib] DELETE: user={user.id[:12]} name={name}")
    return {"deleted": name}


# ── Import/Export (总库 ↔ 当前世界) ─────────────────────

@router.get("/{session_id}/npcs/imported")
async def list_imported_npcs(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """List NPCs in the current session that were imported from the user library."""
    from ane.database.models import NPC as NPCModel
    session = await _get_users_session(db, session_id, user.id)
    result = await db.execute(
        select(NPCModel).where(
            NPCModel.session_id == session_id,
            NPCModel.source_user_npc_id.isnot(None),
        )
    )
    npcs = []
    for n in result.scalars().all():
        lts = dict(n.long_term_state or {}) if isinstance(n.long_term_state, dict) else {}
        model_data = lts.get("model", {})
        npcs.append({
            "name": n.name,
            "source_user_npc_id": n.source_user_npc_id,
            "model_data": model_data if isinstance(model_data, dict) and model_data.get("model_version") else {},
        })
    return {"npcs": npcs}


@router.post("/{session_id}/npcs/import/{name}")
async def import_npc_to_session(
    session_id: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Import an NPC from the user's library into the current session."""
    from ane.database.models import UserNPC, NPC as NPCModel
    from ane.modules.npc_manager import npc_manager as npc_mgr
    from ane.modules.player_manager import player_manager as pm

    session = await _get_users_session(db, session_id, user.id)

    # 1. Get from library
    lib_result = await db.execute(
        select(UserNPC).where(
            UserNPC.user_id == user.id,
            UserNPC.name == name,
        )
    )
    user_npc = lib_result.scalar_one_or_none()
    if not user_npc:
        raise HTTPException(status_code=404, detail=f"NPC「{name}」不在总库中")

    # 2. Check if already in session
    existing = await db.execute(
        select(NPCModel).where(
            NPCModel.session_id == session_id,
            NPCModel.name == name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"NPC「{name}」已在当前世界中")

    # 3. Get current player location
    player = await pm.get_by_session(db, session_id)
    player_loc = player.location if player else ""

    # 4. Copy library model_data into NPC
    model_data = dict(user_npc.model_data or {})
    _basic = model_data.get("basic", {}) if isinstance(model_data, dict) else {}
    # Worldview-generic: xianxia schema uses basic.identity/cultivation/gender/age;
    # other schemas (fantasy → title/rank, etc.) fall back to the raw value.
    _identity = _basic.get("identity") or _basic.get("title") or _basic.get("occupation") or ""
    _gender = _basic.get("gender") or ""
    _age = _basic.get("age")
    npc = NPCModel(
        session_id=session_id,
        name=name,
        identity=_identity,
        cultivation=_basic.get("cultivation") or _basic.get("level") or _basic.get("rank") or "",
        gender=_gender,
        age=int(_age) if _age else None,
        location=player_loc,
        npc_type="named",
        is_important=True,
        source_user_npc_id=user_npc.id,
    )
    # Write model data
    lts = dict(npc.long_term_state or {})
    lts["model"] = model_data
    lts["pending_debut"] = True
    npc.long_term_state = lts
    # Sync relationships from model → NPC.relations (generic parser handles
    # any schema's relationship keys)
    model_rels = model_data.get("relationships") if isinstance(model_data, dict) else None
    if model_rels and isinstance(model_rels, dict):
        npc.relations = {"entries": game_engine._model_rels_to_entries(model_rels)}
    db.add(npc)
    await db.commit()
    logger.info(f"[npc-lib] IMPORT: user={user.id[:12]} session={session_id[:12]} name={name}")
    return {"name": name, "imported": True}


@router.delete("/{session_id}/npcs/import/{name}")
async def remove_imported_npc(
    session_id: str,
    name: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    """Remove an imported NPC from the current session (does not delete from library)."""
    from ane.database.models import NPC as NPCModel
    session = await _get_users_session(db, session_id, user.id)
    result = await db.execute(
        select(NPCModel).where(
            NPCModel.session_id == session_id,
            NPCModel.name == name,
            NPCModel.source_user_npc_id.isnot(None),
        )
    )
    npc = result.scalar_one_or_none()
    if not npc:
        raise HTTPException(status_code=404, detail=f"NPC「{name}」未在当前世界中或不是从总库导入")
    await db.delete(npc)
    await db.commit()
    logger.info(f"[npc-lib] REMOVE: user={user.id[:12]} session={session_id[:12]} name={name}")
    return {"name": name, "removed": True}
