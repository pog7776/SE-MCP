# Space Engineers MCP — Implementation Plan

Let an AI assistant (over MCP) see and control the player's ships, stations and suit in **Space Engineers 1**, and write, push and run Programmable Block (PB) scripts. It's a co-pilot: it answers "how's my ship?", flips blocks, writes scripts, and sees the screen.

> **Rule for implementers:** Many SE member names in this doc are from memory. Before relying on one, check it in the decompiled source (`ref/decompiled/`, see M0) and fix the code to match. Don't guess.

## Environment (user's machine)

| Item | Value |
|---|---|
| Game | SE 1.210.014, `S:\SteamLibrary\steamapps\common\SpaceEngineers\Bin64` |
| Runtime | .NET Framework 4.8 via Pulsar `Legacy.exe`, or .NET 10 via Pulsar `Interim.exe`. We build `net48` + `net9.0` for both |
| .NET SDKs | 5–9 installed (no .NET 10 SDK. The `net9.0` build runs on the .NET 10 runtime) |
| Python | 3.11.9 at `%LOCALAPPDATA%\Programs\Python\Python311` (the `python` on PATH is the Store stub) |
| JSON in Bin64 | `System.Text.Json.dll` 8.0 (+ deps) is shipped. **Newtonsoft.Json is not.** |
| Pulsar | v2.4.3 at `%AppData%\Pulsar`. Local DLLs go in `<Legacy\|Interim>\Local`, enabled via Plugins → **+** |
| Tools | `uv` 0.13 (`%AppData%\Python\Python311\Scripts`, not on PATH), `ilspycmd` 9.1.0.7988 (latest needs .NET 10 SDK) |
| MCP SDK | `mcp` 2.x: `FastMCP` is now `mcp.server.mcpserver.MCPServer`, and pydantic fields are snake_case |

## Architecture

```
MCP client (Claude Code / Desktop)
        │  MCP over stdio
        ▼
Python MCP server  (server/)          ← restarts freely; all tool design lives here
        │  newline-delimited JSON-RPC over TCP 127.0.0.1:8765, token auth
        ▼
Host: Pulsar client plugin (plugin/)  ← runs inside SpaceEngineers.exe
        │  game-thread calls
        ▼
Core (core/)                          ← game logic, ModAPI interfaces only
        ▼
Game world: grids, terminal blocks, PBs, remote controls, character, GPS
```

### Design principles

1. **Thin, generic game side.** .NET Framework can't unload assemblies, so game-side changes are costly. The game side exposes broad primitives. Naming, formatting, fuzzy matching and convenience tools all live in Python.
2. **Core is host-agnostic and uses ModAPI interfaces only** (`Sandbox.ModAPI`, `VRage.Game.ModAPI`, `IMyTerminalBlock`, `IMyProgrammableBlock`, …), no reflection, threads or IO. That keeps it portable to a workshop-mod host or a Torch plugin later. Anything that needs internals (screenshots via the renderer, etc.) lives in the host and is marked host-only.
3. **Game thread only.** Transport runs on background threads. Requests go into a `ConcurrentQueue`, which is drained in `IPlugin.Update()` with a ~2 ms per-frame budget. Results complete a `TaskCompletionSource`. The queue is always drained. Handlers that need a world return `no_session` themselves, so `ping` works from the main menu.
4. **Act as the player.** Only touch grids/blocks the local player may use (owner, faction-shared, or nobody), through normal terminal actions/properties so multiplayer sync behaves like player input. Remote terminal access normally also needs an antenna link. v1 ignores that, and `config.requireAntennaLink` is a later option.
5. **Safe by default.** Bind to 127.0.0.1 only, require a shared token, keep per-feature toggles in config, and log every write to the SE log.

### Host decision: Pulsar plugin vs workshop mod

| | Pulsar plugin (chosen for v1) | Workshop/local mod |
|---|---|---|
| Install | Pulsar (one time) | Nothing extra; add the mod to each world |
| Works in | every world and server, client-side | only worlds with the mod. MP needs the server owner to add it |
| API | everything (ModAPI + internals) | whitelisted ModAPI only |
| IPC | TCP socket | no sockets: poll request/response files in mod Storage |
| Update loop | game restart, or a hot-reload loader (M1) | reload the world |
| In-engine screenshots | yes | no (Python window capture still works) |

v1 ships the plugin host. Because Core only uses ModAPI, a mod host (file-polling transport) stays possible later if a no-Pulsar option is wanted.

## Repo layout

```
SE-MCP/
  PLAN.md  README.md  protocol.md       # protocol.md = RPC reference, kept in sync
  .gitignore                            # ref/, bin/, obj/, .venv/
  ref/decompiled/                       # ilspycmd output (gitignored, local only)
  Directory.Build.props                 # SEBin path (default above, overridable)
  core/      SEMcp.Core.csproj          # net48 lib, ModAPI only
    Dispatcher.cs                       # method name → handler, error mapping
    Access.cs  Ids.cs  Dto.cs
    Services/ Session, Grids, Blocks, Inventory, Production, ProgrammableBlocks,
              RemoteControl, Character, Gps, Notify
  plugin/
    SEMcp.Loader/                       # tiny Pulsar plugin: loads + hot-reloads the host from bytes
    SEMcp.Host/                         # TCP server, game-thread queue, config, host-only methods
  server/
    pyproject.toml                      # uv; deps: mcp, pydantic, mss, pywin32 (or ctypes)
    src/se_mcp/ __main__.py bridge.py tools.py format.py resolve.py screenshot.py primer.md
    tests/ fake_bridge.py test_tools.py
```

