"""测试：用户提示词库（UserPrompt + prompt_routes + turn 注入）。

覆盖：
- /prompts CRUD（创建/列表/编辑/启用/删除）
- 数据隔离：用户 A 的提示词用户 B 不可见/不可改/不可注入
- 单选语义：启用一套自动取消其它套
- turn 注入：prompt_ids 指定后前提示词注入 System 后、后提示词注入玩家输入后

Run: python -m pytest tests/test_prompts.py -v
"""

import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

import httpx

from ane.main import app
from ane.database.models import User, UserPrompt
from ane.database.engine import get_db
from ane.auth import create_access_token, get_current_user
from ane.modules.model_adapter import ModelAdapter
from ane.modules.prompt_builder import PromptBuilder, PromptContext

USER_A = "prompt_user_a"
USER_B = "prompt_user_b"


# ── HTTP client with a given user ──────────────────────────────

@pytest_asyncio.fixture
def make_client(db):
    """Build an auth'd HTTP client for an arbitrary user id (isolation tests).

    Uses real JWT auth (get_current_user is NOT overridden) so each client's
    identity comes from its token — two clients in one test stay isolated.
    """
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


@pytest.fixture
def mock_llm():
    async def _fake(prompt, model=None, **kwargs):
        return json.dumps(
            {"narrative": "一段叙事。", "state_changes": [], "nearby_characters": []},
            ensure_ascii=False,
        )
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake) as m:
        yield m


# ── CRUD ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_list_prompts(client_a):
    """创建后列表返回含 pre/post/enabled。"""
    r = await client_a.post("/prompts", json={
        "name": "文风", "pre_prompt": "多用环境渲染", "post_prompt": "本轮须有一处肢体互动",
    })
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    r = await client_a.get("/prompts")
    assert r.status_code == 200
    prompts = r.json()["prompts"]
    assert len(prompts) == 1
    assert prompts[0]["id"] == pid
    assert prompts[0]["pre_prompt"] == "多用环境渲染"
    assert prompts[0]["post_prompt"] == "本轮须有一处肢体互动"
    assert prompts[0]["enabled"] is False


