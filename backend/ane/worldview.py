"""Worldview loader / registry.

A worldview pack is a directory of pure JSON/text files. Each pack is
registered simply by existing on disk — no database table involved.

Dependency rule: this module imports ONLY stdlib. Engine modules that need
worldview data call `get(id)` and fall back to their own constants for any
field that is missing/invalid. This keeps the module a leaf in the import
graph (no circular imports) and guarantees the engine never crashes on a
missing or malformed pack.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

WORLDVIEWS_DIR = Path(__file__).resolve().parent / "worldviews"
DEFAULT_WORLDVIEW_ID = "xianxia_v1"

# Only safe ids: lowercase letters, digits, underscore — max 48 chars.
# This is the primary defense against path traversal via worldview id.
_ID_RE = re.compile(r"^[a-z0-9_]{1,48}$")

# Files a pack may contain.
_MANIFEST = "manifest.json"
_SYSTEM_PROMPT = "system_prompt.txt"
_INTENT_KEYWORDS = "intent_keywords.json"
_CONSTRAINTS = "constraints.json"
_WORLD_TEMPLATES = "world_templates.json"
_NPC_TEMPLATES = "npc_templates.json"
_PLAYER_TEMPLATES = "player_templates.json"
_PANEL = "panel.json"
_UI = "ui.json"
_MODELER_ROLE = "modeler/role.txt"
_MODELER_AGE_RULES = "modeler/age_rules.txt"
_MODELER_SCHEMA = "modeler/schema.json"
_EVENTS = "events.json"
_FORM = "form.json"
_WORLD_FACTS = "world_facts.json"

_REQUIRED_FILES = (_MANIFEST,)
# Optional per-pack files. Missing ones fall back to xianxia_v1, then engine constants.


@dataclass(frozen=True)
class Worldview:
    """A loaded worldview pack. Fields are None when the artifact is absent/invalid."""
    id: str
    path: Path
    manifest: dict = field(default_factory=dict)
    system_prompt: str | None = None
    intent_keywords: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)
    world_templates: dict = field(default_factory=dict)
    npc_templates: dict = field(default_factory=dict)
    player_templates: dict = field(default_factory=dict)
    panel_spec: dict = field(default_factory=dict)
    ui: dict = field(default_factory=dict)
    modeler_role: str | None = None
    modeler_age_rules: str | None = None
    modeler_schema: dict = field(default_factory=dict)  # modeler/schema.json — per-worldview NPC model field tree
    events: dict = field(default_factory=dict)
    form: dict | None = None  # form.json — declarative character-creation form
    world_facts: dict | None = None  # world_facts.json — authoritative canon for IP worldviews

    # ── Convenience accessors ──
    @property
    def name(self) -> str:
        return self.manifest.get("name", self.id)

    @property
    def description(self) -> str:
        return self.manifest.get("description", "")

    @property
    def tags(self) -> list:
        return self.manifest.get("tags", [])

    @property
    def assembly(self) -> str:
        return self.manifest.get("assembly", "shell+kernel")

    @property
    def player_defaults(self) -> dict:
        return self.manifest.get("player_defaults", {})

    @property
    def extra_event_types(self) -> frozenset:
        return frozenset(self.manifest.get("extra_event_types", []))

    def has_sects(self) -> bool:
        """Whether the world template defines sects/settlements (xianxia-style geography)."""
        wt = self.world_templates
        return bool(wt.get("sects") or wt.get("settlements"))

    def has_golden_fingers(self) -> bool:
        return bool(self.player_templates.get("golden_fingers"))


def _is_valid_id(wv_id: str) -> bool:
    return bool(wv_id) and bool(_ID_RE.match(wv_id))


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        # Optional artifact absent — normal for packs that don't ship it.
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Worldview artifact unreadable: %s — %s", path, e)
        return {}


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning("Worldview artifact unreadable: %s — %s", path, e)
        return None


def _load_pack(wv_id: str, pack_dir: Path) -> Worldview:
    manifest = _read_json(pack_dir / _MANIFEST)
    return Worldview(
        id=wv_id,
        path=pack_dir,
        manifest=manifest,
        system_prompt=_read_text(pack_dir / _SYSTEM_PROMPT),
        intent_keywords=_read_json(pack_dir / _INTENT_KEYWORDS),
        constraints=_read_json(pack_dir / _CONSTRAINTS),
        world_templates=_read_json(pack_dir / _WORLD_TEMPLATES),
        npc_templates=_read_json(pack_dir / _NPC_TEMPLATES),
        player_templates=_read_json(pack_dir / _PLAYER_TEMPLATES),
        panel_spec=_read_json(pack_dir / _PANEL),
        ui=_read_json(pack_dir / _UI),
        modeler_role=_read_text(pack_dir / _MODELER_ROLE),
        modeler_age_rules=_read_text(pack_dir / _MODELER_AGE_RULES),
        modeler_schema=_read_json(pack_dir / _MODELER_SCHEMA),
        events=_read_json(pack_dir / _EVENTS),
        form=_read_json(pack_dir / _FORM) or None,
        world_facts=_read_json(pack_dir / _WORLD_FACTS) or None,
    )


# In-memory cache: id → Worldview | None (None = known-missing, cached)
_cache: dict[str, Worldview | None] = {}
# Ordered dict of last-error per id (for diagnostics)
_load_errors: dict[str, str] = {}


def clear_cache() -> None:
    """Drop the loader cache (e.g. after a pack file changes on disk)."""
    _cache.clear()
    _load_errors.clear()


def reload(wv_id: str | None = None) -> None:
    """Drop the loader cache, optionally for a single pack.

    Pass wv_id to invalidate only that pack; None clears everything.
    """
    if wv_id is not None:
        _cache.pop(wv_id, None)
        _load_errors.pop(wv_id, None)
    else:
        clear_cache()


def validate_pack(wv_id: str) -> dict:
    """Validate a worldview pack and return {ok, errors, warnings}.

    Checks: id validity, required files present, manifest well-formed,
    JSON artifacts parse, referenced template keys exist.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not _is_valid_id(wv_id):
        return {"ok": False, "errors": [f"无效的世界观 ID: {wv_id!r}"], "warnings": []}

    pack_dir = WORLDVIEWS_DIR / wv_id
    if not pack_dir.is_dir():
        return {"ok": False, "errors": [f"包目录不存在: {pack_dir}"], "warnings": []}

    manifest = _read_json(pack_dir / _MANIFEST)
    if not manifest:
        errors.append("manifest.json 缺失或非法")
    else:
        if manifest.get("worldview_id") != wv_id:
            warnings.append(f"manifest.worldview_id ({manifest.get('worldview_id')}) 与目录名 {wv_id} 不一致")

    for name, label in [
        (_SYSTEM_PROMPT, "system_prompt.txt"),
        (_WORLD_TEMPLATES, "world_templates.json"),
        (_PLAYER_TEMPLATES, "player_templates.json"),
    ]:
        if not (pack_dir / name).exists():
            errors.append(f"缺少必需文件 {label}")

    for name in [
        _INTENT_KEYWORDS, _CONSTRAINTS, _NPC_TEMPLATES,
        _PANEL, _UI, _MODELER_ROLE, _MODELER_AGE_RULES, _MODELER_SCHEMA,
    ]:
        path = pack_dir / name
        if not path.exists():
            continue
        if name.endswith(".json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    json.load(f)
            except (OSError, json.JSONDecodeError):
                errors.append(f"{name} 不是合法 JSON")
        else:
            if not _read_text(path):
                errors.append(f"{name} 为空")

    # Semantic checks
    wt = _read_json(pack_dir / _WORLD_TEMPLATES)
    if wt and not (wt.get("regions") or wt.get("sects") or wt.get("settlements")):
        warnings.append("world_templates.json 没有 regions/sects/settlements 任一列表")

    pt = _read_json(pack_dir / _PLAYER_TEMPLATES)
    if pt and not pt.get("identities"):
        errors.append("player_templates.json 缺少 identities 字段")

    # ── Structural rules: NPC name pools ──
    nt = _read_json(pack_dir / _NPC_TEMPLATES)
    if nt:
        _check_name_pools(nt, errors, warnings)

    # ── Structural rules: panel field sources vs player_templates ──
    panel = _read_json(pack_dir / _PANEL)
    if panel and pt:
        _check_panel_sources(panel, pt, errors, warnings)

    # ── Structural rules: world_facts timelines completeness ──
    wf = _read_json(pack_dir / _WORLD_FACTS)
    if wf:
        _check_timelines(wf, errors, warnings)

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _check_name_pools(nt: dict, errors: list, warnings: list) -> None:
    """Validate NPC name pools: surnames are surnames, name pools sane & unique."""
    surn = nt.get("surnames") or []
    gm = nt.get("given_names_male") or []
    gf = nt.get("given_names_female") or []

    def _dups(arr, label):
        seen = set(); dup = []
        for x in arr:
            if x in seen:
                dup.append(x)
            seen.add(x)
        if dup:
            warnings.append(f"npc_templates.{label} 有重复项: {'、'.join(dict.fromkeys(dup))}")

    # No duplicates within each pool
    _dups(surn, "surnames")
    _dups(gm, "given_names_male")
    _dups(gf, "given_names_female")

    # Surnames should be 1-4 chars (clan/family names); flag anything longer
    # or entries that look like full given names (e.g. "我爱罗" is a given name).
    for s in surn:
        if len(s) > 4:
            warnings.append(f"姓氏「{s}」超过 4 字，疑似不是姓氏")

    # Male/female given-name pools should not overlap
    overlap = set(gm) & set(gf)
    if overlap:
        warnings.append(f"男名池与女名池重叠: {'、'.join(sorted(overlap))}")

    # Given-name entries should not equal known clan surnames (they'd be full names)
    surn_set = set(surn)
    full_names = [n for n in gm + gf if n in surn_set and len(n) >= 2]
    if full_names:
        warnings.append(f"名池包含疑似完整姓名（与姓氏池重叠）: {'、'.join(sorted(set(full_names)))}")


def _check_panel_sources(panel: dict, pt: dict, errors: list, warnings: list) -> None:
    """Panel fields referencing attrs should exist in player_templates-derived data.

    Recognizes golden_finger option_map mappings (e.g. {id→golden_finger_id,
    name→golden_finger_name}) so panel fields backed by card-grid options don't
    false-positive.
    """
    attrs_keys = set()
    if isinstance(pt.get("identities"), dict):
        for v in pt["identities"].values():
            if isinstance(v, dict):
                attrs_keys.update(v.keys())
    elif isinstance(pt.get("identities"), list):
        for v in pt["identities"]:
            if isinstance(v, dict):
                attrs_keys.update(v.keys())
    if isinstance(pt.get("golden_fingers"), list):
        for g in pt["golden_fingers"]:
            if isinstance(g, dict):
                attrs_keys.update(g.keys())
    # Known user-supplied attrs (filled at character creation / freeform), not
    # template-derived — don't warn when a panel references them.
    _KNOWN_ATTRS = {
        "age", "gender", "personality", "identity",
        "special_constitution", "background_summary", "identity_desc",
        "spiritual_root", "talent_note", "clothing", "monthly_income",
        "appearance_brief", "appearance_summary", "moral_character",
        "sexual_knowledge", "fertility", "lifestyle_summary",
        "current_action", "current_pose", "visible_state",
        "height", "weight", "savings", "location_hierarchy",
    }
    for f in panel.get("fields", []):
        src = f.get("source")
        if src == "attrs" and f.get("key"):
            key = f["key"]
            if key in ("_savings_amount", "_savings_unit", "_extensions"):
                continue
            if key in _KNOWN_ATTRS:
                continue
            if key in attrs_keys:
                continue
            # Accept keys that golden_fingers option_map produces (golden_finger_*)
            if key.startswith("golden_finger_"):
                continue
            warnings.append(
                f"panel 字段「{f.get('label', key)}」引用 attrs.{key}，但 player_templates 中未见该字段"
            )


def _check_timelines(wf: dict, errors: list, warnings: list) -> None:
    """Validate world_facts.timelines: each node has required keys, ids unique."""
    timelines = wf.get("timelines")
    if not isinstance(timelines, list):
        return
    ids = []
    for i, t in enumerate(timelines):
        if not isinstance(t, dict):
            warnings.append(f"timelines[{i}] 不是对象")
            continue
        t_id = t.get("id")
        if not t_id:
            errors.append(f"timelines[{i}] 缺少 id")
        elif t_id in ids:
            warnings.append(f"timelines 中 id 重复: {t_id}")
        else:
            ids.append(t_id)
        for key in ("label", "description"):
            if not t.get(key):
                warnings.append(f"timeline「{t_id or i}」缺少 {key}")
        if not isinstance(t.get("must_follow"), list):
            warnings.append(f"timeline「{t_id or i}」缺少 must_follow 数组")
        if not isinstance(t.get("forbidden"), list):
            warnings.append(f"timeline「{t_id or i}」缺少 forbidden 数组")
        if not isinstance(t.get("characters"), list):
            warnings.append(f"timeline「{t_id or i}」缺少 characters 数组")
    if len(timelines) > 1 and not ids:
        errors.append("timelines 中所有节点都缺少 id")


def read_form(wv_id: str) -> dict:
    """Read a pack's form.json (empty dict if absent)."""
    pack_dir = WORLDVIEWS_DIR / wv_id
    return _read_json(pack_dir / _FORM)


def read_ui(wv_id: str) -> dict:
    """Read a pack's ui.json (empty dict if absent)."""
    pack_dir = WORLDVIEWS_DIR / wv_id
    return _read_json(pack_dir / _UI)


def read_artifact(wv_id: str, filename: str) -> dict:
    """Read an arbitrary pack JSON artifact (e.g. player_templates.json)."""
    pack_dir = WORLDVIEWS_DIR / wv_id
    return _read_json(pack_dir / filename)


def write_artifact(wv_id: str, filename: str, data: dict) -> dict:
    """Write a pack JSON artifact and refresh the loader cache.

    `filename` must be a bare JSON filename inside the pack (no path traversal),
    or the whitelisted sub-path `modeler/schema.json`.
    """
    if not _is_valid_id(wv_id):
        raise ValueError(f"无效的世界观 ID: {wv_id!r}")
    _ALLOWED_SUB = "modeler/schema.json"
    if filename == _ALLOWED_SUB:
        rel = Path(filename)
    else:
        if not filename.endswith(".json") or "/" in filename or "\\" in filename or ".." in filename:
            raise ValueError(f"非法文件名: {filename!r}")
        rel = Path(filename)
    pack_dir = WORLDVIEWS_DIR / wv_id
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"世界观 {wv_id} 不存在")
    if not isinstance(data, dict):
        raise ValueError("数据必须是 JSON 对象")
    path = pack_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    reload(wv_id)
    return {"saved": wv_id, "file": filename}


