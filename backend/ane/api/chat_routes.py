"""chat_routes — 1v1 虚拟角色陪伴对话 API。

独立于主会话路由（/sessions/*），提供：
  - 角色卡选择列表（复用 UserNPC 总库）
  - 开启 1v1 会话
  - 发消息 / 拉历史 / 列会话
数据由 companion_engine 驱动，不进入世界管线。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ane.auth import get_current_user
from ane.companion_engine import companion_engine
from ane.database.engine import get_db
from ane.database.models import UserNPC, WorldSession, NPC

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


# ── Schemas ─────────────────────────────────────────────────────

class CompanionStartRequest(BaseModel):
    npc_id: str | None = Field(default=None, min_length=1, max_length=64)
    card_id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(default="未命名对话", max_length=100)


class CompanionMessageRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=4000)
    model: str | None = None


class CompanionMessageResponse(BaseModel):
    reply: str
    emotion: str = ""
    relationship_note: str = ""
    npc_name: str = ""
    prompt: str = ""


class NudgeSettingsRequest(BaseModel):
    idle_seconds: float = Field(..., ge=0, le=86400)


# ── 角色卡（UserNPC 总库 + UserCard 角色卡）────────────────────

@router.get("/characters")
async def list_characters(
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """列出可选的陪伴角色：UserNPC 总库条目 + UserCard 角色卡条目。

    UserCard 条目带 source:"card"、card_id、initial_relationship.type 摘要。
    """
    out = []

    # UserNPC 总库条目
    result = await db.execute(
        select(UserNPC).where(UserNPC.user_id == user.id).order_by(UserNPC.updated_at.desc())
    )
    for n in result.scalars().all():
        model = n.model_data if isinstance(n.model_data, dict) else {}
        basic = model.get("basic") or {}
        out.append({
            "id": n.id,
            "name": n.name,
            "source": "npc",
            "tags": n.tags or [],
            "basic": {
                "gender": basic.get("gender", ""),
                "identity": basic.get("identity", ""),
                "cultivation": basic.get("cultivation", ""),
                "age": basic.get("age", ""),
            },
            "initial_relationship": "",
            "updated_at": str(n.updated_at) if n.updated_at else "",
        })

    # UserCard 角色卡条目（从 /cards 语义一致，这里直接查表避免循环依赖）
    from ane.database.models import UserCard
    card_result = await db.execute(
        select(UserCard).where(UserCard.user_id == user.id).order_by(UserCard.updated_at.desc())
    )
    for c in card_result.scalars().all():
        data = c.card_data if isinstance(c.card_data, dict) else {}
        rel = (data.get("initial_relationship") or {}).get("type", "")
        cling = (data.get("clinginess") or {}).get("level", "")
        persona = (data.get("identity") or {}).get("persona", "")
        out.append({
            "id": c.id,
            "card_id": c.id,
            "name": c.name,
            "source": "card",
            "tags": c.tags or [],
            "basic": {
                "gender": (data.get("identity") or {}).get("gender", ""),
                "identity": persona,
                "cultivation": "",
                "age": (data.get("identity") or {}).get("age", ""),
            },
            "initial_relationship": rel,
            "clinginess": cling,
            "updated_at": str(c.updated_at) if c.updated_at else "",
        })

    return {"characters": out}


# ── 会话 ─────────────────────────────────────────────────────────

@router.post("/sessions")
async def start_companion(
    body: CompanionStartRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """开启一个 1v1 会话：card_id（角色卡）或 npc_id（总库），二选一。"""
    if not body.card_id and not body.npc_id:
        raise HTTPException(status_code=400, detail="需提供 card_id 或 npc_id")
    try:
        if body.card_id:
            session = await companion_engine.create_companion_session_from_card(
                db, user.id, body.card_id, name=body.name,
            )
        else:
            session = await companion_engine.create_companion_session(
                db, user.id, body.npc_id, name=body.name,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return session


@router.get("/sessions")
async def list_companion_sessions(
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """列出当前用户的 1v1 会话。"""
    result = await db.execute(
        select(WorldSession)
        .where(WorldSession.user_id == user.id, WorldSession.worldview == "companion_v1")
        .order_by(WorldSession.created_at.desc())
    )
    return {"sessions": [{"session_id": s.id, "name": s.name, "created_at": str(s.created_at) if s.created_at else ""} for s in result.scalars().all()]}


@router.get("/sessions/{session_id}")
async def get_companion_session(
    session_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """拉取一个 1v1 会话的完整对话历史 + 角色形象（头像/背景）。"""
    session = await db.get(WorldSession, session_id)
    if not session or session.user_id != user.id or session.worldview != "companion_v1":
        raise HTTPException(status_code=404, detail="会话不存在")
    history = await companion_engine.get_history(db, session_id)

    # 角色形象：会话内 NPC 的 long_term_state["model"]["visual"]
    visual = {}
    npc_result = await db.execute(
        select(NPC).where(NPC.session_id == session_id, NPC.is_important == True)
    )
    npc = npc_result.scalars().first()
    if npc and isinstance(npc.long_term_state, dict):
        model = npc.long_term_state.get("model")
        if isinstance(model, dict):
            visual = (model.get("visual") or {})

    return {
        "session_id": session_id,
        "name": session.name,
        "history": history,
        "visual": {
            "avatar": visual.get("avatar", ""),
            "background": visual.get("background", ""),
        },
    }


@router.get("/sessions/{session_id}/memories")
async def get_companion_memories(
    session_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """拉取一个 1v1 会话的关系记忆（「TA 记得什么」面板）。"""
    session = await db.get(WorldSession, session_id)
    if not session or session.user_id != user.id or session.worldview != "companion_v1":
        raise HTTPException(status_code=404, detail="会话不存在")
    memories = await companion_engine.get_relationship_memory(db, session_id)
    return {"memories": memories}


@router.get("/sessions/{session_id}/nudge-settings")
async def get_nudge_settings(
    session_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """读取当前会话角色的主动搭话阈值（秒）。"""
    session = await db.get(WorldSession, session_id)
    if not session or session.user_id != user.id or session.worldview != "companion_v1":
        raise HTTPException(status_code=404, detail="会话不存在")
    return await companion_engine.get_nudge_settings(db, session_id)


@router.put("/sessions/{session_id}/nudge-settings")
async def set_nudge_settings(
    session_id: str,
    body: NudgeSettingsRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """设置主动搭话阈值（秒）：越小越粘人。0=几乎总是主动，86400=几乎不主动。"""
    session = await db.get(WorldSession, session_id)
    if not session or session.user_id != user.id or session.worldview != "companion_v1":
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        return await companion_engine.set_nudge_settings(db, session_id, body.idle_seconds)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sessions/{session_id}/nudge")
async def nudge_companion(
    session_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """角色主动搭话轮询。

    距最后对话超阈值（默认30分钟）且距上次主动搭话也超阈值时，
    生成一句角色主动的话返回；否则返回 null。
    """
    session = await db.get(WorldSession, session_id)
    if not session or session.user_id != user.id or session.worldview != "companion_v1":
        raise HTTPException(status_code=404, detail="会话不存在")
    result = await companion_engine.nudge(db, session_id, user_id=user.id)
    return {"nudge": result}


@router.post("/sessions/{session_id}/message", response_model=CompanionMessageResponse)
async def send_message(
    session_id: str,
    body: CompanionMessageRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """发送一条消息，得到角色回复。"""
    session = await db.get(WorldSession, session_id)
    if not session or session.user_id != user.id or session.worldview != "companion_v1":
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        result = await companion_engine.process_chat(
            db, session_id, body.input, user_id=user.id, model=body.model,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.delete("/sessions/{session_id}")
async def delete_companion_session(
    session_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """删除一个 1v1 会话（级联删除关联 NPC/记忆）。"""
    session = await db.get(WorldSession, session_id)
    if not session or session.user_id != user.id or session.worldview != "companion_v1":
        raise HTTPException(status_code=404, detail="会话不存在")
    from ane.database.models import Memory as _Mem
    from ane.database.models import NPC as _NPC
    from sqlalchemy import delete
    await db.execute(delete(_Mem).where(_Mem.session_id == session_id))
    await db.execute(delete(_NPC).where(_NPC.session_id == session_id))
    await db.delete(session)
    await db.commit()
    return {"ok": True}
