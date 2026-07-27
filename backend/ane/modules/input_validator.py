"""Input validation: safety check + intent classification (keyword-based for Phase 1)."""

import re
import logging
from dataclasses import dataclass

from ane.config import TIME_PER_INTENT

logger = logging.getLogger(__name__)

# ── Safety ───────────────────────────────────────────────────

# Content safety patterns
_BLOCKED_PATTERNS = [
    r"(?i)\b(hate|violence|illegal|exploit)\b",
]

# Prompt injection patterns — inputs that try to hijack the AI's role
_INJECTION_PATTERNS = [
    r"(?i)(忽略|忘记|无视).{0,10}(之前|上面|前述|所有|全部).{0,20}(指令|规则|提示|设定|要求)",
    r"(?i)(ignore|forget|disregard|override).{0,10}(previous|above|all|prior).{0,20}(instruction|rule|prompt|directive)",
    r"(?i)(你.{0,5}不是).{0,20}(叙事|讲述|故事|AI|助手)",
    r"(?i)(you.{0,5}are.{0,5}not).{0,20}(narrator|storyteller|AI|assistant)",
    r"(?i)(现在.{0,5}你是|从.{0,5}现在.{0,5}开始.{0,5}你是|你的.{0,5}新.{0,5}身份)",
    r"(?i)(now.{0,5}you.{0,5}are|from.{0,5}now.{0,5}on.{0,5}you.{0,5}are)",
    r"(?i)(system.{0,5}prompt|system.{0,5}message|忽略.{0,5}system)",
    r"(?i)(输出|打印|显示).{0,5}(系统|system).{0,5}(提示|prompt|指令)",
    r"(?i)(ignore|print|reveal|show|dump).{0,5}(system|your).{0,5}(prompt|instruction)",
    r"(?i)DAN\b|越狱|jailbreak",
]

# System commands that bypass normal narrative flow
_SYSTEM_COMMANDS = {
    "/help": "system_help",
    "/facts": "system_list_facts",
    "/addfact": "system_add_fact",
    "/describe-world": "system_describe_world",
    "描述这个世界": "system_describe_world",
}

# ── Intent classification ────────────────────────────────────

# Keyword patterns → intent type (Phase 1: simple keyword matching)
# Order matters: more specific patterns must come before general ones.
INTENT_PATTERNS: list[tuple[list[str], str]] = [
    (["做爱", "上床", "亲热", "交合", "云雨", "欢好", "合欢", "行房",
      "我要你", "要我", "占有我", "进来吧", "要我吧",
      "继续", "别停", "用力", "快点", "慢点",
      "脱衣服", "宽衣", "解带",
      "干你", "干她", "干我", "操你", "操我", "操死", "插进", "插你", "插我", "插她",
      r"干\S*穴", r"操\S*穴", "小穴", "肉棒", "鸡巴"], "nsfw"),
    # NTR-specific intent — regex patterns for pair-based matching (一男一女)
    (["出轨", "偷情", "绿帽", "绿奴", "背叛", "第三者",
      "捉奸", "有夫之妇", "有妇之夫",
      "偷欢", "私通", "通奸",
      r"当着\S*(?:老公|老婆|丈夫|妻子|男友|女友|未婚夫|未婚妻)\S*面",
      r"在他\S*(?:老公|老婆|丈夫|妻子|男友|女友|未婚夫|未婚妻)\S*面前",
      r"当着.*的面干", r"当着.*的面操",
      r"别人的\S*(?:老婆|老公|丈夫|妻子|女人|男人|男友|女友)",
      r"勾引\S*(?:人妻|人夫|有夫之妇|有妇之夫)",
      r"在\S*(?:老公|老婆|丈夫|妻子|男友|未婚夫|未婚妻)\S*(?:旁边|面前|隔壁)",
      r"一边.*一边.*(?:电话|视频).*(?:老公|老婆|丈夫|妻子|男友)",
      r"给.*戴绿帽",
      "偷欢", "私通", "通奸",
      "人妻", "人夫",
      "NTR", "NTL", "寝取", "寝取り",
    ], "ntr"),
    (["时间跳过", "时间快进", "时间推进", "跳过时间", "加速时间",
      "快进时间", "快进", "转眼又是新的一年", "转眼已是", "时间过了一年",
      "时间直接", "直接到", "直接跳", "转到明年", "跳到明年"], "time_skip"),
    (["使用", "服用", "装备", "穿戴", "卸下"], "use_item"),
    (["攻击", "战斗", "出手", "斩杀", "击杀", "制服"], "combat"),
    (["买", "卖", "交易", "购买", "出售", "交换", "价格"], "trade"),
    (["检查", "查看", "观察", "调查", "探查", "看看"], "inspect"),
    (["对话", "交谈", "聊聊", "问", "说", "告诉", "打听", "询问"], "dialogue"),
    (["走", "去", "前往", "出发", "移动", "到", "离开", "回", "进入"], "travel"),
    # cultivate must come last because its keywords easily match descriptive/
    # passive contexts. If any prior intent matched, cultivate won't override.
    (["我要闭关", "开始闭关", "闭关修炼", "打坐", "突破", "晋级",
      "修炼突破", "闭关突破", "我要修炼", "我要修行"], "cultivate"),
]

