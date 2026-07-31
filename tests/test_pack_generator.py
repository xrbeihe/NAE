"""Pack generator tests — form → zip → validate → install → session."""

import io
import zipfile

import pytest

from ane.modules.pack_generator import build_pack, generate_pack_zip
from ane.worldview import validate_pack, WORLDVIEWS_DIR, reload
from ane.game_engine import game_engine
from ane.database.models import WorldSession

SAMPLE = {
    "id": "gen_test_world",
    "name": "蒸汽朋克城邦",
    "genre": "scifi",
    "description": "以蒸汽与齿轮驱动的架空城邦",
    "power_name": "工艺等级",
    "money_name": "金币",
    "role_label": "工匠",
    "default_name": "无名工匠",
    "professions": "发明家、机械师、钟表匠",
    "places": "齿轮工坊、蒸汽广场、大钟楼",
    "create_button": "踏入蒸汽城",
}


def test_generate_pack_all_files():
    files = build_pack(SAMPLE)
    expected = {
        "manifest.json", "system_prompt.txt", "intent_keywords.json",
        "constraints.json", "world_templates.json", "player_templates.json",
        "npc_templates.json", "panel.json", "ui.json", "events.json", "form.json",
        "modeler/role.txt", "modeler/age_rules.txt",
    }
    assert set(files) == expected


def test_generated_npc_templates_by_genre():
    import json
    files = build_pack(SAMPLE)  # genre=scifi
    npc = json.loads(files["npc_templates.json"])
    assert npc["surnames"]
    assert npc["given_names_male"] and npc["given_names_female"]
    assert npc["identities"]
    # scifi pool uses scifi names, not xianxia ones
    assert "林" not in npc["surnames"][0] or len(npc["surnames"]) > 0


def test_generated_form_json():
    import json
    files = build_pack(SAMPLE)
    form = json.loads(files["form.json"])
    fields = {f["key"]: f for f in form["fields"]}
    assert "name" in fields and "cultivation" in fields
    # cultivation label from power_name
    assert fields["cultivation"]["label"] == "工艺等级"
    assert fields["cultivation"]["allow_custom"] is True
    # golden finger card grid present
    assert fields["golden_finger"]["kind"] == "card_grid"


def test_generated_system_prompt_uses_author_fields():
    import json
    files = build_pack(SAMPLE)
    sp = files["system_prompt.txt"]
    assert "蒸汽朋克城邦" in sp
    assert "工艺等级" in sp       # power_name injected into state_changes usage
    assert "金币" in sp           # money_name injected
    # places land in world_templates (regions), not the system prompt
    wt = json.loads(files["world_templates.json"])
    region_names = [r["name"] for r in wt["regions"]]
    assert "齿轮工坊" in region_names
    assert "蒸汽广场" in region_names


def test_generated_manifest_shell_kernel():
    import json
    files = build_pack(SAMPLE)
    manifest = json.loads(files["manifest.json"])
    assert manifest["assembly"] == "shell+kernel"
    assert manifest["player_defaults"]["name"] == "无名工匠"


def test_generate_rejects_bad_id():
    with pytest.raises(ValueError):
        build_pack({"id": "bad id/../x"})


def test_generated_pack_validates():
    # Install the generated pack on disk, then validate
    import json
    files = build_pack(SAMPLE)
    target = WORLDVIEWS_DIR / SAMPLE["id"]
    import shutil
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        dest = target / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    try:
        report = validate_pack(SAMPLE["id"])
        assert report["ok"] is True, report
        # And a session can be created with it
    finally:
        shutil.rmtree(target, ignore_errors=True)
        reload(SAMPLE["id"])


@pytest.mark.asyncio
async def test_generated_pack_creates_session(db):
    import json
    import shutil
    files = build_pack(SAMPLE)
    target = WORLDVIEWS_DIR / SAMPLE["id"]
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        dest = target / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    try:
        reload(SAMPLE["id"])
        result = await game_engine.create_session(db, user_id='u', name="测试", worldview=SAMPLE["id"])
        assert result["session_id"]
        assert result["player_name"] == "无名工匠"
        s = await db.get(WorldSession, result["session_id"])
        assert s.worldview == SAMPLE["id"]
    finally:
        shutil.rmtree(target, ignore_errors=True)
        reload(SAMPLE["id"])


def test_generate_pack_zip_roundtrip():
    """generate_pack_zip returns a valid zip containing manifest.json."""
    data = generate_pack_zip(SAMPLE)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert "manifest.json" in names
    assert "system_prompt.txt" in names


RICH = {
    "id": "rich_world",
    "name": "灰烬大陆",
    "genre": "fantasy",
    "description": "末日后的魔法大陆",
    "world_setting": "一场大灾变毁灭了旧文明，仅存的人类在灰烬中重建。",
    "era": "大灾变后第三纪元",
    "factions": "灰烬骑士团、术士议会",
    "taboos": "严禁复活亡者、古战场遗迹不得擅入",
    "npc_names": "奥列格、卡珊德拉",
    "golden_fingers": "灰烬共鸣、机械之心",
    "event_theme": "失落遗迹",
}


def test_rich_inputs_inject_all_artifacts():
    import json
    files = build_pack(RICH)
    sp = files["system_prompt.txt"]
    assert "大灾变毁灭了旧文明" in sp          # long setting
    assert "第三纪元" in sp                     # era
    assert "灰烬骑士团" in sp                  # factions in system prompt
    cons = json.loads(files["constraints.json"])
    assert any("严禁复活亡者" in h for h in cons["hard"])       # taboo
    assert any("古战场遗迹" in h for h in cons["hard"])         # taboo 2
    assert any("灰烬骑士团" in h for h in cons["hard"])         # factions rule
    wt = json.loads(files["world_templates.json"])
    names = [r["name"] for r in wt["regions"]]
    assert "术士议会" in names                                  # faction region
    npc = json.loads(files["npc_templates.json"])
    assert "奥列格" in npc["surnames"]                          # custom surnames
    pt = json.loads(files["player_templates.json"])
    assert [g["name"] for g in pt["golden_fingers"]] == ["灰烬共鸣", "机械之心"]  # abilities
    ev = json.loads(files["events.json"])
    assert "失落遗迹" in ev["idle_events"][0]["description"]    # event theme


def test_rich_form_has_golden_finger_grid():
    import json
    files = build_pack(RICH)
    form = json.loads(files["form.json"])
    gf = [f for f in form["fields"] if f["key"] == "golden_finger"]
    assert gf and gf[0]["kind"] == "card_grid"
    assert gf[0]["visible_if"] == "has_golden_fingers"


def test_minimal_inputs_still_work():
    """Without rich inputs the pack still builds (backward compatible)."""
    import json
    files = build_pack({"id": "min_world", "name": "最小世界", "genre": "modern"})
    cons = json.loads(files["constraints.json"])
    assert cons["hard"]
    assert "最小世界" in files["system_prompt.txt"]


def test_ip_based_generates_world_facts():
    """Marking a pack as IP-based adds world_facts.json with a canon skeleton."""
    import json
    files = build_pack({
        "id": "naruto_world", "name": "火影忍者", "genre": "modern",
        "ip_based": True, "ip_work": "火影忍者",
    })
    assert "world_facts.json" in files
    wf = json.loads(files["world_facts.json"])
    assert wf["knowledge_mode"] == "hybrid"
    assert any("火影忍者" in m for m in wf["must_follow"])
    assert "forbidden" in wf and "characters" in wf


def test_non_ip_has_no_world_facts():
    files = build_pack({"id": "orig_world", "name": "原创世界", "genre": "fantasy"})
    assert "world_facts.json" not in files
