"""内置世界观包「默认开源」测试。

契约（本次改动锁定）：
- **开源 = 使用权限**：所有账号都能看到、能用这些包开局（角色创建下拉 + 广场都可见）。
- **修改权限不变**：只有白名单管理员（`config.WORLDVIEW_ADMIN_IDS`）或包作者能改包内容。
- 内置包的广场条目由 `open_source: true` 自动发布（`is_official=True`，作者显示「官方内置」），
  幂等；用户自己上传的包仍然走设计器「开源」按钮，可自行下架。

Run: python -m pytest tests/test_open_source.py -q
"""

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

import ane.config as _cfg
from ane.auth import create_access_token
from ane.database.engine import get_db
from ane.database.models import User, WorldSession, WorldviewShare
from ane.game_engine import game_engine
from ane.main import app
from ane.open_source import (
    OFFICIAL_AUTHOR,
    ensure_open_source_shares,
    is_open_source,
    open_source_ids,
)
from ane.worldview import get as get_worldview

BUILTIN = [
    "xianxia_v1",
    "modern_city",
    "fantasy_kingdom",
    "naruto_shippuden",
    "one_piece",
    "sanguo_yanyi",
]
USER_OPEN = "wv_open_user"
USER_ADMIN = "wv_open_admin"


# ── HTTP client（真实 JWT，身份来自 token）──────────────────────

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
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        client.headers["Authorization"] = f"Bearer {token}"
        return client

    yield _make
    app.dependency_overrides.pop(get_db, None)


# ── 1. 包清单声明 ───────────────────────────────────────────────

def test_builtin_packs_declare_open_source():
    """现有世界观包全部声明开源，且都没有 owner（内置系统包）。"""
    for wv_id in BUILTIN:
        wv = get_worldview(wv_id)
        assert wv is not None, f"{wv_id} 未注册"
        assert (wv.manifest or {}).get("open_source") is True, f"{wv_id} 未声明 open_source"
        assert not (wv.manifest or {}).get("owner_user_id"), f"{wv_id} 不应带 owner_user_id"


def test_open_source_ids_cover_all_builtins():
    ids = set(open_source_ids())
    assert set(BUILTIN) <= ids
    assert is_open_source("naruto_shippuden") is True
    assert is_open_source("__not_a_pack__") is False


# ── 2. 自动发布（幂等）─────────────────────────────────────────

async def _seed_user(db, user_id):
    db.add(User(id=user_id, username=user_id, password_hash="x", display_name=user_id, is_adult=True))
    await db.commit()


@pytest.mark.asyncio
async def test_ensure_publishes_official_shares(db):
    await _seed_user(db, USER_OPEN)
    added = await ensure_open_source_shares(db)
    assert set(BUILTIN) <= set(added)

    rows = (await db.execute(select(WorldviewShare))).scalars().all()
    by_id = {r.worldview_id: r for r in rows}
    for wv_id in BUILTIN:
        share = by_id.get(wv_id)
        assert share is not None, f"{wv_id} 未发布到开源库"
        assert share.is_official is True
        assert share.title                       # 名称取自包 manifest
        assert share.user_id == USER_OPEN        # 挂在一个真实用户上（外键要求）


@pytest.mark.asyncio
async def test_ensure_is_idempotent(db):
    await _seed_user(db, USER_OPEN)
    await ensure_open_source_shares(db)
    n1 = (await db.execute(select(func.count(WorldviewShare.id)))).scalar_one()

    added2 = await ensure_open_source_shares(db)
    n2 = (await db.execute(select(func.count(WorldviewShare.id)))).scalar_one()
    assert added2 == []
    assert n1 == n2 == len(BUILTIN)


