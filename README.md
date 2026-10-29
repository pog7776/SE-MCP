# SE-MCP

Lets an AI assistant (over MCP) see and control your Space Engineers 1 ships, stations and suit. See [PLAN.md](PLAN.md) for the design and [protocol.md](protocol.md) for the wire protocol.

Status: **M7 (more of the game), in progress**. Inventory management is done: move items through conveyors, find items, and organize a ship by container tags (`[Ore]`, `[Ingots]`, `[Components]`, ...). Production too: look up recipes in this world's costs and queue assembler jobs. See through camera blocks and take drone shots of a ship, rendered offscreen so your view isn't touched; scan with camera raycasts; read sensors. Read ships, blocks, inventories, production, suit and the crosshair target, take screenshots, and run terminal actions, set properties, rename, toggle blocks and show messages in-game. Write, compile and run programmable block scripts, with the compiler's errors and a backup of the old code. Tools: `se_status`, `list_grids`, `ship_status`, `list_blocks`, `inspect_block`, `looked_at`, `list_groups`, `inventory`, `suit_status`, `screenshot`, `production`, `block_action`, `group_action`, `set_block_property`, `toggle_blocks`, `rename_block`, `set_custom_data`, `say`, `align_to_sun`, `read_pb_code`, `write_pb_script`, `run_pb`, `pb_output`, `recompile_pb`, `fly_to`, `autopilot_status`, `stop_autopilot`, `list_gps`, `add_gps`, `remove_gps`, `list_cameras`, `camera_view`, `ship_photo`, `camera_scan`, `sensors`, `find_items`, `move_items`, `organize_inventory`, `recipe`, `queue_production`, `remove_from_queue`, `clear_production_queue`, `block_cost`, `build_planner`, `projector_status`, `list_screens`, `write_screen`, `set_screen_mode`, `highlight_blocks`, `clear_highlights`, `suit_controls`, `save_world`, `environment`, `forecast`, `weapons`, `set_turrets`, `aim_turrets`, `fire_weapons`, `load_magazine`, `track_target`, `damage_report`, `contacts`, `radio_signals`, `radio_equipment`, `set_radio`, `laser_link`, `toolbar`, `set_toolbar_slot`, `edit_group`, `ore_scan`. Resource: `se://pb-primer`. Combat: see what turrets are targeting and the ammo on board, change their targets, aim them, track ships with camera raycasts, switch interior turret magazines, and get a damage report. Radio: hear what antennas and beacons broadcast (as the HUD does, relays included), manage your antennas and beacons, and link laser antennas. `contacts` merges radio, turret targets and sensors into one picture. Toolbars: read and set any ship controller's, panel's or timer's toolbar slots and your own; create and edit block groups. Ore: `ore_scan` reads what your ore detectors show on the HUD (per ore, nearest first, optional GPS marks). Firing is off until you set `"allowWeapons": true` in `config.json`. Set `"readOnly": true` in `config.json` to block all changes.

```
Claude ──MCP/stdio──▶ server/ (Python) ──TCP 127.0.0.1:8765──▶ SEMcp.Loader ─loads─▶ SEMcp.Host + SEMcp.Core (in game)
```

| Piece | What it is | How it updates |
|---|---|---|
| `plugin/SEMcp.Loader` | The Pulsar plugin. Tiny and stable. | Game restart |
| `plugin/SEMcp.Host` | TCP server, config, game-thread queue | **Hot reload** on build |
| `core/` | Game logic (ModAPI only) | **Hot reload** on build |
| `server/` | Python MCP server | Restart the MCP server |

## Prerequisites

- Space Engineers 1. The game path is set in [Directory.Build.props](Directory.Build.props). Override it with `dotnet build -p:SEBin=<...>\Bin64`.
- .NET SDK 9+ (it builds both the `net48` and `net9.0` targets).
- [Pulsar](https://github.com/SpaceGT/Pulsar/releases/latest), installed to `%AppData%\Pulsar` (the default for the [installer](https://github.com/StarCpt/Pulsar-Installer)).
- Python 3.11+ and [uv](https://docs.astral.sh/uv/).
- Optional: `ilspycmd` (`dotnet tool install -g ilspycmd --version 9.1.0.7988`, the last version that runs on .NET 9) to regenerate the decompiled reference.

## Build

```bash
dotnet build SEMcp.sln -c Release
```

A Release build deploys:

| File | To | Notes |
|---|---|---|
| `SEMcp.Loader.dll` (net48) | `%AppData%\Pulsar\Legacy\Local` | Skipped with a warning while SE has it locked |
| `SEMcp.Loader.dll` (net9.0) | `%AppData%\Pulsar\Interim\Local` | Same |
| Host + Core (+ pdbs), per target | `%AppData%\SpaceEngineers\SEMcp\host\<net48\|net9.0>` | The running game picks these up about 1.5 s later |

So after the first install, **change code → build → the game reloads it**, with no restart. A restart is only needed when the loader itself changes (`plugin/SEMcp.Loader`). You'll see `Host generation N started` in the SE log.

## Load in Pulsar (one time)

1. Build (above).
2. Start the game through Pulsar. Steam launch option: `"%AppData%\Pulsar\Legacy.exe" %command%` (use `Interim.exe` for .NET 10. Interim has its own plugin list, so enable it there separately).
3. Main menu → **Plugins** → **+** → find **SEMcp.Loader** → enable → **Apply** → let the game restart.

Check the newest `%AppData%\SpaceEngineers\SpaceEngineers_*.log`:

```
[SEMcp] Init (loader 0.1.0.0, net48), host dir ...
[SEMcp] Listening on 127.0.0.1:8765
[SEMcp] Host generation 1 started (0.1.0.0)
```

The first start also creates `%AppData%\SpaceEngineers\SEMcp\config.json` with a random token. Keep it private.

## MCP server

```bash
cd server
uv run pytest            # tests against a fake game, no SE needed
uv run se-mcp            # the stdio MCP server (normally your MCP client starts it)
```

The repo's [.mcp.json](.mcp.json) registers it for Claude Code in this folder. Elsewhere:

```json
{ "mcpServers": { "space-engineers": {
    "command": "uv",
    "args": ["--directory", "S:/Dev/SpaceEngineers/SE-MCP/server", "run", "se-mcp"] } } }
```

(`uv` is at `%AppData%\Python\Python311\Scripts\uv.exe` if it isn't on PATH.)

Poke at it by hand with `npx @modelcontextprotocol/inspector`.

## Decompiled reference

`ref/decompiled/` (gitignored) holds ilspycmd output for the game DLLs, to look up real API names. Regenerate after a game update:

```bash
for d in Sandbox.Game Sandbox.Common SpaceEngineers.Game SpaceEngineers.ObjectBuilders VRage VRage.Game VRage.Library VRage.Math VRage.Scripting VRage.Render VRage.Input; do ilspycmd -p -o "ref/decompiled/$d" -r "$SEBIN" "$SEBIN/$d.dll"; done
```
