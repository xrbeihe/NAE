"""Shared player-panel renderer.

Turns a Player ORM row + a worldview panel.json spec into the display
string shown in the frontend / returned in TurnResult.player_panel.

The xianxia_v1 panel spec reproduces the original hardcoded panel
byte-for-byte; other worldviews define their own field lists.
"""

from dataclasses import dataclass
import re


@dataclass
class _Field:
    label: str
    value: str | None  # None → render the label with default, or skip if show_if failed


def _get_source(player, spec: dict) -> str:
    """Resolve 'player' / 'attrs' / 'items' / 'exts' source names."""
    source = spec.get("source", "attrs")
    if source == "player":
        return getattr(player, spec.get("key", ""), None) or None
    if source == "items":
        inv = player.inventory or []
        names = [i.get("name", "?") for i in inv if isinstance(i, dict)]
        return "、".join(names) if names else None
    if source == "exts":
        return None  # handled specially in renderer
    # attrs
    attrs = dict(player.attributes or {}) if isinstance(player.attributes, dict) else {}
    return attrs.get(spec.get("key", ""))


def _render_extensions(player) -> str | None:
    attrs = dict(player.attributes or {}) if isinstance(player.attributes, dict) else {}
    exts = attrs.get("_extensions", {})
    if not exts or not isinstance(exts, dict):
        return None
    parts = []
    for ek, ev in exts.items():
        if not ek or not ev:
            continue
        if isinstance(ev, dict):
            sub = " | ".join(f"{sk}:{sv}" for sk, sv in ev.items() if sk and sv)
            parts.append(f"{ek}→{sub}" if sub else f"{ek}→{ev}")
        else:
            parts.append(f"{ek}→{ev}")
    return " / ".join(parts) if parts else None


_HEADING_RE = re.compile(r"^\s*【(.+?)】\s*$")


def strip_extension_echo_sections(text: str, player) -> str:
    """删掉 info_panel 里"照抄主角面板扩展项"的分节。

    权威主角面板已经用「扩展：」列出全部 `_extensions` 栏目（如「技能栏·粘遁」）。
    但 LLM 经常把这些栏目再抄成一个 `【栏目名】` 分节；而上一轮 info_panel 是
    每轮原样回喂的，于是这份回声会被固化下来、逐轮累积（提示词膨胀，正文也跟着
    复述同一批设定）。这里按"分节标题 == 扩展栏目名"**精确匹配**删除该分节：
    从标题行删到**空行**（或下一个标题行）为止——只删连续块，不碰后面的
    主角动态状态行/交互人物行，避免误删。
    """
    if not text:
        return text
    attrs = dict(player.attributes or {}) if player is not None and isinstance(player.attributes, dict) else {}
    exts = attrs.get("_extensions", {})
    if not isinstance(exts, dict) or not exts:
        return text
    keys = {str(k).strip() for k in exts if k}
    if not keys:
        return text

    kept: list[str] = []
    dropping = False
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            dropping = m.group(1).strip() in keys
            if dropping:
                continue
        if dropping:
            if not line.strip():      # 空行 = 该分节结束，其余内容照常保留
                dropping = False
            continue
        kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


# ── 主角动态段里「照抄权威面板字段」的字段级回声剥离 ──────────────
# 面板已经把 姓名/性别/年龄/身份/能力/性格/位置… 列成权威版本；但 LLM 常会在
# 【主角动态】里再写一遍（例如「北荷茶光：查克拉消耗过半，精神紧绷 ｜位置：林之国·杉谷村口」）。
# 上一轮信息栏是原样回喂的，于是这份回声逐轮固化。这里按**字段名**剥离：
# 只删「字段：值」中字段已出现在权威面板里的片段，保留真正的动态内容（状态/当前行动…）。

_DYN_KEY_RE = re.compile(r"^\s*(状态|当前状态|当前行动|行动|心情|伤势|身体|精神|位置)\s*[：:]")
_DYN_ANY_RE = re.compile(r"[｜|\s](状态|当前状态|当前行动|行动|心情|伤势|身体|精神|位置)\s*[：:]")
_NAMED_FIELD_RE = re.compile(r"^\s*([^：:｜|]{1,12})\s*[：:]\s*(状态|当前状态|当前行动|行动|心情|伤势|身体|精神|位置)\s*[：:]")


def _is_dynamic_line(line: str) -> bool:
    """这一行是不是「主角动态」的内容行（支持「状态：…」「主角名：状态：…」「名字：状态描述 ｜位置：…」）。"""
    s = (line or "").strip()
    if not s:
        return False
    return bool(_DYN_KEY_RE.match(s) or _DYN_ANY_RE.search(s) or _NAMED_FIELD_RE.match(s))


def _panel_field_keys(panel_text: str) -> set[str]:
    """从权威主角面板文本里取出所有「字段：」的字段名。"""
    keys: set[str] = set()
    for m in re.finditer(r"(?:^|[｜|\s、，,])\s*([^\s｜|：:]{1,12})\s*[：:]", panel_text or ""):
        keys.add(m.group(1).strip())
    return keys


