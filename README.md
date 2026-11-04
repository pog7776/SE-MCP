# SE-MCP

Lets an AI assistant see and control your Space Engineers 1 ships, stations and suit, through the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). Ask it what's in your cargo, to dock the fighter, to box every incoming meteor on your HUD, or to write a programmable block script, and it does it in your running game.

```
AI client ──MCP/stdio──▶ server/ (Python) ──TCP 127.0.0.1:8765──▶ SEMcp plugin (inside the game, via Pulsar)
```

Status: in active development (milestone M7 of [PLAN.md](PLAN.md)). Single player and your own client only; there are no prebuilt releases yet, so you build it from source.

## What it can do

The AI gets about 100 tools that work on your live game. You talk to it normally ("why won't my ship fly?", "make 50 steel plates") and it picks the tools. You can refer to ships and blocks by name ("the Red Ship", "the cargo"), and leaving the ship out means the one you're in or standing next to. Each group below lists example requests and the tools behind them.

### Look around

It can read anything you could see in the game's terminal, plus what's around you.
- *"What am I looking at?"*, *"What's wrong with this ship?"*, *"How long will my hydrogen last?"*
- **You and your suit:** what you're controlling and aiming at, health, energy, oxygen, hydrogen, helmet and jetpack, and the gravity, atmosphere, weather and time of day where you are. `se_status`, `looked_at`, `suit_status`, `environment`
- **Ships and stations:**
  - Nearby grids and a one-call status: power, fuel, cargo, speed and damage.
  - A HUD-style check that warns about anything that would stop the ship flying, like thrusters off, recharge-only batteries or overrides still on.
  - How long batteries, uranium, gas and your suit will last at the current rate.
  - `list_grids`, `ship_status`, `ship_hud`, `forecast`, `damage_report`
- **Blocks:** list a ship's blocks, groups and toolbars, or open one block's full terminal page: settings, actions, inventory and CustomData. `list_blocks`, `inspect_block`, `list_groups`, `toolbar`
- **Pictures:**
  - A screenshot of your screen.
  - The view through any camera block, rendered in the background so your own view isn't touched.
  - A "drone shot" of a whole ship from any side.
  - `screenshot`, `list_cameras`, `camera_view`, `ship_photo`

### Change things on your ships

Anything you'd do in the terminal, without opening it.
- *"Turn off all the lights"*, *"Rename the thrusters by direction"*, *"Set the antenna to 5 km"*
- **Blocks:** turn them on or off, run terminal actions, set properties (thrust override, rotor speed, colours...), rename one or many, and edit CustomData. `block_action`, `group_action`, `toggle_blocks`, `set_block_property`, `rename_block`, `rename_blocks`, `set_custom_data`
- **Groups and toolbars:** create and edit block groups, and fill toolbar slots on cockpits, button panels, timers and your own toolbar. `edit_group`, `set_toolbar_slot`
- **Screens:** write text on LCDs and cockpit screens, or switch them to built-in apps like the artificial horizon or the hydrogen gauge. `list_screens`, `write_screen`, `set_screen_mode`
- **Messages and GPS:** pop up a HUD notification or chat line for you, and manage your GPS points. `say`, `list_gps`, `add_gps`, `remove_gps`
- **Your suit:** helmet, jetpack, dampeners and lights, as if you pressed J/X/Z/L. `suit_controls`
- **Safety:** save the world as a restore point before something risky. `save_world`

### Inventory and production

- *"Where's my uranium?"*, *"Put all the ore in the ore containers"*, *"Queue enough parts for 10 gyroscopes"*
- **Items:** find items across a ship, and move them between containers or to and from your suit, through the conveyors like a drag in the terminal. `inventory`, `find_items`, `move_items`
- **Tidying:** tag containers `[Ore]`, `[Ingots]`, `[Components]` and so on, and it proposes a plan to sort everything. It only moves anything after you say yes. `organize_inventory`
- **Production:** see what assemblers and refineries are doing, look up recipes using this world's costs, queue or remove jobs, and work out what a block costs right down to ore, compared with what the ship holds. `production`, `recipe`, `queue_production`, `remove_from_queue`, `clear_production_queue`, `block_cost`
- **Building:** read your build planner (the G menu) and pull its components into your suit; check a projector's progress and queue what's missing. `build_planner`, `projector_status`

### Programmable blocks

- *"Write a script that shows cargo fill on the bridge LCD"*
- Read a programmable block's code and output, write a new script and compile it (with the compiler's errors reported back), run it with an argument, and recompile a crashed one. The old code is backed up before every write. `read_pb_code`, `write_pb_script`, `run_pb`, `pb_output`, `recompile_pb`

