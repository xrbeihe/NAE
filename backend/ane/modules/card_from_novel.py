"""card_from_novel — 从小说构建角色卡的 LLM 读取管线。

不依赖 NPC 建模链：输入 txt 小说，输出 UserCard.card_data（恋爱向 schema）。

流程：
  1. 读取文本（自动编码检测 utf-8 → gbk → utf-8-sig）
  2. 按章节/固定长度切块
  3. 候选角色提取（读开头若干章，LLM 列出主要人物）
  4. 角色聚焦抽样（抽取含角色名的段落 + 台词）
  5. LLM 按 card_schema 填卡 → normalize_card 校验 → 存 UserCard

LLM 标签：llm_read（候选提取）/ llm_read_card（填卡）。
"""

from __future__ import annotations

import json
import logging
import re

from ane.config import DEFAULT_MODEL
from ane.modules.card_schema import normalize_card

logger = logging.getLogger(__name__)

# 候选角色提取时读取的章节数（前 N 章足够列出主角团）
_CANDIDATE_CHAPTERS = 15

# 读取深度档位 → {candidate_chapters, sample_ratio}
#   candidate_chapters: 候选提取读前 N 章（None = 全部）
#   sample_ratio: 角色抽样覆盖前多少比例的章节（1.0 = 全部）
DEPTH_PRESETS = {
    "快速": {"candidate_chapters": 8, "sample_ratio": 0.34},
    "标准": {"candidate_chapters": 15, "sample_ratio": 0.67},
    "深度": {"candidate_chapters": 30, "sample_ratio": 1.0},
    "全文": {"candidate_chapters": None, "sample_ratio": 1.0},
}


def resolve_depth(depth: str | int | None) -> tuple:
    """把档位/章节数解析为 (candidate_chapters, sample_ratio)。

    档位（快速/标准/深度/全文）→ 查 DEPTH_PRESETS。
    整数 → candidate_chapters=N, sample_ratio 按 N/章节总数自适应。
    None → 默认「标准」。
    """
    if isinstance(depth, int) and depth > 0:
        return depth, 1.0  # 数字章节：候选读前 N 章，抽样全量（数字通常表示"读得多"）
    if isinstance(depth, str):
        preset = DEPTH_PRESETS.get(depth.strip())
        if preset:
            return preset["candidate_chapters"], preset["sample_ratio"]
    # 默认标准
    preset = DEPTH_PRESETS["标准"]
    return preset["candidate_chapters"], preset["sample_ratio"]

# 角色聚焦抽样：每个抽样片段的最大字符数
_SAMPLE_CHARS = 6000

# 角色聚焦抽样最多取多少片段
_MAX_SAMPLES = 10

# 单次填卡的上下文上限（字符，约 60K token 内）
_MAX_INPUT_CHARS = 40000


# ── 文本读取与切块 ───────────────────────────────────────────

def read_novel_text(raw: bytes) -> str:
    """读取小说文本，自动检测编码（utf-8 → gbk → utf-8-sig）。"""
    for enc in ("utf-8", "gbk", "utf-8-sig"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # 兜底：errors=replace 不抛异常
    return raw.decode("utf-8", errors="replace")


def split_chapters(text: str) -> list[str]:
    """按「第N章」切块。无章节标记时按固定长度切。"""
    # 匹配行首或行中出现的"第X章"（可含标题）
    pattern = re.compile(r"(?m)^\s*(第[0-9一二三四五六七八九十百千]+章[^\n]*)")
    matches = list(pattern.finditer(text))
    if len(matches) >= 2:
        chapters = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chapters.append(text[start:end])
        # 丢弃文件头的声明/简介（第一个"第1章"之前的内容）
        return [c for c in chapters if c.strip()][:200]
    # 无章节 → 按固定长度切
    step = 8000
    return [text[i:i + step] for i in range(0, len(text), step)] if text else []


def _extract_quotes(chunk: str) -> str:
    """抽取台词（「」/“”包裹），说话方式的学习来源。"""
    quotes = re.findall(r"[「“]([^」”]{2,60})[」”]", chunk)
    return "；".join(quotes[:8])


# ── 候选角色提取 ─────────────────────────────────────────────

async def extract_characters(
    text: str,
    user_id: str = "",
    model: str | None = None,
    depth: str | int | None = None,
) -> list[dict]:
    """读前 N 章（按 depth），让 LLM 列出主要人物。返回 [{name, reason}]。"""
    from ane.modules.model_adapter import model_adapter

    chapters = split_chapters(text)
    candidate_chapters, _ = resolve_depth(depth)
    limit = candidate_chapters if candidate_chapters else len(chapters)
    head = "\n\n".join(chapters[:limit])
    if len(head) > _MAX_INPUT_CHARS:
        head = head[:_MAX_INPUT_CHARS]

    prompt = f"""你是角色分析助手。以下是小说《别装乖》的开头若干章。

你的任务：列出这部小说的主要人物（主角、重要配角），供用户选择想还原哪个角色。

要求：
1. 只输出主要人物（戏份重、有辨识度的），3-8 个。
2. 每个人给一个"一句话角色印象"（基于开头章节的描写）。
3. 只输出 JSON，格式：
{{"characters": [{{"name": "姓名", "reason": "一句话角色印象"}}]}}

小说开头：
{head}"""

    raw = ""
    try:
        raw = await model_adapter.generate(
            prompt, model=model or DEFAULT_MODEL,
            user_id=user_id, label="llm_read",
        )
    except Exception as e:
        logger.exception("llm_read (extract characters) failed: %s", e)
        return []

    return _parse_character_list(raw)


def _parse_character_list(raw: str) -> list[dict]:
    """解析 LLM 输出的角色列表。"""
    if not raw:
        return []
    cleaned = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1)
    try:
        data = json.loads(cleaned)
        chars = data.get("characters") if isinstance(data, dict) else None
        if isinstance(chars, list):
            return [{"name": str(c.get("name", "")).strip(), "reason": str(c.get("reason", ""))} for c in chars if c.get("name")]
    except Exception:
        pass
    # 纯文本兜底：尝试按行解析 "姓名 - 印象"
    out = []
    for line in cleaned.splitlines():
        line = line.strip().lstrip("-• ").strip()
        if not line:
            continue
        parts = re.split(r"[，,：:\-—]", line, maxsplit=1)
        name = parts[0].strip()
        if 1 <= len(name) <= 6:
            out.append({"name": name, "reason": parts[1].strip() if len(parts) > 1 else ""})
    return out


