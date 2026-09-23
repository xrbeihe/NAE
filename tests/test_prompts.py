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


# ── info_panel 去回声：扩展栏目只保留主角面板一条渲染路径 ──────────────

def test_strip_extension_echo_sections_unit():
    """分节标题 == 扩展栏目名 → 删掉该分节（标题+正文）；其余内容原样保留。"""
    from ane.panels import strip_extension_echo_sections

    class _P:
        attributes = {"_extensions": {"技能栏·粘遁": "粘液墙：防御型忍术"}}

    text = (
        "【技能栏·粘遁】\n"
        "粘液墙：防御型忍术\n"
        "粘液分身：制造粘液构成的分身\n"
        "\n"
        "北荷茶光：查克拉严重消耗（约两成），精神平稳\n"
        "\n"
        "【交互人物】\n"
        "白｜原雾隐叛忍属下｜温柔忠诚"
    )
    out = strip_extension_echo_sections(text, _P())
    assert "技能栏·粘遁" not in out
    assert "粘液墙" not in out                      # 栏目正文一并删除
    assert "北荷茶光：查克拉严重消耗" in out          # 主角动态状态保留
    assert "【交互人物】" in out and "白｜" in out    # 交互人物保留

    class _Q:
        attributes = {}

    assert strip_extension_echo_sections(text, _Q()) == text  # 无扩展栏目 → 不动
    assert strip_extension_echo_sections("", _P()) == ""


# ── info_panel 去回声 ②：主角动态段里照抄权威面板字段（身份/性格…）──
# 注意：位置**不在**剥离范围内——位置是主角的基本信息，只写在信息栏里（不进代码层，
# 所以权威面板也不再渲染它），信息栏里的「位置：…」必须保留。

PANEL_TEXT = (
    "【主角面板】\n"
    "姓名：北荷茶光 ｜ 男 ｜ 15岁 ｜ 血继限界/能力：粘遁 ｜ 性格：热情友善 ｜ "
    "身份：曾经是水忍中忍 ｜ 伤势：无"
)


@pytest.mark.asyncio
async def test_position_kept_in_info_panel_but_not_code_layer(db, client_a, mock_llm):
    """位置归信息栏：LLM 写在【主角动态】里的位置原样返回并落库，
    主角面板不渲染它，且 state_changes 里的 location_change 不写回数据库。"""
    from ane.game_engine import game_engine
    from ane.modules.memory_manager import memory_manager
    from ane.modules.player_manager import player_manager
    from ane.modules.model_adapter import ModelAdapter
    from unittest.mock import AsyncMock, patch

    info = await game_engine.create_session(db, user_id=USER_A, name="位置归信息栏")
    session_id = info["session_id"]

    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "llm_main":
            return json.dumps({
                "narrative": "他背着白走进杉谷村。",
                "state_changes": [
                    {"type": "location_change", "target": "player", "value": "林之国·杉谷村口"},
                ],
                "info_panel": (
                    "北荷茶光：查克拉消耗过半，精神紧绷 ｜位置：林之国·杉谷村口\n\n"
                    "【交互人物】\n白｜雾隐叛忍｜昏迷中\n\n"
                    "【推荐行动】\n1. 接受米店老板娘邀请"
                ),
            }, ensure_ascii=False)
        return json.dumps({"narrative": "x", "state_changes": []}, ensure_ascii=False)

    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(f"/sessions/{session_id}/turn", json={"input": "进村"})
        assert r.status_code == 200, r.text
        body = r.json()
        # 信息栏：位置完整保留（这是主角基本信息），动态状态与其余段落也在
        assert "位置：林之国·杉谷村口" in body["info_panel"]
        assert "查克拉消耗过半，精神紧绷" in body["info_panel"]
        assert "【交互人物】" in body["info_panel"] and "【推荐行动】" in body["info_panel"]
        # 落库版本同样保留（下一轮回喂的就是它）
        stored = await memory_manager.get_latest_info_panel(db, session_id)
        assert "位置：林之国·杉谷村口" in stored
        # 代码层：权威主角面板不渲染位置
        assert "位置" not in body["player_panel"]
        # 代码层：location_change 不写回（player.location 保持未设定）
        player = await player_manager.get_by_session(db, session_id)
        assert (player.location or "") == ""


