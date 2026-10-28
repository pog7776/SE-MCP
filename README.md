# SE-MCP

Lets an AI assistant (over MCP) see and control your Space Engineers 1 ships, stations and suit. See [PLAN.md](PLAN.md) for the design and [protocol.md](protocol.md) for the wire protocol.

Status: **M1 (skeleton)**. Hot-reloading plugin, TCP bridge with `ping`, and an MCP server with `se_status`.

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
