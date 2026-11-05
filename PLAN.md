# Space Engineers MCP — Implementation Plan

Let an AI assistant (over MCP) see and control the player's ships, stations and suit in **Space Engineers 1**, and write, push and run Programmable Block (PB) scripts. It's a co-pilot: it answers "how's my ship?", flips blocks, writes scripts, and sees the screen.

> **Rule for implementers:** Many SE member names in this doc are from memory. Before relying on one, check it in the decompiled source (`ref/decompiled/`, see M0) and fix the code to match. Don't guess.

## Environment (user's machine)

| Item | Value |
|---|---|
| Game | SE 1.210.014, `<Steam library>\steamapps\common\SpaceEngineers\Bin64` (set per machine in `Directory.Build.local.props`) |
| Runtime | .NET Framework 4.8 via Pulsar `Legacy.exe`, or .NET 10 via Pulsar `Interim.exe`. We build `net48` + `net9.0` for both |
| .NET SDKs | 5–9 installed (no .NET 10 SDK. The `net9.0` build runs on the .NET 10 runtime) |
| Python | 3.11+ (uv finds or installs it). On Windows the `python` on PATH may be the Store stub; use `uv run python` |
| JSON in Bin64 | `System.Text.Json.dll` 8.0 (+ deps) is shipped. **Newtonsoft.Json is not.** |
| Pulsar | v2.4.3 at `%AppData%\Pulsar`. Local DLLs go in `<Legacy\|Interim>\Local`, enabled via Plugins → **+** |
| Tools | `uv` 0.13, `ilspycmd` 9.1.0.7988 (latest needs .NET 10 SDK) |
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
    "args": ["--directory", "<path to repo>/server", "run", "se-mcp"] } } }
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
9. **Polish**: README, protocol.md, fake-bridge tests, error messages, a `selftest` tool, and the first real run of the Interim (.NET 10) build. The end-to-end MCP stdio run already passes for every read tool.
   - ✅ Public release prep (2026-11-05): MIT licence, portable paths, install docs (README) and developer docs (DEVELOPING.md).
   - ✅ Cold start (2026-11-05): the host failed to load at game launch, because `ChatInbox` subscribed through `MyAPIGateway.Utilities`, which is null until a world loads. It worked before only because hot reloads happen in a world. It now subscribes to `MyAPIUtilities.Static`, which always exists. Anything else the host touches in `Start()` must work from the main menu too.
   - First Interim (.NET 10) run (2026-11-05, .NET 10.0.14, Pulsar Interim): cold start OK (the host loaded at game launch with chat, the Comms tab and the Harmony camera patch). Reads, offscreen camera renders, `ship_photo`, the window `screenshot` and the HUD overlay all work. Pending: the player checking the HUD and `/ai` + Comms in game; writes, flight and docking haven't been run on Interim yet.
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
13b. ✅ **Ship notes** (`ship_notes`, `write_ship_notes`): a per-ship AGENTS.md of scripts and quirks (e.g. the Red Ship's cruise script, which disables reverse thrusters so autopilot can't brake). Files in `%AppData%\SpaceEngineers\SEMcp\ships\`, matched by name, old versions kept. `se_status`/`ship_hud` flag unread notes; flight tools stop once until they're read.

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

Lessons from docking the 1,100 t Blue Ship (no RC) by its right connector to the Red Platform's Boom Dock (2026-10-30). It worked first time, but it took a scratch script for the geometry and ~1,000 hand-rolled raycasts for clearance:

15. **Spatial sensing and distances** (next; the plugin should be able to sense shapes, not just lines). This is a different thing from M7 item 8 (planets, weather):
    - ✅ `docking_status` names each connector's and gear's facing and place on the ship ("faces right, 20 m right, 6 m down, 18 m back of center"), as seen from the cockpit, or from the grid axes on a station. "The right connector" is now one call.
    - ✅ `los.check`/`surface.scan` `ignoreId` looks through everything that moves with the ship: subparts (door leaves, turret barrels) and ships gear-locked onto it. Connector-docked ships still count, so ignoring a docked ship doesn't hide the station.
    - **Swept-shape clearance** (`path.sweep`, game side): sweep the ship's real block cells (or a Havok shape cast of its collision shape) along a path. Return the first contact per leg, with the entity, the block, the distance along the leg and the gap left. One call instead of thousands of rays. The hand-rolled version had a bug the first time (a probe ray started inside the station), which is why this belongs in the plugin. `dock`, `fly_to` and `land` call it first and refuse with "blocked by X at Y m"; it's also a tool of its own.
    - **Distances done right** (a focus): every distance a tool reports says what it measures from and to.
      - `list_grids` today measures from the player to the nearest point of the ship's bounding box: the Blue Ship read "20 m away" while its connector was ~100 m off. Report surface-to-surface (closest blocks, or a shape cast) and center-to-center, labelled.
      - Ship-to-ship gap: the closest approach between two ships' actual blocks, not bounding boxes. Also block-to-block distance.
      - A shared helper and unit tests with known geometry, so every tool uses the same definitions.
    - **Before/after damage diff** on `dock`, `land` and `fly_to`: snapshot integrity and deformation before moving and report only what changed. After the Blue Ship docked, 6 deformed blocks couldn't be told apart from old damage. Also make `ship_status` and `damage_report` count the same way (one counted integrity only, the other deformed blocks too).
    - Small: `ship_photo` comes out black when the side is in shadow; raise exposure or add a fill light.

16. **Pilot v2: roll, speed and the RC**:
    - **Roll control:** `ConnectorPilot` aligns only the connector's facing and leaves roll wherever it is. Add a target up (default: match the target grid's up, i.e. upright to the station; or "keep", or an explicit side). Also give a short summary before it moves: the rotation, each leg's length and the clearance result.
    - **Speed:** the loop runs over RPC a few times a second, so its gains and limits are conservative and the user finds it slow. Move the closed loop into the plugin, running every tick, with a trapezoidal velocity profile (full speed in transit, braking planned from measured thrust per axis). Python sends the goal and polls the status.
    - **The RC:** the RC autopilot is fast in transit and has the game's collision avoidance, but it always turns its leading face toward the travel direction. So it can't hold a sideways connector attitude, and many ships (like the Blue Ship) don't have one. Plan: our pilot (v2) is the main pilot. When the ship has an RC, it's an option for long transit legs to the approach point, and our pilot always does the final approach.

18. **Ship control: rotation first** (2026-10-30; the companion couldn't "rotate the ship for the forward cannons", since only `align_to_sun` could turn a ship):
    - ✅ `rotate_ship`: yaw/pitch/roll by degrees as the pilot sees it. Full-attitude control (`Steering.orient`, quaternion error, so 180 deg turns and pure rolls work), holding the other axes.
    - ✅ `point_ship`: point a side (forward/back/up/down/left/right) or the fixed guns ("guns" or a weapon's name, aimed from where they're mounted) at a ship, station, radio signal, GPS or coordinates. Roll left as is.
    - ✅ `dock` parks thrusters off once connected (`park_thrusters`, default on): leaving them on fights the carrier, as the Red Fighter showed.
    - Verified live 2026-10-30: `point_ship` turned the Red Ship (80 m swing radius, two docked ships aboard) 124 deg at 1 rpm in 27 s to put its fixed Artillery on the Whale, ending 0.5 deg off.
    - ✅ `target_block` (2026-10-30; the companion couldn't aim at "the Whale's remaining hydrogen tank", having no block positions): `block.get` now returns `position`/`forward`/`up`/`gridVelocity`. Aims turrets (leading a moving target) and/or fixed guns (turning the ship) at one block, says what each shot meets first (armour in front is normal; another ship in the way holds that weapon's fire), `fire` shoots once, `miss_by_m` aims beside it for a warning shot. Verified live: 7 Red Ship turrets on the Whale's tank, "6 weapons' shots meet Half Armor Block first, 6-7 m before" it.
    - ✅ Target acquisition (2026-10-30; a warning shot went out before the turret had swung round): `target_block` and `aim_turrets` wait until each barrel points at the target (`aimDirection` within 1.5 deg, 4 deg when leading), report "On target ... (within N s)" or "couldn't get on target (N deg off)", and `target_block` fires only turrets on target. Live: 5 Red Ship turrets on target in 0.4 s; Assault Cannon Turret - Right and Gatling Turret 2 stop 14-28 deg short (likely their arc).
    - ✅ Block transforms (2026-10-30; asked to "aim 10 m forward of the Whale pilot seat", the AI had no block axes): `block.get` adds `right`, `cell`, `cellSize`; `inspect_block` prints the world position, axes, grid cell and the block's place on its ship. `target_block` takes `offset_m` [right, up, forward] in the block's or its ship's axes, so offsets are computed in code rather than by the model. Live: 5 turrets on the point 10 m in front of the Whale's pilot seat.
    - **Moving targets** (2026-11-05 review; only tested at rest so far). Turret ballistics are the game's: `turret.aim` with a velocity calls `TrackTarget(pos, vel)`, and the turret's targeting system solves the intercept from the projectile speed and the relative velocity (as in `MyWeaponPrediction`). Our part is what we feed it, and that has gaps:
        - `target_block` sends one snapshot of the block's position and its ship's velocity. A target that accelerates or turns drifts off. Re-send it every ~0.5 s while acquiring and firing, or hand the turret the entity (`TrackTarget(entity)`, which the game follows itself) when there's no offset.
        - The snapshot uses the grid's linear velocity, not the velocity of the block's own point, so a block far from the centre of a spinning ship is led wrongly. Add angular velocity × offset.
        - Fixed guns get no lead at all: `point_ship` aims at where the block is now. Solve the same intercept in Python (projectile speed from the ammo definition, relative velocity) and keep re-pointing while the target moves (needs `hold_heading`).
        - `track_target` keeps the target with camera raycasts and re-aims turrets each interval, from the last fix.
        - Test live against a moving drone at a few speeds, and report the miss distance.
    - Next: `fire_weapons` bursts that respect reloads ("fire 4 rounds" from a 2-round turret), and target-lock on a block via the game's own lock.
    - Next: `level_ship` (up against gravity, for planets; needs gravity direction in `grid.motion`), a roll target for `point_ship` ("keep upright to the station"), `hold_heading`/track a moving target (re-point every second until told to stop, plugin-side loop from item 16), and a clearance sweep before turning (item 15's `path.sweep`, using the ship's swing radius).
    - **Piloting quality** (2026-10-30; asked whether the AI needs easing curves or PID access): no raw gains for the model, since it acts every few seconds and can't tune a 7 Hz loop. The AI states goals (and later a style: careful/normal/aggressive); controllers stay in code and report how the turn went. Steps:
        1. ✅ Braking curve: the turn loop measures how fast the ship picks up spin as it starts turning (rad/s², braking is as quick) and never spins faster than it can stop from in time, allowing for the loop's own delay: speed = min(gain x angle, max, a(sqrt(d^2 + 2 angle/a) - d)). Heavy ships with weak gyros stop overshooting; nimble ones get a quicker finish. Results say the measured rate and any overshoot.
        2. Next: the same for thrust (`fly_to`/`strafe`/docking): braking distance from thrust / mass per direction.
        3. Next: lead moving targets (feed-forward of the target's angular rate) for `track_target`, `hold_heading` and fixed guns on a moving ship.
        4. Next: a plugin-side 60 Hz inner loop for hovering, `hold_heading` and combat, with Python sending only set-points (the Python loop is ~150 ms plus several RPCs per gyro per step).
    - Next, beyond rotation: `stop_ship` (kill all motion), `strafe`/`move_ship` by meters in the pilot's frame, `set_speed` for cruise, and a `ship_controls` summary (what's on/off, overrides, autopilot) so the AI can undo what it did.

19. **Thrust model and planned maneuvers** (2026-11-05; asked whether the AI can plan burns without constant polling). Builds on item 16 (the pilot loop inside the plugin) and item 18 step 2 (the braking curve for thrust).
    - Today: `grid.motion` with `thrusters` returns each thruster's push direction and `MaxEffectiveThrust` (already scaled for atmosphere), plus the ship's mass. Only the docking pilot uses them, and its loop runs over RPC.
    - **Thrust envelope** (a read-only `ship_thrust` tool): the maximum acceleration in each direction the pilot sees, from the working thrusters' effective thrust ÷ mass, less gravity along that axis, split by kind (ion, atmospheric, hydrogen). Add burn time limited by hydrogen, from fuel use in the thruster definitions. It answers "can it lift off with this cargo?" and "how far does it take to stop from 100 m/s?".
    - **Burn planner** (Python, pure functions with tests): SE has no orbits, just Newtonian motion with a world speed cap and dampeners. So a move is: accelerate, coast, brake.
      - Plan each leg from the envelope: its time, peak speed, braking point and fuel.
      - Plan to a margin (e.g. 80% of thrust), so losing a thruster still stops the ship in time.
      - Rotations use the braking curve the turn loop already measures.
      - The plan is shown before anything moves.
    - **Maneuver executor** (inside the plugin, every tick):
      - Python sends the whole plan once, as legs with a velocity profile, an attitude and an end condition. The host flies it closed loop each frame with thrust and gyro overrides.
      - The host publishes progress, finished and aborted on the event hub, so the AI waits on one event instead of polling.
      - It aborts when something's in the way (`path.sweep`), thrust is lost, the ship drifts off track past a tolerance, or the player takes the controls.
      - Overrides are released on finish, on abort and in `HostModule.Stop()`.
    - **Closed loop, not timed open-loop burns:** mass changes with cargo, atmospheric thrust changes with altitude, gravity changes, and bumps happen, so timed burns drift. The plan sets the profile and the loop tracks it.
    - Then `fly_to`, `dock`, `land` and `strafe` move onto the executor. Later: the maneuver library (orbit a point, strafe run, hold station) from "Later → Flight paths".

20. **Suit piloting and guiding the player** (2026-11-05; asked "can you fly my suit around, help me navigate like you control a ship?"). Two levels, guidance first:
    - ✅ **Guide me** (2026-11-05): `guide_to(to)` takes a GPS, coordinates, a ship or a block, and `stop_guide` removes it.
      - It plans waypoints with `los.check`: detour points beside the obstacle on 8 sides, from 12 to 100 m out. It prefers one turn with both legs clear and recurses up to 2 turns, using at most 120 checks (`guide.py`, tested with sphere geometry).
      - A ship target stops at the hull facing the player. For a block target, hits within 5 m of the block count as arrived.
      - A new `route` overlay element advances waypoints in the game every frame, removes itself at the end and raises an `arrived` event for the companion. Nothing polls.
      - Verified live on Interim: routed to the Red Ship's Corner Medical Room with 1 detour point in 0.35 s. The marker itself still needs the player's eyes, and arrival hasn't been tested yet.
      - Verified live (2026-11-05): the `arrived` event fired at the medical room and the guide cleared itself. Piloting: the route looks through the player's own ship (`ignoreId`), spaces detours by its radius, and checks 4 edge lines `radius` out. `arrive_m` sets "close enough" (e.g. 200 m for a big ship). Unit-tested; not yet flown live.
      - ✅ "Go there and I'll do X": `guide_to(note=...)` carries the note in the `arrived` event, and the runner wakes the in-game AI with it (`event_message`). So "go to the hangar and I'll open the door" needs no polling. Unit-tested; not yet run with the runner live.
      - What the player expected (2026-11-05): **a path drawn in the world**, like a line on the floor through the doors, not a pin on the HUD. Today it's HUD pins with faint screen lines between waypoints, little more than `hud_draw` markers could do. Plan:
        1. **A real path** from the game's avoidance pathfinder (below): many points through corridors and doors, not 1-2 detour points.
        2. **Draw it in 3D**: `MyTransparentGeometry.AddLineBillboard` / `AddPointBillboard` each frame from the host draws glowing segments and chevrons in the scene. They're depth-tested, so walls hide them, and they move with the ship (draw in the grid's frame). Fade the segments already passed, and pulse the next door.
        3. Keep the HUD pin as the "where next" label, and the edge arrow for when it's behind you.
      - Next:
        - Interiors: a single ray can't find a way through corridors and doors, so a block deep inside a ship gets "find a door". Use the game's pathfinders (below).
        - Clearance for the suit: a ray can pass a gap the suit can't. Check a few parallel rays, or use `path.sweep` with a 1 m sphere.
        - Re-plan when the player strays far off the route.
        - "The nearest cargo with ice" as a target.
        - Optional spoken-style hints in chat ("turn left, 20 m").
    - **The game's own pathfinders** (looked at 2026-11-05, to use for guidance and suit flight):
      - **Autopilot avoidance** (`IMyPathfindingSessionComponent.FindAvoidPath(obstacleEntity, start, end, radius, out state)`, ModAPI; reach it with `MyServiceManager.Instance.GetService<IMyPathfindingSessionComponent>()`, as `MyPathFindingAvoidance` does). This is what the AI blocks use.
        - It builds an octree graph of the free space in and around a grid on first use. Its door cache marks doors passable or not. While building, it returns `OctreeGenerating`, so try again.
        - It takes a radius, so a suit-sized path (~1 m) through a station's interior and doors may come straight out of it.
        - ✅ `path.find` (host, 2026-11-05). Live: **around** a grid it works well. It gave an 18-point route round the Red Ship (72 m radius) in one call after its map built, and `guide_to` now uses it whenever a ship or station is in the way (the label says so), falling back to the line-of-sight detours. **Into** a grid it doesn't: the Helm gave `PathNotFound` from outside (closed doors? enclosed rooms?).
        - Next for interiors: test with a door open, and from a start point inside the ship; read how the octree marks inside cells and doors (`MyOctreeGenerator`, `DoorCache`). Otherwise build our own interior graph from the grid's cells: empty cells, passages and door blocks, with A* (we know every block).
        - Seated at a station console counts as "controlling" the station; `guide_to` now ignores static grids for ship mode.
      - **Navmesh** (`MyRDPathfinding`, Recast/Detour, `MyAIComponent.Static.Pathfinding`): walking paths over planet surfaces and grids, for the NPC bots. Tiles generate in the background (`allTilesGenerated`). This one is for on-foot guidance on planets and in ships with gravity, not jetpack flight. `path.find` mode `walk` is wired up but found nothing yet. The test point was the Platform's centre, likely inside blocks. Retry floor to floor, and on a planet.
  - **Build Info compatibility** (what the player meant, 2026-11-05): its only inter-mod API (`InterModAPI`, id 514062285) is one-way, letting mods ask it to hide its extra info for their blocks, so there's no data to read from it. Check that `block.get`'s `customInfo` already includes the extra lines Build Info adds to the terminal info, so `inspect_block` gets them for free. Make sure our HUD overlay doesn't sit on top of its overlays.
    - Optional spoken-style hints in chat ("turn left, 20 m").
    - **Fly my suit** (takeover, opt-in, jetpack first):
      - Input: `IMyControllableEntity.MoveAndRotate(move, rotation, roll)` on the character (ModAPI; `MyCharacter` passes it on to the jetpack component). The game calls it with zero on any frame it wasn't called (`MyCharacter` ~line 4770), so it must be fed every tick from the plugin: the same loop inside the plugin as item 16, fed set-points from Python.
      - To verify: the order relative to the player's own input in the same frame (their zero input may overwrite ours; a Harmony patch on the input step may be needed), and that it syncs in multiplayer like player input.
      - Suit envelope: jetpack force (`MyCharacterJetpackComponent.ForceMagnitude`) ÷ the character's mass including inventory, with the hydrogen use. Then item 19's planner works for the suit too: accelerate, coast, brake, with dampeners on for the final stop.
      - The player always wins: any key or mouse input from them cancels it at once (watch `MyInput` for movement keys), and so does a low hydrogen or energy reserve, or the health dropping. The jetpack must be on. Walking (with gravity or magnetic boots) and ladders come later.
      - Uses: "take me to the Red Ship's airlock", "hold me here while I weld", "bring me back to the cockpit", and recovery if the player drifts off ("I'm out of reach, fly me back").
    - Fair play: own suit, so `own`. Guidance to things only the world knows (an unknown ship) follows M8.

17. **Renaming toolkit** (a skill set players use all the time; found 2026-10-30 when the companion tagged 74 blocks with 74 separate `rename_block` calls, each looked up by name):
    - ✅ `rename_blocks`: pick blocks by name/group/type (or the whole ship), then strip a prefix, find/replace, a template (`{name}` `{type}` `{n}` per type `{i}` overall, numbered in natural order) and add a prefix/suffix (skipped where present, so repeats are safe). `dry_run` previews. One `block.rename_many` call applies it by id. Verified live: 2 renames in 0.05 s.
    - Next: rename the ship itself (grid name, `grid.set_name`) and its subgrids ("Red Ship - Drill Arm").
    - Next: names from position ("Thruster Front Left", "Gear Rear"), using the cockpit frame `docking_status` already computes.
    - Next: a clean-up preset (strip ship tags, renumber per type, tag by ship) and an undo of the last bulk rename (keep the before names).
    - Next: make groups from names or position ("all left thrusters"), with `edit_group`.

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

#### Awareness system: one source of truth for what could be known

Instead of each tool deciding on its own what it's allowed to see, a single awareness service (host side, Core) keeps a **contact table**. Fair mode makes every tool that looks outside the ship read the table instead of the world (`MyEntities`, `list_grids`'s scan, trackers).

- **Contact:**
  - entity id, kind (grid, character, meteor, missile, floating object, ore deposit, GPS point)
  - name as known: the broadcast name, or "Unknown small grid" if only seen
  - last known position and velocity
  - relation, if known: you can't tell a ship's owner by looking, but its broadcast or a turret's targeting tells you
  - **sources** (which sensors know it, and when each last saw it)
  - first seen, last seen
  - stale: true when no source has seen it for a while, so the position is only "last known"
- **Sources** (each a small module, rate-limited and budgeted per frame like the rest of the queue):
  - **Radio:** antenna and beacon broadcasts the player's or ship's antennas receive. These are the HUD signals that `radio.signals` already reads.
    - A broadcast gives the name and exact position.
    - Grids that don't broadcast are invisible to radio.
  - **Relay network:** grids connected through the antenna network share what their own sensors know, including the base's sensors and the fighter's cameras.
  - **Laser antenna link:** the connected grid is known.
  - **Player eyes:** entities in the main camera's view, within view distance, big enough on screen (an angular-size threshold), and with line of sight (a few raycasts to the centre and corners, against grids and voxels). This is what the player could notice on screen.
  - **Ship cameras:** the same view-and-line-of-sight test from each camera block's view, within a camera range.
    - That range is a house rule: vanilla cameras only "see" while someone looks through them. Make it configurable; it could also require the camera to be on.
    - Explicit `camera_scan` raycasts add the hit entity.
  - **Sensors:** `IMySensorBlock` detected entities, within their field and filters.
  - **Turrets and AI combat blocks:** their current targets. Turret targeting also reveals the relation: a turret only targets enemies, or whatever its settings allow.
  - **Cockpit target lock:** the locked target.
  - **Ore detectors:** HUD ore markers, as `ore_scan` reads them now.
  - **Player knowledge:** the player's GPS list. It gives positions only, not what is there.
  - **Memory:** contacts outlive their sources. They're kept as "last known" with a fading confidence and dropped after a configurable time.
- **Two observers:**
  - **Player:** eyes, the suit antenna/HUD signals, GPS.
  - **AI:** the construct and network it lives on. With M10's in-world presence, that's the block the AI core sits in, plus that block's antenna network.
  - Default assistant mode uses the union, because the assistant is helping the player. A strict "ship AI" mode uses the AI side only.
- **Modes:**
  - `sandbox`: today's behaviour, omniscient.
  - `fair`: outside knowledge only from the contact table.
  - Optional per-source switches, such as no camera house rule or radio only.
  - Actions still check their link tag: remote control needs an antenna path to the ship.
- **Who uses it:**
  - `list_grids` and `contacts`: only contacts in fair mode, with "seen by Camera 3, 12 s ago".
  - `hud_track` and HUD markers on things that aren't ours: only contacts. Stale ones are drawn dimmed at the last known position.
  - Targeting and weapons: aim only at known contacts.
  - Autopilot, docking, landing and `surface_map` on other ships: only ones we know about. Raycasts against unknown grids still physically hit them, but they count as "unknown obstacle".
  - `ship_photo`/`camera_view`: they are already sensors themselves. They add what they see to the table.
  - Companion alerts (M10): "new contact", "contact closing", "lost contact".
- **Reporting:** every answer built from contacts says how it's known (radio, eyes, Camera 3, sensor, turret) and how fresh it is. The AI can then say "I saw it 40 s ago, it may have moved" instead of pretending to know.
- **Debugging:** an awareness overlay (`hud_track` style) colours each contact by source and greys stale ones. An `awareness_explain(entity)` tool says why something is or isn't known.
- **Build order:**
  1. The contact table, radio, sensors and turrets, since those are cheap and exact.
  2. Player eyes and cameras, the raycast budget, and memory/decay.
  3. Switch the omniscient tools over (`list_grids`, `ship_photo`, `hud_track`).
  4. Relay network, and AI-only observer mode.
- **Tests:** sources are plain functions over a world snapshot, so the Python fake bridge can test the tools' fair-mode answers. The game side gets a manual checklist: a hidden ship behind an asteroid stays unknown until a camera or the player's eyes catch it.

### M10: Companion

Goal: chat with the assistant from inside the game, privately, and have it speak up on its own when something needs attention.

- ✅ **Pulled forward (2026-10-30): `/ai` chat and the runner.** `ChatInbox` (host) catches `/ai ...` lines and queues them for `chat.poll` (long poll). The TCP server now takes several clients at once. `se-mcp-runner` (server/runner.py) answers each line with `claude -p` (only this server's tools, one resumed conversation, `/ai reset`), replying as "Suit AI"/"Ship AI". Still to do from the list below: the event stream and alerts, in-game triggers, voice, and a Comms-tab "Suit AI" conversation (would take over the game's ChatBot channel with a Harmony patch).
- ✅ **Companion v2 (2026-10-30; verified in game, Comms tab included):**
  1. **Soul.** The AI's personality lives in `SOUL.md` (`%AppData%\SpaceEngineers\SEMcp\runner\`), written with a default on first run. The player can edit the file, or ask in chat ("be grumpier", "call me Captain"), and the AI rewrites it with `ai_soul`/`write_ai_soul`. The runner reads it at the start of each Claude process and restarts the process (resuming the same conversation) when the file changes. The fixed rules (short plain-text replies, ask before risky actions) stay in the runner, so a soul edit can't break them.
  2. **One Claude process.** `claude -p --input-format stream-json --output-format stream-json` stays open; each message is one JSON line on stdin, and the turn ends at its `result` line. Tested: two turns in one session, the second with a tool call in about 5 s. The per-message context (suit or ship, which ship, where it came from) goes at the top of the message, since the system prompt is set at launch. A process that dies, hangs past the timeout, or sees a new soul is restarted with `--resume`.
  3. **Events and watches.** One host-side event hub that everything speaks through: `{id, type, source, at, text, data}`.
     - `events.poll` (long poll) replaces `chat.poll`. Chat lines are `chat` events.
     - **Watches** are the standard way to ask for a callback: a read-only method (the same bindable list as the HUD overlay) plus a JSON path, a test (`<`, `<=`, `>`, `>=`, `==`, `!=`, `changed`) and a value. Optional: `note` (what the AI wanted to do when it fires), `every` (s, default 2), `cooldown` (s, default 120), `once`. They're edge-triggered (fire when the test becomes true, re-arm when it's false), evaluated on the game thread within the frame budget, and kept in `watches.json` so they survive reloads.
     - Defaults on first run: suit oxygen, hydrogen and energy low, health low.
     - MCP tools: `watch`, `list_watches`, `remove_watch`, `recent_events`. So "tell me when the Red Ship's hydrogen is below 20%" becomes a watch.
     - The runner starts a turn for each event with the event text and the watch's note. The AI says something only if it's worth it.
     - Later: Event Controller / sensor / Broadcast Controller triggers (below), as more event sources.
  1b. ✅ **Soul presets** shipped in `server/src/se_mcp/souls/`: `default` (plain helpful), `suit-ai` (SAM-7, an in-universe company-issued suit AI), `crewmate` (Vesper, an in-universe AI partner who is her own person). `ai_soul_presets` lists them; `use_ai_soul` switches, backing up SOUL.md and carrying over the notes about the player. Both in-universe souls stayed in character when asked about "the game".
  5. ✅ **Status on the HUD** (2026-10-30): the runner draws a small icon and a line of text with the overlay ("SAM-7: listening", "thinking... 4s", "point ship... 12s (+1 waiting)", red "couldn't answer"). Tool names come from the stream-json `tool_use` blocks as they happen. Events are polled while a turn runs, so messages sent mid-turn are counted on the HUD and answered in order. It's redrawn with a 75 s ttl, so it disappears if the runner stops.
  6. **AI avatar (later):** a small animated face or figure for the AI on the HUD next to the status, reacting to its state (idle, thinking, talking, alarmed). The user has a "Familiar" project of their own to use as the reference; they'll share it. Likely built on the HUD overlay first (sprite frames as icons), then on the M11 MCP Display app for screens and cockpits. Each soul could bring its own look.
  4. **Comms tab.** The terminal's Comms tab has a built-in bot conversation (`ChatChannel.ChatBot`) that nothing answers in this version (`OnChatBotResponse` is never subscribed). Harmony patches on `MyTerminalChatController` rename it "Suit AI", send what the player types there to the event hub as a `chat` event with `source: comms`, and post the replies into that conversation's history. Patches are removed in `Stop()`.

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

- **Camera feed on a screen** (asked for in game 2026-10-30: "the front camera on my left cockpit screen"): a live camera view on an LCD or cockpit screen. The CameraLCD plugin in `ref/CameraLCD` does exactly this (a TSS that renders a camera into the surface); adapt its approach as a screen app, or at least push a still image to a screen.
- **MCP Display app** (first): one built-in app, e.g. `TSS_MCPDisplay`, that draws whatever the assistant sends as data (text, shapes, bars, gauges, images, simple animation), no PB needed. Uses: status boards, graphs, and the AI's face/avatar for the companion.
  - The drawing is described in JSON, pushed per screen, and kept in the host.
  - Registered from the host, re-registered on hot reload.
  - Multiplayer: other players see it only if they have the app. To fix that, ship the same class in a small companion workshop mod.
- **The assistant's own UI**: the plugin has full access to the game's GUI (`MyGuiSandbox`, `MyGuiScreenBase`, HUD drawing).
  - Possible pieces: a chat/console window for talking to the AI (pairs with M10), confirmation prompts ("Organize the cargo? Yes/No"), HUD overlays (text, markers, bars), and highlights that point at things.
  - ✅ **HUD overlay** (pulled forward from M11, verified in game): `hud_draw` (text, boxes, bars, world markers pinned to a block/grid/position; upsert by id; `bind` to a read-only method keeps it live; `ttl`), `hud_ship_panel` (live H2/O2/battery/uranium/cargo/speed panel, bars go red when low), `hud_remove`, `hud_clear`, `hud_list`. A focus-less `MyGuiScreenBase` above the HUD, closed on host stop.
    - ✅ Lines, arrows (also pinned to a block/grid/position, sliding to the screen edge when it's off screen), circles, icons (named HUD/marker textures or any texture path), corner-bracket boxes round things, edge arrows for off-screen targets, and `hud_track` (live boxes round every meteor/missile/enemy/neutral/friend/ship/player in range, `incoming` filter, {eta} label). Verified in game.
    - Next: progress/countdown helpers, block/item icons from their definitions, a shared layout so panels don't overlap, and warnings that flash.
    - Fair play (M8): `hud_track` and markers on things that aren't ours read the awareness contact table in fair mode (see M8, "Awareness system"). Today they see everything in range.
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
- **Other mods as data sources and displays** (asked 2026-11-05). Both are workshop mods, so their source ships with them in `steamapps/workshop/content/244850/<id>` (reference only, as with `ref/`).
  - **Build Info** (Digi, 514062285): its conveyor overlay is the model for drawing in the world.
    - **Compute** (`ConveyorNetworkCompute.cs`): for every conveyor-capable block it takes the port positions from its own per-definition data (from the model's conveyor dummies). It looks up the neighbour at each port's expected cell (`grid.GetCubeBlock`) and checks that the neighbour has a matching port. That gives links, which group into networks. Each network gets a colour; broken links are red, unused ports grey and isolated blocks dark red, with arrows for one-way links (sorters) and boxes on inventories.
    - Everything is stored in grid-local coordinates, and it rescans on block added/removed, merge/split and connection changes.
    - **Render** (`ConveyorNetworkRender.cs`): each frame it transforms local to world with the grid's matrix and sends lines, dots and arrows as one batch of billboards (`MyTransparentGeometry.AddBillboards`). A darker "shadow" pass is drawn under each for contrast.
    - For us: (1) the same technique for item 20's 3D guide path (grid-local points, line and arrow billboards, shadow pass). (2) A `conveyor_map` tool: which inventories can reach which, what's cut off and why ("Assembler 2 is isolated: the conveyor behind it is broken"), and draw it on demand. Our inventory code already asks the game's own conveyor pathing (`CanConvey`); this adds the port-level "why".
  - **Build Vision** (DarkHelmet, 1697184408): has a small client API over mod messages (`BvApiClient`: register by sending to mod id 1697184408 / queue 1965658453, then read accessors). It gives: menu open or closed, mode (peek / control / copy) and the target block (`IMyTerminalBlock`).
    - A plugin can use mod messages too (`MyAPIUtilities.Static.RegisterMessageHandler` / `SendModMessage`), so the host can be a client.
    - Data source: "the player has Build Vision open on Thruster 4, controlling it", so the AI can answer "what does this setting do?" or "set it like the others" about the block in hand. It could also be a watch or event source ("tell me what this block is when I open it").
    - Display: the API doesn't let other mods add to its menu. Its UI runs on Rich HUD Framework (a separate mod with its own client API), so our own panels could use RHF if the player has it, or stay on our overlay.
- **NPC crew** (asked 2026-11-05: "can you control an NPC?"). The game ships humanoid **Astronaut** bots (`Bots.sbc`: `Astronaut`, `Female_Astronaut`, model `NPC_Astronaut`, behaviour tree `AstronautBehavior` in `AIBehavior.sbc`). They're the people on economy stations.
  - Their actions include `GotoTarget` (navmesh pathfinding), `GotoAndAimTarget`, `SitInSeat`, `FindCockpitWithinDistance`, `FindTerminalBlockWithinDistance`, `PlayEmoteAtBlock`, `GreetPlayer`, `Wander` and `Teleport`.
  - Spawn one with `MyAIComponent.Static.SpawnNewBot(agentDefinition, position)`. Drive it by setting its target and memory, or through `MyAgentBot` navigation directly. The animal bots (wolves, spiders) use the same machinery.
  - Ideas: an AI crewmate who walks the ship ("go sit in the helm"), carries things, stands watch, or is the companion's body (M10's avatar in the world).
  - Unknowns: the navmesh needs gravity to walk, so zero-g crews may need jetpack steering; whether bots can hold tools or work blocks; how this behaves in multiplayer; and server-only AI (`Sync.IsServer`), which is fine for single player.
  - Big, so later.
- AI blocks: "follow me" (AI Basic Task mission) and route recording/replay (AI Recorder); writing AI Flight waypoints through game internals.
- Production: reorder queue entries (`MoveQueueItemRequest`), refinery ore priority.
- **In-world presence (optional):** the assistant reaches ships the way an in-game AI would, not by "god mode". Ideas: require an antenna/broadcast link from the player (suit radio on, ship antenna in range, `requireAntennaLink`); the "AI core" lives in a specific block (a tagged PB or the Automaton block), so damaging it cuts the assistant off; limited range or bandwidth; ships without a link stay unreachable. Off by default, a fun roleplay mode.
