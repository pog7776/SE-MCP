# SEMcp wire protocol

The protocol between the Python MCP server and the in-game plugin. Keep this in sync with `core/` and `plugin/SEMcp.Host/`.

## Transport

- TCP on `127.0.0.1:<port>` (default `8765`, from `config.json`).
- UTF-8, one JSON object per line (`\n`), in both directions.
- Several clients at once (an MCP session, the chat runner and the sessions it starts). Each authenticates on its own.
- Requests may be pipelined. Responses can arrive out of order, so match them by `id`.

## Config

`%AppData%\SpaceEngineers\SEMcp\config.json`, created on first run with a random token:

```json
{ "port": 8765, "token": "...", "readOnly": false, "allowPbCodePush": true,
  "allowAutopilot": true, "allowScreenshots": true, "allowWeapons": false, "maxRangeMeters": 50000,
  "chatPrefix": "/ai" }
```

`chatPrefix` empty turns in-game chat and the Comms tab Suit AI off (see "Companion methods").

## Messages (JSON-RPC 2.0)

Request: `{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}`

Success: `{"jsonrpc":"2.0","id":1,"result":{...}}`

Error: `{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"No world is loaded","data":{"kind":"no_session"}}}`

All errors use code `-32000`. The kind in `error.data.kind` says what went wrong:

| kind | meaning |
|---|---|
| `access_denied` | bad or missing auth, or the player may not use this grid/block |
| `no_session` | no world is loaded |
| `not_found` | no such grid/block/group/GPS |
| `ambiguous` | a name matched more than one thing |
| `out_of_range` | target is beyond `maxRangeMeters` |
| `read_only` | `readOnly` is on and the method writes |
| `invalid_params` | missing/wrong parameters or malformed JSON |
| `feature_disabled` | the config toggle for this feature is off |
| `scripts_disabled` | in-game scripts are disabled in this world |
| `unknown_method` | no such method |
| `game_error` | the game threw; the message has the exception |

Clients may also see `timeout`, `disconnected` or `not_running` from the Python bridge. Those are never sent by the plugin.

## Auth

The first message must be:

```json
{"jsonrpc":"2.0","id":0,"method":"auth","params":{"token":"<token>"}}
```

→ `{"result":{"ok":true}}`, or an `access_denied` error followed by a disconnect.

## Methods

### `ping`

Works with or without a world loaded.

```json
{ "host": "0.1.0.0", "generation": 1, "game": "1.210.014", "runtime": ".NET Framework 4.8.9363.0",
  "sessionLoaded": true, "world": "Star System", "player": "Engineer",
  "onlineMode": "OFFLINE", "isServer": true }
```

`generation` goes up by one on every hot reload. `host` is the Host build's assembly version, which changes on every build. `world`, `player`, `onlineMode` and `isServer` are null when no world is loaded.

### `debug.reload`

Answers `{"reloading": true}`, then on the next frame the plugin stops the host (closing this connection) and loads the latest Host + Core build. The Python bridge reconnects on the next call. A Release build triggers the same reload automatically.

## World methods (M2)

All of these need a loaded world (`no_session` otherwise). Grids and blocks are limited to what the player may use: owned by them, by their faction, or by nobody (`access_denied` otherwise), within `maxRangeMeters` (`out_of_range`). Ids are strings. Volumes are in liters, mass in kg, power in MW/MWh, percentages are 0–100.

A **construct** is a grid plus everything attached by rotors, pistons and hinges (`GridLinkTypeEnum.Mechanical`). The **docked group** adds connector-attached grids (`Logical`; `Physical` also takes in landing gear). Group ids are the smallest entity id in the group.