@pytest.mark.asyncio
async def test_create_duplicate_rejected(client_a):
    """同名提示词 → 409。"""
    await client_a.post("/prompts", json={"name": "文风", "pre_prompt": "a"})
    r = await client_a.post("/prompts", json={"name": "文风", "pre_prompt": "b"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_update_content_and_enable(client_a):
    """编辑内容 + 启用（单选）。"""
    pid = (await client_a.post("/prompts", json={"name": "A", "pre_prompt": "x"})).json()["id"]
    pid2 = (await client_a.post("/prompts", json={"name": "B", "pre_prompt": "y"})).json()["id"]

    # 启用 A
    r = await client_a.put(f"/prompts/{pid}", json={"enabled": True})
    assert r.status_code == 200

    # 再启用 B → A 自动取消（单选）
    r = await client_a.put(f"/prompts/{pid2}", json={"enabled": True})
    assert r.status_code == 200
    prompts = (await client_a.get("/prompts")).json()["prompts"]
    by_id = {p["id"]: p for p in prompts}
    assert by_id[pid]["enabled"] is False
    assert by_id[pid2]["enabled"] is True

    # 编辑内容
    r = await client_a.put(f"/prompts/{pid}", json={"pre_prompt": "new content"})
    assert r.status_code == 200
    prompts = (await client_a.get("/prompts")).json()["prompts"]
    by_id = {p["id"]: p for p in prompts}
    assert by_id[pid]["pre_prompt"] == "new content"
    # 编辑不改 enabled
    assert by_id[pid]["enabled"] is False


@pytest.mark.asyncio
async def test_delete_prompt(client_a):
    """删除后列表为空。"""
    pid = (await client_a.post("/prompts", json={"name": "A", "post_prompt": "z"})).json()["id"]
    r = await client_a.delete(f"/prompts/{pid}")
    assert r.status_code == 200
    prompts = (await client_a.get("/prompts")).json()["prompts"]
    assert prompts == []


# ── 数据隔离 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_b_cannot_see_or_modify_user_a(client_a, client_b):
    """B 不可见 A 的提示词，且 B 访问/改/删 A 的提示词 → 404。"""
    pid = (await client_a.post("/prompts", json={"name": "私藏", "pre_prompt": "secret"})).json()["id"]

    # B 列表看不到
    prompts = (await client_b.get("/prompts")).json()["prompts"]
    assert prompts == []

    # B 访问 A 的 → 404
    assert (await client_b.get(f"/prompts/{pid}")).status_code == 404
    assert (await client_b.put(f"/prompts/{pid}", json={"pre_prompt": "hacked"})).status_code == 404
    assert (await client_b.delete(f"/prompts/{pid}")).status_code == 404

    # A 的内容未被动过
    prompts = (await client_a.get("/prompts")).json()["prompts"]
    assert prompts[0]["pre_prompt"] == "secret"


# ── turn 注入 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_turn_injects_selected_prompts(db, client_a, mock_llm):
    """process_turn 按 prompt_ids 加载用户提示词 → 前/后提示词出现在 prompt 中。"""
    from ane.game_engine import game_engine
    info = await game_engine.create_session(db, user_id=USER_A, name="注入测试")
    session_id = info["session_id"]
    pid = (await client_a.post("/prompts", json={
        "name": "文风", "pre_prompt": "【测试前】多用环境渲染", "post_prompt": "【测试后】本轮须有一处肢体互动",
    })).json()["id"]

    captured = {}
    async def _fake(prompt, model=None, **kwargs):
        # Only capture the main narrative call — the background llm_summary
        # also calls ModelAdapter.generate and would overwrite our capture.
        if kwargs.get("label") == "llm_main":
            captured["pre"] = "【测试前】多用环境渲染" in prompt
            captured["post"] = "【测试后】本轮须有一处肢体互动" in prompt
            captured["has_block_pre"] = "【用户前提示词】" in prompt
            captured["has_block_post"] = "【用户后提示词】" in prompt
        return json.dumps(
            {"narrative": "一段叙事。", "state_changes": [], "nearby_characters": []},
            ensure_ascii=False,
        )
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(
            f"/sessions/{session_id}/turn",
            json={"input": "测试", "prompt_ids": [pid]},
        )
    assert r.status_code == 200, r.text
    assert captured.get("pre") is True, "前提示词应注入"
    assert captured.get("post") is True, "后提示词应注入"


@pytest.mark.asyncio
async def test_turn_without_prompt_ids_injects_nothing(db, client_a, mock_llm):
    """不传 prompt_ids → 不注入任何自定义提示词块。"""
    from ane.game_engine import game_engine
    info = await game_engine.create_session(db, user_id=USER_A, name="无提示词")
    session_id = info["session_id"]
    await client_a.post("/prompts", json={"name": "文风", "pre_prompt": "【测试前】xxx"})

    captured = {}
    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "llm_main":
            captured["pre"] = "【测试前】xxx" in prompt
        return json.dumps(
            {"narrative": "一段叙事。", "state_changes": [], "nearby_characters": []},
            ensure_ascii=False,
        )
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(f"/sessions/{session_id}/turn", json={"input": "测试"})
    assert r.status_code == 200, r.text
    assert captured.get("pre") is False, "未传 prompt_ids 不应注入"


@pytest.mark.asyncio
async def test_turn_ignores_other_users_prompt_ids(db, client_a, client_b, mock_llm):
    """A 创建提示词，B 传 A 的 id → 不注入（跨用户隔离）。"""
    from ane.game_engine import game_engine
    pid = (await client_a.post("/prompts", json={
        "name": "私藏", "pre_prompt": "【测试前】secret-only",
    })).json()["id"]
    info = await game_engine.create_session(db, user_id=USER_B, name="隔离")
    session_id = info["session_id"]

    captured = {}
    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "llm_main":
            captured["leak"] = "【测试前】secret-only" in prompt
        return json.dumps(
            {"narrative": "一段叙事。", "state_changes": [], "nearby_characters": []},
            ensure_ascii=False,
        )
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_b.post(
            f"/sessions/{session_id}/turn",
            json={"input": "测试", "prompt_ids": [pid]},
        )
    assert r.status_code == 200, r.text
    assert captured.get("leak") is False, "他人提示词不得注入"


# ── PromptBuilder 单元 ─────────────────────────────────────────

def test_build_injects_pre_before_input_post_after():
    """前提示词在 System 后、后提示词在玩家输入后，顺序固定。"""
    ctx = PromptContext()
    ctx.user_input = "测试输入"
    ctx.custom_pre_prompts = ["多用环境渲染"]
    ctx.custom_post_prompts = ["本轮须有一处肢体互动"]
    p = PromptBuilder().build(ctx)

    pre_idx = p.find("【用户前提示词】")
    post_idx = p.find("【用户后提示词】")
    input_idx = p.find("【玩家输入】")
    assert pre_idx != -1 and post_idx != -1 and input_idx != -1
    assert pre_idx < input_idx < post_idx
    assert "多用环境渲染" in p
    assert "本轮须有一处肢体互动" in p


def test_build_skips_blank_custom_prompts():
    """空白的前/后提示词不产生注入块。"""
    ctx = PromptContext()
    ctx.user_input = "x"
    ctx.custom_pre_prompts = ["   "]
    ctx.custom_post_prompts = [""]
    p = PromptBuilder().build(ctx)
    assert "【用户前提示词】" not in p
    assert "【用户后提示词】" not in p


# ── info_panel 独立信息区 ──────────────────────────────────────

def test_parse_info_panel():
    """LLM 输出 info_panel → 完整解析进 ParsedOutput。"""
    from ane.modules.output_parser import parse
    raw = ('{"narrative": "正文", "state_changes": [], '
           '"info_panel": "[位置] 青云山·山门\\n[主角] 无名修士 ｜ 凡人\\n[附近] 张三（路人）"}')
    p = parse(raw)
    assert p.info_panel.startswith("[位置]")
    assert "张三" in p.info_panel
    assert p.narrative == "正文"


def test_parse_info_panel_missing_and_malformed():
    """无 info_panel 字段 / 非字符串 → 默认空串，不报错。"""
    from ane.modules.output_parser import parse
    assert parse('{"narrative": "正文"}').info_panel == ""
    assert parse('{"narrative": "正文", "info_panel": {"a": 1}}').info_panel == ""


@pytest.mark.asyncio
async def test_turn_returns_info_panel(db, client_a, mock_llm):
    """turn 响应的 info_panel 原样传回前端。"""
    from ane.game_engine import game_engine
    info = await game_engine.create_session(db, user_id=USER_A, name="信息区")
    session_id = info["session_id"]
    info_text = "[位置] 青云山·山门\n[主角] 无名修士 ｜ 凡人\n[附近] 张三（路人）"

    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "llm_main":
            return json.dumps(
                {"narrative": "一段叙事。", "state_changes": [],
                 "nearby_characters": [], "info_panel": info_text},
                ensure_ascii=False,
            )
        return json.dumps({"narrative": "x", "state_changes": [], "nearby_characters": []},
                          ensure_ascii=False)

    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(f"/sessions/{session_id}/turn", json={"input": "测试"})
    assert r.status_code == 200, r.text
    assert r.json()["info_panel"] == info_text


@pytest.mark.asyncio
async def test_turn_info_panel_passthrough_with_interacting_npc(db, client_a, mock_llm):
    """info_panel 由 LLM 输出并原样透传——「正在交互人物」来自 LLM 自主判断（可能来自附近人物）。"""
    from ane.game_engine import game_engine
    info = await game_engine.create_session(db, user_id=USER_A, name="交互人物")
    session_id = info["session_id"]

    # LLM 自主在 info_panel 里列出正在交互人物（可能来自 nearby_characters）
    info_text = ("[位置] 青云山·山门\n"
                 "[主角] 无名修士 ｜ 凡人\n"
                 "[正在交互] 林清雪（师姐）—— 正在与你交谈")

    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "llm_main":
            return json.dumps(
                {"narrative": "一段叙事。", "state_changes": [],
                 "nearby_characters": [{"name": "林清雪", "identity": "师姐"}],
                 "info_panel": info_text},
                ensure_ascii=False,
            )
        return json.dumps({"narrative": "x", "state_changes": [], "nearby_characters": []},
                          ensure_ascii=False)

    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(f"/sessions/{session_id}/turn", json={"input": "测试"})
    assert r.status_code == 200, r.text
    panel = r.json()["info_panel"]
    # LLM 原样透传，引擎不追加/不修改
    assert panel == info_text
    # 引擎不再硬编码「重要人物」合并
    assert "【正在交互人物】" not in panel
    # nearby_characters 仍是独立字段（前端点击标签用）
    assert r.json()["nearby_characters"][0]["name"] == "林清雪"


@pytest.mark.asyncio
async def test_info_panel_persistence_across_turns(db, client_a, mock_llm):
    """第1轮输出的信息栏存入，第2轮构建 prompt 时整块回喂（持续性）。"""
    from ane.game_engine import game_engine
    from ane.modules.prompt_builder import assemble_system
    info = await game_engine.create_session(db, user_id=USER_A, name="持续")
    session_id = info["session_id"]

    captured = {}
    turn = {"n": 0}

    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "llm_main":
            turn["n"] += 1
            if turn["n"] == 1:
                # 第1轮：建栏目
                captured["turn1_has_feedback"] = "上一轮信息栏" in prompt
                return json.dumps(
                    {"narrative": "一段叙事。", "state_changes": [],
                     "nearby_characters": [],
                     "info_panel": "【主角】无名修士 ｜ 凡人\n【宗门贡献】今日接取清剿任务"},
                    ensure_ascii=False,
                )
            else:
                # 第2轮：应收到上一轮信息栏回喂
                captured["turn2_has_feedback"] = "【上一轮信息栏】" in prompt
                captured["turn2_has_content"] = "宗门贡献" in prompt
                captured["turn2_has_content2"] = "今日接取清剿任务" in prompt
                return json.dumps(
                    {"narrative": "第二段叙事。", "state_changes": [],
                     "nearby_characters": [],
                     "info_panel": "【主角】无名修士 ｜ 凡人\n【宗门贡献】今日接取清剿任务；明日交任务"},
                    ensure_ascii=False,
                )
        return json.dumps({"narrative": "x", "state_changes": [], "nearby_characters": []},
                          ensure_ascii=False)

    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r1 = await client_a.post(f"/sessions/{session_id}/turn", json={"input": "建立栏目「宗门贡献」：记录贡献"})
        assert r1.status_code == 200, r1.text
        # 第1轮信息栏已持久化到 memory
        from ane.modules.memory_manager import memory_manager
        stored = await memory_manager.get_latest_info_panel(db, session_id)
        assert "宗门贡献" in stored
        assert "今日接取清剿任务" in stored

        r2 = await client_a.post(f"/sessions/{session_id}/turn", json={"input": "继续"})
        assert r2.status_code == 200, r2.text

    assert captured.get("turn2_has_feedback") is True, "第2轮应收到上一轮信息栏回喂"
    assert captured.get("turn2_has_content") is True, "回喂内容应含栏目名"
    assert captured.get("turn2_has_content2") is True, "回喂内容应含具体条目"
    # 第2轮输出的新信息栏继续持久化（覆盖）
    stored2 = await memory_manager.get_latest_info_panel(db, session_id)
    assert "明日交任务" in stored2
