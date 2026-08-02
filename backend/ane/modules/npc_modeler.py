"""NPC Modeler — structured character modeling for important NPCs.

Triggered once when a player marks an NPC as important (⭐).
llm_modeling generates a structured character model JSON → validated here → saved
to NPC.long_term_state["model"].

This module replaces the old HTEM-based approach entirely.
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

NPC_MODEL_VERSION = "1.1"  # v1.1: merged fields (face, torso, attire, personality, speech_style)

# Field-name → Chinese label map used by the generic renderer. Covers the
# xianxia default schema (90+ fields); per-worldview schemas may supply their
# own labels via {"label": "…"} nodes, which take precedence over this map.
_FIELD_LABELS = {
    # basic
    "name": "姓名", "race": "种族", "gender": "性别", "age": "年龄", "height": "身高",
    "cultivation": "修为", "identity": "身份", "faction": "势力", "position": "职位",
    # appearance
    "appearance": "外貌", "overall_impression": "整体印象", "body_proportion": "身材比例",
    "aura": "气质", "face": "脸部", "shape": "脸型", "features": "面部特征", "eyes": "眼睛",
    "eyebrows": "眉毛", "nose": "鼻子", "lips": "嘴唇", "expression_habit": "表情习惯",
    "skin": "皮肤", "color": "肤色", "luster": "光泽", "fineness": "细腻程度",
    "hair": "头发", "length": "长度", "style": "发型", "ornament": "发饰",
    "torso": "躯干", "chest": "胸部", "size": "大小", "fullness": "饱满度",
    "waist": "腰部", "muscle_line": "肌肉线条", "slimness": "纤细度", "softness": "柔软度",
    "belly": "腹部", "buttocks": "臀部", "curve": "曲线",
    "legs": "腿部", "muscle_tone": "肌肉线条", "thighs": "大腿",
    "feet": "脚部", "barefoot": "赤足", "hands": "手部", "fingers": "手指", "back": "手背",
    # voice
    "voice": "声音", "timbre": "音色", "speed": "语速", "volume": "音量",
    # attire
    "attire": "穿着", "clothing": "服饰", "jewelry": "首饰",
    # equipment
    "equipment": "装备", "description": "描述", "position": "部位",
    # behavior
    "behavior": "行为特征", "stance": "站姿", "sitting": "坐姿", "gait": "走路",
    "smile": "笑容", "mannerisms": "小动作",
    # speech_style
    "speech_style": "说话风格", "word_habits": "用语习惯", "particles": "语气词",
    "speech_rhythm": "说话节奏", "catchphrase": "口头禅", "battle_cry": "战吼",
    "address_player": "对主角称呼", "address_others": "对他人的称呼", "when_angry": "生气时的表现",
    # combat_style
    "combat_style": "出手风格", "preference": "战斗偏好", "weapon_usage": "法器使用习惯",
    "spirit_power_signature": "灵力特征",
    # personality
    "personality": "性格", "core": "核心性格", "values": "价值观", "principles": "原则",
    "fears": "害怕", "likes": "喜欢", "obsession": "执念",
    # background
    "background": "背景", "history": "过往经历", "major_events": "重大事件",
    "faction_affiliation": "势力所属", "family": "家族",
    # knowledge_bounds
    "knowledge_bounds": "信息边界", "knows": "知道", "does_not_know": "不知道",
    "suspicious_of": "正在怀疑",
    # attitude_to_player
    "attitude_to_player": "对玩家的态度", "surface": "表层态度", "true_feelings": "真实想法",
    "relationship_trend": "关系变化倾向",
    # relationships
    "relationships": "关系网", "father": "父亲", "mother": "母亲", "spouse": "配偶",
    "master": "师父", "senior_brother": "师兄", "senior_sister": "师姐",
    "junior_brother": "师弟", "junior_sister": "师妹", "teacher": "师尊",
    "superior": "上级", "subordinate": "下属", "lover": "恋人", "fiance": "婚约对象",
    "beloved": "爱人", "rival": "竞争者", "pursuer": "追求者", "friends": "朋友", "enemies": "敌人",
    "family": "家人", "allies": "盟友", "liege_lord": "领主", "crush": "暗恋对象",
    "exes": "前任", "colleagues": "同事", "friends_and_foes": "亦敌亦友",
    "relation_to_player": "与主角关系", "debtors": "欠债者", "benefactors": "恩人",
    # cultivation
    "cultivation_details": "修炼详情", "spiritual_root": "灵根", "special_constitution": "特殊体质",
    "techniques": "功法", "divine_powers": "神通", "ring_storage": "储物戒", "wealth": "家产",
    # nsfw
    "nsfw": "身体特征", "is_virgin": "是否处子", "fertility": "生育情况",
    "desire_toward_target": "对互动目标性渴望程度", "rejection_toward_target": "对互动目标性拒绝程度",
    "male_genital": "♂", "female_genital": "♀",
}

def migrate_model_v1_0(data: dict) -> dict:
    """Migrate v1.0 model data to v1.1 (merged field structure).

    Called when loading an old NPC model so it maps to the new 65-field schema.
    This is a forward-only migration — v1.1 models stay as-is.
    """
    if data.get("model_version", "") >= "1.1":
        return data
    app = data.setdefault("appearance", {})
    # face: lashes→eyes, teeth→lips, dimples/tear_mole→features
    face = app.get("face", {})
    if face.get("lashes") and not face.get("eyes"):
        face["eyes"] = "睫毛：" + face.pop("lashes", "")
    elif face.get("lashes"):
        face["eyes"] = (face.get("eyes", "") or "") + "；睫毛：" + face.pop("lashes", "")
    if face.get("teeth") and not face.get("lips"):
        face["lips"] = "牙齿：" + face.pop("teeth", "")
    elif face.get("teeth"):
        face["lips"] = (face.get("lips", "") or "") + "；牙齿：" + face.pop("teeth", "")
    dimples = face.pop("dimples", "")
    tear_mole = face.pop("tear_mole", "")
    if (dimples or tear_mole) and not face.get("features"):
        face["features"] = ("酒窝：" + dimples if dimples else "") + ("；" if dimples and tear_mole else "") + ("泪痣：" + tear_mole if tear_mole else "")
    elif dimples:
        face["features"] = (face.get("features", "") or "") + "；酒窝：" + dimples
    elif tear_mole:
        face["features"] = (face.get("features", "") or "") + "；泪痣：" + tear_mole

    # torso: merge neck/collarbone/shoulders/belly/hips
    torso_parts = []
    for key, label in [("neck", "脖颈"), ("collarbone", "锁骨"), ("shoulders", "肩膀"), ("belly", "腹部"), ("hips", "胯部")]:
        val = app.pop(key, "")
        if val:
            torso_parts.append(f"{label}：{val}")
    if torso_parts and not app.get("torso"):
        app["torso"] = "；".join(torso_parts)

    # clothing + jewelry → attire
    cloth = data.pop("clothing", {})
    jew = data.pop("jewelry", {})
    if (cloth or jew) and not data.get("attire"):
        at = {}
        if cloth:
            parts = [f"{k}：{v}" for k, v in cloth.items() if v and k != "model_version"]
            at["clothing"] = "；".join(parts)
        if jew:
            parts = [f"{k}：{v}" for k, v in jew.items() if v and k != "model_version"]
            at["jewelry"] = "；".join(parts)
        data["attire"] = at

    # personality: bottom_line→principles, interests→likes, aversions→fears
    pers = data.setdefault("personality", {})
    bl = pers.pop("bottom_line", "")
    if bl and not pers.get("principles"):
        pers["principles"] = bl
    intr = pers.pop("interests", "")
    if intr:
        pers["likes"] = (pers.get("likes", "") or "") + ("；" if pers.get("likes") else "") + intr
    avr = pers.pop("aversions", "")
    if avr:
        pers["fears"] = (pers.get("fears", "") or "") + ("；" if pers.get("fears") else "") + avr

    # behavior: speech_rhythm+catchphrase→speech_style
    beh = data.get("behavior", {})
    sp = data.setdefault("speech_style", {})
    sr = beh.pop("speech_rhythm", "")
    if sr and not sp.get("speech_rhythm"):
        sp["speech_rhythm"] = sr
    cp = beh.pop("catchphrase", "")
    if cp and not sp.get("catchphrase"):
        sp["catchphrase"] = cp

    # combat_style: battle_cry→speech_style
    comb = data.get("combat_style", {})
    bc = comb.pop("battle_cry", "")
    if bc and not sp.get("battle_cry"):
        sp["battle_cry"] = bc

    data["model_version"] = "1.1"
    return data


def parse_modeling_response(raw: str) -> dict[str, Any] | None:
    """Extract and validate the JSON model from the LLM response.

    Returns the parsed model dict, or None if parsing fails.
    """
    if not raw:
        return None

    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        # Find the first { after ```
        first_brace = text.find("{")
        if first_brace != -1:
            text = text[first_brace:]
        else:
            text = text.split("```", 2)[1] if "```" in text[3:] else text
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]

    # Try JSON parsing
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try extracting JSON from braces
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            json_str = brace_match.group()
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                # Try json_repair for truncated / malformed LLM output
                try:
                    import json_repair
                    data = json_repair.loads(json_str)
                    logger.info(f"json_repair recovered modeling JSON ({len(json_str)} chars)")
                except Exception:
                    logger.warning("llm_modeling: JSON parse failed after brace extraction")
                    return None
        else:
            logger.warning("llm_modeling: No JSON found in response")
            return None

    # Basic validation: must have at least basic.name
    basic = data.get("basic", {})
    if not basic.get("name"):
        logger.warning("llm_modeling: Response missing basic.name")
        return None

    # Ensure model_version
    data["model_version"] = NPC_MODEL_VERSION
    return data


def render_model_for_prompt(model: dict, include_nsfw: bool = False, schema: dict | None = None) -> str:
    """Render a stored NPC model into formatted text for llm_main's prompt.

    This is the block injected into [重要人物] or [当前交互角色] sections.
    When include_nsfw=False, the nsfw block is omitted entirely.

    Worldview-generic: iterates the model dict itself (not a hardcoded field
    set), so per-worldview schemas (modeler/schema.json) render naturally.
    Chinese labels come from a keymap, then from the schema's `label` nodes,
    then fall back to the raw field name. Automatically migrates v1.0 data.
    """
    model = migrate_model_v1_0(model)
    lines = []
    labels = _FIELD_LABELS

    def _label(key: str, node=None) -> str:
        """Resolve a human-readable label for a field key."""
        if isinstance(node, dict) and node.get("label"):
            return str(node["label"])
        return labels.get(key, key)

    def _fmt(value) -> str:
        if isinstance(value, bool):
            return "是" if value else "否"
        if value is None:
            return ""
        s = str(value)
        return s if len(s) <= 200 else s[:200] + "…"

    def _render_dict(d: dict, out: list, depth: int = 1, is_equipment: bool = False) -> None:
        """Render a nested dict into labelled lines."""
        for k, v in d.items():
            if k == "model_version" or k == "model_version":
                continue
            if is_equipment and k == "position":
                continue  # 装备不显示位置字段
            if isinstance(v, dict):
                # sub-object → grouped lines
                sub = []
                _render_dict(v, sub, depth + 1)
                if sub:
                    out.append("  " * depth + f"{_label(k)}：")
                    out.extend(sub)
            elif isinstance(v, list):
                if not v:
                    continue
                out.append("  " * depth + f"{_label(k)}：")
                for item in v[:8]:
                    if isinstance(item, dict):
                        # 装备数组元素：name 用「名称」标签，跳过 position
                        is_eq = (k == "equipment")
                        item_lines = []
                        _render_dict(item, item_lines, depth + 1, is_equipment=is_eq)
                        out.extend(item_lines)
                    else:
                        out.append("  " * (depth + 1) + "· " + _fmt(item))
            else:
                if v is None or v == "":
                    continue
                if is_equipment and k == "name":
                    out.append("  " * depth + f"名称：{_fmt(v)}")
                elif isinstance(v, bool):
                    out.append("  " * depth + f"{_label(k)}：{'是' if v else '否'}")
                else:
                    out.append("  " * depth + f"{_label(k)}：{_fmt(v)}")

    # ── Basic identity (single line) ──
    basic = model.get("basic") or {}
    basic_parts = []
    for k, v in basic.items():
        if v is None or str(v) in ("", "0", "0.0"):
            continue
        if isinstance(v, (dict, list)):
            continue
        basic_parts.append(f"{_label(k)} {v}")
    if basic_parts:
        lines.append("—— 基础身份 ——")
        lines.append(" | ".join(basic_parts))

    # ── Everything else, grouped by top-level section ──
    for section, data in model.items():
        if section in ("basic", "model_version", "nsfw"):
            continue
        if not isinstance(data, dict):
            continue
        if not any(v not in (None, "", [], {}) for v in data.values()):
            continue
        # nsfw handled separately below
        lines.append(f"—— {_label(section)} ——")
        _render_dict(data, lines)

    # ── NSFW (only when include_nsfw=True) ──
    if include_nsfw:
        nsfw_data = model.get("nsfw")
        if isinstance(nsfw_data, dict):
            nsfw_parts = []
            for k, v in nsfw_data.items():
                if v is None or v == "":
                    continue
                nsfw_parts.append(f"{_label(k)}：{_fmt(v)}")
            if nsfw_parts:
                lines.append("—— 身体特征 ——")
                lines.extend(nsfw_parts)

    return "\n".join(lines)
