# SE-MCP

Lets an AI assistant see and control your Space Engineers 1 ships, stations and suit, through the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). Ask it what's in your cargo, to dock the fighter, to box every incoming meteor on your HUD, or to write a programmable block script, and it does it in your running game.

```
AI client ──MCP/stdio──▶ server/ (Python) ──TCP 127.0.0.1:8765──▶ SEMcp plugin (inside the game, via Pulsar)
```

Status: in active development (milestone M7 of [PLAN.md](PLAN.md)). Single player and your own client only; there are no prebuilt releases yet, so you build it from source.

## What it can do

- **See:** ships, stations, blocks and their settings, inventories, your suit, what your crosshair is on, screenshots, camera blocks and offscreen drone shots of a ship, sensors, camera raycasts, ore detectors, radio broadcasts, the environment (gravity, air, sun).
- **Run things:** terminal actions and properties, block groups and toolbars, GPS points, production queues and recipes, inventory moves through conveyors and tidying containers by tag, LCD screens, programmable block scripts (write, compile with errors, run, with backups).
- **Fly:** autopilot to a point, point and rotate a ship, dock a connector onto another and undock, land on landing gear after measuring the surface.
- **Fight** (off until you allow it): turret targets and aiming, weapons, magazines, tracking, damage reports.
- **Your HUD:** text, live fuel/power panels, bars, lines, arrows and icons, boxes around ships and blocks, and trackers that box every meteor, enemy or ship nearby. Only you see them.
- **Talk in game:** `/ai` in chat or a Comms-tab conversation, with watches that speak up on their own ("tell me when hydrogen is below 20%") and a personality you can change.
- **Ship notes:** a per-ship notes file (scripts, quirks) that the AI reads before flying a ship.

## Install

You need Windows, Space Engineers 1 on Steam, and about 15 minutes.

### 1. Prerequisites

