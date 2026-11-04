# Developing SE-MCP

For people and coding agents changing or extending this project. Install it first ([README](README.md#install)); [PLAN.md](PLAN.md) has the design, milestones and roadmap, and [protocol.md](protocol.md) every RPC method.

## How it fits together

```
AI client ──MCP/stdio──▶ server/ (Python) ──TCP 127.0.0.1:8765, JSON-RPC + token──▶ SEMcp.Loader ─loads─▶ SEMcp.Host + SEMcp.Core
```

| Piece | Language | What it is | Updates by |
|---|---|---|---|
| `plugin/SEMcp.Loader` | C# | The Pulsar plugin. Tiny and stable: watches the host folder and (re)loads Host + Core | Game restart |
| `plugin/SEMcp.Host` | C# | TCP server, config, the game-thread queue, and anything that needs game internals (GUI, rendering, Harmony) | **Hot reload** on build |
| `core/` | C# | Game logic through the ModAPI only: one `*Service.cs` per area, registered on the `Dispatcher` | **Hot reload** on build |
| `server/` | Python | The MCP server: tools, name resolution, formatting, safety checks, and the in-game companion runner | Restart the MCP server |

- **Threads:** RPCs arrive on a background thread and run on the game thread through `GameThreadQueue`, drained in the plugin's `Update()` with a small per-frame budget. Game objects are only touched there.
- **Hot reload:** a Release build copies Host + Core to `%AppData%\SpaceEngineers\SEMcp\host\<net48|net9.0>`; the loader notices and swaps them in about 1.5 s later (`Host generation N started` in the SE log). Every build gets a fresh assembly version (see `Directory.Build.props`) so .NET Framework doesn't hand back the old Core.
  - The rule that keeps this safe: the Host must leave nothing behind in the game. Anything it registers or adds (event subscriptions, GUI screens, highlights) is undone in `HostModule.Stop()`.
- **Two runtimes:** Pulsar Legacy runs the game on .NET Framework 4.8, Pulsar Interim on .NET 10. Everything builds for `net48` and `net9.0`; write C# that works on both (no newer-BCL-only APIs). JSON is `System.Text.Json` from the game's own `Bin64` (there's no Newtonsoft).
- **Python:** `bridge.py` speaks the protocol, `resolve.py` turns names into ids ("Red Ship", "the cargo"), `tools.py` defines the MCP tools, and topic modules (`docking.py`, `surface.py`, `overlay.py`, ...) hold the logic so it can be unit-tested. `fair.py` tags every tool with where its knowledge comes from (see PLAN, M8).

## Dev loop

```bash
dotnet build SEMcp.sln -c Release      # with the game running: hot-reloads Host + Core
cd server && uv run pytest             # Python tests against a fake game, no SE needed
```

- Python changes need the MCP server restarted (in Claude Code: a new session, or `/mcp` → reconnect). To call tools without restarting, use a scratch script: `create_server().call_tool("name", {...})`, or raw RPCs through `se_mcp.bridge.Bridge().call(method, params)`.
- `npx @modelcontextprotocol/inspector` lets you call tools by hand.
- Logs: `%AppData%\SpaceEngineers\SpaceEngineers_*.log` (newest), lines starting `[SEMcp]`.
- Changing `plugin/SEMcp.Loader` needs a game restart; the build skips copying it while the game has it locked.

## Adding a feature

1. **Game side.** ModAPI is enough → add a method to the right `core/Services/*Service.cs` and register it (`dispatcher.Register("area.verb", Handler)`). Needs internals (`MyGuiManager`, render, Harmony) → put it in `plugin/SEMcp.Host`. Use `Access` for ownership and range checks, `RequireWritable()` before changing anything, and `Dto` helpers for output.
2. **Protocol.** Add a row to [protocol.md](protocol.md): params, result, and anything surprising.
3. **Tool.** Add the MCP tool in `server/src/se_mcp/tools.py` (logic in a topic module). Docstrings are what the AI reads: say what it does, when to use it, and the units.
4. **Fairness tag.** Add the tool to `fair.py`; a test fails if you forget.
5. **Tests.** Add tests in `server/tests/` using `tests/fake_bridge.py` (canned game replies).
6. **Try it in game**, ideally on a throwaway world, and note anything learned in PLAN.md.

## Reading the game's code (decompiling)

The game ships no source, and many member names you'll remember (or an AI will guess) are wrong or out of date. Look them up in a decompiled copy before relying on them. PLAN.md's rule: don't guess.

**Set up once** with [ilspycmd](https://github.com/icsharpcode/ILSpy) (9.1.0.7988 is the last version that runs on the .NET 9 SDK):

```bash
dotnet tool install -g ilspycmd --version 9.1.0.7988
SEBIN="C:/Program Files (x86)/Steam/steamapps/common/SpaceEngineers/Bin64"   # your game's Bin64
for d in Sandbox.Game Sandbox.Common SpaceEngineers.Game SpaceEngineers.ObjectBuilders VRage VRage.Game VRage.Library VRage.Math VRage.Scripting VRage.Render VRage.Render11 VRage.Input; do
  ilspycmd -p -o "ref/decompiled/$d" -r "$SEBIN" "$SEBIN/$d.dll"
done
```

`ref/` is gitignored. Regenerate it after game updates. **Never commit decompiled code or paste large parts of it into this repo**: it's Keen's code, kept locally as a reference only.

For a single type from a DLL you didn't decompile, add `-t`:

```bash
ilspycmd -t Sandbox.Graphics.GUI.MyGuiScreenBase -r "$SEBIN" "$SEBIN/Sandbox.Graphics.dll"
```

**What lives where:**

| DLL | What you'll find | Examples |
|---|---|---|
| `Sandbox.Common` | The **ModAPI** interfaces Core is written against: `Sandbox.ModAPI` (`IMyTerminalBlock`, `IMyThrust`, `IMyShipConnector`, ...) and `Sandbox.ModAPI.Ingame` (what PB scripts see) | Any block's properties and actions |
| `VRage.Game` | More ModAPI (`VRage.Game.ModAPI`: `IMyCubeGrid`, `IMySlimBlock`, `IMyInventory`), definitions, relations, `MyCamera` | `MyRelationsBetweenPlayerAndBlock`, `MyCamera.WorldToScreen` |
| `Sandbox.Game` | The game itself: entities (`MyCubeGrid`, `Sandbox.Game.Entities.Blocks`), `MySession`, characters, weapons (`Sandbox.Game.Weapons`), conveyors, GPS, chat, the HUD and screens (`Sandbox.Game.Gui`: `MyHud`, `MyGuiScreenHudSpace`, `MyHudMarkerRender`, terminal screens), session components such as `MyHighlightSystem`, `MyGuiSandbox` | How the HUD draws markers; what a block does on `UpdateAfterSimulation` |
| `SpaceEngineers.Game` | SE-specific blocks and their ModAPI: landing gear, solar, gravity generators, button panels, timers, the space HUD bindings | `IMyLandingGear` |
| `SpaceEngineers.ObjectBuilders`, `VRage.Game` | Object builders (the save format) | Fields a block saves |
| `Sandbox.Graphics` (use `-t`) | GUI plumbing: `MyGuiManager` (drawing text and sprites, screen coordinates), `MyGuiScreenBase`, `MyScreenManager` | The HUD overlay screen |
| `VRage.Render` | `MyRenderProxy`: the messages the game sends the renderer (sprites, debug draw, model highlights) | `DrawSpriteExt` for rotated lines |
| `VRage.Render11` | The DirectX 11 renderer itself: sprite batching, scene drawing, render targets | Offscreen camera capture (`MyRender11.DrawScene`) |
| `VRage.Scripting` | The programmable block compiler and the mod/PB API whitelist | Why a PB script doesn't compile |
| `VRage.Input` | Keyboard, mouse and controller input | Key bindings |
| `VRage`, `VRage.Library`, `VRage.Math` | Base utilities: `IPlugin` (`VRage.Plugins`), `MyStringId`, collections, logging, `VRageMath` vectors and matrices | `Vector3D.Transform` does the perspective divide |

**Tips:**
- Search by member name: `grep -rn "public .* WorldToScreen" ref/decompiled`. Search for usages too: the game's own call sites show the right arguments and coordinate spaces.
- Decompiled locals are named `num2`, `flag` and so on; the names on types, fields and methods are real.
- Prefer ModAPI in Core: it's what mods use and it changes rarely. Internals (anything `My...` that isn't behind an `IMy...` interface) go in the Host, may break on game updates, and need a comment saying why.
- Pulsar's own source is on [GitHub](https://github.com/SpaceGT/Pulsar) if you need the plugin loading details.

## Gotchas

- `python` on Windows PATH may be the Store stub. Use `uv run python`.
- The window screenshot is sometimes blank (the game window covered or minimised). The in-engine render (`hide_hud=true`) works, but it doesn't include GUI or HUD overlay elements.
- Thrust overrides below about 5% may not move a heavy small-grid ship even though the game reports thrust. See the docking pilot's `settle()`.
- Test on a world you don't mind breaking, and keep anything you change reversible. Ships can and do crash.