@pytest.mark.asyncio
async def test_migration_then_publish_on_existing_db(tmp_path, monkeypatch):
    """服务器升级路径：老库（worldview_shares 表**没有** is_official 列）→ init_db 迁移 → 自动发布。

    线上库是"旧表 + 新代码"，这条路径必须能跑通：init_db 用 ALTER TABLE 补列，
    随后 ensure_open_source_shares 才能写 is_official=True 的官方条目。
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import ane.database.engine as eng_mod

    db_file = tmp_path / "old_server.db"
    url = f"sqlite+aiosqlite:///{db_file}"

    old = create_async_engine(url)
    async with old.begin() as conn:
        # 旧版建的表：worldview_shares 无 is_official；users 里已有历史账号
        await conn.execute(text(
            "CREATE TABLE users (id VARCHAR PRIMARY KEY, username VARCHAR UNIQUE NOT NULL, "
            "password_hash VARCHAR NOT NULL, display_name VARCHAR, is_adult BOOLEAN, "
            "created_at DATETIME, is_active BOOLEAN)"
        ))
        await conn.execute(text(
            "CREATE TABLE worldview_shares (id VARCHAR PRIMARY KEY, user_id VARCHAR NOT NULL, "
            "worldview_id VARCHAR NOT NULL, title VARCHAR NOT NULL, description TEXT, tags JSON, "
            "version VARCHAR, created_at DATETIME, updated_at DATETIME)"
        ))
        await conn.execute(text(
            "INSERT INTO users (id, username, password_hash, display_name, created_at) "
            "VALUES ('old_admin','old_admin','x','老管理员','2026-01-01 00:00:00')"
        ))
    await old.dispose()

    # 让 init_db 作用在这个临时库上（它读模块级 engine / DATABASE_URL）
    tmp_engine = create_async_engine(url)
    monkeypatch.setattr(eng_mod, "engine", tmp_engine)
    monkeypatch.setattr(eng_mod, "DATABASE_URL", url)
    await eng_mod.init_db()

    async with tmp_engine.begin() as conn:
        cols = await conn.execute(text("PRAGMA table_info(worldview_shares)"))
        assert "is_official" in {row[1] for row in cols.fetchall()}, "迁移没补上 is_official 列"

    factory = async_sessionmaker(tmp_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        added = await ensure_open_source_shares(session)
        assert set(BUILTIN) <= set(added), "老库上没能自动发布内置开源包"
        rows = (await session.execute(select(WorldviewShare))).scalars().all()
        assert {r.worldview_id for r in rows} >= set(BUILTIN)
        assert all(r.is_official for r in rows)
        assert all(r.user_id == "old_admin" for r in rows)   # 挂到库里已有的老账号
    await tmp_engine.dispose()


@pytest.mark.asyncio
async def test_plaza_explains_when_no_user_exists(db):
    """库里没有用户时不会抛错，但必须把原因带出去（否则前端只看到空广场、无从排查）。"""
    import httpx

    async def _db_override():
        yield db
    app.dependency_overrides[get_db] = _db_override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/worldviews/shared")
            assert r.status_code == 200
            body = r.json()
            assert body["worldviews"] == []
            assert "没有任何用户账号" in body["publish_error"]
            assert set(BUILTIN) <= set(body["declared_open_source"])
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_plaza_reports_publish_error_and_status_endpoint(db, make_client):
    """广场接口带回 publish_error 与声明列表；诊断接口给出 declared/published/missing。"""
    client = await make_client(USER_OPEN)
    r = await client.get("/worldviews/shared")
    assert r.status_code == 200
    body = r.json()
    assert body["publish_error"] == ""                       # 正常情况没有错误
    assert set(BUILTIN) <= set(body["declared_open_source"])  # 声明开源
    assert len(body["worldviews"]) >= len(BUILTIN)            # 自愈后条目齐全

    r = await client.get("/worldviews/open-source")
    assert r.status_code == 200
    st = r.json()
    assert st["error"] == ""
    assert set(BUILTIN) <= set(st["declared"])
    assert st["missing"] == []
    assert set(BUILTIN) <= set(st["published"])


@pytest.mark.asyncio
async def test_ensure_skips_when_no_user_exists(db):
    """数据库里一个用户都没有时跳过（等有账号后再补），不抛异常。"""
    assert await ensure_open_source_shares(db) == []


# ── 3. 广场可见 + 作者标记 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_plaza_lists_builtins_as_official(db, make_client):
    client = await make_client(USER_OPEN)
    r = await client.get("/worldviews/shared")
    assert r.status_code == 200
    items = {w["worldview_id"]: w for w in r.json()["worldviews"]}
    for wv_id in BUILTIN:
        assert wv_id in items, f"{wv_id} 没有出现在开源广场"
        assert items[wv_id]["official"] is True
        assert items[wv_id]["author"] == OFFICIAL_AUTHOR
        assert items[wv_id]["installed"] is True     # 本机已安装（可直接用）


# ── 4. 使用权限：任何账号都能用内置包开局 ────────────────────────

@pytest.mark.asyncio
async def test_any_user_can_use_builtin_pack(db, make_client):
    client = await make_client(USER_OPEN)

    # 角色创建下拉：全部内置包可见
    r = await client.get("/worldviews")
    assert r.status_code == 200
    ids = {w["id"] for w in r.json()["worldviews"]}
    assert set(BUILTIN) <= ids

    # 引擎层开局：非管理员用内置包创建会话成功
    res = await game_engine.create_session(db, user_id=USER_OPEN, name="路人开局", worldview="naruto_shippuden")
    assert res["session_id"]
    sess = await db.get(WorldSession, res["session_id"])
    assert sess.worldview == "naruto_shippuden"


# ── 5. 修改权限：非白名单账号改不动 ──────────────────────────────

@pytest.mark.asyncio
async def test_non_admin_cannot_edit_builtin_pack(db, make_client, monkeypatch):
    # 保证测试账号不在白名单里
    monkeypatch.setattr(_cfg, "WORLDVIEW_ADMIN_IDS", ["someone_else"], raising=False)
    client = await make_client(USER_OPEN)

    r = await client.get("/worldviews")
    for w in r.json()["worldviews"]:
        if w["id"] in BUILTIN:
            assert w["editable"] is False

    r = await client.put("/worldviews/xianxia_v1/ui", json={"ui": {"labels": {}}})
    assert r.status_code == 403
    r = await client.put("/worldviews/xianxia_v1/form", json={"form": {"fields": []}})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_edit_and_official_pack_cannot_be_unshared(db, make_client, monkeypatch):
    monkeypatch.setattr(_cfg, "WORLDVIEW_ADMIN_IDS", [USER_ADMIN], raising=False)
    client = await make_client(USER_ADMIN)
    await ensure_open_source_shares(db)

    r = await client.get("/worldviews")
    assert [w["editable"] for w in r.json()["worldviews"] if w["id"] == "xianxia_v1"] == [True]

    # 内置开源包不能下架（要下架就改 manifest 的 open_source）
    r = await client.delete("/worldviews/share", params={"worldview_id": "xianxia_v1"})
    assert r.status_code == 400
    assert "内置开源包" in r.json()["detail"]
