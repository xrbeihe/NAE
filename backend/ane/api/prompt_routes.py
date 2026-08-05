"""prompt_routes — 用户自定义提示词库 API。

一套提示词 = 前提示词（pre_prompt，注入 System 后）+ 后提示词（post_prompt，注入玩家输入后）。
用户可建多套，单选启用一套（enabled）。所有端点按 user.id 过滤，数据严格用户隔离。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from ane.auth import get_current_user
from ane.database.engine import get_db
from ane.database.models import UserPrompt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/prompts", tags=["prompts"])


# ── Schemas ─────────────────────────────────────────────────────

class PromptCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    pre_prompt: str = Field(default="", max_length=5000)
    post_prompt: str = Field(default="", max_length=5000)


class PromptUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    pre_prompt: str | None = Field(default=None, max_length=5000)
    post_prompt: str | None = Field(default=None, max_length=5000)
    enabled: bool | None = Field(default=None, description="启用本套（单选，自动取消其它套）")


# ── CRUD ────────────────────────────────────────────────────────

@router.get("")
async def list_prompts(
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """列出当前用户的提示词（含 enabled 状态）。"""
    result = await db.execute(
        select(UserPrompt)
        .where(UserPrompt.user_id == user.id)
        .order_by(UserPrompt.sort_order, UserPrompt.created_at)
    )
    out = []
    for p in result.scalars().all():
        out.append({
            "id": p.id,
            "name": p.name,
            "pre_prompt": p.pre_prompt or "",
            "post_prompt": p.post_prompt or "",
            "enabled": bool(p.enabled),
            "sort_order": p.sort_order or 0,
            "updated_at": str(p.updated_at) if p.updated_at else "",
        })
    return {"prompts": out}


@router.post("")
async def create_prompt(
    body: PromptCreateRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """新建一套提示词。"""
    existing = await db.execute(
        select(UserPrompt).where(UserPrompt.user_id == user.id, UserPrompt.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"提示词「{body.name}」已存在")

    count = await db.execute(
        select(UserPrompt).where(UserPrompt.user_id == user.id)
    )
    sort_order = count.scalars().all().__len__()

    prompt = UserPrompt(
        user_id=user.id,
        name=body.name,
        pre_prompt=body.pre_prompt,
        post_prompt=body.post_prompt,
        sort_order=sort_order,
    )
    db.add(prompt)
    await db.commit()
    await db.refresh(prompt)
    return {"id": prompt.id, "name": prompt.name}


@router.put("/{prompt_id}")
async def update_prompt(
    prompt_id: str,
    body: PromptUpdateRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """编辑内容/名称，或启用本套（enabled=true 时自动取消其它套，单选语义）。"""
    prompt = await db.get(UserPrompt, prompt_id)
    if not prompt or prompt.user_id != user.id:
        raise HTTPException(status_code=404, detail="提示词不存在")

    if body.name is not None and body.name != prompt.name:
        dup = await db.execute(
            select(UserPrompt).where(
                UserPrompt.user_id == user.id,
                UserPrompt.name == body.name,
                UserPrompt.id != prompt_id,
            )
        )
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"提示词「{body.name}」已存在")
        prompt.name = body.name

    if body.pre_prompt is not None:
        prompt.pre_prompt = body.pre_prompt
    if body.post_prompt is not None:
        prompt.post_prompt = body.post_prompt

    if body.enabled is not None:
        if body.enabled:
            # 单选：先取消当前用户其它套的启用
            await db.execute(
                update(UserPrompt)
                .where(UserPrompt.user_id == user.id, UserPrompt.id != prompt_id)
                .values(enabled=False)
            )
        prompt.enabled = body.enabled

    prompt.updated_at = datetime.utcnow()
    await db.commit()
    return {"id": prompt.id, "name": prompt.name}


@router.delete("/{prompt_id}")
async def delete_prompt(
    prompt_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """删除一套提示词。"""
    prompt = await db.get(UserPrompt, prompt_id)
    if not prompt or prompt.user_id != user.id:
        raise HTTPException(status_code=404, detail="提示词不存在")
    await db.delete(prompt)
    await db.commit()
    return {"ok": True}