## Part 1: Game side (C#)

### Build

- SDK-style csproj, `net48`, `x64`, `LangVersion latest`, `Private=false` references from `$(SEBin)`: `VRage`, `VRage.Game`, `VRage.Library`, `VRage.Math`, `Sandbox.Common`, `Sandbox.Game`, `SpaceEngineers.Game`, `SpaceEngineers.ObjectBuilders`, `System.Text.Json` (+ its deps like `System.Memory` if needed, all from Bin64, never NuGet copies).
- Pulsar loads local plugins via a **development folder** (launch Pulsar with `-sources`, then Sources → add folder). Pulsar compiles client plugins from source on the player's machine, so Release builds are what users get. Confirm the exact steps against current Pulsar docs/template and write them into README.
- Dev world: small Creative save. Launch option `-skipintro`.

### Hot reload (M1)

`SEMcp.Loader` is the only thing Pulsar loads. It reads `SEMcp.Host.dll` + `SEMcp.Core.dll` bytes from the build output, `Assembly.Load`s them, and calls `Start()` / `Update()` / `Stop()`. A `debug.reload` RPC (or a file watcher) stops the current instance, closes the socket, and loads the new bytes. Old assemblies leak in memory, which is fine for dev. Rule that makes this safe: **the host never subscribes to game events or registers session components**, and everything goes through the drained queue.

### Config (`%AppData%\SpaceEngineers\SEMcp\config.json`, created on first run)

```json
{
  "port": 8765,
  "token": "<random, generated on first run>",
  "readOnly": false,
  "allowPbCodePush": true,
  "allowAutopilot": true,
  "allowScreenshots": true,
  "maxRangeMeters": 50000
}
```

`maxRangeMeters` = max distance from the player's character (or controlled grid) for any grid the tools may see or touch. `readOnly` blocks every write method, including GPS and notifications. Python reads the token from this file or `SE_MCP_TOKEN`.

### Transport

- `TcpListener` on `127.0.0.1:port` (no http.sys, no URL ACL). One UTF-8 JSON object per line, both directions.
- First message must be `{"method":"auth","params":{"token":"..."}}`, otherwise disconnect.
- JSON-RPC 2.0 shape: `{"jsonrpc":"2.0","id":1,"method":"blocks.list","params":{}}` → `{"jsonrpc":"2.0","id":1,"result":...}` or `{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"...","data":{"kind":"not_found"}}}`.
- Error kinds (in `error.data.kind`): `no_session`, `not_found`, `ambiguous`, `access_denied`, `out_of_range`, `read_only`, `invalid_params`, `feature_disabled`, `scripts_disabled`, `game_error`.
- One client at a time. A new authenticated connection replaces the old one.
- Reserved for later: server→client notifications `{"jsonrpc":"2.0","method":"event","params":{...}}`.

### Identifiers

- Entity ids (grids, blocks, characters) are serialized as **strings**.
- Blocks can be addressed by `blockId`, or by `{gridId, name}` (exact `CustomName`). Fuzzy matching is Python's job.
- Grid groups: `grids.list` reports each grid's mechanical group (rotors/pistons/hinges) and logical group (connectors), using `GridLinkTypeEnum`, so Python can present "a ship" as one thing.

### RPC methods

Read:
| Method | Params | Returns |
|---|---|---|
| `ping` | — | plugin + game version, session loaded, world name. Works with no session |
| `session.info` | — | world, player name/identityId, game time, online mode, scripts enabled |
| `player.context` | — | controlled entity (seat/cockpit/RC) and its grid, the grid the character stands on/in, **the block/grid being looked at** (aim raycast, ~10 m / interact range) |
| `grids.list` | `range?`, `includeUnowned?` | id, name, size, static, block count, relation, position, distance, velocity, mechanical/logical group ids |
| `grid.get` | `gridId` | above + mass, speed, power (produce/consume/stored), battery / H₂ / O₂ tank fill, cargo fill %, damaged block count, connected grids |
| `blocks.list` | `gridId`, `type?`, `nameContains?`, `group?`, `limit=200`, `offset=0` | terminal blocks: id, name, type/subtype, enabled, functional, integrity %, relation. Plus `total` |
| `block.get` | `blockId` | above + `DetailedInfo`, `CustomInfo`, `CustomData`, terminal properties (id, type, value), actions (id, name) |
| `groups.list` | `gridId` | group names + member ids |
| `inventory.get` | `gridId` or `blockId`, `includeConnected?` | per-inventory items (type/subtype, amount), volume used/max, plus a totals rollup |
| `production.get` | `gridId` | assemblers/refineries: queue items, producing?, blocked reason (from DetailedInfo if needed) |
| `pb.get_code` | `blockId` | source (`ProgramData`) |
| `pb.output` | `blockId` | echo text + compile/runtime errors (`DetailedInfo`) |
| `gps.list` | — | name, coords, description, visible |
| `character.status` | — | health, O₂, H₂, energy, jetpack, dampeners, helmet, position, velocity, gravity |

