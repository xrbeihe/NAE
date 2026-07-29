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
    recommendations: list[str] = field(default_factory=list)  # 10 recs from llm_main
    offstage_npcs: list[dict] = field(default_factory=list)   # NPCs named in narrative but not revealed to player
    player_relationships: list[dict] = field(default_factory=list)  # named NPCs with player relationship


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


def parse(raw_response: str) -> ParsedOutput:
    """Parse LLM output into narrative and structured state changes.

    Tries multiple strategies in order:
      1. Extract JSON code block (```json ... ```) — best case
      2. Balanced brace extraction for top-level JSON objects
      3. Fall back to plain text
    """
    # Strategy 1: fenced JSON block
    match = re.search(r"```json\s*(.*?)\s*```", raw_response, re.DOTALL)
    if match:
        inner = match.group(1)
        # Use balanced extraction inside the code fence too (safety net)
        objects = _extract_balanced_json(inner)
        if objects:
            return _parse_json(objects[0], raw_response)
        return _parse_json(inner, raw_response)

    # Strategy 2: balanced brace extraction — handles nested JSON
    objects = _extract_balanced_json(raw_response)
    # Pick the best candidate: the one containing "narrative"
    for obj in objects:
        if '"narrative"' in obj:
            return _parse_json(obj, raw_response)
    # Fallback: try the longest JSON object
    if objects:
        longest = max(objects, key=len)
        return _parse_json(longest, raw_response)

    # Fallback: treat whole response as narrative
    logger.warning("No JSON found in LLM output — falling back to plain text")
    return ParsedOutput(
        narrative=raw_response.strip(),
        state_changes=[],
        is_valid_json=False,
        parse_error="No JSON structure found in response",
    )


def _parse_json(json_str: str, fallback_raw: str) -> ParsedOutput:
    """Attempt to parse a JSON string. Return ParsedOutput on success or failure."""
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
    raw_offstage = data.get("offstage_npcs", [])
    raw_player_rels = data.get("player_relationships", [])

    # ── Ensure minimum narrative length for quality ──
    if narrative and len(narrative) < 300:
        logger.info(f"Narrative too short ({len(narrative)} chars), ignoring word count constraint")
        # Don't cap or pad — the LLM will see the length requirement next turn

    # Validate each state_change
    valid_changes = []
    for change in raw_changes:
        if _validate_change(change):
            valid_changes.append(change)
        else:
            logger.warning(f"Dropped invalid state_change: {change}")

    # ── Strip low-effort filler phrases from narrative ──
    for phrase in ["指甲掐进掌心", "指节发白", "喉咙发紧"]:
        narrative = narrative.replace(phrase, "")

    return ParsedOutput(
        narrative=narrative,
        state_changes=valid_changes,
        nearby_characters=raw_nearby if isinstance(raw_nearby, list) else [],
        is_valid_json=True,
        character_model=character_model if isinstance(character_model, dict) else None,
        recommendations=raw_recs if isinstance(raw_recs, list) else [],
        offstage_npcs=raw_offstage if isinstance(raw_offstage, list) else [],
        player_relationships=raw_player_rels if isinstance(raw_player_rels, list) else [],
    )


# ── Validation ────────────────────────────────────────────────

VALID_EVENT_TYPES = {
    # Core state changes
    "location_change", "cultivation_change", "status_change",
    "character_status", "npc_status",
    "item_added", "item_removed",
    "relationship_change", "quest_accepted", "quest_completed",
    "breakthrough", "death", "marriage",
    # Narrative events (accepted but not all trigger DB changes)
    "dialogue", "travel", "combat", "trade",
    "npc_enters", "npc_leaves", "npc_action",
    "environment", "event", "time_skip",
    "player_name_change",
    # Important NPC marking
    "npc_important",
    # Nearby NPC seeding
    "npc_nearby",
}


def _validate_change(change: dict) -> bool:
    """Check that a state_change dict has required fields and valid values."""
    if not isinstance(change, dict):
        return False

    event_type = change.get("type", "")
    if event_type not in VALID_EVENT_TYPES:
        logger.debug(f"Unknown event type: {event_type}")
        return False

    target = change.get("target", "")
    if not target:
        logger.debug("State change missing 'target'")
        return False

    return True