# ── 角色聚焦抽样 ─────────────────────────────────────────────

def sample_character(text: str, name: str, depth: str | int | None = None) -> str:
    """抽取目标角色的高相关片段。

    策略：正文段落优先（含名字的完整段落提供人物上下文），台词作为点缀。
    depth 控制遍历的章节范围（快速=前1/3，标准=前2/3，深度/全文=全部），
    按章节遍历，每章取含名字的段落（去重），直至样本总量达目标。
    """
    chapters = split_chapters(text)
    _, sample_ratio = resolve_depth(depth)
    limit = max(1, int(len(chapters) * sample_ratio))
    scan = chapters[:limit]
    samples: list[str] = []
    for ch in scan:
        if name not in ch:
            continue
        # 含名字的完整段落（正文上下文）
        paras = [p.strip() for p in re.split(r"\n+", ch) if name in p and p.strip()]
        for p in paras:
            if len(p) >= 40:  # 跳过过短的碎片
                samples.append(p[:_SAMPLE_CHARS])
        if len(samples) >= _MAX_SAMPLES:
            break
    # 如果正文段落不足，补台词
    if len(samples) < 3:
        for ch in scan:
            if name not in ch:
                continue
            quoted = _extract_quotes(ch)
            if quoted:
                samples.append(f"（{name}相关台词）{quoted}")
            if len(samples) >= _MAX_SAMPLES:
                break
    if not samples:
        return f"（未在文中找到「{name}」的直接片段，可能为次要角色）"
    # 去重 + 合并截断
    seen = set()
    uniq = []
    for s in samples:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
        if sum(len(x) for x in uniq) >= _MAX_INPUT_CHARS:
            break
    return "\n\n".join(uniq)[:_MAX_INPUT_CHARS]


# ── LLM 填卡 ────────────────────────────────────────────────

async def generate_card_from_sample(
    name: str,
    sample: str,
    relationship_note: str = "",
    user_id: str = "",
    model: str | None = None,
) -> dict:
    """LLM 读抽样片段，按 card_schema 填角色卡。返回 normalize 后的 card_data。"""
    from ane.modules.model_adapter import model_adapter
    from ane.modules.card_schema import CARD_SCHEMA

    schema_json = json.dumps(CARD_SCHEMA, ensure_ascii=False, indent=2)
    rel_hint = relationship_note or f"想以「相识」的身份与{name}相处"

    prompt = f"""你是角色卡构建助手。根据小说原文片段，为角色「{name}」构建一张角色卡。

你将扮演{name}，与用户进行 1v1 亲密对话。角色卡决定你如何表现。

【小说原文片段】
{sample}

【你的任务】
根据原文，推断{name}的性格、外貌、说话方式等。对原文没有的信息，基于角色气质合理推演（大胆补全，不填空）。
特别关注：
- 说话方式：从原文台词学（语气、口头禅、语癖）
- 性格：从行动和他人评价推
- 对你的称呼：根据你们的关系（{rel_hint}）合理设定一个符合角色气质的称呼

【初始关系】
用户设定你们的关系是：{rel_hint}
请在 initial_relationship 里填：type 选最接近的（陌生人/相识/青梅竹马/前恋人/暗恋者/追求者/恋人/伴侣/网友），history 写一段符合原著的背景，current_mood 写开场时对你的态度。

【输出】
严格按以下 JSON schema 输出，所有 key 都在。name 字段必须填「{name}」。
{schema_json}
只输出 JSON，不要多余文字。"""

    raw = ""
    try:
        raw = await model_adapter.generate(
            prompt, model=model or DEFAULT_MODEL,
            user_id=user_id, label="llm_read_card",
        )
    except Exception as e:
        logger.exception("llm_read_card failed: %s", e)
        return {}

    data = _parse_card_json(raw)
    if not data:
        return {}
    # 强制填 name + 初始关系类型
    if "identity" not in data or not isinstance(data["identity"], dict):
        data["identity"] = {}
    data["identity"]["name"] = name
    return normalize_card(data)


def _parse_card_json(raw: str) -> dict:
    """解析 LLM 输出的角色卡 JSON。"""
    if not raw:
        return {}
    cleaned = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(cleaned[start:end + 1])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
