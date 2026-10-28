# SE-MCP

Lets an AI assistant (over MCP) see and control your Space Engineers 1 ships, stations and suit. See [PLAN.md](PLAN.md) for the design.

Status: **M0 (groundwork)**. A hello-world plugin that logs to the SE log.

## Prerequisites

- Space Engineers 1. The default game path is set in [Directory.Build.props](Directory.Build.props). Override it with `dotnet build -p:SEBin=<...>\Bin64`.
- .NET SDK (any recent one, used to build `net48` projects).
- [Pulsar](https://github.com/SpaceGT/Pulsar/releases/latest): extract it into its own empty folder. It cleans that folder on update, so keep nothing else there. Use **`Legacy.exe`** (SE1 on .NET Framework). There's also a Windows [installer](https://github.com/StarCpt/Pulsar-Installer) that can set up Steam launch for you.
- Python 3.11+ and [uv](https://docs.astral.sh/uv/) (from M1, for the MCP server).
- Optional: `ilspycmd` (`dotnet tool install -g ilspycmd --version 9.1.0.7988`, the last version that runs on .NET 9) to regenerate the decompiled reference.

## Build

```bash
dotnet build SEMcp.sln -c Release
```

A Release build also copies `SEMcp.Loader.dll` into Pulsar's local plugin folder, `%AppData%\Pulsar\Legacy\Local` (override with `-p:PulsarLocal=...`). If SE is running, the DLL is locked, so the copy is skipped with a warning (M1 adds hot reload).

## Load in Pulsar (one time)

1. Build (above), so the DLL is in `%AppData%\Pulsar\Legacy\Local`.
2. Start the game with `%AppData%\Pulsar\Legacy.exe`.
3. In the main menu, open **Plugins**. **SEMcp.Loader** is in the list. Enable it and apply. Pulsar asks to restart the game.

Pulsar's **Sources** button (only shown with the `-sources` launch flag) → *Add Precompiled Plugin* only opens that same `Local` folder, so you don't need it.

## M0 check

Start the game, load any world, then exit. The newest `%AppData%\SpaceEngineers\SpaceEngineers_*.log` should contain:

```
[SEMcp] Init (loader 0.0.1.0)
[SEMcp] Session ready: <world name>
```

## Decompiled reference

`ref/decompiled/` (gitignored) holds ilspycmd output for the game DLLs, to look up real API names. Regenerate after a game update:

```bash
for d in Sandbox.Game Sandbox.Common SpaceEngineers.Game SpaceEngineers.ObjectBuilders VRage VRage.Game VRage.Library VRage.Math VRage.Scripting VRage.Render VRage.Input; do ilspycmd -p -o "ref/decompiled/$d" -r "$SEBIN" "$SEBIN/$d.dll"; done
```
