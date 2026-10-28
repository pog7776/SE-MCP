# Space Engineers MCP — Implementation Plan

Let an AI assistant (over MCP) see and control the player's ships, stations and suit in **Space Engineers 1**, and write, push and run Programmable Block (PB) scripts. It's a co-pilot: it answers "how's my ship?", flips blocks, writes scripts, and sees the screen.

> **Rule for implementers:** Many SE member names in this doc are from memory. Before relying on one, check it in the decompiled source (`ref/decompiled/`, see M0) and fix the code to match. Don't guess.

## Environment (user's machine)

| Item | Value |
|---|---|
| Game | SE 1.210.014, `S:\SteamLibrary\steamapps\common\SpaceEngineers\Bin64` |
| Runtime | .NET Framework 4.8 (game), x64 |
| .NET SDKs | 5–9 installed (SDK-style csproj targeting `net48` works) |
| Python | 3.11.9 at `%LOCALAPPDATA%\Programs\Python\Python311` (the `python` on PATH is the Store stub) |
| JSON in Bin64 | `System.Text.Json.dll` is shipped. **Newtonsoft.Json is not.** |
| To install | Pulsar, `uv`, `ilspycmd` (`dotnet tool install -g ilspycmd`) |

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
    se_mcp/ __main__.py bridge.py tools.py format.py resolve.py screenshot.py primer.md
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

- `uv` project, Python 3.11+, official `mcp` SDK (`FastMCP`), stdio transport. Check the current SDK API before writing tools.
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

0. **Groundwork**: `git init` + `.gitignore`. Install `ilspycmd`, decompile the SE DLLs into `ref/decompiled/`. Install `uv` and Pulsar. Confirm Pulsar's dev-folder workflow with a hello-world plugin that logs to the SE log.
1. **Skeleton**: Loader + Host + Core, TCP + auth, `ping`, `debug.reload`. Python `se_status`. Verify end-to-end from Claude Code and from `npx @modelcontextprotocol/inspector`.
2. **See the world**: `session.info`, `player.context`, `grids.list`, `grid.get`, `blocks.list`, `block.get`, `groups.list`, `character.status`, `inventory.get`, and screenshots (window capture first, in-engine next). Matching tools, name resolution, the current-grid default.
3. **Control**: `block.action`, `block.set_property`, set name/custom data, group actions, `notify`, `readOnly`, access + range checks, `production.get`.
4. **Programmable blocks**: get/set code, recompile, run, output, the primer. Loop test: push an Echo script, read the output, break it, read the compile error, fix it.
5. **Autopilot + GPS**: `rc.fly_to` / `rc.stop`, GPS list/add/remove.
6. **Polish**: README, protocol.md, fake-bridge tests, error messages, `debug.selftest`.

## Testing

- **Python:** `tests/fake_bridge.py` speaks the protocol with canned data, and `pytest` covers tools, resolution and formatting without the game.
- **Game side:** a per-milestone manual checklist in README, run in the dev world. `debug.selftest` for quick smoke checks after a reload.

## Later

- Event stream (damage, low O₂/H₂/power, docking, PB echo changes) as notifications.
- Workshop-mod host (file-polling transport over Core) for a no-Pulsar install.
- Torch/dedicated-server host with per-player auth.
- Camera/sensor raycasts, connector/merge docking helpers, inventory transfer, assembler queueing.
- `debug.eval`: compile and run a C# snippet in-game via the Roslyn DLLs in Bin64. Off by default. Full code execution, local dev only.
- Optional antenna-link requirement for remote access (`requireAntennaLink`).
