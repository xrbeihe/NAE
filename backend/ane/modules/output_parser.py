"""Output Parser — separates AI narrative from structured state changes.

Phase 1 strategy:
  - Expect JSON with {narrative, state_changes}
  - On parse failure, fall back to treating entire output as narrative (no state changes)
  - Validate state_changes: drop invalid entries, keep valid ones
"""

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ParsedOutput:
    narrative: str
    state_changes: list[dict] = field(default_factory=list)
    nearby_characters: list[dict] = field(default_factory=list)
    is_valid_json: bool = True
    parse_error: str | None = None
    character_model: dict | None = None  # llm_modeling data from llm_main (only when marking important)
    recommendations: list[str] = field(default_factory=list)  # 5 recs from llm_main
    player_relationships: list[dict] = field(default_factory=list)  # named NPCs with player relationship
    info_panel: str = ""  # 独立信息区：主角信息 + 附近人物等，一整个区别于正文的文本区域


# ── Robust list-of-dicts cleaning ──────────────────────────────

def _coerce_dict(item) -> dict | None:
    """Best-effort coerce a nearby/relationship item into a dict.

    Handles the LLM failure mode where json_repair recovers a list of
    *string fragments* instead of objects (e.g. `'name": "卖炊饼老汉'`).
    Returns None for items that can't be salvaged — they get dropped.
    """
    if isinstance(item, dict):
        return item
    if not isinstance(item, str):
        return None
    s = item.strip()
    if not s:
        return None
    # Re-wrap a fragment that lost its opening brace (json_repair artifact).
    if not s.startswith("{"):
        s = "{" + s
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        try:
            import json_repair
            repaired = json_repair.loads(s)
            return repaired if isinstance(repaired, dict) else None
        except Exception:
            return None


def _as_dict_list(value) -> list[dict]:
    """Normalize a parsed LLM list-of-objects field to list[dict].

    Non-list input → []; non-dict items → best-effort coerce, drop failures.
    """
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        d = _coerce_dict(item)
        if d is not None:
            out.append(d)
    return out


def _as_str_list(value) -> list[str]:
    """Normalize a parsed LLM list-of-strings field to list[str]."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def _as_str(value) -> str:
    """Normalize a parsed LLM string field to str (robust vs non-str)."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _clean_narrative_leakage(narrative: str) -> str:
    """Strip leaked JSON structures / structured markers from narrative.

    LLM 偶发会把 nearby_characters、JSON 片段、字段名等写进 narrative 字段，
    导致前端渲染出数据结构。这里剥离这些泄漏，只保留纯正文。
    """
    import re as _re

    if not narrative:
        return narrative

    original = narrative

    # 1) 剥离【附近人物】等标记行（只删该行，后续 JSON 由第 2 步平衡括号处理）
    narrative = _re.sub(r"【附近人物】[^\n]*\n?", "", narrative)

    # 2) 剥离独立的 JSON 对象 {…} / 数组 […]（平衡括号；仅当内容含 JSON 特征才剥离）
    def _strip_balanced(text: str, open_ch: str, close_ch: str) -> str:
        out = []
        depth = 0
        open_idx = -1
        i = 0
        while i < len(text):
            c = text[i]
            if c == open_ch:
                if depth == 0:
                    open_idx = len(out)
                depth += 1
                out.append(c)
            elif c == close_ch and depth > 0:
                depth -= 1
                if depth == 0:
                    # 成对闭合：检查内容是否像 JSON（含引号或键值冒号）
                    block = "".join(out[open_idx + 1:])
                    looks_json = ('"' in block) or (':' in block)
                    if looks_json:
                        del out[open_idx:]
                        out.append(" ")
                    else:
                        out.append(c)  # 正常文字花括号（如 {好}）保留闭合符
                else:
                    out.append(c)
            else:
                out.append(c)
            i += 1
        return "".join(out)

    narrative = _strip_balanced(narrative, "{", "}")
    narrative = _strip_balanced(narrative, "[", "]")

    # 3) 剥离 "key": "value" / "key": value 残留
    narrative = _re.sub(r'"[^"]{1,40}"\s*:\s*("[^"]*"|[^\s,}\]]{1,60})', "", narrative)
    # 剥离悬挂的字段名 "key"：仅当它后面跟逗号/冒号/花括号（JSON 结构位置），
    # 避免误删正文里的英文引用（如 他说："Hello"，然后…）。
    narrative = _re.sub(r'"[a-zA-Z_]{1,40}"\s*(?=[,}:])', "", narrative)

    # 4) 清理：多余空行、行尾逗号/冒号
    narrative = _re.sub(r"\n{3,}", "\n\n", narrative)
    narrative = _re.sub(r"[,\s:]+$", "", narrative)
    narrative = narrative.strip()

    # 5) 若清洗后空（说明正文全是泄漏），回退原始，避免全丢
    if not narrative:
        return original
    return narrative