def write_form(wv_id: str, form: dict) -> dict:
    """Write a pack's form.json and refresh the loader cache."""
    if not _is_valid_id(wv_id):
        raise ValueError(f"无效的世界观 ID: {wv_id!r}")
    pack_dir = WORLDVIEWS_DIR / wv_id
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"世界观 {wv_id} 不存在")
    if not isinstance(form, dict):
        raise ValueError("form 必须是 JSON 对象")
    # Basic sanity: title + fields list
    if "fields" not in form or not isinstance(form.get("fields"), list):
        raise ValueError("form.json 必须包含 fields 数组")
    path = pack_dir / _FORM
    path.write_text(json.dumps(form, ensure_ascii=False, indent=2), encoding="utf-8")
    reload(wv_id)
    return {"saved": wv_id, "field_count": len(form.get("fields", []))}


def write_ui(wv_id: str, ui: dict) -> dict:
    """Write a pack's ui.json (frontend labels/buttons/recommendations) and refresh cache."""
    if not _is_valid_id(wv_id):
        raise ValueError(f"无效的世界观 ID: {wv_id!r}")
    pack_dir = WORLDVIEWS_DIR / wv_id
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"世界观 {wv_id} 不存在")
    if not isinstance(ui, dict):
        raise ValueError("ui 必须是 JSON 对象")
    path = pack_dir / _UI
    path.write_text(json.dumps(ui, ensure_ascii=False, indent=2), encoding="utf-8")
    reload(wv_id)
    return {"saved": wv_id, "ui_keys": sorted(ui.keys())}