# Exclusion contexts: if a cultivate keyword appears in one of these
# passive/descriptive patterns, demote to dialogue.
_CULTIVATE_EXCLUSION = re.compile(
    r'(?:教|教导?|指导?|指点?|让|叫|帮|替|为|给).{0,6}'
    r'(?:修炼|修行|闭关)'
    r'|(?:修炼|修行|闭关).{0,4}(?:了|过|的|时|之|后|中|者|人)'
    r'|(?:讲|说|谈|聊|问|告诉|描述).{0,10}(?:修炼|修行|闭关)'
    r'|(?:修炼|修行|闭关).{0,10}(?:讲|说|谈|聊)'
)

# ── NSFW body keyword filter (for DeepSeek content safety bypass) ──

_NSFW_BODY_WORDS = [
    # 器官
    "阴道", "阴茎", "龟头", "阴蒂", "阴唇",
    "肉棒", "小穴", "蜜穴", "嫩穴", "鸡巴",
    "子宫", "花蕊", "精液", "爱液",
    # 动作（多字短语）
    "肏", "插入", "抽送", "抽插", "内射",
    "吸允", "舔弄", "舔舐",
    "深喉", "把玩", "套弄",
    # 状态
    "赤裸", "一丝不挂", "全裸",
    "淫荡", "淫秽",
    # 修仙
    "双修", "炉鼎",
]

# ── Chinese numeral parser ───────────────────────────────────

# Map Chinese numeral characters to values
_CN_DIGITS = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
_CN_UNITS  = {'十':10, '百':100, '千':1000, '万':10000}

_CN_NUM_RE = re.compile(r'([一二三四五六七八九十百千万]+)\s*字')


def _parse_chinese_numeral(text: str) -> int:
    """Parse Chinese numeral expressions like 五百三十, 三千, 二百五十 etc.

    Uses a position-aware algorithm that correctly handles:
      - 五十 = 50 (not 15)
      - 五百三十 = 530 (not 513)
      - 三千 = 3000
      - 二百五 = 205 (colloquial)
    """
    match = _CN_NUM_RE.search(text)
    if not match:
        return 0

    cn = match.group(1)
    total = 0
    section = 0  # accumulator for the current unit group

    for ch in cn:
        if ch in _CN_DIGITS:
            section = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if section == 0:
                # Leading unit: 十 alone means 10
                section = 1
            # For 万 (10000) and above, accumulate and reset
            if unit >= 10000:
                total = (total + section) * unit
                section = 0
            else:
                section *= unit
                # For 十/百/千, add to total and reset section
                # ... but only if not followed by a digit
                total += section
                section = 0
        # else: ignore non-numeral characters

    total += section  # add trailing digit if any
    return total

# Derive time hints from config's TIME_PER_INTENT
def _time_hint(intent: str) -> str:
    ticks = TIME_PER_INTENT.get(intent, 1)
    if ticks <= 2:
        return "short"
    elif ticks <= 12:
        return "medium"
    elif ticks <= 720:
        return "long"
    else:
        return "very_long"


@dataclass
class ValidationResult:
    is_safe: bool
    is_system_command: bool
    system_command: str | None       # e.g. "/summary"
    intent: str                       # "dialogue" | "travel" | "cultivate" | ...
    time_hint: str                    # "short" | "medium" | "long"
    cleaned_input: str                # input with injection patterns sanitized
    injection_detected: bool = False
    target_word_count: int = 0        # user-specified word count (0 = no target)
    mark_important_npc: bool = False  # user checked the "重要人物" checkbox
    is_ntr: bool = False              # whether input involves NTR themes
    nsfw_confirmed: bool = False      # user appended "HO" to confirm NSFW intent


