# Ship Computer — Plan

Real Linux running on a ship: a screen (later a **Computer** block) boots a small Linux, the ship shows up inside it as files, and you type into it with cursor mode. Drives are blocks and items you can carry. Status: plan only (2026-11-07). Nothing is built yet.

See [PLAN.md](PLAN.md) for the parent project, [protocol.md](protocol.md) for the RPCs, [plugin/SEScreens/README.md](plugin/SEScreens/README.md) for the screens this builds on, and [SCREEN-PAGES.md](SCREEN-PAGES.md) for the page bridge and scoped access it shares.

## Goals

- A real Linux shell in game, with real binaries, scripts, cron and pipes, not an imitation.
- The ship as a filesystem: blocks, sensors, power and cargo are files you `cat`, `tail -f` and `echo` into, the way `/sys` works.
- Hardware that fits the game: Computer, Hard Drive and Disk Drive blocks, plus thumb drives that carry files between ships and players.
- A computer's state is saved with the world. Reloading puts it back exactly where it was.
- Safe by default: the guest can't reach the host machine or the network, and it can only touch the ship it's on.

## What SEScreens already gives us

| SEScreens piece | Use here |
|---|---|
| Cursor mode (real mouse and keyboard on the screen under the cursor) | The screen *is* the terminal. A separate terminal window isn't needed. |
| Browser screens (`SEScreens.Browser.exe`, Chromium via CefSharp, one process per screen) | Runs the console page and, in route A, the VM itself, sandboxed in its own process. |
| Helper process pattern: shared-memory frames, stdin commands, stdout events, exits with the game, restarts after a crash, a folder per screen | The model for a QEMU process if we need one (route B). |
| `IFrameSource` / `IInputSink` / `IAudioSource` / `ITextPrompter` | A native terminal source could plug in here (route C). |
| Screen apps in the script list, settings in CustomData (`[SEScreens]`) | A **Terminal** app on any LCD stands in for the Computer block until the hardware mod exists. |
| Block menu and left-click use (cursor mode, in the host) | Power, reboot and disk controls on the computer block. |
| Screen Pages' `se.call` bridge, scoped to the screen's grid (planned) | How the guest reaches the ship. Build the scoped access once for both. |

## Running Linux: options

| Route | What it is | Pros | Cons |
|---|---|---|---|
| **A. v86 in a browser screen** (spike first) | A page with **xterm.js** as the console and **v86** (an x86 emulator compiled to WebAssembly) booting a small Linux, all inside the screen's Chromium. | Nothing new to install or bundle. Isolated by Chromium's sandbox and its own process. v86 can save and restore its whole state, and has a 9p filesystem implemented in JS that `/ship` can sit on (check both in the spike). | Slower than QEMU; 32-bit x86 only. Each screen's Chromium costs a few hundred MB plus the VM's RAM. The VM lives in that screen's browser process. Client-side only. |
| B. QEMU process behind the same page | The plugin starts one QEMU per computer, following the browser helper's process pattern. The xterm.js page talks to its serial console. | Real speed, 64-bit, `savevm` snapshots. Could run on the server for multiplayer. | QEMU to bundle or install (GPLv2, fine as a separate process). The page needs a channel to it (QEMU's websocket chardev, or `se.call`). |
| C. Native terminal source | QEMU serial into a C# terminal emulator that draws an `IFrameSource`. | No Chromium per computer. | We'd write the VT parser and glyph rendering that xterm.js gives for free. |
| D. Fake shell and filesystem in C# | | Cheap, simple in multiplayer. | Not real Linux. |

Containers (Docker, WSL) were considered and rejected: they isolate fine, but there's no easy way to freeze one and save it with the world on Windows.

**Plan:** start with A. The xterm.js page stays the console in A and B, so moving to B later changes only what's behind the page. Choose between A and B after the spike, by speed and memory.

## Architecture (route A)

```
LCD running the "Terminal" app
  └─ browser screen (SEScreens.Browser.exe, Chromium)
       └─ terminal page (se://computer/ once Screen Pages lands)
            ├─ xterm.js  ◀── cursor-mode keyboard and mouse
            ├─ v86       ── serial ──▶ xterm.js
            │    ├─ /ship  (9p, implemented in page JS) ── se.call ──▶ SEMcp Dispatcher, scoped to this grid
            │    ├─ /dev/vdb… ◀── Hard Drive images
            │    └─ /media/usb0 ◀── Datapad in a Disk Drive
            └─ state save/restore ──▶ the plugin ──▶ stored with the world
```

### Guest

- A small Linux image built for v86: kernel plus busybox, `jq`, an editor, maybe Lua or MicroPython. v86's own examples (Buildroot images) are the starting point. Building our own needs Linux, so run Buildroot under WSL.
- No network device. The only links out are the serial console and `/ship`.

### `/ship` filesystem

The page implements a 9p filesystem in JS. Each read or write becomes an `se.call` RPC, run under the screen's scope. Route B would use a FUSE daemon in the guest over virtio-serial instead, with the same layout.