def list_worldviews() -> list[dict]:
    """Scan the worldviews directory and return manifest summaries."""
    if not WORLDVIEWS_DIR.is_dir():
        logger.warning("Worldviews directory missing: %s", WORLDVIEWS_DIR)
        return []
    results = []
    for entry in sorted(WORLDVIEWS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if not _is_valid_id(entry.name):
            continue
        manifest = _read_json(entry / _MANIFEST)
        if not manifest:
            continue  # not a pack
        results.append({
            "id": entry.name,
            "name": manifest.get("name", entry.name),
            "description": manifest.get("description", ""),
            "version": manifest.get("version", ""),
            "tags": manifest.get("tags", []),
            "maturity_rating": manifest.get("maturity_rating", ""),
        })
    return results


def get(wv_id: str) -> Worldview:
    """Return the loaded pack for wv_id, with graceful degradation.

    Resolution order:
      1. cached pack
      2. pack on disk
      3. DEFAULT_WORLDVIEW_ID pack (if wv_id itself is not the default)
      4. a minimal in-code fallback pack (engine never crashes)
    Each resolution logs a warning so silent misconfiguration is visible.
    """
    if not _is_valid_id(wv_id):
        logger.warning("Invalid worldview id %r — falling back to %s", wv_id, DEFAULT_WORLDVIEW_ID)
        return get(DEFAULT_WORLDVIEW_ID)

    cached = _cache.get(wv_id)
    if cached is not None:
        return cached
    if wv_id in _cache:  # cached as missing
        return get(DEFAULT_WORLDVIEW_ID)

    pack_dir = WORLDVIEWS_DIR / wv_id
    if pack_dir.is_dir():
        pack = _load_pack(wv_id, pack_dir)
        if pack.manifest:
            _cache[wv_id] = pack
            return pack
        _load_errors[wv_id] = "manifest.json missing or invalid"
        logger.warning("Pack %r has no valid manifest — treating as missing", wv_id)

    _cache[wv_id] = None  # mark known-missing
    if wv_id != DEFAULT_WORLDVIEW_ID:
        logger.warning("Worldview %r not found — falling back to %s", wv_id, DEFAULT_WORLDVIEW_ID)
        return get(DEFAULT_WORLDVIEW_ID)

    # Even the default pack is missing — minimal in-code fallback so the
    # engine keeps working with engine-side constants.
    logger.error("Default worldview pack %s is missing — using in-code fallback", DEFAULT_WORLDVIEW_ID)
    fallback = Worldview(
        id=DEFAULT_WORLDVIEW_ID,
        path=WORLDVIEWS_DIR,
        manifest={"worldview_id": DEFAULT_WORLDVIEW_ID, "name": "修仙世界"},
    )
    _cache[DEFAULT_WORLDVIEW_ID] = fallback
    return fallback