| Method | Params | Returns |
|---|---|---|
| `player.context` | — | `player`, `identityId`, `position`, `controlled` (`kind`, `blockId`, `blockName`, `gridId`, `gridName`, or `{kind:"Character"}`), `lookingAt` (camera ray, 200 m: `kind` grid/character/voxel/other, `distance`, and for grids `gridId`, `gridName`, `relation`, `block`), `nearestGrid` (closest usable grid within 50 m) |
| `character.status` | — | `alive`, `health`, `energy`/`oxygen`/`hydrogen` %, `environmentOxygen` % (`oxygen` and `environmentOxygen` are null when the world has oxygen off), `jetpack`, `dampeners`, `helmet`, `lights`, `movement`, `position`, `speed`, `gravityG`, `inventoryFill` |
| `grids.list` | `range?` (m, capped at max), `includeAll?` | `range`, `grids[]`: usable grids get `id`, `name`, `size`, `isStatic`, `blocks`, `relation`, `position`, `distance`, `speed`, `constructId`, `dockedGroupId`, `accessible:true`. With `includeAll`, others get name/size/relation/distance and `accessible:false` |
| `grid.get` | `gridId`, `includeSubgrids?`=true | `summary` (as in `grids.list`), `grids[]` in the construct, `docked[]`, `mass`, `power` (`batteries`, `producers[]` by type), `gas` (`hydrogen`, `oxygen` tank totals), `cargo` (cargo containers only), `damage` (`count`, worst 15 `blocks[]` with `missing` components), `functionalCount`, `terminalBlocks` |
| `blocks.list` | `gridId`, `type?`, `nameContains?`, `group?`, `includeSubgrids?`=true, `limit?`=200, `offset?` | `total`, `offset`, `blocks[]`: `id`, `name`, `type`, `subtype`, `displayType`, `gridId`, `enabled` (null if it can't be switched), `functional`, `working`, `integrity`, `accessible`. `type` matches type id, subtype or display name, case-insensitive |
| `block.get` | `blockId` | `block` (as above), `gridName`, `position` (world, centre of its cells), `forward`, `up`, `right` (world axes: with position, the full transform), `cell` (grid cell [x,y,z]), `cellSize` (m), `gridVelocity` (m/s), `detailedInfo`, `customInfo`, `customData`, `missingComponents`, `properties[]` (`id`, `type`, `value`: Boolean/Single/Int64/StringBuilder/Color as `#RRGGBBAA`), `actions[]` (`id`, `name`), `inventories[]` |
| `groups.list` | `gridId` | `groups[]`: `name`, `blocks[]` (`id`, `name`). Covers the grid's whole terminal system |
| `inventory.get` | one of `gridId` (+`includeSubgrids?`), `blockId`, `character: true` | `volumeL`, `maxVolumeL`, `fill`, `totals[]` (`type`, `subtype`, `amount`), `inventories[]` (non-empty ones, per block) |
| `production.get` | `gridId`, `includeSubgrids?`=true | `blocks[]`: `id`, `name`, `type`, `displayType`, `enabled`, `working`, `producing`, assembler `mode`/`repeating`/`cooperative` (null otherwise), `queue[]` (`blueprint`, `amount`), `input`/`output` (`fill`, `items`) |

## Control methods (M3)

These write to the game, so they all fail with `read_only` when `readOnly` is on. Each change is logged to the SE log as `[SEMcp] ...`. They use the block's own terminal actions and properties, which the game syncs in multiplayer like player input. Targets follow the same access and range rules as reads.

| Method | Params | Returns |
|---|---|---|
| `block.action` | `blockId`, or `gridId` + `group`; `action` (terminal action id, e.g. `OnOff_On`) | `action`, `applied[]` (block names), `skipped[]` (reasons). A single block without the action → `invalid_params` listing the available ids |
| `block.set_property` | `blockId`, or `gridId` + `group`; `property`; `value` | `property`, `changed[]` (`block`, `before`, `after`), `skipped[]`. Types: Boolean (true/false, "on"/"off"), Single (number, clamped to the block's min/max), Int64, StringBuilder/String, Color (`#RRGGBB`, `#RRGGBBAA` or `{r,g,b,a?}`) |
| `block.set_name` | `blockId`, `value` | `before`, `after` |
| `block.rename_many` | `renames[]`: `{blockId, name}` | `renamed[]` (`id`, `before`, `after`), `failed[]` (`id`, `error`). All in one game-thread call, by id. Blocked by `readOnly` |
| `block.set_custom_data` | `blockId`, `value` | `block`, `beforeLength`, `afterLength` |
| `notify` | `text`, `kind?` (`hud` default, or `chat`), `durationMs?` (hud, 500–60000, default 5000), `font?`, `sender?` (chat, default "Claude") | `shown`, `kind` |

## Programmable block methods (M4)

All take `blockId` (a programmable block, otherwise `invalid_params`) and return the PB state:

- `id`, `name`, `enabled`, `working`
- `scriptsEnabled`: the world setting
- `isServer`: false on a multiplayer client. There the server compiles and runs scripts, so `output` arrives a moment later.
- `hasCompileErrors`: null on a client
- `terminalArgument`: the default run argument
- `codeLength`, plus `code` (only from `pb.get`)
- `output`: the block's DetailedInfo, which holds Echo text and exceptions
- `compilerMessages[]`: errors and warnings from the last compile. The game keeps these private, so the host reads them with reflection. Null if unavailable.

In single player, compiling finishes before the call returns.

| Method | Params | Notes |
|---|---|---|
| `pb.get` | `blockId`, `includeCode?`=true | |
| `pb.set_code` | `blockId`, `code` | Sets `ProgramData`, which recompiles. Blocked by `readOnly` and by `allowPbCodePush: false` |
| `pb.recompile` | `blockId` | Like the terminal's Recompile button |
| `pb.run` | `blockId`, `argument?` (default: the terminal argument) | Runs with `UpdateType.Terminal`, like pressing Run. `invalid_params` when the block isn't working |

The Python `write_pb_script` tool saves the previous code to `%AppData%\SpaceEngineers\SEMcp\pb-backups\` before replacing it. The `se://pb-primer` MCP resource explains how PB scripts are written.

## Autopilot and GPS methods (M5)

The `rc.*` methods take `blockId` (a Remote Control) and return its state:

- `id`, `name`, `gridId`, `gridName`, `working`, `autopilot`
- `flightMode`: Patrol, Circle, OneWay, Track or WorkArea
- `direction`: the RC face that leads
- `speedLimit`, `collisionAvoidance`, `dockingMode` (precision)
- `position`, plus the RC's `forward`/`up`/`right`
- `frontDirection`: the RC face matching the ship's main (or occupied, or first) cockpit. Cryo chambers and passenger seats don't count. `frontReference` names that cockpit.
- `speed`, `gravityG`
- `currentWaypoint`: `name`, `coords`, `distance`
- `waypoints[]`
- `remoteControlled`: someone is driving through this RC

| Method | Params | Notes |
|---|---|---|
| `rc.get` | `blockId` | |
| `rc.fly_to` | `blockId`, `target` (`[x,y,z]` or `{x,y,z}`) + `name?`, or `waypoints[]` (`{name?, coords}`); `mode?`=OneWay; `direction?`=auto; `speedLimit?`; `collisionAvoidance?`=true; `precision?`=false | Replaces the waypoints and engages autopilot. Blocked by `readOnly` and `allowAutopilot: false`. A single waypoint needs OneWay mode (game rule). `game_error` if the game won't engage, e.g. while someone remote-controls the RC. Big ships spend a while lining up before they thrust |
| `rc.stop` | `blockId`, `clearWaypoints?`=false | |
| `gps.list` | — | `gps[]` for the local player, closest first: `hash`, `name`, `description`, `coords`, `distance`, `showOnHud`, `color` |
| `gps.add` | `name`, `coords`, `description?`, `showOnHud?`=true | the new entry |
| `gps.remove` | `hash` or `name` | `removed` |

## Sensing methods (M6)

| Method | Params | Returns |
|---|---|---|
| `camera.list` | `gridId` | `cameras[]`: `id`, `name`, `gridName`, `working`, `active` (the player is looking through it), `raycastEnabled`, `scanRange` (m charged), `position`, `forward`, `up`, `distance` |
| `camera.raycast` | `blockId`; `distance?`=2000 with `pitch?`/`yaw?` (deg, within the cone), or `target` `[x,y,z]` | `camera`, `enabledRaycast` (true if this call turned raycasting on, which starts the charge), `scanned`, `distance`, `hit` (null for nothing, else `id`, `name`, `type`, `relation`, `position`, `hitPosition`, `distance`, `size`, `speed`, `velocity`), `scanRangeLeft`, `msUntilScan`, `coneLimitDeg`, `maxDistance`. Write method (it changes `EnableRaycast` and uses charge) |
| `sensors.get` | `gridId` or `blockId` | `sensors[]`: `id`, `name`, `working`, `active`, `extents` (left, right, top, bottom, front, back in m), `maxRange`, `detects[]`, `relations[]`, `detected[]` (as `hit` above) |

## Inventory methods (M7)

| Method | Params | Returns |
|---|---|---|
| `inventory.layout` | `gridId` | `gridName`, `shipId`, and `inventories[]` for every usable inventory on the construct, docked ships included. Each has: `blockId`, `name`, `type`, `subtype`, `gridName`, `shipId`/`shipName`, `customData` (only when it contains `[`), `index`, `role` (`input`/`output` for production blocks), `working`, `conveyorGroup`, `volumeL`, `maxVolumeL`, `items[]`. `shipId` is the mechanical group (grids joined by rotors and pistons, not by connectors), named after its biggest grid. Inventories with the same `conveyorGroup` are joined by conveyors |
| `inventory.transfer` | `fromBlockId` + `fromIndex?`=0, or `fromCharacter: true`; `toBlockId` + `toIndex?`=0, or `toCharacter: true`; `type` (short, e.g. `Ingot`); `subtype`; `amount?` (default all) | `item`, `from`, `to`, `route`, `requested`, `moved`, `pending`, `leftInSource`, `reason` (why some or all didn't move: no conveyor path, conveyor too small for the item, not accepted, destination full, none there). `out_of_range` when the suit can't reach. See the rules below |

`inventory.transfer` follows the inventory screen's rules (`MyTerminalInventoryController.CanTransferItem`):
- **Block to block:** the item must travel along conveyors big enough for it. The host checks with the game's own pathfinding (`MyGridConveyorSystem.ComputeCanTransfer` with the item id), which also works for access points without an inventory.
- **Suit to or from a block:** through an access point.
  - If seated, the access point is your seat. If the seat isn't on the conveyors, only its own inventory is reachable.
  - On foot, it's the target itself if it's within 5 m (`MyConstants.DEFAULT_INTERACTIVE_DISTANCE`). Otherwise it's the nearest block within 5 m that can convey the item to the target.
  - `route` says which one was used.
- **How the move is sent:** through `MyInventory.TransferByUser`, the same validated request the screen sends. So it works in single player, when hosting, and as a multiplayer client.
- **As a multiplayer client:** the server carries it out a moment later. The reply then has `pending: true`, and `moved` is the amount requested for the first stack.

## Production methods (M7)

| Method | Params | Returns |
|---|---|---|
| `recipes.list` | `blockId?` (only what that block can make) | `assemblerSpeed`, `assemblerEfficiency`, `refinerySpeed` (world multipliers), `recipes[]`: `id` (e.g. `BlueprintDefinition/SteelPlate`), `name`, `madeIn[]` (production block names), `inputs[]` / `outputs[]` (`type`, `subtype`, `amount`, in kg for ore and ingots), `seconds` (base time per run), `ignoresEfficiency`. Covers blueprints that some production block can make and whose outputs are real items. Left out: the build planner's per-block component kits, and gas tank / O2/H2 generator / irrigation "recipes" (refills with a placeholder output). Assemblers use `inputs / assemblerEfficiency` |
| `production.queue` | `blockId`, `blueprint` (an `id` from `recipes.list`), `amount?`=1 (blueprint runs), `index?` (insert position; default the end) | `block`, `queued`, `amount`. `invalid_params` if the block can't make it |
| `production.remove` | `blockId`, `index`, `amount?` (default the whole entry) | `block`, `removed`, `amount` |
| `production.clear` | `blockId` | `block`, `cleared` |

`production.get` queue entries also have `index` and `blueprintId`. Queue changes use the game's own requests (`AddQueueItemRequest` and the like), so they sync in multiplayer like the production screen.

`recipes.list` also returns `refineryEfficiency`: refinery type name to its ore-to-ingot yield factor (`MaterialEfficiency`, before yield modules).

## Build planning methods (M7)

| Method | Params | Returns |
|---|---|---|
| `blockdefs.find` | `query` (words that must all appear in the name or subtype id), `size?` (`large`/`small`), `limit?`=30 | `blocks[]`: `id` (e.g. `Gyro/LargeBlockGyro`), `name`, `size`, `pcu`, `count`, `components[]` (`type`, `subtype`, `count`). Exact name first, then names containing every word, then shorter names. Public blocks with components only |
| `projector.get` | `blockId` | `name`, `projecting`, `working`. When projecting, also: `projectedGrid`, `totalBlocks`, `remainingBlocks`, `remainingArmorBlocks`, `buildableNow`, `remaining[]` (block types not placed yet, as `blockdefs.find` entries with `count`), `unfinishedBlocks` and `unfinishedComponents[]` (missing components of placed but unfinished or damaged blocks on the projector's ship) |

## Screen methods (M7)

These cover every text surface of a block: LCDs, cockpit screens, programmable blocks, button panels and so on.

| Method | Params | Returns |
|---|---|---|
| `screens.list` | `gridId`, or `blockId` (full text) | `blocks[]`: `blockId`, `name`, `type`, `working`, `surfaces[]` (`index`, `name`, `content` (`NONE`/`TEXT_AND_IMAGE`/`SCRIPT`), `script`, `images[]`, `font`, `fontSize`, `fontColor`, `background`, `alignment`, `padding`, `size`, `textLength`, `text` (first 200 chars in grid listings)) |
| `screen.write` | `blockId`, `surface?`=0, `text?`, `append?`, `fontSize?`, `font?`, `fontColor?`, `background?` (`#RRGGBB` or `{r,g,b}`), `alignment?` (left/center/right), `padding?`, `clearImages?`=true | `block`, `before` (the surface as it was, full text), `after`. Switches the surface to text mode. Uses `IMyTextSurface.WriteText`, like a PB script; the block syncs it |
| `screen.mode` | `blockId`, `surface?`=0, `mode` (`text`/`script`/`none`), `script` (for `script`; must be one the surface offers) | `block`, `before`, `after` |

## Player and session methods (M7)

| Method | Params | Returns |
|---|---|---|
| `character.switch` | any of `helmet`, `jetpack`, `dampeners`, `lights` (bool) | `changed[]`, and the four states. Uses the character's own `Switch*` calls (like pressing J/X/Z/L), which sync like player input; only the keys given that differ are toggled |
| `session.save` | — | `saved`, `world`, `path`, `backups` (`MaxBackupSaves`), `seconds`. A normal save of the current world (`IMySession.Save()` with no name; a custom name would act like "Save As" and move the session). The game keeps earlier versions in the world's Backup folder. Server/single player only (`feature_disabled` on a client). Blocks the game thread while saving |

## Combat methods (M7)

Weapons are `IMyUserControllableGun` blocks (turrets and fixed guns) and custom turret controllers (`IMyTurretControlBlock`) on the construct. Firing is opt-in: `weapon.fire`, and `ShootOnce`/`Shoot*` through `block.action`/`block.set_property`, return `feature_disabled` unless `allowWeapons: true` (default false). Aiming and target settings are not gated.

| Method | Params | Returns |
|---|---|---|
| `weapons.get` | `gridId` | `gridName`, `weapons[]` (`id`, `name`, `type`, `kind` (`turret`/`fixed`/`turret controller`), `gridName`, `enabled`, `working`, `integrity`; guns: `shooting`, `magazines[]` (accepted subtypes), `loaded`, `roundsLoaded` (left in the loaded magazine), `magazinesInside`, `position`, `forward`, `up` (how it's mounted); turrets and controllers: `ai`, `underControl`, `hasTarget`, `aimed`, `target` (detected entity, as `camera.raycast` hits), `range`, `targets` (flags), `targetingGroup`, `targetingGroups[]`; turrets: `idleRotation`, `azimuthDeg`, `elevationDeg`, `aimDirection` (barrel, world space); controllers: `tools[]`, `camera`, `azimuthRotor`, `elevationRotor`), `ammo` (per accepted magazine subtype: `name`, `rounds` per magazine, `carried` on the construct) |
| `turret.set` | `blockId` or `blockIds[]`; any of `reset` (keeps idle rotation as it was), `range`, `idleRotation`, `targetingGroup`, `targets` (`{enemies, neutrals, characters, largeGrids, smallGrids, stations, missiles, meteors}`; controllers take `friends` instead of `enemies`) | `changed[]`. `reset` = `ResetTargetingToDefault` (undo `turret.aim`) |
| `turret.aim` | `blockId(s)`; `position` (+ `velocity` to lead a moving target), or `entityId`, or `azimuth`/`elevation` (degrees) | `aimed[]`. `SetTarget`/`TrackTarget`/`SetManualAzimuthAndElevation`. Controllers only take `entityId`; fixed guns refuse |
| `weapon.fire` | `blockId(s)`, `mode` (`once`/`on`/`off`) | `fired[]`, `mode`. Terminal actions `ShootOnce`/`Shoot_On`/`Shoot_Off` (sync like a timer or PB) |
| `weapon.magazine` | `blockId`, `magazine` (subtype or display name; must be one the weapon accepts) | `before`, `after`, `switched`, `magazine`. Host hook: `MyGunBase.SwitchAmmoMagazine`; server/single player only |
| `camera.track` | `gridId`, `position` (where the target should be now), `entityId?` (the target being tracked), `overshoot?`=50 | `camera` (the one used), `hit`, `lost` (nothing hit, or a different entity), `scanRangeLeft`; or `camera: null` + `notReady[]` (`camera`, `reason` `charging`/`outside its view cone`, `msUntilScan`). One step of a lidar-style track: raycasts a little past the point with the most-charged camera that can scan it |
| `damage.get` | `gridId` | `gridName`, `blocks` (count), `damaged[]` (`id` for terminal blocks, `name`, `type`, `gridName`, `position`, `integrity`, `damage`, `functional`, `deformed`, `missing` components) |

## Radio methods (M7)

| Method | Params | Returns |
|---|---|---|
| `radio.signals` (host) | `gridId?` (listen from that ship; default the player: their suit, or the ship they control) | `listener`, `listenerKind` (`ship`/`suit`), `signals[]` nearest first: `antennaId`, `entityId` (the grid or character carrying it), `name` (HUD text), `gridName`, `kind` (`antenna`/`beacon`/`laser`/`suit`), `body` (`large ship`/`small ship`/`station`/`character`), `relation`, `position`, `distance`, `radius`, `direct` (false = only heard through relays), `ownShip` (on the listener's own construct). `MyAntennaSystem.GetAllRelayedBroadcasters`: what the HUD shows |
| `radio.antennas` | `gridId` | `gridName`, `equipment[]`: `id`, `name`, `gridName`, `enabled`, `working`, `ownerId`, `owner` (relation), `position`, `forward`, `up`, `kind`; antennas: `radius`, `broadcasting`, `isBroadcasting`, `hudText`, `showShipName`; beacons: `radius`, `hudText`; lasers: `status`, `range` (null = unlimited), `targetCoords`, `permanent`, `requireLoS`, `outsideLimits`, `other` (`id`, `name`, `gridName` of the linked laser) |
| `radio.set` | `blockId`; any of `enabled`, `radius`, `broadcasting`, `hudText`, `showShipName` (antennas and beacons) | the grid's `radio.antennas` |
| `laser.connect` | `blockId`, `target?` (`[x,y,z]`), `permanent?` | `block`, `status`. `SetTargetCoords` + `Connect`, like the terminal; the other laser must aim back and have line of sight |
| `los.check` | `from`, `to` (`[x,y,z]`), `skip?`=2.5 (meters ignored at each end), `layer?`=30, `ignoreId?` (look through that ship and everything that moves with it: rotor/piston/hinge subgrids, door/turret subparts, ships gear-locked onto it; not connector-docked ships) | `clear`, `distance`, `hit?` (`entity`, `entityId`, `block` (`name`, `type`), `position`, `fromStart`, `voxel` when it's rock). Physics raycast plus a voxel-shape test (host hook), since asteroids away from ships have no physics |
| `ore.markers` (host) | `gridId?` (distances from that ship, and list its detectors), `limit?`=500 | `visible`, `controlling` (`name`, `grid` of the ship controller the player is in, or null), `detectors[]` (`id`, `name`, `working`, `range`, `broadcastUsingAntennas`), `markers[]` nearest first (`ore`, `material`, `position`, `distance`, `body`, `detectorId`), `total`. Reads `MyHud.OreMarkers`: only what ore detectors have reported to the player's HUD, never voxel data |
| `surface.scan` | `origin`, `normal`, `u`, `v` (`[x,y,z]`), `u0`/`u1`/`v0`/`v1` (meters, default ±10), `step?`=1, `top?`=20, `depth?`=10, `ignoreId?`, `layer?`=30 | `u0`, `v0`, `step`, `nu`, `nv`, `heights[nu][nv]` (meters along normal from origin, null = nothing), `hazards[]` (`"i,j,code"`: T thruster, X weapon, D drill, G grinder, W welder, H door, M rotor/hinge/piston). Rays from `top` down to `-depth`; max 20000 cells. Physics only (no voxels); a ray starting inside a block doesn't see that block |
| `block.position` | `blockId` | `id`, `name`, `gridId`, `position`, `forward`, `up` |
| `laser.idle` | `blockId` | `block`, `status`. The `Idle` terminal action (drops the link) |

## Docking and flight methods (M7)

| Method | Params | Returns |
|---|---|---|
| `ship.hud` | `gridId` | One ship's systems in one call, for the ship's mechanical group only (not ships docked to it): `id`, `name`, `size`, `isStatic`, `grids`, `blocks`, `mass`, `speed`, `distance`, `pilot`, `dampeners`, `handbrake`, `autopilot[]`, `thrusters`/`gyros` (`count`, `on`, `working`; gyros `overriding`), `thrustOverrides`, `batteries` (stored/max MWh, in/out MW, recharge/discharge counts), `reactors` (out/max MW, `uraniumKg`), `solar`, `hydrogen`/`oxygen` (`count`, `on`, `fill`, `stockpile`), `cargo`, `connectors`, `docked[]` (`connector`, `to`, `ship`, `shipId`), `gear`, `gearLocked`, `antennas[]`, `beacons[]`, `weapons` (count/working/turrets/targeting/shooting), `damaged`, `subgrids[]` (`id`, `name`, `size`, `blocks`, `joint` (`name`, `kind` rotor/hinge/piston, `onGrid`, `working`, `angleDeg`, `locked`, `extensionM`, `maxM`)) |
| `target.lock` (host) | `gridId` | `ship`, `controller` (seat with target locking), `lockingEnabled`, `target` (`id`, `name`, `distance`), `locked`, `progressPercent`, `losingLock`, `component` (where the lock lives: the pilot's character). Read-only; only meaningful while someone pilots the ship |
| `dock.get` | `gridId` | `gridName`, `isStatic`, `connectors[]` (`id`, `name`, `gridName`, `size`, `working`, `status` (`Unconnected`/`Connectable`/`Connected`), `other` (`id`, `name`, `gridName`, `gridId`), `position`, `forward` (the joining face), `up`, `parking`, `collectAll`, `throwOut`), `landingGear[]` (`id`, `name`, `working`, `locked`, `mode`, `autoLock`), `mergeBlocks[]` (`id`, `name`, `working`, `enabled`, `connected`). The ship's own mechanical group only |
| `dock.connector` | `blockId`, `action` (`connect`/`disconnect`/`toggle`) | `block`, `before`, `after`. `connect` refuses when Unconnected |
| `dock.gear` | `blockId` or `gridId`, `action` (`lock`/`unlock`/`autolock_on`/`autolock_off`) | `changed[]` (`name`, `mode`, `autoLock`) |
| `flight.set` | `thrusters?` (`{id: fraction 0..1}`, 0 = not overriding), `gyros?` (`{id: [pitch, yaw, roll] RPM}` or `{id: null}` to release), `gridId?` with `dampeners?`, `thrustersOn?`, `release?` (drop every override on the ship) | `thrusters`, `gyros` (counts). Blocked by `allowAutopilot: false`. For closed-loop flying from the client (one call per control tick) |

`grid.motion` takes `thrusters: true` to add `mass` (the ship's own, physics), `dampeners`, and `thrusters[]` (`id`, `name`, `push` (world direction it pushes the ship), `maxThrust`, `current`, `overridePercent`, `working`), all for the ship's mechanical group only.

## Toolbar and group methods (host)

Game internals (ModAPI can read groups but not edit them, and can't see toolbars). Changes use the calls the game's own screens make, which sync in multiplayer.

| Method | Params | Returns |
|---|---|---|
| `toolbar.get` | `blockId?` (any block with a toolbar: cockpits, seats, remote controls, button panels, timers, sensors, event controllers...); none = the player's own (`MyToolbarComponent.CharacterToolbar`, the one the HUD shows) | `owner` (`player` or the block name), `type` (`Ship`/`Character`/`ButtonPanel`/...), `pages`, `currentPage`, `slots[]` (non-empty only): `index`, `page`, `slot` (1-9), `text`, `kind` (`block`/`group`/`weapon`/`cubeblock`/...), `target`, `blockId`, `action`, `actionName` |
| `toolbar.set` | `blockId?`; `page`+`slot` or `index`; then `clear`, or `itemName` (in-game name of a tool, weapon or block) or `definitionId` (`Type/Subtype`), or `action` + `targetBlockId` or `group` + `gridId` | `owner`, `before`, `after` (slots as in `toolbar.get`). `invalid_params` lists the valid action ids; block/group actions are refused on Character toolbars |
| `group.set` | `gridId`, `name`, `blockIds[]`, `mode?` (`replace`/`add`/`remove`/`delete`), `newName?` | `name`, `created`, `deleted`, `before` (count), `blocks[]`. `MyCubeGrid.ModifyGroup`, like the terminal; an emptied group is deleted |

## Host-only methods

These use game internals beyond ModAPI, so they live in `plugin/SEMcp.Host` rather than Core.

`camera.capture` renders the world offscreen from some viewpoint and returns an image. The player's own view isn't changed.

How it works (`CameraCapture.cs`):
- A Harmony prefix on `MyRender11.DrawScene` points the render camera at the requested view.
- It renders into a borrowed render target and encodes the result with the game's `MyTextureData.ToData`.
- Then it restores the player's camera before the normal frame draws.
- The patch is removed when the host stops, including on hot reload.

The approach comes from the CameraLCD plugin.

Params: `blockId` (a camera block, at its zoom), or `position` + `forward` (+ `up?`, `fovDegrees?`=70) for a free viewpoint within `maxRangeMeters`, or neither for the player's view without HUD. Optional: `width?`=1280 and `height?` (16:9 by default, max 1920 each), `fovDegrees?`, `format?` (`jpeg` or `png`).

Returns `source`, `format`, `width`, `height`, `fovDegrees`, `position` and `image` (base64; the buffer may have trailing zero padding). It times out after 8 s if no frame renders, for example while the game is minimized. `allowScreenshots: false` turns it off (`feature_disabled`).

| Method | Params | Returns |
|---|---|---|
| `sun.info` | `gridId?` | `sunDirection` (unit vector toward the sun, `[x,y,z]`). With `gridId`, `panels[]` for every solar panel in the construct: `id`, `name`, `normal` (world), `twoSided`, `exposure` (0–1, the game's own formula: `dot(normal, sunDirection)`), `maxOutputMW` (definition), `currentMaxMW` (what it can make now, including shadowing) |
| `highlight.set` | `blockIds[]`, `color?` (default yellow), `thickness?`=8, `pulseFrames?`=60 (0 = steady) | `highlighted[]` (names), `skipped[]`. Makes blocks glow for the local player only (`MyHighlightSystem`, the scenario highlight system). Only blocks that are entities: functional blocks, not plain armor |
| `highlight.clear` | `blockIds[]?` (default: everything highlighted) | `cleared`. The host also clears its highlights when it stops (hot reload, unload) |
| `overlay.set` | `elements[]`: `{id, kind: text\|rect\|bar\|line\|arrow\|circle\|icon\|marker\|track\|route, x?, y?, w?, h?, x2?, y2?` (screen fractions, 0,0 top-left; x2/y2 = line/arrow end), `icon?` (short name or `Textures\...` path), `style?` box\|point, `offscreen?`=true (edge arrow when off screen), `track?` [meteors\|missiles\|enemies\|neutrals\|friends\|unowned\|grids\|players], `range?`=3000 (capped by maxRangeMeters), `incoming?`, `maxTargets?`=30, `text?` (`{path}` from the bound result, `{pct}` bar percent, `{dist}` marker distance), `color?`, `background?`, `scale?`=0.8, `align?` left\|center\|right, `font?`="White", `fill?`=true, `thickness?`=2, `value?`, `max?`=100, `valuePath?`, `maxPath?`, `lowBelow?` (percent), `lowColor?`, `ttl?` (s), anchor (marker, or the tip of a line/arrow, or the centre of a circle/icon) `position?` [x,y,z] \| `blockId?` \| `gridId?`, `bind?: {method, params?, every?=1}`}` | `set[]` (ids), `total`. Markers and trackers draw corner brackets round the entity's projected bounds (8 corners of its local AABB); trackers rescan `MyEntities` twice a second (one box per ship, not subgrids; the player's own construct is skipped). Upsert by id: an existing element keeps fields not given. Drawn by a focus-less GUI screen (`MyGuiScreenBase`, `CanHaveFocus=false`) above the HUD, local player only; hidden when the HUD is hidden or another screen has focus. `bind` re-reads a read-only method (ship.hud, grid.get, grid.motion, character.status, player.context, inventory.get, ...) on the game thread every `every` s; it's called once at set time so errors surface. A `route` takes `route` (`[[x,y,z], ...]`, at least one point), `name?`, `note?` (passed on in the `arrived` event's `data.note`, so the runner wakes the AI with what it meant to do there), `arriveRadius?`=3 and optionally `blockId`/`gridId` (the last point follows that entity). It marks the next point (text placeholders `{name}`, `{dist}` = distance left along the route, `{step}` = "2/3") with faint lines and dots through the rest. Each frame the host moves on when the player is within `arriveRadius` of the last point, or twice that of a turn. At the end it removes itself and raises an `arrived` event (`source: guide`, `data.id`, `data.name`). `overlay.list` adds `leg`/`legs` for routes |
| `overlay.remove` | `ids[]` | `removed`, `total` |
| `overlay.clear` | | `removed`. Closes the screen. The host also closes it when it stops (hot reload, unload); a world reload closes it too and the next `overlay.set` re-adds it |
| `overlay.list` | | `elements[]`: `{id, kind, x, y, w, h, text, bind, bindError, anchored, expiresIn}` |
| `environment.get` | `gridId?` (default: the player's position) | `position`, `naturalGravityG`, `artificialGravityG`, `gravityDirection`, `planet` (nearest: `name`, `distanceToCenterKm`, `altitude`, `radiusKm`, `surfaceGravityG`, `hasAtmosphere`, `atmosphereTopAltitude`, `inAtmosphere`, `airDensity`, `oxygen`, `sunElevation` in degrees, + is day; null in deep space), `weather` (`name`, `intensity`, `solar`/`wind`/`oxygen` multipliers), `sunDirection`, `dayLengthMinutes` |
| `environment.bodies` | `range?`=50000 m (asteroids), `limit?`=30 | `planets[]` (all: `name`, `distanceKm` to the surface, `radiusKm`, `surfaceGravityG`, `hasAtmosphere`, `center`), `asteroids[]` within range (`name`, `distanceKm`, `sizeM`, `center`), nearest first |
| `buildplanner.get` | — | `items[]` from the player's build planner (G menu), stored on their identity: `index`, `block`, `blockId`, `size`, `inProgress`, `components[]` still needed |
| `grid.motion` | `gridId` | `center` and `radius` (bounding box of the construct), `cockpit` (`name`, `forward`, `up` of the main, occupied or first flight seat, else null), `isStatic`, `gravityG` (natural gravity), `forward`/`up`/`right` (world unit vectors), `angularVelocity` (rad/s), `linearVelocity` (m/s), `gyros[]`: `id`, `name`, `forward`/`up`/`right`, `overriding`, `working` |

Gyro override properties (`Override`, `Pitch`, `Yaw`, `Roll` in RPM) are set with `block.set_property`. Their sign conventions relative to the gyro's axes aren't obvious, so the Python `steering` module measures them with a short test spin before turning.

## Companion methods (host)

Everything that wants the assistant's attention is one **event** (`EventHub.cs`): `id`, `type` (`chat`, `watch`), `source` (`chat`, `comms`, or the watched method), `at` (UTC, ISO 8601), `text`, `context` (`player`, `persona`: `ship` when seated in a cockpit/seat/RC, else `suit`; `ship?`, `shipId?`, `creative`), `data`.

Event sources:
- **Chat** (`ChatInbox.cs`): the host hooks `MessageEntered` on `MyAPIUtilities.Static` (unhooked in `Stop()`). This is the object `MyAPIGateway.Utilities` points to, but that property is null until a world loads, and the host starts at game launch. A line that is the prefix, or starts with the prefix and a space, gets `sendToOthers = false`, is echoed to the local chat (the game hides it otherwise) and becomes a `chat` event with `source: chat`. The bare prefix shows a help line.
- **Comms tab** (`CommsChannel.cs`): Harmony patches on the internal `MyTerminalChatController` take over the bot conversation (`ChatChannel.ChatBot`), which nothing answers in this game version. `RefreshPlayerList` (postfix) renames its entry "-Suit AI-". `SendMessage` (prefix) turns what the player sends there into a `chat` event with `source: comms`, adds it to that conversation's history and skips the game's own send. A leading prefix (`/ai`) is dropped. Patches are removed in `Stop()`.
- **Watches** (`Watches.cs`): `watch` events, below.
- **Guide** (`OverlayScreen.cs`): an `arrived` event when the player reaches the end of a `route` overlay element (`guide_to`).

If nobody has polled in the last ~75 s, chat and Comms messages are refused with a "start the runner" line. At most 50 events wait.

| Method | Params | Returns |
|---|---|---|
| `events.poll` | `waitMs?` (0–30000, default 0): how long to wait when nothing is queued | `events[]`, taking them off the queue. Answered off the game thread. The runner (`se-mcp-runner`) is the intended poller: two pollers would split the events |
| `events.recent` | `limit?`=20 (max 50), `type?` | `events[]`: the last ones raised, delivered or not, newest last |
| `comms.post` | `text`, `sender?` (default "Suit AI"/"Ship AI") | `posted`. Adds a line to the Comms tab's Suit AI conversation and redraws it if it's open. `feature_disabled` when the Comms patch isn't active |
| `watch.set` | `id`; `method` (one of the overlay's bindable read-only methods), `params?`, `path` (dotted, case-insensitive; array index or `length`), `op` (`<` `<=` `>` `>=` `==` `!=` `changed`), `value` (not for `changed`), `label?`, `note?`, `every?`=2 s (min 0.5), `cooldown?`=120 s, `once?`=false | `watch` (as in `watch.list`), `current`, `replaced`. Upsert by id: unset fields keep their values, and the edge state is kept. Reads once at set time, so a bad method/params/path fails here (`invalid_params`) |
| `watch.remove` | `id` (`*` = all) | `removed` (count) |
| `watch.list` | — | `watches[]`: the saved fields plus `current`, `active` (test true now), `fired` (count), `error`; `file` |

Watches are evaluated on the game thread, at most 1 ms per frame, each every `every` s while a world is loaded. They're edge-triggered: a `watch` event when the test becomes true (numbers compare as numbers, otherwise case-insensitive text; a missing or null value never fires), then nothing until it has been false again and `cooldown` s have passed. `changed` fires on any change after the first read. `once` removes the watch after it fires. Event `text` is e.g. `Suit oxygen low: oxygen is 12 (< 20)`; `data` has `watch`, `label`, `method`, `params`, `path`, `op`, `threshold`, `value`, `previous`, `note`.

They're saved in `watches.json` next to `config.json`. The first run writes defaults: suit oxygen < 20, hydrogen < 15, energy < 10, health < 40. `character.status` reports `oxygen` and `environmentOxygen` as null when the world has oxygen off, so those never fire there.