```
/ship/info                              # name, grid id, mass, speed, position (if the ship could know it)
/ship/blocks/<name>/{type,enabled,integrity,properties/…}
/ship/blocks/<name>/actions/<action>     # write anything to run it
/ship/groups/<group>/…
/ship/sensors/<name>/detected           # tail -f friendly
/ship/power/{stored,input,output}
/ship/cargo/<container>/<item>          # counts
/ship/pb/<name>/{code,run,echo}
/dev/lcd/<name>                         # write text to another screen
```

- Block names in paths are escaped or de-duplicated (names can repeat and contain `/`). Each block directory also gets a stable alias based on its entity id.
- Reads are cached briefly (Screen Pages' cost guard), so `ls -R /ship` doesn't send hundreds of RPCs.
- `tail -f` and change notifications come from SEMcp watches and events where they exist, and from polling otherwise.

### Hardware (content mod)

Block and item definitions with models, in a workshop mod used alongside the plugins.

| Thing | Kind | Role |
|---|---|---|
| Computer | Block (small and large grid) with a screen | Runs the Terminal app on its own screen. Needs power. Turning it off saves state and stops the VM. |
| Hard Drive | Block | An extra disk for computers on the same grid (conveyor-connected or just on the grid; decide in C4). Images are stored with the save, keyed by entity id. |
| Disk Drive | Block with a small inventory | Putting a thumb drive in mounts it at `/media/usb0`; taking it out unmounts it. |
| Thumb drive | Item | A custom subtype of the game's **Datapad**. `MyObjectBuilder_Datapad` has its own `Name` and `Data` strings per item, so drives don't stack and each carries its own data (checked in `ref/decompiled`). Small files go straight into `Data`; big ones keep an id pointing to an image stored with the save. |

Until the mod exists, any LCD running the Terminal app is a computer, configured in its CustomData (for example `[SEScreens]` `terminal0=`, or a `[Computer]` section).

### Saving and loading

- The VM is tied to the block and surface, not to the browser process. When the browser closes (the world saves or unloads, someone picks another app, the helper crashes), the page saves the VM state, and the plugin stores it in `<save>/ShipComputer/<blockId>-<surface>/`, along with the disk images.
- The world loads or the app starts → the page restores the state. If it's missing or broken, the computer does a cold boot.
- Copying a blueprint doesn't copy disks; the copy starts fresh. Later: an option to copy disks too.
- A crash loses everything since the last save, so also save every few minutes.

### Multiplayer

- Route A runs on the client that has SEScreens, like every SEScreens screen today. Other players see the "needs SEScreens" card. That's acceptable for single player and the first versions.
- Real multiplayer (one shared computer) needs the VM on the server, which means route B, with the console sent to clients. Decide when we get there.
- Who can use a computer follows the block's ownership and share settings.

## Work in other parts of the repo

Shared with Screen Pages. Build these once:

1. **Scoped access.** A call carries a scope: allowed grid ids, read-only or read/write, allowed methods, and optionally fair-play awareness (M8). The Dispatcher enforces it. Page calls and any TCP client with a scoped token both use it.
2. **The `se.call` bridge** in the browser helper, and the `se://` page scheme, which also fixes `file://` blocking the requests v86 uses to load its BIOS and disk images.
3. **Protocol version** reported by `ping` / `auth`, so other clients can check compatibility.

Ship computer only:

4. **Terminal screen app** in SEScreens, plus the state store (save/restore commands between the page and the plugin).
5. **README wording:** describe the plugin as an RPC bridge into SE that the MCP server is one client of.

## Milestones

0. **Spike (no plugin changes):** an xterm.js + v86 page boots a small Linux in desktop Chrome, served by a local static server. Then on an LCD with `browser.open` and cursor mode. Measure boot time, memory, typing latency and state size. Check v86's state save and its 9p hooks. Decide route A or B.
1. **Terminal app:** "Terminal" in the screen's script list, with settings in CustomData. The page ships with the plugin, like the browser helper. State saves when the browser closes and restores on start.
2. **Scoped access + `se.call` + read-only `/ship`:** together with Screen Pages' bridge. `info`, `blocks`, `power`, `cargo`, `sensors`.
3. **`/ship` writes:** block actions and properties, PB files, `/dev/lcd`.
4. **Hardware mod:** Computer, Hard Drive and Disk Drive blocks; disks attached by grid.
5. **Thumb drives:** Datapad subtype, mounting from the Disk Drive's inventory, small files inline and big ones as stored images.
6. **Saving with the world:** state and disks in the save folder, periodic saves, blueprint behaviour.
7. **Multiplayer:** decide whether route B on the server is worth it.

## Open questions

- Is v86 fast enough for a pleasant shell, and how much memory does a VM plus its Chromium take? How many computers before it hurts?
- Should computers far from the player pause, and how much do they save on doing so?
- Font size and colours on small LCDs: what's readable on a cockpit screen?
- How do computers talk to Programmable Blocks: through `/ship/pb`, IGC, or both?
- Network between computers on the same grid (or over antennas) as a later feature?
- Where does the page live before the `se://` scheme exists: a local server started by the plugin, or wait for Screen Pages?