def _strip_echo_segments(line: str, panel_keys: set[str]) -> str:
    """逐「｜」段丢掉字段已在面板里的片段；返回空串表示整行都是回声。"""
    segs = [s.strip() for s in re.split(r"[｜|]", line) if s.strip()]
    kept: list[str] = []
    for i, seg in enumerate(segs):
        m = re.match(r"^([^：:]{1,12})[：:](.*)$", seg)
        key = m.group(1).strip() if m else ""
        rest = m.group(2).strip() if m else ""
        # 「主角名：状态：…」这种首段：名字不算字段名，看它后面那个真字段
        if i == 0 and rest:
            inner = re.match(r"^([^：:]{1,12})[：:](.*)$", rest)
            if inner and inner.group(1).strip() in panel_keys:
                continue                      # 整段就是回声（如「某人：位置：X」）
            kept.append(seg)
            continue
        if key and key in panel_keys:
            continue
        kept.append(seg)
    return " ｜ ".join(kept)


# 已整体从角色数据里移除的字段：面板不再展示，但信息栏里出现就一律剥掉
_ALWAYS_ECHO_KEYS = {"位置", "地点", "所在地", "location"}


def strip_dynamic_field_echoes(text: str, panel_text: str) -> str:
    """把 info_panel 的【主角动态】段里"照抄权威面板字段"的片段删掉。

    只处理动态段（首个【…】段，或模型没写标题时最前面那几行裸写的动态行），
    后面的【交互人物】【附近人物】【推荐行动】等段落原样保留。
    整行删空则删该行；整段删空则连标题一起删。

    除了"面板里已有的字段"，还有一份**固定剥离名单**（`_ALWAYS_ECHO_KEYS`）：
    已经整体从角色数据里移除的字段（位置/地点）即便面板不再展示，也不该出现在信息栏里。
    """
    if not text:
        return text
    panel_keys = _panel_field_keys(panel_text or "") | _ALWAYS_ECHO_KEYS
    if not panel_keys:
        return text

    lines = text.splitlines()
    first = next((i for i, l in enumerate(lines) if l.strip()), -1)
    if first < 0:
        return text
    head = _HEADING_RE.match(lines[first])
    start, end = -1, len(lines)
    if head:
        j = first + 1
        body: list[str] = []
        while j < len(lines) and not _HEADING_RE.match(lines[j]):
            body.append(lines[j]); j += 1
        if head.group(1).replace(" ", "") == "主角动态" or any(_is_dynamic_line(l) for l in body):
            start, end = first, j
    elif _is_dynamic_line(lines[first]):
        j = first + 1
        while j < len(lines) and not _HEADING_RE.match(lines[j]):
            j += 1
        start, end = first, j
    if start < 0:
        return text

    bare_start = start == first and not head        # 无标题形态：起始那行本身就是内容
    out: list[str] = []
    for idx in range(start, end):
        line = lines[idx].strip()
        if idx == start and head and not bare_start:
            out.append(lines[idx])                  # 保留标题行
            continue
        if not line:
            out.append("")                          # 段落内空行先留着，最后统一压缩
            continue
        stripped = _strip_echo_segments(line, panel_keys)
        out.append(stripped)
    # 标题行后若已无内容，连标题一起删
    body_only = [l for l in (out[1:] if (head and not bare_start) else out) if l.strip()]
    if head and not bare_start and not body_only:
        out = []
    merged = [l for l in out if l.strip()]
    tail = lines[end:]
    sep = [""] if (merged and any(l.strip() for l in tail)) else []
    rest = lines[:start] + merged + sep + tail
    return re.sub(r"\n{3,}", "\n\n", "\n".join(rest)).strip()


def render_player_panel(player, panel_spec: dict) -> str:
    """Render the player panel string per a worldview panel.json spec.

    panel_spec fields:
      title: str, join: str (default ' ｜ ')
      fields: [ {label, kind: 'composite'|'simple', ...}, ... ]
    """
    title = panel_spec.get("title", "【主角面板】")
    join = panel_spec.get("join", " ｜ ")

    if not player:
        return title + "\n（无玩家数据）\n"

    lines: list[str] = []
    attrs = dict(player.attributes or {}) if isinstance(player.attributes, dict) else {}

    for f in panel_spec.get("fields", []):
        kind = f.get("kind", "simple")
        if kind == "composite":
            # e.g. format: "{name} ｜ {gender} ｜ {age}岁"
            fmt = f.get("format", "")
            src = f.get("source", {})
            values = {}
            for k, path in (src or {}).items():
                if path == "player.name":
                    values[k] = getattr(player, "name", "")
                elif path == "player.location":
                    values[k] = getattr(player, "location", "")
                elif path.startswith("attrs."):
                    values[k] = attrs.get(path[len("attrs."):], "")
            try:
                line = fmt.format(**values)
            except (KeyError, IndexError):
                line = fmt
            lines.append(f"{f.get('label', '')}：{line}")
            continue

        # simple / items / exts
        if f.get("source") == "exts":
            val = _render_extensions(player)
            if val:
                lines.append(f"{f.get('label', '')}：{val}")
            continue

        val = _get_source(player, f)

        # show_if controls
        show_if = f.get("show_if", "")
        if show_if == "truthy" and not val:
            continue
        if show_if == "nonzero" and not val:
            continue

        if val is None:
            default = f.get("default")
            if default is None:
                continue
            val = default

        if f.get("source") == "items":
            lines.append(f"{f.get('label', '')}：{val}")
            continue

        unit_attr = f.get("unit_attr")
        if unit_attr:
            unit = attrs.get(unit_attr) or f.get("default_unit", "")
            lines.append(f"{f.get('label', '')}：{val}{unit}")
        else:
            lines.append(f"{f.get('label', '')}：{val}")

    return title + "\n" + join.join(lines)