def test_strip_dynamic_field_echoes_unit():
    """动态段里字段已在权威面板中 → 只删该字段片段，动态内容与位置保留；其余段落不动。"""
    from ane.panels import strip_dynamic_field_echoes, _ALWAYS_ECHO_KEYS

    # 固定剥离名单里不能有位置（回归锁：位置属于信息栏该写的内容）
    assert "位置" not in _ALWAYS_ECHO_KEYS and "地点" not in _ALWAYS_ECHO_KEYS

    # ① 用户实际形态：无标题 + 名字前缀 + ｜位置（位置保留，动态状态保留）
    out = strip_dynamic_field_echoes(
        "北荷茶光：查克拉消耗过半，精神紧绷 ｜位置：林之国·杉谷村口\n\n【推荐行动】\n1. 接受邀请",
        PANEL_TEXT,
    )
    assert "位置：林之国·杉谷村口" in out                        # 位置是有效信息 → 保留
    assert "北荷茶光：查克拉消耗过半，精神紧绷" in out            # 动态状态保留
    assert "【推荐行动】" in out and "1. 接受邀请" in out

    # ② 有【主角动态】标题 + 多字段：面板已有的身份/性格被删，位置与动态内容保留
    out2 = strip_dynamic_field_echoes(
        "【主角动态】\n状态：精神紧绷 ｜ 位置：村口 ｜ 身份：水忍中忍 ｜ 性格：热情友善 ｜ 当前行动：赶路\n\n【交互人物】\n白｜昏迷中",
        PANEL_TEXT,
    )
    assert "身份：" not in out2 and "性格：" not in out2
    assert "状态：精神紧绷" in out2 and "当前行动：赶路" in out2 and "位置：村口" in out2
    assert "【交互人物】" in out2 and "白｜昏迷中" in out2

    # ③ 整行只有回声（面板里真实存在的字段）→ 整段消失
    out3 = strip_dynamic_field_echoes("北荷茶光：伤势：无\n\n【推荐行动】\n1. x", PANEL_TEXT)
    assert "伤势：" not in out3 and "【主角动态】" not in out3
    assert out3.startswith("【推荐行动】")

    # ④ 交互人物/附近人物段里 NPC 自己的「位置/状态」不受影响（只处理动态段）
    text4 = "【主角动态】\n状态：平稳\n\n【交互人物】\n白｜雾隐叛忍｜状态：昏迷中｜位置：身边\n"
    out4 = strip_dynamic_field_echoes(text4, PANEL_TEXT)
    assert "状态：昏迷中" in out4 and "位置：身边" in out4

    # ⑤ 没有面板文本 / 空文本 → 原样返回
    assert strip_dynamic_field_echoes("状态：x", "") == "状态：x"
    assert strip_dynamic_field_echoes("", PANEL_TEXT) == ""


def test_prompt_nearby_section_has_no_person_type_quota():
    """【附近人物】不做人数/性别等人物类型约束——由模型按场景自行决定谁在场。"""
    from ane.modules.prompt_builder import _EFFECTIVE_SYSTEM_PROMPT, NARRATIVE_KERNEL_PROMPT

    for name, text in (("system", _EFFECTIVE_SYSTEM_PROMPT), ("kernel", NARRATIVE_KERNEL_PROMPT)):
        assert "【附近人物】" in text, f"{name} prompt 缺少附近人物段"
        # 旧约束（人数/性别配额）不应再出现
        assert "1 男 2 女" not in text, f"{name} prompt 仍有性别配额"
        assert "3 位场景路人" not in text, f"{name} prompt 仍写死 3 位"
        assert "1-3 位" not in text, f"{name} prompt 仍有人数上限"
        # 明确交给模型自行决定
        assert "由你按当前场景自行决定" in text, f"{name} prompt 缺少「自行决定」的说明"
        assert "不做人数或性别限制" in text, f"{name} prompt 缺少「无限制」的说明"
        # 格式与必须列入的规则保留
        assert "姓名｜身份｜外貌｜正在做什么" in text
        # player_relationships 那条不再用含糊的「路人不要输出」，而是明确指路到【附近人物】段
        assert "只写在 info_panel 的【附近人物】段" in text
        assert "背景npc路人npc不要输出" not in text


