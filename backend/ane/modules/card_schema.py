"""card_schema — 角色卡专属 schema（恋爱向 1v1 卡片）。

单一数据源：后端 _render_companion_card / 前端 /card-editor 表单 /
/cards/schema 端点共用。与 NPC 建模档案（modeler/schema.json）彻底分离。

设计取向：不是"这个人是谁"（配角视角），而是"这个人怎么爱你"
（恋爱向视角）：身份 / 外貌 / 性格 / 说话方式 / 初始关系 / 关系行为 /
粘人倾向 / 开场白。
"""

# ── 角色卡字段树（嵌套默认值，风格同 modeler/schema.json）──

CARD_SCHEMA = {
    "identity": {
        "name": "",
        "gender": "",
        "age": "",
        "occupation": "",
        "persona": "",        # 一句话人设（核心）
        "background": "",     # 背景经历
    },
    # 沉浸感：头像/背景（base64 图片数据，由前端上传生成，非 LLM 字段）
    "visual": {
        "avatar": "",         # 角色头像 base64
        "background": "",     # 聊天区背景图 base64
    },
    "appearance": {
        "overall_impression": "",
        "face": "",
        "eyes": "",
        "hair": "",
        "build": "",
        "dress_style": "",
    },
    "personality": {
        "core": "",           # 核心性格
        "values": "",
        "quirks": "",         # 小怪癖
        "likes": "",
        "dislikes": "",
        "fears": "",
    },
    "speech_style": {
        "tone": "",           # 说话语气
        "catchphrases": "",   # 口头禅
        "verbal_ticks": "",   # 语癖
        "address_you": "",    # 对你的称呼（亲密感的来源）
        "speech_habit": "",   # 说话习惯（慢/快/简洁/啰嗦等）
    },
    "initial_relationship": {
        "type": "陌生人",     # 见 CARD_SELECTS
        "history": "",        # 关系背景（怎么认识的/有什么过往）
        "current_mood": "",   # 开场时对你的态度基调
    },
    "relationship_behavior": {
        "expressing_affection": "",   # 如何表达爱意
        "jealousy": "",               # 吃醋表现
        "intimate_terms": [],         # 亲密称呼列表（字符串数组）
        "anger_behavior": "",         # 生气闹别扭时什么样
        "boundaries": "",             # 雷区与底线
    },
    "clinginess": {
        "level": "适中",      # 见 CARD_SELECTS（粘人/适中/含蓄/高冷）
        "notes": "",
    },
    "opening": {
        "greeting": "",       # 开场白
        "follow_up": "",      # 开场后的追问
    },
}


# ── 中文标签（渲染 + 表单）──────────────────────────────────

CARD_LABELS = {
    "visual": "形象",
    "avatar": "头像",
    "background": "背景图",
    "identity": "身份",
    "name": "姓名",
    "gender": "性别",
    "age": "年龄",
    "occupation": "职业",
    "persona": "一句话人设",
    "background": "背景经历",
    "appearance": "外貌",
    "overall_impression": "整体印象",
    "face": "脸型",
    "eyes": "眼眸",
    "hair": "头发",
    "build": "身材",
    "dress_style": "穿着风格",
    "personality": "性格",
    "core": "核心性格",
    "values": "价值观",
    "quirks": "小怪癖",
    "likes": "喜欢",
    "dislikes": "讨厌",
    "fears": "害怕",
    "speech_style": "说话方式",
    "tone": "语气",
    "catchphrases": "口头禅",
    "verbal_ticks": "语癖",
    "address_you": "对你的称呼",
    "speech_habit": "说话习惯",
    "initial_relationship": "初始关系",
    "type": "关系类型",
    "history": "关系背景",
    "current_mood": "开场态度",
    "relationship_behavior": "关系中的行为",
    "expressing_affection": "如何表达爱意",
    "jealousy": "吃醋表现",
    "intimate_terms": "亲密称呼",
    "anger_behavior": "生气闹别扭时",
    "boundaries": "雷区与底线",
    "clinginess": "主动程度",
    "level": "粘人程度",
    "notes": "补充说明",
    "opening": "开场白",
    "greeting": "开场白",
    "follow_up": "开场后的追问",
}


# ── 下拉选项（按 data-path 定位）────────────────────────────

