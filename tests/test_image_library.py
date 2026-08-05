"""测试：开源共享图片库（ImageCategory + UserImage + image_routes）。

覆盖：
- 分类：预置存在、新增、重名409、预置不可删
- 母分类严格性：上传不带 mother / 非男非女 → 400
- 图片 CRUD：上传、列表全局共享（用户B见用户A的图）、删除仅上传者
- 图片服务：GET /images/file/{id} 无需鉴权返回 200

Run: python -m pytest tests/test_image_library.py -v
"""

import io
import json
import pytest
import pytest_asyncio
from unittest.mock import patch

import httpx

from ane.main import app
from ane.database.models import User
from ane.database.engine import get_db
from ane.auth import create_access_token

USER_A = "img_user_a"
USER_B = "img_user_b"

# 1x1 PNG bytes
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d4944415478da63fcff9fbf1f0004010001869a9b"
    "eb0000000049454e44ae426082"
)


@pytest_asyncio.fixture
def make_client(db):
    async def _make(user_id):
        u = User(id=user_id, username=user_id, password_hash="x",
                 display_name=user_id, is_adult=True)
        db.add(u)
        await db.commit()
        token = create_access_token({"sub": user_id})

        async def _db_override():
            yield db

        app.dependency_overrides[get_db] = _db_override
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://test")
        client.headers["Authorization"] = f"Bearer {token}"
        return client

    yield _make

    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def client_a(make_client):
    return await make_client(USER_A)


@pytest_asyncio.fixture
async def client_b(make_client):
    return await make_client(USER_B)


# ── 分类 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preset_categories_seeded(client_a):
    """预置分类自动插入（玄幻/都市等 8 个）。"""
    r = await client_a.get("/images/categories")
    assert r.status_code == 200
    cats = r.json()["categories"]
    assert len(cats) >= 8
    names = {c["name"] for c in cats}
    assert {"玄幻", "都市", "武侠", "历史", "西幻", "海贼王", "火影忍者"}.issubset(names)
    # 预置分类 preset=True
    preset = [c for c in cats if c["name"] == "玄幻"][0]
    assert preset["preset"] is True


@pytest.mark.asyncio
async def test_create_category_and_duplicate(client_a):
    """新增分类成功；重名 → 409。"""
    r = await client_a.post("/images/categories", json={"name": "蒸汽朋克"})
    assert r.status_code == 200
    r2 = await client_a.post("/images/categories", json={"name": "蒸汽朋克"})
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_preset_category_cannot_delete(client_a):
    """预置分类不可删 → 403。"""
    r = await client_a.get("/images/categories")
    cat_id = [c for c in r.json()["categories"] if c["name"] == "玄幻"][0]["id"]
    r = await client_a.delete(f"/images/categories/{cat_id}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_user_created_category_delete(client_a):
    """用户自建分类可删（连带该分类下图片）。"""
    r = await client_a.post("/images/categories", json={"name": "临时分类"})
    cat_id = r.json()["id"]
    # 上传一张图到该分类
    r = await client_a.post(
        "/images/upload",
        files={"file": ("a.png", _PNG, "image/png")},
        data={"category_id": cat_id, "mother": "男"},
    )
    assert r.status_code == 200, r.text
    r = await client_a.delete(f"/images/categories/{cat_id}")
    assert r.status_code == 200


# ── 母分类严格性 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_requires_mother(client_a):
    """上传不带 mother → 校验失败（FastAPI 422）。"""
    r = await client_a.get("/images/categories")
    cat_id = r.json()["categories"][0]["id"]
    r = await client_a.post(
        "/images/upload",
        files={"file": ("a.png", _PNG, "image/png")},
        data={"category_id": cat_id},
    )
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_upload_invalid_mother(client_a):
    """mother 非男/女 → 400。"""
    r = await client_a.get("/images/categories")
    cat_id = r.json()["categories"][0]["id"]
    r = await client_a.post(
        "/images/upload",
        files={"file": ("a.png", _PNG, "image/png")},
        data={"category_id": cat_id, "mother": "其他"},
    )
    assert r.status_code == 400


# ── 图片 CRUD + 全局共享 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_and_list_global_share(client_a, client_b):
    """A 上传图，B 能看到（全局共享）；带母分类与标签。"""
    r = await client_a.get("/images/categories")
    cat_id = [c for c in r.json()["categories"] if c["name"] == "玄幻"][0]["id"]

    r = await client_a.post(
        "/images/upload",
        files={"file": ("hero.png", _PNG, "image/png")},
        data={"category_id": cat_id, "mother": "男", "tags": "主角,剑客"},
    )
    assert r.status_code == 200, r.text
    img_id = r.json()["id"]

    # B 也能看到 A 的图
    r = await client_b.get(f"/images?category_id={cat_id}&mother=男")
    assert r.status_code == 200
    imgs = r.json()["images"]
    assert any(i["id"] == img_id for i in imgs)
    img = [i for i in imgs if i["id"] == img_id][0]
    assert img["mother_category"] == "男"
    assert img["tags"] == ["主角", "剑客"]
    # 只有 A 能删（B 的 can_delete=False）
    assert img["can_delete"] is False


@pytest.mark.asyncio
async def test_upload_invalid_ext(client_a):
    """非图片扩展名 → 400。"""
    r = await client_a.get("/images/categories")
    cat_id = r.json()["categories"][0]["id"]
    r = await client_a.post(
        "/images/upload",
        files={"file": ("a.txt", b"hello", "text/plain")},
        data={"category_id": cat_id, "mother": "女"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_owned_only(client_a, client_b):
    """只能删自己上传的；删他人 → 403。"""
    r = await client_a.get("/images/categories")
    cat_id = r.json()["categories"][0]["id"]
    r = await client_a.post(
        "/images/upload",
        files={"file": ("a.png", _PNG, "image/png")},
        data={"category_id": cat_id, "mother": "男"},
    )
    img_id = r.json()["id"]

    # B 删 A 的 → 403
    r = await client_b.delete(f"/images/{img_id}")
    assert r.status_code == 403
    # A 删自己的 → 200
    r = await client_a.delete(f"/images/{img_id}")
    assert r.status_code == 200


# ── 图片服务（无鉴权）─────────────────────────────────────────

@pytest.mark.asyncio
async def test_serve_image_no_auth(client_a):
    """GET /images/file/{id} 无需登录返回 200。"""
    r = await client_a.get("/images/categories")
    cat_id = r.json()["categories"][0]["id"]
    r = await client_a.post(
        "/images/upload",
        files={"file": ("a.png", _PNG, "image/png")},
        data={"category_id": cat_id, "mother": "男"},
    )
    img_id = r.json()["id"]

    # 构造无鉴权请求
    async def _db_override():
        yield None  # placeholder — real db via engine

    # 直接请求（ASGI app 的 DB 依赖已被 override 到测试 db）
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        rr = await c.get(f"/images/file/{img_id}")
        assert rr.status_code == 200
        assert rr.headers.get("content-type", "").startswith("image/")

    # 不存在的图片 → 404
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        rr = await c.get("/images/file/nonexistent")
        assert rr.status_code == 404
