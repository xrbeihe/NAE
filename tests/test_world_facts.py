"""world_facts.json tests — authoritative canon injection for IP worldviews."""

import pytest

from ane.modules.prompt_builder import PromptContext, PromptBuilder, WorldContext

SAMPLE_FACTS = {
    "knowledge_mode": "hybrid",
    "must_follow": ["故事开始于中忍考试之前", "鸣人尚未学会仙人模式"],
    "forbidden": ["不得出现佩恩与晓组织主线剧情", "不得让佐助提前叛逃"],
    "characters": [
        {"name": "漩涡鸣人", "desc": "九尾人柱力，木叶下忍"},
        {"name": "宇智波佐助", "desc": "写轮眼拥有者"},
    ],
}


def _build(facts):
    ctx = PromptContext()
    ctx.world_facts = facts
    ctx.world = WorldContext(name="测试世界")
    ctx.user_input = "测试"
    return PromptBuilder().build(ctx)


def test_facts_block_injected():
    prompt = _build(SAMPLE_FACTS)
    assert "【本世界权威设定】" in prompt
    assert "故事开始于中忍考试之前" in prompt          # must_follow
    assert "不得让佐助提前叛逃" in prompt            # forbidden
    assert "漩涡鸣人" in prompt and "九尾人柱力" in prompt  # characters
    assert "冲突裁定" in prompt                      # conflict resolution rule


def test_no_facts_no_block():
    prompt = _build(None)
    assert "【本世界权威设定】" not in prompt


def test_knowledge_mode_texts():
    # pack_only
    p1 = _build({"knowledge_mode": "pack_only", "must_follow": ["x"], "forbidden": [], "characters": []})
    assert "只依据本文件与世界观包中的设定行事" in p1
    # full_ip
    p2 = _build({"knowledge_mode": "full_ip", "must_follow": ["x"], "forbidden": [], "characters": []})
    assert "基于既有作品创作" in p2
    # hybrid default
    p3 = _build({"must_follow": ["x"], "forbidden": [], "characters": []})
    assert "知识使用模式（hybrid）" in p3


def test_character_strings_supported():
    """characters may also be plain strings (not just dicts)."""
    prompt = _build({"knowledge_mode": "full_ip", "must_follow": [], "forbidden": [], "characters": ["旗木卡卡西", "春野樱"]})
    assert "旗木卡卡西" in prompt
    assert "春野樱" in prompt


def test_loader_loads_world_facts():
    """A pack with world_facts.json exposes it via the loader."""
    from ane.worldview import get as get_worldview

    # Temporarily write a world_facts.json into a temp pack dir
    import json, shutil
    from ane.worldview import WORLDVIEWS_DIR, reload as wv_reload
    target = WORLDVIEWS_DIR / "wf_test_pack"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(json.dumps({"worldview_id": "wf_test_pack", "name": "测试", "version": "0.1.0"}, ensure_ascii=False), encoding="utf-8")
    (target / "world_facts.json").write_text(json.dumps(SAMPLE_FACTS, ensure_ascii=False), encoding="utf-8")
    try:
        wv_reload("wf_test_pack")
        wv = get_worldview("wf_test_pack")
        assert wv.world_facts is not None
        assert wv.world_facts["knowledge_mode"] == "hybrid"
    finally:
        shutil.rmtree(target, ignore_errors=True)
        wv_reload("wf_test_pack")


def test_no_facts_returns_none():
    from ane.worldview import get as get_worldview
    wv = get_worldview("xianxia_v1")
    assert wv.world_facts is None


# ── Timeline variants (world_facts.timelines) ─────────────

TIMELINE_FACTS = {
    "knowledge_mode": "hybrid",
    "must_follow": ["基础规则"],
    "forbidden": ["基础禁止"],
    "characters": [{"name": "基础角色", "desc": "base"}],
    "timelines": [
        {
            "id": "t1",
            "label": "忍者学校时期",
            "description": "九尾之乱后重建，鸣人6-11岁。",
            "must_follow": ["鸣人是学生，未毕业"],
            "forbidden": ["不得出现中忍考试"],
            "characters": [{"name": "海野伊鲁卡", "desc": "鸣人的老师"}],
        },
        {
            "id": "t2",
            "label": "第七班成立",
            "description": "鸣人12岁，卡卡西带班。",
        },
    ],
}


def test_timeline_variant_overrides_base():
    """Selecting a timeline replaces base must_follow/forbidden/characters."""
    from ane.game_engine import _resolve_world_facts_for_timeline
    r = _resolve_world_facts_for_timeline(TIMELINE_FACTS, "t1")
    assert r["must_follow"] == ["鸣人是学生，未毕业"]     # overridden
    assert r["forbidden"] == ["不得出现中忍考试"]
    assert r["characters"][0]["name"] == "海野伊鲁卡"
    assert r["timeline_label"] == "忍者学校时期"          # exposed for prompt
    assert r["timeline_description"].startswith("九尾之乱后")
    # knowledge_mode preserved from base
    assert r["knowledge_mode"] == "hybrid"


def test_timeline_variant_missing_keys_keep_base():
    """A variant that omits some keys falls back to base for those."""
    from ane.game_engine import _resolve_world_facts_for_timeline
    r = _resolve_world_facts_for_timeline(TIMELINE_FACTS, "t2")
    # t2 has no must_follow/forbidden/characters → base kept
    assert r["must_follow"] == ["基础规则"]
    assert r["characters"][0]["name"] == "基础角色"
    assert r["timeline_label"] == "第七班成立"


def test_no_timeline_returns_base_unchanged():
    from ane.game_engine import _resolve_world_facts_for_timeline
    r = _resolve_world_facts_for_timeline(TIMELINE_FACTS, "")
    assert r is TIMELINE_FACTS
    assert "timeline_label" not in r


def test_unknown_timeline_falls_back_to_base():
    from ane.game_engine import _resolve_world_facts_for_timeline
    r = _resolve_world_facts_for_timeline(TIMELINE_FACTS, "nope")
    assert r is TIMELINE_FACTS


def test_timeline_rendered_in_prompt():
    """The chosen timeline label/description appears in the canon block."""
    from ane.game_engine import _resolve_world_facts_for_timeline
    r = _resolve_world_facts_for_timeline(TIMELINE_FACTS, "t1")
    prompt = _build(r)
    assert "当前时间线：忍者学校时期" in prompt
    assert "九尾之乱后重建" in prompt


def test_naruto_pack_ships_timelines():
    """The naruto pack ships fine-grained timeline nodes (19 total)."""
    from ane.worldview import get as get_worldview
    wf = get_worldview("naruto_shippuden").world_facts
    timelines = wf.get("timelines") or []
    assert len(timelines) == 19
    labels = {t["id"] for t in timelines}
    expected = {
        # 鸣人出生前
        "warring_states", "konoha_founding", "first_war", "second_war", "third_war", "nine_tails_attack",
        # 鸣人出生后 —— 细分关键事件
        "academy_era", "team7_founded", "wave_mission", "chunin_exam", "sasuke_retrieval",
        "pre_shippuden", "shippuden_return", "gaara_rescue", "akatsuki_suppression",
        "sasuke_itachi", "pain_invasion", "five_kage_summit", "pre_fourth_war",
    }
    assert expected <= labels
    # each node has the essentials
    for t in timelines:
        assert t.get("id") and t.get("label") and t.get("description")
        assert isinstance(t.get("must_follow", []), list)
        assert isinstance(t.get("forbidden", []), list)