### Fly

- *"Fly to the Iron Asteroid GPS"*, *"Turn us around"*, *"Dock the fighter on the Red Ship"*, *"Land on the platform"*
- **Autopilot:** fly to a GPS point or coordinates using the ship's Remote Control, check progress, and stop. `fly_to`, `autopilot_status`, `stop_autopilot`
- **Turning:** rotate by angles, point a side or the fixed guns at a ship or GPS point, or face the solar panels at the sun. `rotate_ship`, `point_ship`, `align_to_sun`
- **Docking:** fly a ship's connector onto another ship's or station's connector and lock it, even starting from parked on landing gear. Undock and back off. `docking_status`, `dock`, `undock`
- **Landing:** measure the surface of a ship or station, find a flat spot the whole ship fits on, land on landing gear and magnetic plates, and lock. `surface_map`, `land`, `landing_gear`

### Sensors, radio and ore

- *"Is anything out there?"*, *"What's that signal?"*, *"Where's the nearest ice?"*
- **Contacts:** one merged picture of what the ship knows about: radio signals, turret targets and sensor detections, with speeds. `contacts`
- **Radio:** hear antenna, beacon and laser antenna signals, including ones relayed through your own antennas. Manage your antennas and beacons (radius, going quiet), and link laser antennas. `radio_signals`, `radio_equipment`, `set_radio`, `laser_link`
- **Sensors and scanning:** sensor blocks and what they detect; camera raycasts like a scanner script; and tracking a moving ship with the cameras, like a lidar script. `sensors`, `camera_scan`, `track_target`
- **Ore:** what your ore detectors show on the HUD, per ore and nearest first, with an optional GPS mark. `ore_scan`

### Combat

Firing is off until you set `"allowWeapons": true` in the settings (see Settings below).
- *"What are the turrets shooting at?"*, *"Target their reactor"*
- See every weapon, what it's targeting and its ammo. Change turret targeting, aim turrets (leading moving targets), aim at one block on another ship, fire, and switch magazines. `weapons`, `set_turrets`, `aim_turrets`, `target_block`, `fire_weapons`, `load_magazine`

### Draw on your HUD

Only you see these. They hide with the HUD and while a menu or terminal is open.
- *"Put my fuel on the HUD"*, *"Box any incoming meteors"*, *"Point me at the connector"*
- **Panels and drawings:** live ship status panels (hydrogen, battery, uranium, cargo, speed) whose bars go red when low. Text, boxes, bars, lines, arrows, circles and game icons, any of which can stay live with game data.
- **Pinned to the world:** brackets around a ship or block, and arrows pointing at it. Off screen, an arrow at the screen edge points the way.
- **Trackers:** keep a box with name, distance and time to impact around every meteor, missile, enemy, neutral, ship or player nearby.
- **Glow:** make blocks glow so you can find them, for example every damaged one.
- `hud_ship_panel`, `hud_draw`, `hud_track`, `hud_list`, `hud_remove`, `hud_clear`, `highlight_blocks`, `clear_highlights`

### Remember things

- **Ship notes:** a notes file per ship for the next agent: custom scripts (cruise control, auto-docking), odd layouts and your habits. The AI reads it before flying that ship. `ship_notes`, `write_ship_notes` (see [Ship notes](#ship-notes))
- **Watches:** "tell me when hydrogen is below 20%". A watch checks a value every few seconds and wakes the in-game AI when the condition is met. `watch`, `list_watches`, `remove_watch`, `recent_events`
- **Personality:** the in-game AI's name and manner, and what it has learned about you. `ai_soul`, `write_ai_soul`, `ai_soul_presets`, `use_ai_soul` (see [In-game companion](#in-game-companion))

### Limits and safety

- It works in **single player** (or as the host) on **your own game**. It only changes grids you own, share with your faction, or that nobody owns, within `maxRangeMeters`. Other players' ships can be looked at, but not controlled.
- **Off switches** in the settings: `readOnly` blocks every change; weapons need `allowWeapons`; autopilot, scripts and screenshots each have their own switch.
- **Asks before acting:** tools that would be costly to get wrong, such as organizing inventory, report the plan first. The in-game AI is told to ask before risky actions.
- **Not a cheat tool:** it doesn't spawn items or blocks, and everything it changes is something you could do yourself in the game. Some tools do know more than you could see. For example, `list_grids` and the HUD trackers find ships behind an asteroid. A "fair play" mode is planned that limits it to what your antennas, cameras and sensors could know ([PLAN.md](PLAN.md), M8).

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
