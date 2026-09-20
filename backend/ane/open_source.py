# -*- coding: utf-8 -*-
"""内置包「默认开源」：把 manifest 里 `open_source: true` 的包自动发布到开源共享库。

语义（与「开源世界观共享平台」一致）：
- **开源 = 使用权限**：所有账号都能在角色创建里用这些包开局、也能在广场看到/安装它们。
- **修改权限不变**：仍是白名单管理员（`config.worldview_admin_ids`）或包作者，与开源无关。

`open_source: true` 的包由启动时（以及每次打开广场时）自动补一条 `worldview_shares`
记录，`is_official=True`（广场作者显示为「官方内置」），所以：
- 用户上传的包默认不开源，仍走设计器的「开源」按钮（作者自己推、可下架）。
- 内置开源包不可通过「下架」按钮撤销（要下架就把 manifest 的 `open_source` 去掉）。

条目必须挂在一个真实用户上（`worldview_shares.user_id` 非空且外键），这里优先挂给
白名单管理员账号，其次挂给最早注册的用户；数据库里一个用户都没有时跳过，等下次启动
或下次打开广场时再补。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ane.worldview import get as get_worldview, list_worldviews

logger = logging.getLogger(__name__)

OFFICIAL_AUTHOR = "官方内置"
OFFICIAL_TAGS = ["官方内置"]


def is_open_source(wv_id: str) -> bool:
    """包是否在 manifest 里声明了 `open_source: true`。

    注意 `get_worldview` 对未知 id 会降级返回 xianxia_v1，所以这里必须核对
    `wv.id == wv_id`，否则任意不存在的 id 都会被判成"开源"。
    """
    try:
        wv = get_worldview(wv_id)
    except Exception:  # noqa: BLE001 — 包不存在/读失败都视为未开源
        return False
    if not wv or getattr(wv, "id", None) != wv_id:
        return False
    return bool((wv.manifest or {}).get("open_source"))


def open_source_ids() -> list[str]:
    """当前目录下所有声明开源的包 id（按注册表顺序）。"""
    out: list[str] = []
    for summary in list_worldviews():
        wv_id = summary.get("id")
        if wv_id and is_open_source(wv_id):
            out.append(wv_id)
    return out


async def _official_author_id(db: AsyncSession) -> str | None:
    """给自动开源条目挑一个可挂靠的真实用户：白名单管理员优先，其次最早注册的用户。"""
    from ane.config import WORLDVIEW_ADMIN_IDS
    from ane.database.models import User

    if WORLDVIEW_ADMIN_IDS:
        row = await db.execute(
            select(User.id)
            .where(User.id.in_(list(WORLDVIEW_ADMIN_IDS)))
            .order_by(User.created_at)
            .limit(1)
        )
        uid = row.scalar_one_or_none()
        if uid:
            return uid
    row = await db.execute(select(User.id).order_by(User.created_at).limit(1))
    return row.scalar_one_or_none()


async def ensure_open_source_shares(db: AsyncSession) -> list[str]:
    """幂等地为所有 `open_source: true` 的包补齐开源广场条目。

    返回本次新发布的包 id 列表（已存在的不会重复插入，也不会覆盖作者手写的简介/标签）。
    """
    from ane.database.models import WorldviewShare

    wanted = open_source_ids()
    if not wanted:
        return []

    rows = await db.execute(
        select(WorldviewShare).where(WorldviewShare.worldview_id.in_(wanted))
    )
    existing = {s.worldview_id: s for s in rows.scalars().all()}

    added: list[str] = []
    author_id: str | None = None
    for wv_id in wanted:
        share = existing.get(wv_id)
        if share:
            # 已手动推过：仅补上"官方内置"标记，不动作者写的简介/标签。
            if not share.is_official:
                share.is_official = True
            continue
        if author_id is None:
            author_id = await _official_author_id(db)
            if author_id is None:
                logger.info(
                    "Open-source packs %s present but no user account yet — will publish later",
                    wanted,
                )
                break
        try:
            wv = get_worldview(wv_id)
        except Exception:  # noqa: BLE001
            continue
        manifest = getattr(wv, "manifest", {}) or {}
        db.add(
            WorldviewShare(
                user_id=author_id,
                worldview_id=wv_id,
                title=manifest.get("name", wv_id),
                description=manifest.get("description", ""),
                tags=list(OFFICIAL_TAGS),
                version=str(manifest.get("version", "")),
                is_official=True,
            )
        )
        added.append(wv_id)

    if added or any(not s.is_official for s in existing.values()):
        await db.commit()
    if added:
        logger.info("Open-source: published built-in packs to the shared library: %s", added)
    return added
