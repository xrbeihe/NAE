"""World template data — loaded from world_templates.json.

Contains sects (49) and settlements (34) for world generation.
"""

from ane.content.json_loader import world_data

_data = world_data()

SECTS: list[dict]       = _data["sects"]
SETTLEMENTS: list[dict] = _data["settlements"]