def test_prompt_position_only_in_info_panel():
    """位置退出代码层，但仍是主角基本信息：提示词里不再有位置注入 / location_change 指引，
    只保留「位置写在信息栏【主角动态】段」这一条叙事层要求。"""
    from ane.modules.prompt_builder import _EFFECTIVE_SYSTEM_PROMPT, NARRATIVE_KERNEL_PROMPT

    for name, text in (("system", _EFFECTIVE_SYSTEM_PROMPT), ("kernel", NARRATIVE_KERNEL_PROMPT)):
        assert "具体位置" not in text, f"{name} prompt 仍在注入具体位置"
        assert "当前位置：" not in text, f"{name} prompt 仍在注入当前位置"
        assert "位置层级" not in text, f"{name} prompt 仍在注入位置层级"
        assert "location_change" not in text, f"{name} prompt 仍在教 location_change"
        # 位置仍要写：写在信息栏【主角动态】段里（含格式示例）
        assert "位置是主角的基本信息" in text, f"{name} prompt 缺少『位置是主角的基本信息』说明"
        assert "｜位置：" in text, f"{name} prompt 的【主角动态】示例里缺少位置"
        assert "不进程序" in text, f"{name} prompt 未说明位置不进程序（别用 state_changes 写它）"


def test_player_panel_specs_have_no_location_field():
    """6 个包的 panel.json 都不再渲染「位置」（位置只在信息栏里由模型写，权威面板不管它）。"""
    import json
    from pathlib import Path
    from ane.worldview import list_worldviews, get as get_worldview

    for wv in list_worldviews():
        spec = get_worldview(wv["id"]).panel_spec or {}
        keys = [f.get("key") for f in spec.get("fields", [])]
        labels = [f.get("label") for f in spec.get("fields", [])]
        assert "location" not in keys, f"{wv['id']} 的面板仍引用 location"
        assert "位置" not in labels, f"{wv['id']} 的面板仍显示「位置」"
        assert not any("location" in json.dumps(f, ensure_ascii=False) for f in spec.get("fields", [])), \
            f"{wv['id']} 的面板仍含 location 引用"


def test_built_prompt_has_no_location_lines():
    """运行时拼出的 prompt 里不再注入角色位置（玩家块/场景块都没有位置行），
    只有 info_panel 规则里「位置写在【主角动态】」那条叙事层要求会出现「位置」二字。"""
    from ane.modules.prompt_builder import (
        prompt_builder, PromptContext, PlayerContext, AgenticContext,
        WorldContext, SceneContext,
    )
    ctx = PromptContext(
        world=WorldContext(name="测试世界"),
        player=PlayerContext(
            name="某人", cultivation="炼气期",
            location="青云宗·山门", location_hierarchy="青云宗·山门",
        ),
        agentic=AgenticContext(),
        scene=SceneContext(
            location_name="山门", location_hierarchy="青云宗·山门",
            location_description="云雾缭绕，石阶生苔",
        ),
        user_input="你好",
    )
    prompt = prompt_builder.build(ctx)
    assert "具体位置" not in prompt
    assert "当前位置" not in prompt
    assert "位置层级" not in prompt
    assert "青云宗·山门" not in prompt                  # 旧位置字段的值也不再被渲染
    assert "环境描写：云雾缭绕，石阶生苔" in prompt      # 场景氛围保留，只是不再声明"谁在哪"
    assert "位置是主角的基本信息" in prompt              # 位置只作为信息栏写作要求出现


def test_active_set_ignores_location_and_uses_mentions(tmp_path):
    """在场判定不再看位置：被提到的人在、没人提的（哪怕同地点）不在、重要人物始终在、死者不在。"""
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from ane.database.models import Base, NPC
    from ane.modules.retrieval_engine import retrieval_engine, is_name_mentioned

    async def _run():
        eng = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add_all([
                NPC(id="n1", session_id="s1", name="白", location="林之国·杉谷村", npc_type="named"),
                NPC(id="n2", session_id="s1", name="米店老板娘", location="林之国·杉谷村", npc_type="background"),
                NPC(id="n3", session_id="s1", name="蒙奇·D·路飞", location="东海·风车村", is_important=True),
                NPC(id="n4", session_id="s1", name="死者", location="林之国·杉谷村", is_alive=False),
                NPC(id="n5", session_id="s1", name="路人甲", location="", npc_type="background"),   # 空位置的路人
            ])
            await db.commit()
            # 玩家在杉谷村，但只有「白」被叙事提到
            active = await retrieval_engine.get_active_set(
                db, "s1", "林之国·杉谷村", mentioned_text="白背着人在村口张望",
            )
            names = {n.name for n in active.present_npcs}
            assert "白" in names                     # 被提到 → 在场
            assert "米店老板娘" not in names          # 同地点但没被提到 → 不再自动在场
            assert "路人甲" not in names              # 空位置的路人不再"永远在场"
            assert "死者" not in names                # 死亡 NPC 不注入
            assert "蒙奇·D·路飞" in names             # 重要人物始终在场
        await eng.dispose()

    asyncio.run(_run())
    # 后缀简称也算提到
    assert is_name_mentioned("蒙奇·D·路飞", "路飞笑了") is True
    assert is_name_mentioned("林星如", "林星来了") is False



