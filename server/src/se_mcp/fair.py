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

TOOLS.update({
    "suit_controls": Fairness("player", "suit"),
    "save_world": Fairness("player", None, "out-of-game action (saving), not something an in-game AI does"),
    "highlight_blocks": Fairness("own", None, "a HUD aid shown only to the player"),
    "clear_highlights": Fairness("own", None),
})

TOOLS.update({
    "environment": Fairness("sensed", None, "local gravity/air/weather are what the suit and cockpit sense; listing every planet and asteroid is omniscient"),
    "forecast": Fairness("own", None),
})

TOOLS.update({
    "weapons": Fairness("own", None, "a turret's current target is sensed by the turret itself"),
    "set_turrets": Fairness("own", "construct"),
    "aim_turrets": Fairness("own", "construct"),
    "fire_weapons": Fairness("own", "construct", "also gated by allowWeapons in the plugin config"),
    "load_magazine": Fairness("own", "construct"),
    "track_target": Fairness("sensed", "construct", "camera raycasts only; GPS/coords start points must come from a fair source"),
    "damage_report": Fairness("own", None),
})

TOOLS.update({
    "contacts": Fairness("sensed", None, "radio, turrets and sensors only"),
    "radio_signals": Fairness("sensed", None, "what the HUD shows from antennas and beacons in range or relayed"),
    "radio_equipment": Fairness("own", None),
    "set_radio": Fairness("own", "construct"),
    "laser_link": Fairness("own", "construct", "aims both ends when the other laser is on our own grid (its terminal, via the network)"),
})

TOOLS.update({
    "toolbar": Fairness("own", None),
    "set_toolbar_slot": Fairness("own", "construct", "the player's own toolbar is 'suit'"),
    "edit_group": Fairness("own", "construct"),
})

TOOLS.update({
    "ore_scan": Fairness("sensed", None, "the HUD's ore markers from the ship's ore detectors; never the map's voxel data"),
})

TOOLS.update({
    "docking_status": Fairness("own", None),
    "landing_gear": Fairness("own", "construct"),
    "undock": Fairness("own", "construct"),
    "dock": Fairness("own", "construct", "reads the target connector's position: fine for our own ships/stations; others need sensing"),
})

TOOLS.update({
    "ship_hud": Fairness("own", None, "other ships: needs antenna link"),
    "ship_notes": Fairness("own", None, "notes the player and agents wrote; not game state"),
    "write_ship_notes": Fairness("own", None, "writes a notes file outside the game"),
})

TOOLS.update({
    "surface_map": Fairness("own", None, "raycasts against grids; other ships' surfaces: fair mode should limit to what cameras could see"),
    "land": Fairness("own", "construct", "the target surface is measured with raycasts; fine for our own ships/stations"),
})

TOOLS.update({
    "hud_draw": Fairness("own", None, "a HUD aid shown only to the player; markers on things you can't see are M8's to filter"),
    "hud_ship_panel": Fairness("own", None, "a HUD aid shown only to the player"),
    "hud_track": Fairness("omniscient", None, "boxes enemies/meteors anywhere in range; fair mode: only what antennas, cameras, sensors or turrets see"),
    "hud_remove": Fairness("own", None),
    "hud_clear": Fairness("own", None),
    "hud_list": Fairness("own", None),
    "guide_to": Fairness("omniscient", None, "routes with raycasts against everything in the way; fair mode: obstacles and targets only from the contact table"),
    "stop_guide": Fairness("own", None),
})

TOOLS.update({
    "ai_soul": Fairness("own", None, "the companion's personality file; not game state"),
    "write_ai_soul": Fairness("own", None, "writes the personality file outside the game"),
    "rename_blocks": Fairness("own", "construct"),
    "rotate_ship": Fairness("own", "construct"),
    "point_ship": Fairness("own", "construct", "the target's position must be known: radio, GPS or sensed"),
    "target_block": Fairness("sensed", "construct", "fair mode: the block must be seen by a camera, turret or the player"),
    "ai_soul_presets": Fairness("own", None, "packaged personality files"),
    "use_ai_soul": Fairness("own", None, "writes the personality file outside the game"),
    "watch": Fairness("own", None, "reads what its method reads; same fairness as that method"),
    "list_watches": Fairness("own", None),
    "remove_watch": Fairness("own", None),
    "recent_events": Fairness("player", None, "chat the player sent and watches that fired"),
})