CARD_SELECTS = {
    "identity.gender": ["男", "女", "不透露"],
    "initial_relationship.type": [
        "陌生人", "相识", "青梅竹马", "前恋人", "暗恋者",
        "追求者", "恋人", "伴侣", "网友",
    ],
    "clinginess.level": ["粘人", "适中", "含蓄", "高冷"],
}


# ── 粘人程度 → 主动搭话间隔（秒）────────────────────────────
# 对齐 chat.html 的 NUDGE_PRESETS 语义（10min / 30min / 60min / 6h）。

CLINGINESS_IDLE_SECONDS = {
    "粘人": 600,
    "适中": 1800,
    "含蓄": 3600,
    "高冷": 21600,
}


# ── 工具函数 ─────────────────────────────────────────────────

def normalize_card(card_data: dict | None) -> dict:
    """深合并默认 schema，补全缺省字段，保证结构完整。

    输入可能缺任意层，输出始终包含 CARD_SCHEMA 的全部键。
    """
    import copy

    def _merge(base: dict, override: dict) -> dict:
        out = copy.deepcopy(base)
        for k, v in (override or {}).items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = _merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out

    return _merge(CARD_SCHEMA, card_data or {})


def render_card_preview(card_data: dict) -> str:
    """把角色卡渲染成进入 LLM prompt 前的纯文本预览（镜像 _render_companion_card）。"""
    d = normalize_card(card_data)
    sections = []

    ident = d["identity"]
    parts = [f"姓名: {ident['name']}"] if ident["name"] else []
    if ident["gender"]:
        parts.append(f"性别: {ident['gender']}")
    if ident["age"]:
        parts.append(f"年龄: {ident['age']}")
    if ident["occupation"]:
        parts.append(f"职业: {ident['occupation']}")
    if ident["persona"]:
        parts.append(f"人设: {ident['persona']}")
    sections.append("、".join(parts) if parts else "（未填写身份）")
    if ident["background"]:
        sections.append(f"背景: {ident['background']}")

    appr = d["appearance"]
    appr_lines = [v for v in [
        appr["overall_impression"], appr["face"], appr["eyes"], appr["hair"],
        appr["build"], appr["dress_style"],
    ] if v]
    if appr_lines:
        sections.append(f"外貌: {', '.join(appr_lines)}")

    pers = d["personality"]
    pers_lines = [f"{k}: {v}" for k, v in [
        ("核心", pers["core"]), ("价值观", pers["values"]),
        ("小怪癖", pers["quirks"]), ("喜欢", pers["likes"]),
        ("讨厌", pers["dislikes"]), ("害怕", pers["fears"]),
    ] if v]
    if pers_lines:
        sections.append(f"性格: {' / '.join(pers_lines)}")

    sp = d["speech_style"]
    sp_lines = [f"{k}: {v}" for k, v in [
        ("语气", sp["tone"]), ("口头禅", sp["catchphrases"]),
        ("语癖", sp["verbal_ticks"]), ("对你的称呼", sp["address_you"]),
        ("说话习惯", sp["speech_habit"]),
    ] if v]
    if sp_lines:
        sections.append(f"说话方式: {' / '.join(sp_lines)}")

    rel = d["initial_relationship"]
    rel_lines = [f"关系类型: {rel['type']}"]
    if rel["history"]:
        rel_lines.append(f"关系背景: {rel['history']}")
    if rel["current_mood"]:
        rel_lines.append(f"开场态度: {rel['current_mood']}")
    sections.append(f"初始关系: {' / '.join(rel_lines)}")

    rb = d["relationship_behavior"]
    rb_lines = [f"{k}: {v}" for k, v in [
        ("表达爱意", rb["expressing_affection"]), ("吃醋", rb["jealousy"]),
        ("生气", rb["anger_behavior"]), ("雷区", rb["boundaries"]),
    ] if v]
    if rb["intimate_terms"]:
        rb_lines.append("亲密称呼: " + "、".join(rb["intimate_terms"]))
    if rb_lines:
        sections.append(f"关系行为: {' / '.join(rb_lines)}")

    cl = d["clinginess"]
    cl_line = f"主动程度: {cl['level']}"
    if cl["notes"]:
        cl_line += f"（{cl['notes']}）"
    sections.append(cl_line)

    op = d["opening"]
    if op["greeting"]:
        op_line = f"开场白: {op['greeting']}"
        if op["follow_up"]:
            op_line += f" → {op['follow_up']}"
        sections.append(op_line)

    return "\n".join(sections)
