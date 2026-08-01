"""card_routes — 角色卡制作工具 API。

独立于 NPC 建模链：角色卡（UserCard）由结构化表单制作，不经过 LLM 建模。
端点：schema（表单源）/ CRUD / import（可选从总库预填）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy import select

from ane.auth import get_current_user
from ane.database.engine import get_db
from ane.database.models import UserCard, UserNPC
from ane.modules.card_schema import (
    CARD_LABELS,
    CARD_SCHEMA,
    CARD_SELECTS,
    normalize_card,
    render_card_preview,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cards", tags=["cards"])


# ── Schemas ─────────────────────────────────────────────────────

class CardCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    card_data: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class CardUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    card_data: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class CardImportRequest(BaseModel):
    source_npc_id: str = Field(..., min_length=1)
    name: str | None = Field(default=None, max_length=50)


# ── Schema（必须先注册，避免被 /{card_id} 动态参数捕获）────────

@router.get("/schema")
async def get_card_schema(
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """编辑器表单源：字段树 + 中文标签 + 下拉选项。"""
    return {
        "schema": CARD_SCHEMA,
        "labels": CARD_LABELS,
        "selects": CARD_SELECTS,
    }


# ── CRUD ────────────────────────────────────────────────────────

@router.get("")
async def list_cards(
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """列出当前用户的角色卡。"""
    result = await db.execute(
        select(UserCard).where(UserCard.user_id == user.id).order_by(UserCard.updated_at.desc())
    )
    out = []
    for c in result.scalars().all():
        data = c.card_data if isinstance(c.card_data, dict) else {}
        rel = (data.get("initial_relationship") or {}).get("type", "")
        cling = (data.get("clinginess") or {}).get("level", "")
        persona = (data.get("identity") or {}).get("persona", "")
        out.append({
            "id": c.id,
            "name": c.name,
            "tags": c.tags or [],
            "initial_relationship": rel,
            "clinginess": cling,
            "preview": persona or render_card_preview(data).split("\n")[0],
            "updated_at": str(c.updated_at) if c.updated_at else "",
        })
    return {"cards": out}


@router.post("")
async def create_card(
    body: CardCreateRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """新建角色卡。card_data 经 normalize 补全缺省字段。"""
    existing = await db.execute(
        select(UserCard).where(UserCard.user_id == user.id, UserCard.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"角色卡「{body.name}」已存在")

    card = UserCard(
        user_id=user.id,
        name=body.name,
        card_data=normalize_card(body.card_data),
        tags=body.tags or [],
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return {"id": card.id, "name": card.name}


@router.get("/{card_id}")
async def get_card(
    card_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """读取完整角色卡（编辑器用）。"""
    card = await db.get(UserCard, card_id)
    if not card or card.user_id != user.id:
        raise HTTPException(status_code=404, detail="角色卡不存在")
    return {
        "id": card.id,
        "name": card.name,
        "card_data": card.card_data or {},
        "tags": card.tags or [],
        "updated_at": str(card.updated_at) if card.updated_at else "",
    }


@router.put("/{card_id}")
async def update_card(
    card_id: str,
    body: CardUpdateRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """整卡替换保存。"""
    card = await db.get(UserCard, card_id)
    if not card or card.user_id != user.id:
        raise HTTPException(status_code=404, detail="角色卡不存在")
    # 查重（改名时避免撞名）
    dup = await db.execute(
        select(UserCard).where(
            UserCard.user_id == user.id,
            UserCard.name == body.name,
            UserCard.id != card_id,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"角色卡「{body.name}」已存在")

    card.name = body.name
    card.card_data = normalize_card(body.card_data)
    card.tags = body.tags or []
    await db.commit()
    return {"id": card.id, "name": card.name}


@router.delete("/{card_id}")
async def delete_card(
    card_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """删除角色卡。活跃会话持有创建时快照，不受影响。"""
    card = await db.get(UserCard, card_id)
    if not card or card.user_id != user.id:
        raise HTTPException(status_code=404, detail="角色卡不存在")
    await db.delete(card)
    await db.commit()
    return {"ok": True}


# ── Import（可选：从 NPC 总库预填 common 字段）────────────────

@router.post("/import")
async def import_from_npc(
    body: CardImportRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """从 UserNPC 预填角色卡 common 字段（手动物理映射，非自动批量）。

    basic → identity，personality → personality，appearance → appearance。
    用户可在编辑器继续细化。
    """
    npc = await db.get(UserNPC, body.source_npc_id)
    if not npc or npc.user_id != user.id:
        raise HTTPException(status_code=404, detail="NPC 不存在")
    md = npc.model_data if isinstance(npc.model_data, dict) else {}
    basic = md.get("basic") or {}
    pers = md.get("personality") or {}
    appr = md.get("appearance") or {}
    sp = md.get("speech_style") or {}

    card_data = normalize_card({
        "identity": {
            "name": basic.get("name", npc.name),
            "gender": basic.get("gender", ""),
            "age": str(basic.get("age", "")) if basic.get("age") else "",
            "occupation": basic.get("identity", basic.get("occupation", "")),
            "persona": basic.get("identity", ""),
            "background": (md.get("background") or {}).get("history", ""),
        },
        "appearance": {
            "overall_impression": appr.get("overall_impression", ""),
        },
        "personality": {
            "core": pers.get("core", ""),
            "values": pers.get("values", ""),
            "likes": pers.get("likes", ""),
            "dislikes": pers.get("dislikes", ""),
            "fears": pers.get("fears", ""),
        },
        "speech_style": {
            "address_you": sp.get("address_player", ""),
        },
    })

    name = body.name or npc.name
    card = UserCard(
        user_id=user.id,
        name=name,
        card_data=card_data,
        tags=md.get("tags", []),
        source_user_npc_id=npc.id,
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return {"id": card.id, "name": card.name}


# ── 小说 → 角色卡（llm_read 管线）──────────────────────────────

@router.post("/from-novel")
async def from_novel_upload(
    file: UploadFile = File(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """上传小说 txt，提取候选角色列表。文件 ≤8MB。"""
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大（上限 8MB）")
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")

    from ane.modules.card_from_novel import read_novel_text, extract_characters
    text = read_novel_text(raw)
    if len(text.strip()) < 200:
        raise HTTPException(status_code=400, detail="文本过短，无法提取角色")

    characters = await extract_characters(text, user_id=user.id)
    if not characters:
        raise HTTPException(status_code=502, detail="未能从小说中提取角色，请重试")

    return {
        "characters": characters,
        "total_chars": len(text),
        "filename": file.filename,
    }


@router.post("/from-novel/character")
async def from_novel_generate(
    file: UploadFile = File(...),
    character: str = Form(..., min_length=1, max_length=50),
    relationship_note: str = Form("", max_length=100),
    name: str | None = Form(None, max_length=50),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """选定角色后，读小说抽样片段，生成角色卡并存入 UserCard。

    multipart 表单：file（txt）+ character（角色名）+ relationship_note（可选）
    + name（可选，卡片名，默认用角色名）。
    """
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大（上限 8MB）")

    from ane.modules.card_from_novel import (
        read_novel_text,
        sample_character,
        generate_card_from_sample,
    )
    text = read_novel_text(raw)
    sample = sample_character(text, character)
    card_data = await generate_card_from_sample(
        character,
        sample,
        relationship_note=relationship_note,
        user_id=user.id,
    )
    if not card_data or not card_data.get("identity", {}).get("name"):
        raise HTTPException(status_code=502, detail="角色卡生成失败，请重试")

    name = name or character
    existing = await db.execute(
        select(UserCard).where(UserCard.user_id == user.id, UserCard.name == name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"角色卡「{name}」已存在，可改名后重试")

    card = UserCard(
        user_id=user.id,
        name=name,
        card_data=card_data,
        tags=["小说生成"],
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return {"id": card.id, "name": card.name}
