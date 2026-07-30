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


def render_model_for_prompt(model: dict, include_nsfw: bool = False) -> str:
    """Render a stored NPC model into formatted text for llm_main's prompt.

    This is the block injected into [重要人物] or [当前交互角色] sections.
    When include_nsfw=False, the nsfw block is omitted entirely.

    Automatically migrates v1.0 data on load.
    """
    model = migrate_model_v1_0(model)
    lines = []
    basic = model.get("basic", {})

    # ── Basic identity ──
    if basic.get("name"):
        identity_parts = []
        for k in ("race", "gender", "age", "height", "cultivation", "identity", "faction", "position"):
            v = basic.get(k)
            if v and str(v) != "0":
                identity_parts.append(str(v))
        lines.append("—— 基础身份 ——")
        lines.append(" | ".join(identity_parts))

    # ── Appearance ──
    app = model.get("appearance", {})
    if app.get("overall_impression") or app.get("aura"):
        lines.append("—— 外貌（整体） ——")
        if app.get("overall_impression"):
            lines.append(f"整体印象：{app['overall_impression']}")
        if app.get("body_proportion"):
            lines.append(f"身材比例：{app['body_proportion']}")
        if app.get("aura"):
            lines.append(f"气质：{app['aura']}")

    # Face details
    face = app.get("face", {})
    face_parts = []
    for k, label in [("shape", "脸型"), ("features", "面部特征"), ("eyes", "眼睛"),
                     ("eyebrows", "眉毛"), ("nose", "鼻子"),
                     ("lips", "嘴唇"), ("expression_habit", "表情习惯")]:
        v = face.get(k)
        if v:
            face_parts.append(f"{label}：{v}")
    if face_parts:
        lines.append("—— 脸部 ——")
        lines.extend(face_parts)

    # Skin, hair
    skin = app.get("skin", {})
    skin_parts = []
    for k, label in [("color", "肤色"), ("luster", "光泽"), ("fineness", "细腻程度")]:
        v = skin.get(k)
        if v:
            skin_parts.append(f"{label}：{v}")
    if skin_parts:
        lines.append("—— 皮肤 ——")
        lines.extend(skin_parts)

    hair = app.get("hair", {})
    hair_parts = []
    for k, label in [("length", "长度"), ("style", "发型"), ("color", "发色"), ("ornament", "发饰")]:
        v = hair.get(k)
        if v:
            hair_parts.append(f"{label}：{v}")
    if hair_parts:
        lines.append("—— 头发 ——")
        lines.extend(hair_parts)

    # Body parts (non-NSFW level) — consolidated into torso
    if app.get("torso"):
        lines.append(f"躯干：{app['torso']}")
    # chest, waist, buttocks still independent sub-objects
    chest = app.get("chest", {})
    if chest.get("size") or chest.get("shape") or chest.get("fullness"):
        chest_parts = [f"{k}：{v}" for k, v in [("size", "大小"), ("shape", "形状"), ("fullness", "饱满度")] if chest.get(v, k) is not chest.get]
        # simpler approach
        chest_str = "、".join(f"{l}：{chest[k]}" for k, l in [("size","大小"),("shape","形状"),("fullness","饱满度")] if chest.get(k))
        if chest_str:
            lines.append(f"胸部：{chest_str}")
    waist = app.get("waist", {})
    waist_str = "、".join(f"{l}：{waist[k]}" for k, l in [("muscle_line","肌肉线条"),("slimness","纤细度"),("softness","柔软度")] if waist.get(k))
    if waist_str:
        lines.append(f"腰部：{waist_str}")
    if app.get("belly"):
        lines.append(f"腹部：{app['belly']}")
    buttocks = app.get("buttocks", {})
    butt_str = "、".join(f"{l}：{buttocks[k]}" for k, l in [("size","大小"),("curve","曲线")] if buttocks.get(k))
    if butt_str:
        lines.append(f"臀部：{butt_str}")

    # Legs, feet, hands
    legs = app.get("legs", {})
    legs_parts = []
    for k, label in [("length", "长度"), ("muscle_tone", "肌肉线条"), ("thighs", "大腿")]:
        v = legs.get(k)
        if v:
            legs_parts.append(f"{label}：{v}")
    if legs_parts:
        lines.append("—— 腿部 ——")
        lines.extend(legs_parts)

    feet = app.get("feet", {})
    if feet.get("shape") or feet.get("size"):
        lines.append(f"脚部：{feet.get('shape', '')}" + (f" | {feet['size']}" if feet.get('size') else ""))

    hands = app.get("hands", {})
    hands_parts = []
    for k, label in [("fingers", "手指"), ("back", "手背")]:
        v = hands.get(k)
        if v:
            hands_parts.append(f"{label}：{v}")
    if hands_parts:
        lines.append("—— 手部 ——")
        lines.extend(hands_parts)

    # ── Voice ──
    voice = model.get("voice", {})
    voice_parts = []
    for k, label in [("timbre", "音色"), ("speed", "语速"), ("volume", "音量")]:
        v = voice.get(k)
        if v:
            voice_parts.append(f"{label}：{v}")
    if voice_parts:
        lines.append("—— 声音 ——")
        lines.extend(voice_parts)

    # ── Attire ──
    attire = model.get("attire", {})
    if attire.get("clothing"):
        lines.append("—— 穿着 ——")
        lines.append(attire["clothing"])
    if attire.get("jewelry"):
        lines.append("—— 首饰 ——")
        lines.append(attire["jewelry"])

    # ── Equipment ──
    equip_list = model.get("equipment", [])
    if equip_list and isinstance(equip_list, list):
        lines.append("—— 法宝/武器 ——")
        for eq in equip_list[:5]:
            eq_name = eq.get("name", "")
            eq_desc = eq.get("description", "")
            eq_pos = eq.get("position", "")
            if eq_name:
                parts = [eq_name]
                if eq_pos:
                    parts.append(f"({eq_pos})")
                if eq_desc:
                    parts.append(f"：{eq_desc}")
                lines.append("  " + " ".join(parts))

    # ── Behavior ──
    beh = model.get("behavior", {})
    beh_parts = []
    for k, label in [("stance", "站姿"), ("sitting", "坐姿"), ("gait", "走路"),
                     ("smile", "笑容"), ("mannerisms", "小动作")]:
        v = beh.get(k)
        if v:
            beh_parts.append(f"{label}：{v}")
    if beh_parts:
        lines.append("—— 行为特征 ——")
        lines.extend(beh_parts)

    # ── Speech style ──
    speech = model.get("speech_style", {})
    speech_parts = []
    for k, label in [("word_habits", "用语习惯"), ("particles", "语气词"),
                     ("speech_rhythm", "说话节奏"), ("catchphrase", "口头禅"), ("battle_cry", "战吼"),
                     ("address_player", "对主角称呼"), ("address_others", "对他人的称呼"),
                     ("when_angry", "生气时的表现")]:
        v = speech.get(k)
        if v:
            speech_parts.append(f"{label}：{v}")
    if speech_parts:
        lines.append("—— 说话风格 ——")
        lines.extend(speech_parts)

    # ── Combat style ──
    combat = model.get("combat_style", {})
    combat_parts = []
    for k, label in [("preference", "战斗偏好"), ("weapon_usage", "法器使用习惯"),
                     ("spirit_power_signature", "灵力特征")]:
        v = combat.get(k)
        if v:
            combat_parts.append(f"{label}：{v}")
    if combat_parts:
        lines.append("—— 出手风格 ——")
        lines.extend(combat_parts)

    # ── Personality ──
    pers = model.get("personality", {})
    pers_parts = []
    for k, label in [("core", "核心性格"), ("values", "价值观"), ("principles", "原则"),
                     ("fears", "害怕"),
                     ("likes", "喜欢"), ("obsession", "执念")]:
        v = pers.get(k)
        if v:
            pers_parts.append(f"{label}：{v}")
    if pers_parts:
        lines.append("—— 性格 ——")
        lines.extend(pers_parts)

    # ── Background ──
    bg = model.get("background", {})
    bg_parts = []
    for k, label in [("history", "过往经历"), ("major_events", "重大事件"),
                     ("faction_affiliation", "势力所属"), ("family", "家族")]:
        v = bg.get(k)
        if v:
            bg_parts.append(f"{label}：{v}")
    if bg_parts:
        lines.append("—— 背景 ——")
        lines.extend(bg_parts)

    # ── Knowledge bounds ──
    kb = model.get("knowledge_bounds", {})
    if kb.get("knows") or kb.get("does_not_know") or kb.get("suspicious_of"):
        lines.append("—— 信息边界 ——")
        if kb.get("knows"):
            lines.append("  知道：")
            if isinstance(kb["knows"], list):
                for item in kb["knows"][:5]:
                    lines.append(f"    · {item}")
            else:
                lines.append(f"    {kb['knows']}")
        if kb.get("does_not_know"):
            lines.append("  不知道：")
            if isinstance(kb["does_not_know"], list):
                for item in kb["does_not_know"][:5]:
                    lines.append(f"    · {item}")
            else:
                lines.append(f"    {kb['does_not_know']}")
        if kb.get("suspicious_of"):
            lines.append("  正在怀疑：")
            if isinstance(kb["suspicious_of"], list):
                for item in kb["suspicious_of"][:5]:
                    lines.append(f"    · {item}")
            else:
                lines.append(f"    {kb['suspicious_of']}")

    # ── Attitude to player ──
    att = model.get("attitude_to_player", {})
    att_parts = []
    for k, label in [("surface", "表层态度"), ("true_feelings", "真实想法"),
                     ("relationship_trend", "关系变化倾向")]:
        v = att.get(k)
        if v:
            att_parts.append(f"{label}：{v}")
    if att_parts:
        lines.append("—— 对玩家的态度 ——")
        lines.extend(att_parts)

    # ── Relationships ──
    rel = model.get("relationships", {})
    rel_lines = []
    for k, label in [("father", "父亲"), ("mother", "母亲"), ("spouse", "配偶"),
                     ("master", "师父"),
                     ("senior_brother", "师兄"), ("senior_sister", "师姐"),
                     ("junior_brother", "师弟"), ("junior_sister", "师妹"),
                     ("teacher", "师尊"), ("superior", "上级"),
                     ("subordinate", "下属"), ("lover", "恋人"),
                     ("fiance", "婚约对象"), ("beloved", "爱人"),
                     ("rival", "竞争者"), ("pursuer", "追求者")]:
        v = rel.get(k)
        if v:
            rel_lines.append(f"  {label}：{v}")
    for k, label in [("friends", "朋友"), ("enemies", "敌人")]:
        v = rel.get(k)
        if isinstance(v, list) and v:
            rel_lines.append(f"  {label}：" + "、".join(v[:5]))
    if rel_lines:
        lines.append("—— 关系网 ——")
        lines.extend(rel_lines)

    # ── NSFW (only when include_nsfw=True) ──
    if include_nsfw:
        nsfw_data = model.get("nsfw", {})
        nsfw_parts = []
        for k, label in [("is_virgin", "是否处子"), ("fertility", "生育情况"),
                         ("desire_toward_target", "对互动目标性渴望程度"),
                         ("rejection_toward_target", "对互动目标性拒绝程度"),
                         ("male_genital", "♂"), ("female_genital", "♀")]:
            v = nsfw_data.get(k)
            if v is not None and v != "":
                nsfw_parts.append(f"{label}：{v}")
        if nsfw_parts:
            lines.append("—— 身体特征 ——")
            lines.extend(nsfw_parts)

    return "\n".join(lines)