# ── Balanced brace JSON extraction ──────────────────────────────

def _extract_balanced_json(text: str) -> list[str]:
    """Extract all top-level balanced JSON objects from text.

    Uses brace-counting (not regex) so nested objects/arrays
    within state_changes are handled correctly.
    """
    candidates: list[str] = []
    i = 0
    while i < len(text):
        if text[i] != '{':
            i += 1
            continue
        depth = 0
        start = i
        while i < len(text):
            ch = text[i]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    i += 1
                    break
            elif ch == '"':
                # Skip string literals to avoid brace miscounts
                i += 1
                while i < len(text):
                    if text[i] == '\\':
                        i += 2  # skip escaped char
                    elif text[i] == '"':
                        i += 1
                        break
                    else:
                        i += 1
                continue
            i += 1
        else:
            i += 1  # never closed — skip past this opening brace
    return candidates


def parse(raw_response: str, worldview: str | None = None) -> ParsedOutput:
    """Parse LLM output into narrative and structured state changes.

    Tries multiple strategies in order:
      1. Extract JSON code block (```json ... ```) — best case
      2. Balanced brace extraction for top-level JSON objects
      3. Fall back to plain text
    """
    # Resolve the valid event-type set once for this parse call
    event_types = _event_types_for(worldview)

    # Strategy 1: fenced JSON block
    match = re.search(r"```json\s*(.*?)\s*```", raw_response, re.DOTALL)
    if match:
        inner = match.group(1)
        # Use balanced extraction inside the code fence too (safety net)
        objects = _extract_balanced_json(inner)
        if objects:
            return _parse_json(objects[0], raw_response, event_types)
        return _parse_json(inner, raw_response, event_types)

    # Strategy 2: balanced brace extraction — handles nested JSON
    objects = _extract_balanced_json(raw_response)
    # Pick the best candidate: the one containing "narrative"
    for obj in objects:
        if '"narrative"' in obj:
            return _parse_json(obj, raw_response, event_types)
    # Fallback: try the longest JSON object
    if objects:
        longest = max(objects, key=len)
        return _parse_json(longest, raw_response, event_types)

    # Fallback: treat whole response as narrative
    logger.warning("No JSON found in LLM output — falling back to plain text")
    return ParsedOutput(
        narrative=_clean_narrative_leakage(raw_response.strip()),
        state_changes=[],
        is_valid_json=False,
        parse_error="No JSON structure found in response",
    )


