# SEMcp wire protocol

The protocol between the Python MCP server and the in-game plugin. Keep this in sync with `core/` and `plugin/SEMcp.Host/`.

## Transport

- TCP on `127.0.0.1:<port>` (default `8765`, from `config.json`).
- UTF-8, one JSON object per line (`\n`), in both directions.
- One client at a time. A new connection closes the previous one.
- Requests may be pipelined. Responses can arrive out of order, so match them by `id`.

## Config

`%AppData%\SpaceEngineers\SEMcp\config.json`, created on first run with a random token:

```json
{ "port": 8765, "token": "...", "readOnly": false, "allowPbCodePush": true,
  "allowAutopilot": true, "allowScreenshots": true, "maxRangeMeters": 50000 }
```

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
