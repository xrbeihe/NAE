"""Shared player-panel renderer.

Turns a Player ORM row + a worldview panel.json spec into the display
string shown in the frontend / returned in TurnResult.player_panel.

The xianxia_v1 panel spec reproduces the original hardcoded panel
byte-for-byte; other worldviews define their own field lists.
"""

from dataclasses import dataclass


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
