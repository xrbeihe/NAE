"""Worldview registry routes — list, validate, reload, upload, delete packs."""

import io
import json
import shutil
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from ane.auth import get_optional_user
from ane.database.engine import get_db
from ane.worldview import (
    list_worldviews,
    reload as reload_pack,
    validate_pack,
    read_form,
    write_form,
    read_ui,
    write_ui,
    read_artifact,
    write_artifact,
    _is_valid_id,
    DEFAULT_WORLDVIEW_ID,
    WORLDVIEWS_DIR,
)

router = APIRouter(prefix="/worldviews", tags=["worldviews"])


@router.get("")
async def get_worldviews():
    """List all installed worldview packs (manifest summaries)."""
    return {"worldviews": list_worldviews()}


@router.get("/{worldview_id}/validate")
async def validate_worldview(worldview_id: str):
    """Validate a pack and return a structured error/warning report."""
    if not _is_valid_id(worldview_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {worldview_id!r}")
    return validate_pack(worldview_id)


@router.get("/{worldview_id}/form")
async def get_worldview_form(worldview_id: str):
    """Return the pack's form.json (the declarative character-creation form)."""
    if not _is_valid_id(worldview_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {worldview_id!r}")
    if not (WORLDVIEWS_DIR / worldview_id).is_dir():
        raise HTTPException(status_code=404, detail=f"世界观 {worldview_id} 不存在")
    return {"worldview": worldview_id, "form": read_form(worldview_id)}


@router.put("/{worldview_id}/form")
async def put_worldview_form(
    worldview_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """Save the pack's form.json (author edits the character-creation form)."""
    if not _is_valid_id(worldview_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {worldview_id!r}")
    form = body.get("form") if isinstance(body, dict) else None
    if not isinstance(form, dict):
        raise HTTPException(status_code=400, detail="请求需包含 form 对象")
    try:
        return write_form(worldview_id, form)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{worldview_id}/ui")
async def get_worldview_ui(worldview_id: str):
    """Return the pack's ui.json (labels/buttons/initial recommendations)."""
    if not _is_valid_id(worldview_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {worldview_id!r}")
    if not (WORLDVIEWS_DIR / worldview_id).is_dir():
        raise HTTPException(status_code=404, detail=f"世界观 {worldview_id} 不存在")
    return {"worldview": worldview_id, "ui": read_ui(worldview_id)}


@router.put("/{worldview_id}/ui")
async def put_worldview_ui(
    worldview_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """Save the pack's ui.json (author edits frontend copy / recommendations)."""
    if not _is_valid_id(worldview_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {worldview_id!r}")
    ui = body.get("ui") if isinstance(body, dict) else None
    if not isinstance(ui, dict):
        raise HTTPException(status_code=400, detail="请求需包含 ui 对象")
    try:
        return write_ui(worldview_id, ui)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{worldview_id}/reload")
async def reload_worldview(worldview_id: str):
    """Drop the loader cache for a pack (after editing files on disk)."""
    if not _is_valid_id(worldview_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {worldview_id!r}")
    reload_pack(worldview_id)
    return {"reloaded": worldview_id}


@router.post("/generate")
async def generate_worldview(
    body: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """Generate a complete worldview pack from a short author form.

    The generic narrative kernel is auto-applied by the engine (shell+kernel),
    so the author only describes the world. Returns an installable zip.
    """
    from ane.modules.pack_generator import generate_pack_zip
    try:
        zip_bytes = generate_pack_zip(body or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    wv_id = ((body or {}).get("id") or "new_world").strip()
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{wv_id}.zip"'},
    )


@router.post("/upload")
async def upload_worldview(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """Upload a worldview pack zip and install it to worldviews/<id>/.

    The zip must contain a top-level manifest.json (or a single top-level
    directory whose name is a valid worldview id and contains manifest.json).
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        names = zf.namelist()
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="不是有效的 zip 文件")

    if not names:
        raise HTTPException(status_code=400, detail="zip 为空")

    # Determine the pack root: either files at zip root, or one top-level dir.
    top_levels = {n.split("/")[0] for n in names if n.split("/")[0]}
    has_root_manifest = any(n == "manifest.json" or n.startswith("./manifest.json") for n in names)
    if has_root_manifest:
        prefix = ""
        wv_id = None
    elif len(top_levels) == 1:
        prefix = top_levels.pop() + "/"
        wv_id = prefix.rstrip("/")
    else:
        raise HTTPException(status_code=400, detail="zip 需包含顶层 manifest.json 或单一顶层目录")

    # Find the manifest to determine wv_id if not from the top-level dir.
    manifest_name = prefix + "manifest.json"
    try:
        manifest_raw = zf.read(manifest_name)
    except KeyError:
        raise HTTPException(status_code=400, detail="zip 缺少 manifest.json")
    try:
        manifest = json.loads(manifest_raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="manifest.json 不是合法 JSON")

    wv_id = wv_id or (manifest.get("worldview_id") or "").strip()
    if not _is_valid_id(wv_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {wv_id!r}（manifest.worldview_id 缺失或非法）")

    if wv_id == DEFAULT_WORLDVIEW_ID:
        raise HTTPException(status_code=400, detail="不允许覆盖默认世界观")

    # Security: reject any path escaping the target dir.
    target = (WORLDVIEWS_DIR / wv_id).resolve()
    if not str(target).startswith(str(WORLDVIEWS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="非法的包路径")

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    allowed_suffix = (".json", ".txt", ".md")
    written = 0
    for n in names:
        if n.endswith("/") or not n.startswith(prefix):
            continue
        rel = n[len(prefix):]
        dest = (target / rel).resolve()
        if not str(dest).startswith(str(target)):
            continue  # path traversal — skip
        if not any(rel.endswith(s) for s in allowed_suffix):
            continue  # only text/json files
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(zf.read(n))
        written += 1

    # Validate the installed pack
    report = validate_pack(wv_id)
    reload_pack(wv_id)
    return {
        "installed": wv_id,
        "files_written": written,
        "manifest": {"name": manifest.get("name", wv_id), "version": manifest.get("version", "")},
        "validation": report,
    }


# Whitelisted pack data files editable via the generic artifact endpoints.
# `modeler/schema.json` lives in a subdir — write_artifact whitelists it explicitly.
_EDITABLE_ARTIFACTS = {
    "player_templates.json",
    "world_templates.json",
    "npc_templates.json",
    "constraints.json",
    "intent_keywords.json",
    "events.json",
    "panel.json",
    "world_facts.json",
    "modeler/schema.json",
}


@router.get("/{worldview_id}/data/{filename}")
async def get_worldview_data(worldview_id: str, filename: str):
    """Read a pack JSON artifact (player/world/npc templates, constraints, …)."""
    if not _is_valid_id(worldview_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {worldview_id!r}")
    if filename not in _EDITABLE_ARTIFACTS:
        raise HTTPException(status_code=400, detail=f"不允许读取该文件: {filename!r}")
    return {"worldview": worldview_id, "file": filename, "data": read_artifact(worldview_id, filename)}


@router.put("/{worldview_id}/data/{filename}")
async def put_worldview_data(
    worldview_id: str,
    filename: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """Save a pack JSON artifact (author edits templates/constraints)."""
    if not _is_valid_id(worldview_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {worldview_id!r}")
    if filename not in _EDITABLE_ARTIFACTS:
        raise HTTPException(status_code=400, detail=f"不允许写入该文件: {filename!r}")
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求需包含 data 对象")
    try:
        return write_artifact(worldview_id, filename, data)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/share")
async def unshare_worldview(
    worldview_id: str = "",
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """Remove a worldview from the shared library (author only).

    Registered before `DELETE /{worldview_id}` so `/share` isn't captured by
    the dynamic path param.
    """
    from sqlalchemy import select
    from ane.database.models import WorldviewShare
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    wv_id = worldview_id.strip()
    if not wv_id:
        raise HTTPException(status_code=400, detail="缺少 worldview_id")
    existing = await db.execute(
        select(WorldviewShare).where(WorldviewShare.worldview_id == wv_id)
    )
    share = existing.scalar_one_or_none()
    if not share:
        raise HTTPException(status_code=404, detail=f"世界观 {wv_id} 不在共享库")
    if share.user_id != user.id:
        raise HTTPException(status_code=403, detail="只能撤销自己推送的世界观")
    await db.delete(share)
    await db.commit()
    return {"unshared": wv_id}


@router.delete("/{worldview_id}")
async def delete_worldview(
    worldview_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """Delete an installed pack (default pack is protected)."""
    if not _is_valid_id(worldview_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {worldview_id!r}")
    if worldview_id == DEFAULT_WORLDVIEW_ID:
        raise HTTPException(status_code=400, detail="不允许删除默认世界观")

    target = WORLDVIEWS_DIR / worldview_id
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"世界观 {worldview_id} 不存在")

    shutil.rmtree(target)
    reload_pack(worldview_id)
    return {"deleted": worldview_id}


# ── 开源共享库（worldview sharing platform）──────────────
# 所有已登录用户都能推送自己的世界观（公开共享）、浏览他人开源的世界观、
# 评分，以及一键安装使用。

@router.post("/share")
async def share_worldview(
    req: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """Push an installed worldview to the public shared library.

    Body: {worldview_id, title?, description?, tags?: []}
    Tags are free-form short labels chosen by the author.
    """
    from sqlalchemy import select
    from ane.database.models import WorldviewShare
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    wv_id = (req.get("worldview_id") or "").strip()
    if not _is_valid_id(wv_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {wv_id!r}")
    if wv_id == DEFAULT_WORLDVIEW_ID:
        raise HTTPException(status_code=400, detail="默认世界观不需要开源共享")

    # The pack must be installed on disk.
    pack_dir = WORLDVIEWS_DIR / wv_id
    if not pack_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"世界观 {wv_id} 不存在")

    title = (req.get("title") or "").strip() or wv_id
    description = (req.get("description") or "").strip()
    tags_raw = req.get("tags") or []
    tags = [str(t).strip() for t in tags_raw if str(t).strip()][:12]

    existing = await db.execute(
        select(WorldviewShare).where(WorldviewShare.worldview_id == wv_id)
    )
    share = existing.scalar_one_or_none()
    if share:
        # Re-push updates the share metadata (author re-publishes).
        share.title = title
        share.description = description
        share.tags = tags
        share.version = (req.get("version") or "").strip() or share.version
        share.updated_at = datetime.utcnow()
        share.user_id = user.id
    else:
        from ane.worldview import get as get_worldview
        wv = get_worldview(wv_id)
        share = WorldviewShare(
            user_id=user.id,
            worldview_id=wv_id,
            title=title,
            description=description,
            tags=tags,
            version=(req.get("version") or "").strip() or str(wv.manifest.get("version", "")),
        )
        db.add(share)
    await db.commit()
    return {"shared": wv_id, "title": title, "tags": tags}


@router.get("/shared")
async def list_shared_worldviews(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """List all open-sourced worldviews with avg rating + rating count."""
    from sqlalchemy import select, func
    from ane.database.models import WorldviewShare, WorldviewRating, User
    result = await db.execute(select(WorldviewShare).order_by(WorldviewShare.updated_at.desc()))
    shares = result.scalars().all()

    # Batch-load author names (avoid SQLAlchemy lazy-load in async — would raise MissingGreenlet).
    author_ids = {s.user_id for s in shares}
    author_names = {}
    if author_ids:
        users = await db.execute(select(User.id, User.username, User.display_name).where(User.id.in_(author_ids)))
        for uid, uname, dname in users.fetchall():
            author_names[uid] = dname or uname

    # Aggregate ratings per worldview_id.
    rating_rows = await db.execute(
        select(
            WorldviewRating.worldview_id,
            func.count(WorldviewRating.id),
            func.avg(WorldviewRating.rating),
        ).group_by(WorldviewRating.worldview_id)
    )
    agg = {wv_id: (cnt, avg) for wv_id, cnt, avg in rating_rows.fetchall()}

    # Whether the current user has installed this pack locally (worldview on disk).
    installed_ids = {w["id"] for w in list_worldviews()}
    # Whether the current user rated it.
    my_ratings = set()
    if user:
        mine = await db.execute(
            select(WorldviewRating.worldview_id).where(WorldviewRating.user_id == user.id)
        )
        my_ratings = {r[0] for r in mine.fetchall()}

    items = []
    for s in shares:
        cnt, avg = agg.get(s.worldview_id, (0, 0))
        author_name = author_names.get(s.user_id, "")
        # 详细历史（lore）从该包 world_facts.json 读取——作者在设计器写的科普文本
        from ane.worldview import get as _get_wv
        lore = ""
        try:
            wv = _get_wv(s.worldview_id)
            wf = getattr(wv, "world_facts", None) or {}
            lore = wf.get("lore") or ""
        except Exception:
            lore = ""
        items.append({
            "worldview_id": s.worldview_id,
            "title": s.title or s.worldview_id,
            "description": s.description,
            "lore": lore,
            "tags": s.tags or [],
            "version": s.version,
            "author": author_name,
            "created_at": s.created_at.isoformat() if s.created_at else "",
            "rating_count": int(cnt),
            "avg_rating": round(float(avg), 1) if avg else 0.0,
            "installed": s.worldview_id in installed_ids,
            "mine": user is not None and s.user_id == user.id,
            "my_rating": s.worldview_id in my_ratings,
        })
    return {"worldviews": items}


@router.post("/shared/{worldview_id}/rate")
async def rate_shared_worldview(
    worldview_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """Rate a shared worldview 1-5 stars. Re-rating replaces the old value."""
    from sqlalchemy import select
    from ane.database.models import WorldviewRating
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    rating = req.get("rating")
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="评分必须是 1-5 的整数")
    if rating < 1 or rating > 5:
        raise HTTPException(status_code=400, detail="评分必须是 1-5 的整数")

    existing = await db.execute(
        select(WorldviewRating).where(
            WorldviewRating.user_id == user.id,
            WorldviewRating.worldview_id == worldview_id,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.rating = rating
    else:
        db.add(WorldviewRating(user_id=user.id, worldview_id=worldview_id, rating=rating))
    await db.commit()
    return {"rated": worldview_id, "rating": rating}


@router.post("/shared/{worldview_id}/install")
async def install_shared_worldview(
    worldview_id: str,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_optional_user),
):
    """Install a shared worldview for the current user.

    The pack is already on this server's disk (sharing is server-local), so
    "install" mainly marks it as adopted — and since worldviews are global
    packs (not per-user), installing means it becomes selectable. If the pack
    was deleted locally meanwhile, we cannot restore it (no zip archive kept),
    so we report that clearly.
    """
    if not _is_valid_id(worldview_id):
        raise HTTPException(status_code=400, detail=f"无效的世界观 ID: {worldview_id!r}")
    pack_dir = WORLDVIEWS_DIR / worldview_id
    if not pack_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"世界观 {worldview_id} 不在本机（包可能已被删除）")
    reload_pack(worldview_id)
    return {"installed": worldview_id, "available": True}

