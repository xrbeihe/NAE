"""NPC template data — loaded from npc_templates.json.

Provides the same interface as before, now sourced from a JSON file.

Usage:
    python -m ane.content.npc_templates      # 正确
    python backend/ane/content/npc_templates.py  # 也正确（会自行添加路径）
"""

import sys
from pathlib import Path

# Allow running as a script from project root or backend/
if __name__ == "__main__" and "ane" not in sys.modules:
    _p = Path(__file__).resolve().parent.parent.parent
    if _p.name == "backend":
        sys.path.insert(0, str(_p.parent))
    sys.path.insert(0, str(_p))

from ane.content.json_loader import npc_data

_data = npc_data()

SURNAMES: list[str]            = _data["surnames"]
GIVEN_NAMES_MALE: list[str]    = _data["given_names_male"]
GIVEN_NAMES_FEMALE: list[str]  = _data["given_names_female"]
REALMS: list[str]              = _data["realms"]
IDENTITIES: list[str]          = _data["identities"]
PERSONALITIES: list[str]       = _data["personalities"]
CORE_ARCHETYPES: list[dict]    = _data["core_archetypes"]


# ── Direct run: print template info ─────────────────────────

if __name__ == "__main__":
    print(f"NPC templates loaded from npc_templates.json")
    print(f"  Surnames:       {len(SURNAMES)}")
    print(f"  Male names:     {len(GIVEN_NAMES_MALE)}")
    print(f"  Female names:   {len(GIVEN_NAMES_FEMALE)}")
    print(f"  Realms:         {len(REALMS)}")
    print(f"  Identities:     {len(IDENTITIES)}")
    print(f"  Personalities:  {len(PERSONALITIES)}")
    print(f"  Core Archetypes:{len(CORE_ARCHETYPES)}")
    print()
    print("Usage:  python -m ane.content.npc_templates")
    print("   or:  python backend/ane/content/npc_templates.py")