- [Pulsar](https://github.com/SpaceGT/Pulsar/releases/latest), the plugin loader, installed to its default `%AppData%\Pulsar` (the [installer](https://github.com/StarCpt/Pulsar-Installer) does this). Launch the game through it once to check it works.
- The [.NET SDK](https://dotnet.microsoft.com/download) 9 or newer, to build the plugin.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), which runs the Python side and fetches Python 3.11+ if you don't have it. Make sure `uv` is on your PATH (`uv --version` in a new terminal).
- An MCP client, such as [Claude Code](https://claude.com/claude-code) or Claude Desktop.

### 2. Get the code and point it at your game

```bash
git clone <this repo's URL> SE-MCP
cd SE-MCP
```

If the game isn't in Steam's default library (`C:\Program Files (x86)\Steam\steamapps\common\SpaceEngineers`), copy `Directory.Build.local.props.example` to `Directory.Build.local.props` and set `SEBin` to your game's `Bin64` folder. That file is yours and isn't committed.

### 3. Build

```bash
dotnet build SEMcp.sln -c Release
```

This puts the plugin where Pulsar and the game look for it:

| File | Goes to |
|---|---|
| `SEMcp.Loader.dll` | `%AppData%\Pulsar\Legacy\Local` and `...\Interim\Local` |
| The rest of the plugin | `%AppData%\SpaceEngineers\SEMcp\host\` |

### 4. Enable it in Pulsar (one time)

1. Start the game through Pulsar. Steam launch option: `"%AppData%\Pulsar\Legacy.exe" %command%`. Use `Interim.exe` for the .NET 10 version; it has its own plugin list.
2. Main menu → **Plugins** → **+** → find **SEMcp.Loader** → enable → **Apply**. The game restarts.
3. Load a world. The newest `%AppData%\SpaceEngineers\SpaceEngineers_*.log` should show:

```
[SEMcp] Listening on 127.0.0.1:8765
[SEMcp] Host generation 1 started (...)
```

The first start writes `%AppData%\SpaceEngineers\SEMcp\config.json` with a random access token. Keep it private: anything with the token can control your game.

### 5. Connect your AI client

**Claude Code:** open it in this folder. The repo's [.mcp.json](.mcp.json) registers the `space-engineers` server (it needs `uv` on PATH). Approve it when asked.

**Other clients** (Claude Desktop's `claude_desktop_config.json` and similar): add

```json
{ "mcpServers": { "space-engineers": {
    "command": "uv",
    "args": ["--directory", "C:/path/to/SE-MCP/server", "run", "se-mcp"] } } }
```

Use the full path to `uv.exe` as `command` if it isn't on PATH.

Then, with a world loaded, ask "what am I looking at?". The AI should call `se_status` and tell you.

### Settings

`%AppData%\SpaceEngineers\SEMcp\config.json` (restart the game after editing):

| Key | Default | What it does |
|---|---|---|
| `readOnly` | `false` | Block every change; the AI can only look |
| `allowWeapons` | `false` | Allow firing weapons |
| `allowAutopilot` | `true` | Allow flying ships |
| `allowPbCodePush` | `true` | Allow writing programmable block scripts |
| `allowScreenshots` | `true` | Allow screenshots and camera renders |
| `maxRangeMeters` | `50000` | How far away the AI can see and act |
| `chatPrefix` | `"/ai"` | Chat lines starting with this go to the in-game AI; empty turns it off |
| `port`, `token` | `8765`, random | The local connection |

### Troubleshooting

- **No `[SEMcp]` lines in the log:** the plugin isn't enabled in Pulsar for the flavour you launched (Legacy and Interim have separate lists).
- **Build says "Space Engineers not found":** set `SEBin` (step 2).
- **The client can't start the server:** `uv` isn't on PATH for the client; use the full path to `uv.exe`.
- **Screenshots come back blank:** a known quirk of the window capture; `screenshot` with `hide_hud=true` renders the view in-engine instead.

## In-game companion

Talk to the assistant from inside the game (only you see it):
- **Chat:** type `/ai <message>`. `/ai reset` starts a fresh conversation.
- **Comms tab:** open the terminal's Comms tab and pick **-Suit AI-**. Type normally; replies stay in that conversation.

Something has to answer, so run the companion runner while you play. It uses the [Claude Code](https://claude.com/claude-code) CLI on your Claude plan (log in once with `claude auth login`):

```bash
cd server
uv run se-mcp-runner     # options: --model, --timeout, --claude <path to claude CLI>
```

It keeps one headless Claude Code process with only this server's tools and one conversation (reset after 3 h idle). Replies come from "Suit AI" on foot or "Ship AI" when seated, unless you give it a name.

**Watches:** "tell me when the Red Ship's hydrogen is below 20%" makes it add a watch. A watch re-reads a game value every few seconds and wakes the AI when its test becomes true; the AI decides whether to tell you. Suit oxygen, hydrogen, energy and health warnings are on by default. Stored in `%AppData%\SpaceEngineers\SEMcp\watches.json`.

**Personality:** `%AppData%\SpaceEngineers\SEMcp\runner\SOUL.md` holds its name, personality, how it talks to you and what it has learned about you. Edit it, or tell it ("/ai be grumpier", "/ai call me Captain") and it rewrites the file. Old versions go to `old/`. Built-in personalities ([server/src/se_mcp/souls](server/src/se_mcp/souls)), switched with "/ai switch to the suit-ai soul":
- `default`: a capable, easy-going crewmate, no role-play.
- `suit-ai`: SAM-7, a company-issued Suit Assistance Module. Fully in-universe.
- `crewmate`: Vesper, an AI who is your partner by choice, with her own history and opinions. Fully in-universe.

Add your own `.md` there: a `> one-line description` near the top, a `Name: ...` line, and `## ` sections (the last is where it keeps notes about you). Safety rules (short replies, asking before risky actions) live in the runner, so a personality can't remove them.

## Ship notes

One markdown file per ship in `%AppData%\SpaceEngineers\SEMcp\ships\<Ship Name>.md` (override with `SE_MCP_SHIP_NOTES`): custom scripts, odd layouts, habits. `se_status` and `ship_hud` point at unread notes, and `fly_to`, `dock`, `undock` and `land` stop once until they've been read.

## Developing

See [DEVELOPING.md](DEVELOPING.md) for how it's built, the hot-reload loop, adding a tool, testing, and which game DLLs to decompile to understand or extend it. [PLAN.md](PLAN.md) has the design and roadmap, and [protocol.md](protocol.md) the plugin's wire protocol.

## License

[MIT](LICENSE). Space Engineers is a trademark of Keen Software House; this project is not affiliated with or endorsed by Keen. It contains no game code or assets: it builds against your installed copy.