def validate(user_input: str, mark_important_npc: bool = False, is_adult: bool = True) -> ValidationResult:
    """Validate and classify user input. Phase 1: keyword-based + injection detection."""
    text = user_input.strip()

    # 1. Safety check
    is_safe = True
    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, text):
            is_safe = False
            logger.warning(f"Blocked input (content safety): {text[:50]}...")
            break

    # 2. Injection detection — sanitize but don't block (Phase 1: warning + sanitize)
    injection_detected = False
    cleaned = text
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, text):
            injection_detected = True
            logger.warning(f"Injection pattern detected: '{pattern}' in input: {text[:80]}...")
            # Replace injection-like content with a safe marker
            cleaned = re.sub(pattern, "[内容已过滤]", cleaned, flags=re.IGNORECASE)
            break

    # 3. System command detection (only exact prefix match for most)
    is_system_cmd = False
    cmd = None
    for prefix, cmd_type in _SYSTEM_COMMANDS.items():
        if prefix == "描述这个世界":
            # Exact match only — "描述这个世界" can appear in normal text
            if text.strip() == prefix or text.strip().startswith(prefix + "\n"):
                is_system_cmd = True
                cmd = cmd_type
                break
        elif text.startswith(prefix):
            is_system_cmd = True
            cmd = cmd_type
            break

    # 4. Intent classification (supports both plain keywords and regex patterns)
    intent = "dialogue"  # default
    for keywords, intent_type in INTENT_PATTERNS:
        matched = False
        for kw in keywords:
            # Regex patterns start with r" in the source — detected by containing regex metacharacters
            if any(c in kw for c in ['.', '*', '?', '+', '[', ']', '(', ')', '\\', '|', '^', '$']):
                if re.search(kw, text):
                    matched = True
                    break
            else:
                if kw in text:
                    matched = True
                    break
        if matched:
            intent = intent_type
            break

    # Cultivate exclusion: if the text is describing cultivation (teaching,
    # storytelling, past events) rather than commanding it, demote to dialogue.
    if intent == "cultivate" and _CULTIVATE_EXCLUSION.search(text):
        logger.info(
            "Cultivate intent demoted to dialogue — passive/descriptive context "
            "detected: %s", text[:60]
        )
        intent = "dialogue"

    # 5. Time hint
    time_hint = _time_hint(intent)

    # 6. Word count target detection (e.g. "500字", "三百五十字")
    target_word_count = 0
    wc_match = re.search(r'(\d+)\s*字', text)
    if wc_match:
        target_word_count = int(wc_match.group(1))
    else:
        target_word_count = _parse_chinese_numeral(text)

    # 7. NTR detection: check if input contains NTR keywords (specific pair-based patterns)
    ntr_patterns = [
        "出轨", "偷情", "绿帽", "绿奴", "捉奸",
        "人妻", "人夫", "背叛", "第三者",
        "偷欢", "私通", "通奸",
        r"当着\S*(?:老公|老婆|丈夫|妻子)\S*面",
        r"别人的\S*(?:老婆|老公|妻子|女人|男人)",
        r"在\S*(?:老公|老婆|丈夫|妻子|男友|未婚夫)\S*(?:旁边|面前|隔壁)",
        r"勾引.*人[妻夫]",
        r"给.*戴绿帽",
        "NTR", "NTL", "寝取",
    ]
    is_ntr = False
    for pat in ntr_patterns:
        if any(c in pat for c in ['.', '*', '?', '+', '[', ']', '(', ')', '\\', '|', '^', '$']):
            if re.search(pat, text):
                is_ntr = True
                break
        else:
            if pat in text:
                is_ntr = True
                break

    # ── Context exclusion: demote NSFW/NTR when keywords appear in
    #     narrative/descriptive/third-person contexts ──
    if intent in ("nsfw", "ntr") or is_ntr:
        _non_nsfw_context = re.compile(
            r'(?:听说|据说|故事|记载|传说|'  # 第三人称叙事信号
            r'被.{0,4}(?:背叛|出卖|绿|NTR)|'  # 被动承受
            r'犯了.{0,4}(?:背叛|出轨)|'        # 第三方陈述
            r'因.{0,4}(?:出轨|背叛)|'
            r'(?:背叛|出轨).{0,4}(?:了|过|的|者|案|罪)|'
            r'人妻.{0,4}(?:身份|长老|修士|高手|前辈|道友)|'
            r'人夫.{0,4}(?:身份|长老|修士|高手|前辈|道友)'
            r')'
        )
        if _non_nsfw_context.search(text):
            # Don't demote when strong action verbs override the context
            _override = re.compile(
                r'(?:干你|干她|干我|操死|射进|插进|进入她|进入我|操你|'
                r'当着.{0,8}(?:老公|老婆|丈夫|妻子).{0,4}面)'
            )
            if _override.search(text):
                pass  # keep original intent
            else:
                logger.info(
                    f"Intent {intent} demoted to dialogue — passive/descriptive context: {text[:60]}"
                )
                intent = "dialogue"
                is_ntr = False

    if target_word_count:
        logger.info(f"Word count target detected: {target_word_count}字")

    # 8. NSFW confirmation: user appended "HO" — input must end with HO after strip
    # "ABCHO" = True, "ABCHO " = False (HO has space before it), "ABCHO。" = False
    nsfw_confirmed = text.endswith("HO") and len(text) >= 2 and text[-2:] == "HO" and is_adult
    if nsfw_confirmed:
        # Strip the HO marker — exact slice, never rstrip (would eat all trailing H/O chars)
        cleaned = text[:-2].strip()
        logger.info(f"NSFW confirmed via HO marker")

    return ValidationResult(
        is_safe=is_safe,
        is_system_command=is_system_cmd,
        system_command=cmd,
        intent=intent,
        time_hint=time_hint,
        cleaned_input=cleaned,
        injection_detected=injection_detected,
        target_word_count=target_word_count,
        mark_important_npc=mark_important_npc,
        is_ntr=is_ntr,
        nsfw_confirmed=nsfw_confirmed,
    )