@pytest.mark.asyncio
async def test_extension_echo_stripped_from_info_panel(db, client_a):
    """LLM 把主角面板「扩展：」栏目抄进 info_panel → 存库/返回前剔除；
    权威主角面板仍带该栏目（保证只有一条渲染路径，不丢信息）。"""
    from ane.game_engine import game_engine
    from ane.modules.memory_manager import memory_manager

    info = await game_engine.create_session(db, user_id=USER_A, name="去回声")
    session_id = info["session_id"]
    ext = {"技能栏·粘遁": "粘液墙：防御型忍术"}
    echo = (
        "【技能栏·粘遁】\n粘液墙：防御型忍术\n\n"
        "无名修士：查克拉充足，精神平稳\n\n"
        "【交互人物】\n白｜原雾隐叛忍属下｜温柔忠诚"
    )
    turn = {"n": 0}

    async def _fake(prompt, model=None, **kwargs):
        # 按 prompt 里的 ASCII 标记分派：prompt 自带的规则文本里就含「建立栏目」示例词，
        # 用它做判据会误命中；短输出重试也会让 llm_main 被调两次，所以不能按调用次数。
        if kwargs.get("label") == "llm_main":
            turn["n"] += 1
            if "TURN1_MARKER" in prompt:
                return json.dumps(
                    {"narrative": "叙事一。",
                     "state_changes": [{"type": "status_change", "target": "player",
                                        "field": "_extensions",
                                        "value": json.dumps(ext, ensure_ascii=False)}],
                     "nearby_characters": [],
                     "info_panel": "无名修士：查克拉充足"},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"narrative": "叙事二。", "state_changes": [], "nearby_characters": [],
                 "info_panel": echo},
                ensure_ascii=False,
            )
        return json.dumps({"narrative": "x", "state_changes": [], "nearby_characters": []},
                          ensure_ascii=False)

    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r1 = await client_a.post(f"/sessions/{session_id}/turn",
                                 json={"input": "TURN1_MARKER 建立栏目「技能栏·粘遁」：记录技能"})
        assert r1.status_code == 200, r1.text
        r2 = await client_a.post(f"/sessions/{session_id}/turn",
                                 json={"input": "TURN2_MARKER 继续"})
        assert r2.status_code == 200, r2.text

    body = r2.json()
    # ① 抄写分节被剔除，但同轮的其他内容保留
    assert "技能栏·粘遁" not in body["info_panel"]
    assert "【交互人物】" in body["info_panel"]
    assert "无名修士：查克拉充足" in body["info_panel"]
    # ② 权威主角面板仍承载该栏目 → 信息没有丢，只是不再重复
    assert "扩展" in body["player_panel"]
    assert "技能栏·粘遁" in body["player_panel"]
    # ③ 落库版本同样已剔除 → 下一轮回喂不再带重复
    stored = await memory_manager.get_latest_info_panel(db, session_id)
    assert "技能栏·粘遁" not in stored


# ── 自定义性格/身份存储修复（__custom__ 不泄漏）───────────────

