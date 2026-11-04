# Notes for coding agents

Start with [DEVELOPING.md](DEVELOPING.md) (architecture, dev loop, adding a feature, decompiling) and [PLAN.md](PLAN.md) (design and where each milestone stands).

- **Verify game APIs before using them.** Look names up in `ref/decompiled/` (see DEVELOPING.md to generate it); don't rely on remembered member names. Never commit `ref/` or paste decompiled code into the repo.
- **Core uses the ModAPI only**; game internals go in `plugin/SEMcp.Host`, and anything the Host adds to the game is undone in `HostModule.Stop()` (hot reload).
- **Every new RPC** gets a row in `protocol.md`; **every new MCP tool** gets a `fair.py` entry (a test enforces it) and tests in `server/tests/`.
- Build with `dotnet build SEMcp.sln -c Release` (hot-reloads into a running game); test Python with `cd server && uv run pytest`.
- Live tests touch a real world: check the game is running (port 8765), keep changes reversible, and check clearance before moving ships.
- Don't put machine paths or personal details in committed files; per-machine build settings go in `Directory.Build.local.props` (gitignored).
