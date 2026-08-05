"""image_routes — 开源共享图片库 API。

分类：世界类型（一级分类/仓库）全局共享——预置 + 用户可新增。
图片：全局共享（开源性质），浏览无需登录；上传/删除需登录（仅上传者可删）。
母分类「男/女」为固定概念，图片上传必须指定且严格校验。

存储：data/images/<user_id>/<image_id>.<ext>，文件名服务端生成防路径穿越。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, delete

from ane.auth import get_current_user, get_optional_user
from ane.config import IMAGE_DIR
from ane.database.engine import get_db
from ane.database.models import ImageCategory, UserImage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["images"])

# ── 预置世界类型分类 ──────────────────────────────────────────
DEFAULT_CATEGORIES = ["玄幻", "都市", "武侠", "历史", "西幻", "科幻", "海贼王", "火影忍者"]

# 允许的图片扩展名
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB

MOTHER_CATEGORIES = ("男", "女")


class CategoryCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=20)


async def _ensure_default_categories(db):
    """惰性插入预置分类（首次访问且表为空时）。"""
    result = await db.execute(select(ImageCategory.id).limit(1))
    if result.scalar_one_or_none() is not None:
        return
    for name in DEFAULT_CATEGORIES:
        db.add(ImageCategory(name=name, created_by=None))
    await db.commit()
    logger.info(f"Seeded {len(DEFAULT_CATEGORIES)} default image categories")


# ── 分类 CRUD（全局共享）────────────────────────────────────

@router.get("/categories")
async def list_categories(
    db=Depends(get_db),
    user=Depends(get_optional_user),
):
    """列出全部世界类型分类（预置 + 用户新增）。"""
    await _ensure_default_categories(db)
    result = await db.execute(
        select(ImageCategory).order_by(ImageCategory.created_at)
    )
    return {"categories": [
        {
            "id": c.id,
            "name": c.name,
            "preset": c.created_by is None,
        }
        for c in result.scalars().all()
    ]}


@router.post("/categories")
async def create_category(
    body: CategoryCreateRequest,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """新增世界类型分类（全局共享，重名 409）。"""
    existing = await db.execute(
        select(ImageCategory).where(ImageCategory.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"分类「{body.name}」已存在")
    cat = ImageCategory(name=body.name.strip(), created_by=user.id)
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return {"id": cat.id, "name": cat.name}


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """删除分类。预置分类不可删；用户自建可删（连带该分类下所有图片文件）。"""
    cat = await db.get(ImageCategory, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")
    if cat.created_by is None:
        raise HTTPException(status_code=403, detail="预置分类不可删除")
    if cat.created_by != user.id:
        raise HTTPException(status_code=403, detail="只能删除自己创建的分类")

    # 删除该分类下所有图片文件 + DB 记录
    imgs = (await db.execute(
        select(UserImage).where(UserImage.category_id == category_id)
    )).scalars().all()
    for img in imgs:
        _delete_image_file(img)
    await db.execute(
        delete(UserImage).where(UserImage.category_id == category_id)
    )
    await db.delete(cat)
    await db.commit()
    return {"ok": True}


# ── 图片 CRUD（全局共享浏览，私有上传/删除）──────────────────

def _image_path(img: UserImage) -> Path:
    """磁盘绝对路径（按上传者分目录）。"""
    return IMAGE_DIR / img.user_id / img.filename


def _delete_image_file(img: UserImage) -> None:
    try:
        p = _image_path(img)
        if p.exists():
            p.unlink()
    except Exception as e:
        logger.warning(f"Failed to delete image file {img.id}: {e}")


@router.get("")
async def list_images(
    category_id: str,
    mother: str | None = None,
    db=Depends(get_db),
    user=Depends(get_optional_user),
):
    """列出某仓库（分类）下的全部图片（所有用户共享可见）。"""
    q = select(UserImage).where(UserImage.category_id == category_id)
    if mother:
        q = q.where(UserImage.mother_category == mother)
    result = await db.execute(q.order_by(UserImage.created_at.desc()))
    imgs = result.scalars().all()
    # 批量查上传者名
    from ane.database.models import User as _U
    user_ids = {i.user_id for i in imgs}
    if user_ids:
        unames = (await db.execute(
            select(_U.id, _U.display_name).where(_U.id.in_(user_ids))
        )).all()
        name_map = {uid: dn for uid, dn in unames}
    else:
        name_map = {}
    return {"images": [
        {
            "id": i.id,
            "url": f"/images/file/{i.id}",
            "mother_category": i.mother_category,
            "original_name": i.original_name or "",
            "tags": i.tags or [],
            "uploader": name_map.get(i.user_id, ""),
            "can_delete": bool(user) and user.id == i.user_id,
            "created_at": str(i.created_at) if i.created_at else "",
        }
        for i in imgs
    ]}


@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    category_id: str = Form(..., min_length=1),
    mother: str = Form(...),
    tags: str = Form(""),
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """上传图片到某分类（必须指定母分类 男/女）。"""
    if mother not in MOTHER_CATEGORIES:
        raise HTTPException(status_code=400, detail="母分类必须是「男」或「女」")
    cat = await db.get(ImageCategory, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="分类不存在")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if len(raw) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="图片过大（上限 10MB）")

    # 校验扩展名
    orig_name = file.filename or ""
    ext = Path(orig_name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="仅支持 jpg/jpeg/png/gif/webp 图片")

    from ane.database.models import UserImage as _UI
    from ane.database.models import _new_id as _gen_id
    # 先定 id → filename（避免 flush 时 filename 违反 NOT NULL）
    img_id = _gen_id()
    img = _UI(
        id=img_id,
        user_id=user.id,
        category_id=category_id,
        mother_category=mother,
        filename=f"{img_id}{ext}",
        original_name=orig_name,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
    )
    db.add(img)
    await db.flush()

    # 写磁盘：<user_id>/<image_id>.<ext>
    user_dir = IMAGE_DIR / user.id
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / img.filename).write_bytes(raw)

    await db.commit()
    await db.refresh(img)
    return {"id": img.id, "url": f"/images/file/{img.id}", "original_name": orig_name}


@router.delete("/{image_id}")
async def delete_image(
    image_id: str,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    """删除图片（仅上传者本人，连带删磁盘文件）。"""
    img = await db.get(UserImage, image_id)
    if not img:
        raise HTTPException(status_code=404, detail="图片不存在")
    if img.user_id != user.id:
        raise HTTPException(status_code=403, detail="只能删除自己上传的图片")
    _delete_image_file(img)
    await db.delete(img)
    await db.commit()
    return {"ok": True}


# ── 图片服务（无鉴权，开源共享）─────────────────────────────

@router.get("/file/{image_id}")
async def serve_image(image_id: str, db=Depends(get_db)):
    """返回图片文件。开源共享，无需登录。"""
    img = await db.get(UserImage, image_id)
    if not img:
        raise HTTPException(status_code=404, detail="图片不存在")
    p = _image_path(img)
    if not p.exists():
        raise HTTPException(status_code=404, detail="图片文件缺失")
    return FileResponse(str(p), media_type="image/" + img.filename.rsplit(".", 1)[-1])