@pytest.mark.asyncio
async def test_custom_personality_not_leaking_marker(db, client_a):
    """form 路径选「自定义性格」→ attrs.personality 存自定义文本而非 __custom__。"""
    from ane.game_engine import game_engine
    from ane.database.models import Player
    from sqlalchemy import select
    info = await game_engine.create_session(db, user_id=USER_A, name="自定义测试")
    session_id = info["session_id"]

    r = await client_a.post(
        f"/sessions/{session_id}/character",
        json={
            "name": "测试角色",
            "fields": {
                "personality": "__custom__",
                "personality_custom": "我的自定义性格：坚韧内敛",
                "identity": "见习海贼",
            },
        },
    )
    assert r.status_code == 200, r.text

    player = (await db.execute(
        select(Player).where(Player.session_id == session_id)
    )).scalar_one()
    attrs = dict(player.attributes or {})
    # 核心断言：personality 不再是 __custom__ 标记
    assert attrs.get("personality") == "我的自定义性格：坚韧内敛"
    assert attrs.get("personality_custom") == "我的自定义性格：坚韧内敛"
    assert "__custom__" not in str(attrs.get("personality"))


# ── max_tokens 透传（🛠 滑动条自定义输出上限）───────────────

@pytest.mark.asyncio
async def test_max_tokens_passthrough(db, client_a, mock_llm):
    """turn 请求的 max_tokens 透传给 LLM 调用。"""
    from ane.game_engine import game_engine
    info = await game_engine.create_session(db, user_id=USER_A, name="token测试")
    session_id = info["session_id"]

    captured = {}
    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "llm_main":
            captured["max_tokens"] = kwargs.get("max_tokens")
        return json.dumps(
            {"narrative": "一段叙事。", "state_changes": [], "nearby_characters": []},
            ensure_ascii=False,
        )
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(
            f"/sessions/{session_id}/turn",
            json={"input": "测试", "max_tokens": 8192},
        )
    assert r.status_code == 200, r.text
    assert captured.get("max_tokens") == 8192


# ── 短输出自动重试 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_short_narrative_triggers_retry(db, client_a, mock_llm):
    """narrative 低于字数下限 → 重试一次写充分。"""
    from ane.game_engine import game_engine
    info = await game_engine.create_session(db, user_id=USER_A, name="短输出重试")
    session_id = info["session_id"]

    call_count = {"n": 0}
    captured = {}

    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") != "llm_main":
            return json.dumps({"narrative": "x", "state_changes": []}, ensure_ascii=False)
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 第一次：极短正文（低于 500 下限）
            captured["first_has_retry_hint"] = "远低于要求的" in prompt
            return json.dumps(
                {"narrative": "晨雾未散。你站在码头边。", "state_changes": [], "nearby_characters": []},
                ensure_ascii=False,
            )
        # 第二次（重试）：写充分
        captured["retry_triggered"] = "重新输出本轮叙事" in prompt
        return json.dumps(
            {"narrative": "晨雾未散，海面泛着灰白的光。你蹲在码头木桩上攥着钓线，浪头拍在桩腿发出闷响。" * 20,
             "state_changes": [], "nearby_characters": []},
            ensure_ascii=False,
        )

    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(
            f"/sessions/{session_id}/turn",
            json={"input": "等路飞等人到来", "word_count_min": 500, "word_count_max": 1200},
        )
    assert r.status_code == 200, r.text
    # 重试被触发
    assert call_count["n"] >= 2, "短输出应触发重试"
    assert captured.get("retry_triggered") is True
    # 最终正文达到要求（重试后的长文本被采用）
    narrative = r.json()["narrative"]
    assert len(narrative) > 300


# ── 关系人名归一化（简称→全名防重复）─────────────────────────

@pytest.mark.asyncio
async def test_relationship_name_normalization(db, client_a, mock_llm):
    """LLM 输出简称「路飞」时，归一化到已登记全名「蒙奇·D·路飞」，不重复建档。"""
    from ane.game_engine import game_engine
    from ane.database.models import NPC, NPC_Relationship
    from sqlalchemy import select
    info = await game_engine.create_session(db, user_id=USER_A, name="关系归一化")
    session_id = info["session_id"]
    # 预置已登记 NPC 全名
    db.add(NPC(session_id=session_id, name="蒙奇·D·路飞", is_important=False))
    await db.commit()

    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "llm_main":
            # LLM 输出简称「路飞」的关系
            return json.dumps(
                {"narrative": "一段叙事。", "state_changes": [],
                 "player_relationships": [{"name": "路飞", "description": "草帽小子，目标海贼王"}]},
                ensure_ascii=False,
            )
        return json.dumps({"narrative": "x", "state_changes": []}, ensure_ascii=False)

    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(f"/sessions/{session_id}/turn", json={"input": "遇到路飞"})
    assert r.status_code == 200, r.text

    # 不应创建第二个 NPC
    npcs = (await db.execute(select(NPC).where(NPC.session_id == session_id))).scalars().all()
    assert len(npcs) == 1, f"应只有1个NPC, 实际 {len(npcs)}: {[n.name for n in npcs]}"
    assert npcs[0].name == "蒙奇·D·路飞"
    # 关系边应挂到全名
    rels = (await db.execute(select(NPC_Relationship).where(NPC_Relationship.session_id == session_id))).scalars().all()
    assert len(rels) == 1
    assert rels[0].target_name == "蒙奇·D·路飞"