# ── Player info extraction from user input ─────────────────────

_RE_PLAYER_NAME = re.compile(
    r'(?:我(?:叫|是|名叫)|我叫|我是)\s*([一-鿿]{2,4})'
)
# Only match "我是XX期/修为" or "我修为是XX" — self-referential only.
# Must NOT match "她是金丹期" or "白慕彩是金丹期" (NPC descriptions).
_RE_SELF_CULTIVATION = re.compile(
    r'(?:我.{0,3}(?:修为|境界).{0,3}'
    r'|我.{0,1}(?:是|为|在)'
    r')((?:炼气|筑基|金丹|元婴|化神|炼虚|合体|大乘|渡劫)'
    r'(?:期|初|中|后|巅峰|初期|中期|后期|一重|二重|三重|四重|五重|六重|七重|八重|九重)?)'
)
_RE_MASTER = re.compile(
    r'(?:师尊|师父|世尊)(?:叫|是|名叫|：|:)?\s*([一-鿿]{2,4})'
)

_CULTIVATION_NORMALIZE: dict[str, str] = {
    "炼气": "炼气期", "炼气初": "炼气期", "炼气初期": "炼气期",
    "炼气中": "炼气期", "炼气中期": "炼气期",
    "筑基": "筑基期", "筑基初": "筑基期", "筑基初期": "筑基期",
    "筑基一重": "筑基期", "筑基二重": "筑基期", "筑基三重": "筑基期",
    "筑基中": "筑基期", "筑基中期": "筑基期",
    "筑基后": "筑基期", "筑基后期": "筑基期",
    "筑基巅峰": "筑基期", "筑基圆满": "筑基期",
    "金丹": "金丹期", "金丹初": "金丹期", "金丹初期": "金丹期",
    "金丹中": "金丹期", "金丹中期": "金丹期",
    "元婴": "元婴期", "化神": "化神期", "炼虚": "炼虚期",
    "合体": "合体期", "大乘": "大乘期", "渡劫": "渡劫期",
}


def extract_player_info(user_input: str) -> dict:
    """Extract player self-declarations from user input.

    Returns a dict with optional keys: name, cultivation, master_name.
    These should be applied to the Player model immediately, before
    the LLM sees the turn prompt — so the LLM works with current state.
    """
    result: dict = {}

    m = _RE_PLAYER_NAME.search(user_input)
    if m:
        result["name"] = m.group(1)

    m = _RE_SELF_CULTIVATION.search(user_input)
    if m:
        raw = m.group(1)
        result["cultivation"] = _CULTIVATION_NORMALIZE.get(raw, raw)

    m = _RE_MASTER.search(user_input)
    if m:
        result["master_name"] = m.group(1)

    return result