Write (all blocked by `readOnly`):
| Method | Params | Notes |
|---|---|---|
| `block.action` | `blockId` or `{gridId, group}`, `action` | `GetActionWithName(id).Apply(block)` |
| `block.set_property` | `blockId`, `property`, `value` | typed via `ITerminalProperty<T>`: bool / float / Color / string / long |
| `block.set_name` / `block.set_custom_data` | `blockId`, `value` | |
| `pb.set_code` | `blockId`, `code` | gated by `allowPbCodePush`. Sets `ProgramData` (recompiles automatically). Returns immediately, and Python polls `pb.output` for the compile result (it's async in MP) |
| `pb.recompile` | `blockId` | |
| `pb.run` | `blockId`, `argument` | like pressing Run |
| `rc.fly_to` | `blockId`, `target` {x,y,z}, `speedLimit?`, `collisionAvoidance?`, `mode?` | gated by `allowAutopilot`. Clear waypoints, add one, enable |
| `rc.stop` | `blockId` | |
| `gps.add` / `gps.remove` | `name`, `coords?`, `description?` | |
| `notify` | `text`, `durationMs?`, `kind`=`hud`/`chat` | assistant speaks in-game (`ShowNotification` / `ShowMessage`) |

Host-only:
| Method | Notes |
|---|---|
| `screenshot` | in-engine capture (e.g. `MyRenderProxy.TakeScreenshot`), `hideHud?`, downscaled JPEG/PNG as base64, gated by `allowScreenshots`. Python falls back to window capture when unavailable |
| `debug.reload` | hot-reload host + core |
| `debug.selftest` | runs safe reads, reports failures |

### Things to verify in `ref/decompiled/`

- Entities: `MyAPIGateway.Entities.GetEntities`, `IMyCubeGrid.GetBlocks` / `GetFatBlocks`, `MyAPIGateway.TerminalActionsHelper.GetTerminalSystemForGrid` (`GetBlockGroups`, `GetBlocksOfType`), `IMyCubeGrid.GetGridGroup(GridLinkTypeEnum)`.
- Player: `MyAPIGateway.Session.Player` (IdentityId, Character, Controller.ControlledEntity), `IMyCubeBlock.GetUserRelationToOwner`.
- Aim target: a raycast from the camera (`MyAPIGateway.Session.Camera`) with `MyAPIGateway.Physics.CastRay`, or the character's existing interaction target.
- PB: `Sandbox.ModAPI.IMyProgrammableBlock.ProgramData` (setter recompiles), `Run`/`TryRun`, `Recompile`. Check `MySession.Static.EnableIngameScripts` / session settings for "scripts disabled". Confirm `ProgramData` set syncs in MP.
- RC: `Sandbox.ModAPI.Ingame.IMyRemoteControl` (`ClearWaypoints`, `AddWaypoint`, `SetAutoPilotEnabled`, `SpeedLimit`, `SetCollisionAvoidance`, `FlightMode`).
- Character: `IMyCharacter` (`SuitEnergyLevel`, `OxygenLevel`, `GetSuitGasFillLevel`, `EnabledThrusts`, `EnabledDamping`, `EnabledHelmet`), health via the stat component.
- GPS: `MyAPIGateway.Session.GPS` (`GetGpsList`, `Create`, `AddGps`, `RemoveGps`).
- Screenshot API in the render proxy, and whether it can write to a path / memory.

Every handler catches exceptions and returns `game_error`. The `Update()` drain is wrapped in try/catch and logs to `MyLog.Default.WriteLine("[SEMcp] ...")`.

## Part 2: Python MCP server

- `uv` project (`server/src/se_mcp`), Python 3.11+, official `mcp` SDK 2.x (`MCPServer`), stdio transport. Check the installed SDK's API before writing tools.
- `bridge.py`: asyncio TCP client, auth on connect, auto-reconnect with backoff, id → future map, 10 s default timeout. When the game is down, tools say "Space Engineers isn't running or the plugin isn't loaded."
- `resolve.py`: grid/block names → ids. Fuzzy match. "Ambiguous: did you mean …". Defaults to the **current grid** from `player.context` when `grid` is omitted.
- `screenshot.py`: returns an MCP image. Uses the plugin's `screenshot` if available, else captures the SE window (borderless/windowed. Exclusive fullscreen may return black). Downscale to ~1280 px wide.

### MCP tools

Tools are named for the model, with short descriptions and compact text output.

- `se_status`: connection, world, player, what you're controlling / looking at
- `screenshot(hide_hud=False)`: what the player sees
- `list_grids(range_m=5000)`
- `ship_status(grid=current)`: power, H₂/O₂, cargo, speed, damage
- `list_blocks(grid=current, type=None, name_contains=None)`
- `inspect_block(block)` / `looked_at()`: properties, actions, DetailedInfo, CustomData
- `block_action(block, action)` / `group_action(group, action, grid=current)`
- `set_block_property(block, property, value)`
- `toggle_blocks(on, grid=current, name_contains|group|type)`
- `inventory(grid=current, item=None)` / `production(grid=current)`
- `read_pb_code(block)` / `write_pb_script(block, code)` (waits for and returns the compile result) / `run_pb(block, argument="")` / `pb_output(block)`
- `fly_to(remote_control, target)` (target = x,y,z or GPS name) / `stop_autopilot(remote_control)`
- `list_gps()` / `add_gps(name, x, y, z, description="")`
- `suit_status()`
- `say(text)`: in-game notification

Resource/prompt `se://pb-primer`: PB script structure (`Program()`, `Main(string argument, UpdateType updateSource)`, `Save()`), `GridTerminalSystem`, `Echo`, `Runtime.UpdateFrequency`, `Me`, `Storage`, sandbox limits (instruction count, no IO/reflection, whitelisted namespaces).

Client config (README):
```json
{ "mcpServers": { "space-engineers": {
    "command": "uv",
    "args": ["--directory", "S:/Dev/SpaceEngineers/SE-MCP/server", "run", "se-mcp"] } } }
```

## Milestones

0. ✅ **Groundwork**: `git init` + `.gitignore`. Install `ilspycmd`, decompile the SE DLLs into `ref/decompiled/`. Install `uv` and Pulsar. Hello-world plugin logs to the SE log (verified in-game).
1. ✅ **Skeleton**: Loader + Host + Core, TCP + auth, `ping`, `debug.reload` + auto-reload on build, `net48` + `net9.0` (Legacy + Interim). Python `se_status`. Verify end-to-end from Claude Code and from `npx @modelcontextprotocol/inspector`.
2. ✅ **See the world**: `session.info`, `player.context`, `grids.list`, `grid.get`, `blocks.list`, `block.get`, `groups.list`, `character.status`, `inventory.get`, and screenshots (window capture first, in-engine next). Matching tools, name resolution, the current-grid default. (Screenshots are window capture via PrintWindow. In-engine capture comes in M6.)
3. ✅ **Control**: `block.action`, `block.set_property`, set name/custom data, group actions, `notify`, `readOnly`, access + range checks, `production.get`.
4. ✅ **Programmable blocks**: `pb.get` / `pb.set_code` / `pb.recompile` / `pb.run`, the primer, code backups. Compiler messages come from the host, because the game discards them outside the editor. The loop test passed live: push, run, break, read the errors, fix.
5. ✅ **Autopilot + GPS**: `rc.get` / `rc.fly_to` / `rc.stop`, plus GPS list/add/remove. Flies nose-first automatically: the RC face matching the cockpit is picked, and cryo pods and passenger seats are ignored. Live test passed: 150 m at 15 m/s, stopped about 1 m from the target. Not covered yet: the newer AI blocks (AI Flight (Move), AI Basic (Task), Offensive/Defensive, Recorder). Their ModAPI can't add waypoints, so they're left to the generic block tools for now.
6. ✅ **Vision + sensing**: `camera.capture` (host, offscreen render via a Harmony prefix on `MyRender11.DrawScene`; CameraLCD approach), `camera.list` / `camera.raycast` / `sensors.get`. Tools: `list_cameras`, `camera_view`, `ship_photo` (drone shot from any side), `camera_scan`, `sensors`, `screenshot(hide_hud=True)`. Captures are saved to `%AppData%\SpaceEngineers\SEMcp\captures`. Verified live: every camera on the Red Ship renders in about 50 ms. Sensors were verified live too (one triggered by the player, one idle). Not verified yet: Interim (.NET 10).
7. **More of the game**: inventories, production, build planning, screens, highlights, suit, environment, forecasts, ore detectors, combat, saves, docking. See below.
8. **Fair play**: a switchable mode that limits what the assistant knows and can reach to what the ship could plausibly sense and talk to. See below.
9. **Polish**: README, protocol.md, fake-bridge tests, error messages, a `selftest` tool, and the first real run of the Interim (.NET 10) build (deliberately left until all milestones are done). The end-to-end MCP stdio run already passes for every read tool.
10. **Companion** (after the core is done): the assistant as a ship/suit AI you talk to in-game, plus game events (damage, destroyed blocks, low resources, chat, new contacts). See below.
11. **In-game UI and displays**: the MCP Display screen app, the assistant's own HUD and windows, and (opt-in) authoring new screen apps at runtime. See below.

### M6: Vision + sensing

Goal: let the assistant look through any camera block on a usable grid, without changing what the player sees, and read the ship's sensors.

- **Offscreen camera render** (host-only, the main piece). Render the scene a second time from the camera block's view matrix into our own render target, then copy it to a CPU staging texture and encode a JPEG. Prior art: **CameraLCD Remastered** (StarCpt, MPL-2.0, on PluginHub) already does this to show cameras on LCDs. It patches `MyRender11.DrawGameScene` with Harmony and runs the render scheduler again with the camera's matrices (`CameraViewRenderer.Draw`), with fixes for occlusion and the first-person head. Study it and write our own (credit it; copying its files would bring MPL-2.0 terms with them). Notes:
  - Render on demand only: one extra scene pass for the frame we capture, so normal play costs nothing.
  - Rendering happens on the render thread. The request goes there from the game thread, and the frame comes back via a `TaskCompletionSource`.
  - The Harmony patch must survive hot reload: patch with our own Harmony id and unpatch in `Stop()`, or put the tiny patch stub in the Loader and have it call into the current host.
  - Verify Interim (.NET 10) compatibility. Render internals may differ.
  - Fallback if offscreen render fails: briefly switch the player's view to the camera (`MyCameraBlock.RequestSetView()` / `OnExitView()`) and capture the screen.
- **In-engine `screenshot`** (host-only): capture the main view via the same readback, with `hideHud`. It works when the game is minimized or in exclusive fullscreen. Keeps the Python window capture as a fallback.
- **`camera.list`** (Core): camera blocks on a grid with name, direction, FOV, raycast range available.
- **`camera.raycast`** (Core, `IMyCameraBlock.Raycast`): what a camera hits along its axis or toward a point, as `MyDetectedEntityInfo` (name, type, relation, distance, hit position). Needs `EnableRaycast`. Range charges over time (`AvailableScanRange`).
- **`sensors.get`** (Core, `IMySensorBlock`): per sensor block on a grid: active or not, detection settings (players, ships, asteroids, owner/friendly/enemy...), field extents, and `DetectedEntities` (name, type, relation, position, distance).
- **MCP tools:** `list_cameras(grid)`, `camera_view(camera, hide_hud=True)` returning an image, `camera_scan(camera, distance)`, `sensors(grid)`, and `screenshot(hide_hud=False)`.
- **Multiplayer note:** rendering and screenshots are client-local and invisible to other players.

### M7: More of the game

Everything here is tagged for fair play as it's built (see M8). The order is roughly what the player asked for most.

1. ✅ **Inventories** (must-have). Done: `inventory.layout` / `inventory.transfer` and the tools `find_items`, `move_items` and `organize_inventory`. The organizer works on one ship at a time, never touches docked ships, and plans before it moves. All moves follow the inventory screen's conveyor rules (size-aware pathfinding; the suit goes through an access point) and are sent as the screen's player request (`TransferByUser`), so they should work in multiplayer. Multiplayer is untested. Tools also carry fair-play tags (`server/src/se_mcp/fair.py`). Originally planned as:
   - **Primitives** for working through it together: move items between inventories (a type and amount, or "all"), pull everything of a type out of a block, and look up where an item is.
   - **Transfer rules:** moves go through conveyors like the terminal (`IMyInventory.CanTransferItemTo` / `TransferItemTo` with connection checks). Moves to or from the suit need the player seated on that construct or standing near the block.
   - **Organizer** (bundled): a tag on the container name or CustomData (`[Ore]`, `[Ingots]`, `[Components]`, `[Ammo]`, `[Tools]`, `[Ice]`, ...) sends those items there. Untagged items follow defaults: ore and stone to refineries or ore cargo, ice to H2/O2 generators, uranium to reactors, ammo to the guns that use it, ingots to assemblers or cargo, the rest to cargo. It always shows a plan first and moves only on approval.
2. ✅ **Production**:
   - `recipes.list`, plus `production.queue` / `production.remove` / `production.clear`.
   - Tools: `recipe` (world-adjusted cost, time, which blocks, ship shortfall), `queue_production` (picks the assembler), `remove_from_queue` and `clear_production_queue`.
   - Verified live.
   - Not done: reordering the queue (`MoveQueueItemRequest`) and refinery ore priority.
3. ✅ **Build planning**: projector progress (remaining and buildable blocks, missing components) and component costs from block definitions. "What do I need to finish this, and what am I short of?"
   - Methods: `blockdefs.find`, `projector.get`, `buildplanner.get` (host).
   - Tools:
     - `block_cost`: components, then ingots at world efficiency, then ore at Refinery yield, each against what the ship has.
     - `build_planner`: show, withdraw to the suit through the conveyor rules, or queue what's missing.
     - `projector_status`: progress, needs and shortfall, with optional queueing.
   - Verified live: block costs, the build planner (show, and withdraw of 195 components to the suit), and a projector on the Red Platform.
4. ✅ **Screens**: write text, and later sprites, on LCDs and cockpit screens. Status boards, and eventually the AI's own face.
   - Done: `screens.list` / `screen.write` / `screen.mode`, and the tools `list_screens`, `write_screen` (by screen name, with styling; reports a replaced script) and `set_screen_mode`.
   - Verified live on the Control Seat's spare screen and photographed with `screenshot(hide_hud=True)`.
   - Later: sprites (`DrawFrame`) for drawn status boards or a face.
   - Later: the "MCP Display" app (M11).
   - The tools are dynamic: `set_screen_mode` checks the screen's own app list (`GetScripts()`) at call time, so modded apps work. They work on any block screen (`IMyTextSurfaceProvider`), modded blocks included.
Remaining items, reordered by value for effort, quick wins first and docking last because it needs new flight control:

5. ✅ **Highlights** (done: `highlight_blocks` by name, type, group or damaged; `clear_highlights`; verified live, and visible in offscreen renders too): make blocks glow (the scenario highlight system) or draw markers. "Show me the damaged blocks."
6. ✅ **Suit**: helmet, jetpack, dampeners, lights (`suit_controls`, verified live).
7. ✅ **Safety net** (`save_world`: a normal save, with the game's own Backup folder as the restore point): save the world before risky operations, such as long autopilot runs or docking.
8. ✅ **Environment** (`environment`: gravity, nearest planet, altitude, air, sun height, weather, nearby bodies; verified live in deep space, planet output tested with fakes): nearby planets and moons (gravity, atmosphere, altitude), asteroids, weather, day/night.
9. ✅ **Forecasts** (`forecast`: batteries from in/out power, uranium at 1 kg/MWh, gas and suit from two samples; verified live): battery, hydrogen, oxygen and uranium time-to-empty or full, from two samples or a short history.
10. ✅ **Combat**: what turrets are targeting and their ammo; change target settings, aim turrets (leading moving targets), fire (opt-in `allowWeapons`), switch magazines (interior turrets, flare launchers); lidar-style camera tracking that can steer turrets; damage report. Later: AI Offensive/Defensive combat blocks (with flight paths and maneuvers). The cockpit target lock is shown (`weapons`, while someone pilots); setting/releasing it is later: `SetLockedTarget` is ignored and the locking component's own calls locked the wrong entity, needs a proper look at how the lock key drives it. The lock component lives on the pilot's character. Also later: the lock *candidate* under the crosshair (probably `MyTargetFocusComponent`; the seat's `RaycasterHitBlock` is the build ray, not this).
10b. ✅ **Radio**: what the ship or suit hears (antennas, beacons, laser antennas, relays included; speeds from two samples), the ship's own antennas and beacons (radius, broadcasting, HUD text), laser antenna links (aims both ends when both are ours; can turn the ship so the hull doesn't block it). Feeds combat: `contacts` (radio + turret targets + sensors), and signal names work as targets for `aim_turrets` and `track_target`. Later: radio *data* (IGC messages) through a relay programmable block. Known gap: `los.check` (physics + voxel raycast) said the line to one blocked laser was clear, so the game's own laser line-of-sight test sees something ours doesn't; trying another laser worked.
10c. ✅ **Toolbars and block groups**: read and edit a ship's toolbar (cockpit/remote control/button panel slots: which block or group, which action, per page) and the player's own toolbar; create, rename, and change block groups. Lets the assistant set up a ship's controls for the player ("put the drills and the ore-detector toggle on page 2"). Toolbars are game internals (`MyToolbar`, `MyToolbarItemTerminalBlock/Group`), so host side; groups are ModAPI-reachable.
11. ✅ **Ore detectors**: what the ship's ore detectors currently see (the HUD ore markers), as summaries or GPS points. No reading the map's voxel data directly, which would be cheating.
12. ✅ **Docking** (connectors; landing on landing gear is next):
    - Read connector, merge block and landing gear status.
    - Dock a ship myself: gyro overrides hold our connector facing theirs; thruster overrides fly it along their axis (position -> velocity -> force loop over RPC); connect at "Connectable". The RC autopilot couldn't do it: it always turns its leading face toward the travel direction, so sideways corrections swing the ship.
    - Next: land on landing gear (gear face square to a surface), and the big Red Ship onto the base (same pilot, gentler limits).

13. ✅ **Ship HUD** (`ship_hud`): one ship's systems at a glance for agents, ship-only (docked ships separate), subgrids with their rotor/hinge/piston joint and state, and warnings (thrusters off, dampeners off, recharge-only batteries, no fuel, overrides active...). Later: deeper subgrid tools (move a rotor/hinge/piston to an angle or extension, list blocks per subgrid).

14. **Landing on gear + surface measuring** (in progress):
    - ✅ `surface_map`: height map of a grid face in one game-side scan (`surface.scan`, ~4000 rays in 0.05 s), with hazard letters (T thruster, X weapon, D/G/W tools, H doors, M joints).
    - ✅ `land` (first version): picks the gear group, finds a spot where the ship's footprint fits (flat, level for the gear, hazard-free + 2 m, 60% backed), transit height over the route, slow final descent, stall stop, lock. One clean landing (both plates locked) via the script that became it.
    - ✅ Touchdown: the closed loop asks for ~1-2% thrust in the last centimeters, and the 36 t Red Fighter didn't move on 2% (the game showed the thrust, the ship stayed put) but did on 5%. So it hovered a few cm short, never reaching ReadyToLock. `ConnectorPilot.settle()` now presses the last bit in 0.25 s pulses at 5% (gyros at zero spin hold the attitude, brakes above 0.2 m/s); verified by hand: 6 cm -> under 1 cm -> ReadyToLock -> locked. Not yet run inside `land` itself.
    - ✅ `dock` from a gear-locked rest: unlocks, lifts 6 m straight along the target connector's axis, then approaches (verified: Red Fighter deck -> Connector 3). `land` starts its "what's below" ray 1 m behind the gear, so a surface it already rests on counts.
    - ✅ `los.check` distances are to the millimeter (were rounded to 0.1 m, which hid a 0.24 m gap).
    - **Spot search is too strict** (next). Retest on the Red Ship: no spot within 40 m although the deck has a level band ~13 m wide. Of 1473 deck cells: 978 failed "other plate at the same height (±0.15 m)", 459 "level 1 m around each gear", 28 "something under the footprint taller than the gear +0.3 m", 8 "backed". Fixes:
      - **Belly profile instead of a box.** The footprint is the ship's bounding box (11 x 14 m for the fighter), so any bump anywhere under it rules the spot out. Scan the ship's own underside (upward rays from below, ignoring the target) into a height map relative to the gear face, dilate it by a cell, and accept a spot when deck height < gear height + belly clearance - 0.3 m at every cell. The real fighter belly sits 0.2-0.5 m above the plates almost everywhere.
      - **Gear-sized level check.** Level is checked over ±1 m neighbours on a 1 m grid; a small-grid magnetic plate is ~0.5 m. Check the gear's own footprint (finer step, e.g. 0.25 m sub-scan around candidates) instead.
      - **Other gear between cells.** The other plate sits at a fractional offset (0.64, 1.36) that `at()` rounds onto a neighbouring cell; sample it at the exact point (fine sub-scan) rather than the rounded cell.
      - **Two-stage search:** coarse 1 m scan to shortlist, then a fine scan (0.25 m) around the best few, so token and ray cost stay low.
      - Report the reason counts when nothing fits (as above), so the agent can pick near= or a bigger search sensibly.
      - Optional: allow landing with a subset of gear (one plate locked holds a fighter fine), and tilt tolerance (the plate face was 2.6 deg off the deck normal and still locked).
    - Misread to note: the "overhang" was not real (the fighter was a few cm above open deck). The ray-starts-inside-hull blind spot is still real in principle: upward rays to find ceilings, reject spots with anything overhead lower than ship height + margin.
    - Also: auto-orient the ship to a chosen face first (today it must already face the surface), and recompute the footprint after turning.
    - Later: hazard checks for weapons' and thrusters' *directions* (a thruster blasting across a spot, not just under it), landing on planets (voxel surfaces).

### M8: Fair play

The rule: everything the assistant can do is useful in plain sandbox mode, and fair mode limits it to what an in-game AI could know and reach.

- **Knowledge sources:** every tool declares where its information comes from.
  - `own`: the construct's own blocks.
  - `sensed`: sensors, cameras, turrets, ore detectors, antennas or radio broadcasts.
  - `omniscient`: the world itself, such as grid listing or GPS.
- **Links:** every action declares the link it needs, for example an antenna in range, or being on the same construct.
- **Fair mode:** filters the tools to these rules.
  - "Grids nearby" comes only from antenna broadcasts, cameras, sensors and turrets, with no magic 50 km scan.
  - Remote control needs an antenna link.
  - Optionally, the AI core lives in a specific block (see "In-world presence" under Later).
- **Built in M7:** the tags only.

### M10: Companion

Goal: chat with the assistant from inside the game, privately, and have it speak up on its own when something needs attention.

- **Private chat channel.** The host hooks the game's chat-entered event (`MyAPIGateway.Utilities.MessageEntered`, verify the current signature) and catches messages with a prefix (configurable, e.g. `/ai`). It sets `sendToOthers = false` so they never reach other players, and queues them for the client. Replies use `notify` with `kind: chat`, which is shown only locally. The host must unsubscribe in `Stop()` so hot reload stays safe (an exception to the "no game events" rule, handled explicitly).
- **Event stream.** Server→client notifications (`{"method":"event"}`, reserved in protocol.md) for chat messages and alerts: low O₂/H₂/energy, damage, power running out, docking/undocking, a production block stalling, PB errors. Thresholds go in config. The host polls cheaply on the game thread (e.g. once per second), not per frame.
- **In-game triggers.** Let the ship itself call the AI, so players can wire it up with vanilla blocks:
  - **Event Controller:** `IMyEventControllerBlock.ActionTriggered` (ModAPI) fires when its condition is met (tank below 10%, a door opens, a sensor trips...). Event controllers named with a tag (e.g. `[AI] Low hydrogen`) become events: "event controller 'Low hydrogen' fired". The player sets the condition in the normal terminal.
  - **Sensor blocks:** `IMySensorBlock.StateChanged` on tagged sensors ("someone/something entered the docking zone").
  - **Button panels and timers:** they can't send messages directly, but they can trigger a tagged event controller, or run a PB that writes a request we read.
  - **Broadcast Controller:** it posts chat lines and can target the owner only (`BroadcastTarget.Owner`). Verify how those messages arrive client-side (whether `MessageRecieved` sees them or a patch is needed); if they're catchable, a broadcast message starting with `/ai` is handled like a player's `/ai` chat. Note that "Faction" and "Everyone" targets are visible to others.
  - Same hot-reload rule as chat: subscribe in `Start()`, unsubscribe in `Stop()`, and re-scan tagged blocks periodically (blocks get built and removed).
- **Background runner.** MCP tools only run while an MCP client is in a turn, so an in-game message needs something that starts a turn. A small runner process (in `server/`) listens for events and starts a Claude turn for each chat message or alert, with the same tools. Options: Claude Code headless (`claude -p` with this MCP server, runs under the user's plan), or the Claude Agent SDK (needs an API key, paid per use). Pick one when we get here. Keep a short rolling conversation so follow-ups make sense.
- **Persona.** A configurable name and voice for the AI ("ship AI" vs "suit AI" depending on whether the player is seated), and rules for when to stay quiet.
- **Optional voice.** Local speech-to-text for push-to-talk input and text-to-speech for replies.

### M11: In-game UI and displays

Background: screen "apps" are text surface scripts (TSS). Each is a C# class tagged `[MyTextSurfaceScript("TSS_Id", "Name")]`, deriving `MyTSSCommon`. `MyTextSurfaceScriptFactory.LoadScripts` collects them at world load from the game, mod scripts and client plugin assemblies (`MyPlugins.UserAssemblies`). `RegisterFromAssembly` is public, so the plugin can add its own. Apps run on each client (one instance per screen, `Run()` every 10/100 ticks, drawing sprites). The screen only stores the app id, which syncs.

- **MCP Display app** (first): one built-in app, e.g. `TSS_MCPDisplay`, that draws whatever the assistant sends as data (text, shapes, bars, gauges, images, simple animation), no PB needed. Uses: status boards, graphs, and the AI's face/avatar for the companion.
  - The drawing is described in JSON, pushed per screen, and kept in the host.
  - Registered from the host, re-registered on hot reload.
  - Multiplayer: other players see it only if they have the app. To fix that, ship the same class in a small companion workshop mod.
- **The assistant's own UI**: the plugin has full access to the game's GUI (`MyGuiSandbox`, `MyGuiScreenBase`, HUD drawing).
  - Possible pieces: a chat/console window for talking to the AI (pairs with M10), confirmation prompts ("Organize the cargo? Yes/No"), HUD overlays (text, markers, bars), and highlights that point at things.
  - ✅ **HUD overlay** (pulled forward from M11, verified in game): `hud_draw` (text, boxes, bars, world markers pinned to a block/grid/position; upsert by id; `bind` to a read-only method keeps it live; `ttl`), `hud_ship_panel` (live H2/O2/battery/uranium/cargo/speed panel, bars go red when low), `hud_remove`, `hud_clear`, `hud_list`. A focus-less `MyGuiScreenBase` above the HUD, closed on host stop.
    - Next: lines/arrows (rotated sprites), icons from the HUD atlas, an off-screen arrow for markers behind you, progress/countdown helpers, and a shared layout so panels don't overlap.
    - Fair play (M8): markers on things the player can't sense should be filtered like `list_grids`.
  - Hot reload: open screens must be closed in `Stop()`.
  - Our own HUD drawing only. Text HUD API (workshop 758597413) was considered and dropped.
- **Authoring new apps at runtime** (opt-in, off by default): an endpoint takes C# for a screen app, compiles it with the game's own script compiler against the mod whitelist (as for PBs and mods), registers it under a versioned id, and reports compiler errors like `write_pb_script`. It runs code inside the game, so it's gated in config and local only. MCP Display should cover most needs first.

## Testing

- **Python:** `tests/fake_bridge.py` speaks the protocol with canned data, and `pytest` covers tools, resolution and formatting without the game.
- **Game side:** a per-milestone manual checklist in README, run in the dev world. `debug.selftest` for quick smoke checks after a reload.

## Later

- Workshop-mod host (file-polling transport over Core) for a no-Pulsar install.
- Torch/dedicated-server host with per-player auth.
- `debug.eval`: compile and run a C# snippet in-game via the Roslyn DLLs in Bin64. Off by default. Full code execution, local dev only. Shares its compiler plumbing with M11's runtime app authoring.
- **Creative/admin tools** (the debug menu, exposed): teleport the player or a ship (to a planet, a GPS, a grid), spawn items or blueprints, set weather (`IMyWeatherEffects.SetWeather`) and time of day, and so on. Allowed only when the world is creative or the player has admin creative tools on. Tagged "omniscient"/cheat in fair play.
- **Flight paths and maneuvers** (after docking and basic flight are solid): a modular route system.
  - Routes: named, saved lists of waypoints (GPS or relative offsets), each with speed, orientation and actions on arrival (wait, dock, run a timer). Driven by the RC autopilot or our own steering.
  - Maneuvers: reusable building blocks such as orbit a point, strafe run, hold station, approach and dock, patrol loop. Routes are built from them.
  - Formations: ships hold offsets relative to a leader (wedge, line, escort).
  - Definitions: bundle a few standard ones, and let the agent define, save and reuse its own (JSON, stored like PB backups). Built-in ones can come later.
- AI blocks: "follow me" (AI Basic Task mission) and route recording/replay (AI Recorder); writing AI Flight waypoints through game internals.
- Production: reorder queue entries (`MoveQueueItemRequest`), refinery ore priority.
- **In-world presence (optional):** the assistant reaches ships the way an in-game AI would, not by "god mode". Ideas: require an antenna/broadcast link from the player (suit radio on, ship antenna in range, `requireAntennaLink`); the "AI core" lives in a specific block (a tagged PB or the Automaton block), so damaging it cuts the assistant off; limited range or bandwidth; ships without a link stay unreachable. Off by default, a fun roleplay mode.