@pytest.mark.asyncio
async def test_relationship_no_prefix_confusion(db, client_a, mock_llm):
    """「林星」不得误并入「林星如」（前缀≠简称）——各自独立建档。"""
    from ane.game_engine import game_engine
    from ane.database.models import NPC
    from sqlalchemy import select
    info = await game_engine.create_session(db, user_id=USER_A, name="前缀混淆")
    session_id = info["session_id"]
    db.add(NPC(session_id=session_id, name="林星如", is_important=False))
    await db.commit()

    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "llm_main":
            # LLM 输出「林星」——与「林星如」是前缀关系，不应合并
            return json.dumps(
                {"narrative": "一段叙事。", "state_changes": [],
                 "player_relationships": [{"name": "林星", "description": "另一人"}]},
                ensure_ascii=False,
            )
        return json.dumps({"narrative": "x", "state_changes": []}, ensure_ascii=False)

    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(f"/sessions/{session_id}/turn", json={"input": "遇到林星"})
    assert r.status_code == 200, r.text

    npcs = (await db.execute(select(NPC).where(NPC.session_id == session_id))).scalars().all()
    names = sorted(n.name for n in npcs)
    assert names == ["林星", "林星如"], f"不应合并前缀名字, 实际: {names}"


# ── temperature 透传（🛠 滑动条）────────────────────────────

@pytest.mark.asyncio
async def test_temperature_passthrough(db, client_a, mock_llm):
    """turn 请求的 temperature 透传给 LLM 调用。"""
    from ane.game_engine import game_engine
    info = await game_engine.create_session(db, user_id=USER_A, name="温度测试")
    session_id = info["session_id"]

    captured = {}
    async def _fake(prompt, model=None, **kwargs):
        if kwargs.get("label") == "llm_main":
            captured["temperature"] = kwargs.get("temperature")
        return json.dumps(
            {"narrative": "一段叙事。", "state_changes": [], "nearby_characters": []},
            ensure_ascii=False,
        )
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(
            f"/sessions/{session_id}/turn",
            json={"input": "测试", "temperature": 1.0},
        )
    assert r.status_code == 200, r.text
    assert captured.get("temperature") == 1.0

    # 不传 temperature → 不透传（用全局默认）
    captured.clear()
    with patch.object(ModelAdapter, "generate", new_callable=AsyncMock, side_effect=_fake):
        r = await client_a.post(f"/sessions/{session_id}/turn", json={"input": "测试"})
    assert r.status_code == 200, r.text
    assert "temperature" not in captured or captured.get("temperature") is None


# ── narrative 泄漏清洗（JSON/附近人物混入正文）───────────────

def test_clean_narrative_leakage_strips_json_and_markers():
    """正文混入 JSON + 【附近人物】 → 剥离泄漏，保留前后正文。"""
    from ane.modules.output_parser import _clean_narrative_leakage
    leaked = ('晨雾未散。你站在码头边。{"name": "老渔夫", "action": "整理渔网"}\n'
              '【附近人物】[{"name": "老渔夫"}, {"name": "卖菜妇人"}]\n你转身走向镇子。')
    clean = _clean_narrative_leakage(leaked)
    assert "你转身走向镇子" in clean
    assert "{" not in clean and "【附近人物" not in clean
    assert "晨雾未散" in clean


def test_clean_narrative_leakage_keeps_normal():
    """正常正文不受影响。"""
    from ane.modules.output_parser import _clean_narrative_leakage
    normal = "晨雾未散。你站在码头边，看着海面。浪头拍岸，海风很冷。"
    assert _clean_narrative_leakage(normal) == normal