def _parse_json(json_str: str, fallback_raw: str, event_types: set[str] | None = None) -> ParsedOutput:
    """Attempt to parse a JSON string. Return ParsedOutput on success or failure."""
    event_types = event_types or VALID_EVENT_TYPES
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # Fallback: try json_repair for truncated / malformed LLM output
        try:
            import json_repair
            data = json_repair.loads(json_str)
            logger.info(f"json_repair recovered JSON ({len(json_str)} chars)")
        except Exception as e2:
            logger.warning(f"JSON parse failed (json_repair also failed): {e2}")
            return ParsedOutput(
                narrative=fallback_raw.strip(),
                state_changes=[],
                is_valid_json=False,
                parse_error=str(e2),
            )

    # LLM sometimes wraps the response in an array [{...}] instead of {...}
    if isinstance(data, list):
        for candidate in data:
            if isinstance(candidate, dict) and "narrative" in candidate:
                data = candidate
                break
        else:
            # No object with narrative found in array — use first dict
            data = data[0] if data and isinstance(data[0], dict) else {}
    elif not isinstance(data, dict):
        logger.warning(f"Unexpected JSON type: {type(data).__name__}")
        return ParsedOutput(
            narrative=fallback_raw.strip(),
            state_changes=[],
            is_valid_json=False,
            parse_error=f"Expected JSON object, got {type(data).__name__}",
        )

    narrative = data.get("narrative", "")
    raw_changes = data.get("state_changes", [])
    raw_nearby = data.get("nearby_characters", [])
    logger.info("nearby_characters from LLM: %d items", len(raw_nearby) if isinstance(raw_nearby, list) else -1)
    character_model = data.get("character_model", None)
    raw_recs = data.get("recommendations", [])
    raw_player_rels = data.get("player_relationships", [])

    # ── 泄漏清洗：剥离 narrative 里混入的 JSON 结构 / 结构化标记 ──
    #   偶发时 LLM 把 nearby_characters、JSON 片段等写进了 narrative 字段，
    #   这里检测并剥离，只保留纯正文。
    if narrative:
        narrative = _clean_narrative_leakage(narrative)

    # ── Ensure minimum narrative length for quality ──
    if narrative and len(narrative) < 300:
        logger.info(f"Narrative too short ({len(narrative)} chars), ignoring word count constraint")
        # Don't cap or pad — the LLM will see the length requirement next turn

    # ── 偶发不分段兜底：narrative 较长且无换行 → 按句末标点分组分段 ──
    if narrative and len(narrative) > 200 and "\n" not in narrative:
        import re as _re
        # 按句末标点切句（保留标点）
        sentences = _re.findall(r"[^。！？]*[。！？]", narrative)
        leftover = _re.sub(r"[^。！？]*[。！？]", "", narrative).strip()
        if leftover:
            sentences.append(leftover)
        # 每 2-3 句一组（约 60-80 字一段），段落间用空行分隔
        paragraphs = []
        buf = ""
        for s in sentences:
            if not s.strip():
                continue
            buf += s
            if len(buf) >= 60:
                paragraphs.append(buf)
                buf = ""
        if buf.strip():
            paragraphs.append(buf)
        narrative = "\n\n".join(paragraphs) if paragraphs else narrative
        logger.info(f"Narrative had no newlines — auto-paragraphed into {len(paragraphs)} paragraphs")

    # Validate each state_change
    valid_changes = []
    for change in raw_changes:
        if _validate_change(change, event_types):
            valid_changes.append(change)
        else:
            logger.warning(f"Dropped invalid state_change: {change}")

    # ── Strip low-effort filler phrases from narrative ──
    for phrase in ["指甲掐进掌心", "指节发白", "喉咙发紧"]:
        narrative = narrative.replace(phrase, "")

    return ParsedOutput(
        narrative=narrative,
        state_changes=valid_changes,
        nearby_characters=_as_dict_list(raw_nearby),
        is_valid_json=True,
        character_model=character_model if isinstance(character_model, dict) else None,
        recommendations=_as_str_list(raw_recs),
        player_relationships=_as_dict_list(raw_player_rels),
        info_panel=_as_str(data.get("info_panel", "")),
    )


# ── Validation ────────────────────────────────────────────────

# Engine-core event types valid in EVERY worldview.
CORE_EVENT_TYPES = {
    "location_change", "status_change",
    "character_status", "npc_status",
    "item_added", "item_removed",
    "relationship_change", "quest_accepted", "quest_completed",
    "death", "marriage",
    "dialogue", "travel", "combat", "trade",
    "npc_enters", "npc_leaves", "npc_action",
    "environment", "event", "time_skip",
    "player_name_change",
    "npc_important",
    "npc_nearby",
}

# Worldview-specific event types (xianxia: cultivation_change / breakthrough).
# Kept as the legacy default so `parse()` without a worldview behaves as before.
_XIANXIA_EVENT_TYPES = {"cultivation_change", "breakthrough"}

# Legacy full set (core + xianxia) — used when parse() is called without a worldview.
VALID_EVENT_TYPES = CORE_EVENT_TYPES | _XIANXIA_EVENT_TYPES


def _event_types_for(worldview: str | None) -> set[str]:
    """Resolve the valid event-type set for a worldview.

    None → legacy full set (core + xianxia), preserving existing behavior.
    Otherwise → core ∪ pack.extra_event_types (unknown types still dropped).
    """
    if not worldview:
        return VALID_EVENT_TYPES
    from ane.worldview import get as get_worldview, DEFAULT_WORLDVIEW_ID
    wv = get_worldview(worldview or DEFAULT_WORLDVIEW_ID)
    return CORE_EVENT_TYPES | set(wv.extra_event_types)


def _validate_change(change: dict, event_types: set[str]) -> bool:
    """Check that a state_change dict has required fields and valid values."""
    if not isinstance(change, dict):
        return False

    event_type = change.get("type", "")
    if event_type not in event_types:
        logger.debug(f"Unknown event type: {event_type}")
        return False

    target = change.get("target", "")
    if not target:
        logger.debug("State change missing 'target'")
        return False

    return True
