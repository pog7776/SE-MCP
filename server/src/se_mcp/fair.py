"""Fair-play metadata for every tool: where its knowledge comes from and what link an action needs.

Nothing enforces this yet (that's the M8 fair mode). It's recorded now so fair mode can filter tools later
without rewriting them, and a test makes sure every new tool declares its entry.

Knowledge:
- "own": the ship's own blocks and terminal, as seen from a seat or terminal on it.
- "player": what the player's suit and eyes know (their inventory, status, screen, crosshair).
- "sensed": what the ship's sensors/cameras/detectors/antennas pick up.
- "omniscient": world knowledge no in-game AI would have (e.g. listing every grid within 5 km).
Link (for actions): "construct" = needs to be on or terminal-connected to that ship; "suit" = the player's
own body; "antenna" = remote, needs a radio/antenna path in fair mode; None = changes nothing.
"""

from __future__ import annotations

from typing import NamedTuple


class Fairness(NamedTuple):
    knowledge: str
    link: str | None
    note: str = ""


TOOLS: dict[str, Fairness] = {
    "se_status": Fairness("player", None),
    "list_grids": Fairness("omniscient", None, "fair mode: only grids seen by antennas, cameras, sensors or turrets"),
    "ship_status": Fairness("own", None, "other ships: needs antenna link"),
    "list_blocks": Fairness("own", None),
    "inspect_block": Fairness("own", None),
    "looked_at": Fairness("player", None),
    "list_groups": Fairness("own", None),
    "inventory": Fairness("own", None),
    "find_items": Fairness("own", None),
    "suit_status": Fairness("player", None),
    "production": Fairness("own", None),
    "block_action": Fairness("own", "construct"),
    "group_action": Fairness("own", "construct"),
    "set_block_property": Fairness("own", "construct"),
    "toggle_blocks": Fairness("own", "construct"),
    "rename_block": Fairness("own", "construct"),
    "set_custom_data": Fairness("own", "construct"),
    "move_items": Fairness("own", "construct", "suit moves need the player in reach"),
    "organize_inventory": Fairness("own", "construct"),
    "say": Fairness("player", "suit"),
    "align_to_sun": Fairness("own", "construct", "uses the game's sun direction, which solar panels also sense"),
    "read_pb_code": Fairness("own", None),
    "write_pb_script": Fairness("own", "construct"),
    "run_pb": Fairness("own", "construct"),
    "pb_output": Fairness("own", None),
    "recompile_pb": Fairness("own", "construct"),
    "fly_to": Fairness("own", "antenna", "remote control of a ship the player isn't on needs a link"),
    "autopilot_status": Fairness("own", None),
    "stop_autopilot": Fairness("own", "antenna"),
    "list_gps": Fairness("player", None),
    "add_gps": Fairness("player", "suit"),
    "remove_gps": Fairness("player", "suit"),
    "screenshot": Fairness("player", None),
    "list_cameras": Fairness("own", None),
    "camera_view": Fairness("sensed", "construct"),
    "ship_photo": Fairness("omniscient", None, "a free-floating viewpoint; fair mode: needs a drone or camera there"),
    "camera_scan": Fairness("sensed", "construct"),
    "sensors": Fairness("sensed", None),
}

TOOLS.update({
    "recipe": Fairness("own", None, "game knowledge (recipes) plus the ship's own stock"),
    "queue_production": Fairness("own", "construct"),
    "remove_from_queue": Fairness("own", "construct"),
    "clear_production_queue": Fairness("own", "construct"),
})

TOOLS.update({
    "block_cost": Fairness("own", None, "game knowledge (block definitions) plus the ship's own stock"),
    "build_planner": Fairness("player", "suit", "withdraw follows the inventory screen's conveyor rules"),
    "projector_status": Fairness("own", "construct"),
})

TOOLS.update({
    "list_screens": Fairness("own", None),
    "write_screen": Fairness("own", "construct"),
    "set_screen_mode": Fairness("own", "construct"),
})
