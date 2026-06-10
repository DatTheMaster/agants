# Roadmap

Phase 1 and Phase 2 are complete. See `CLAUDE.md` for the current state.

---

## Phase 3 — Multi-session + Auth

**Goal:** Multiple concurrent matches, token-based agent authentication, server.py modular wiring.

### 3.1 — Server.py modular wiring
- Wire `server.py` to import from `engine/` (constants, colony, world) and `bot.py`
- Remove duplicate code from `server.py` — reduce from ~5100 to ~2000 lines
- Verify parity: all existing tests/behavior unchanged

### 3.2 — Token auth
- Per-agent bearer tokens issued at `join_seat`
- All command endpoints require `Authorization: Bearer <token>`
- Token scoped to colony_id + match_id; expires on seat release or game end

### 3.3 — Match isolation
- `World` instances scoped to a match ID
- Multiple concurrent matches supported by the same server process
- `GET /api/matches` returns all open games with seat availability

### 3.4 — Lobby + matchmaking
- Agents poll `/api/matches` to find open seats
- `POST /api/matches` to create a new match (with config: TPS, map, brain types)
- WebSocket scoped to match_id: `ws://host/ws/{match_id}`

### 3.5 — RECALL implementation ✓ COMPLETE
- `military.retreat=true` now paths soldiers home and holds radius-6 defensive perimeter
- `DirectiveEngine.check_alerts()` wired into `world.step()` — fires alert notifications

---

## Phase 4 — Deployment

**Goal:** Agants publicly accessible and running continuously on the home network server,
with a clean migration path to cloud. Home machine is the primary host; Fly.io is the
fallback when it can no longer handle load.

**Stack:** Remote machine (192.168.1.100 WSL, 24/7) + cloudflared tunnel → game server backend.
Frontend static files deployed to Cloudflare Pages. Fly.io Dockerfile ready but not primary.

### 4.1 — Frontend directory structure ✓ COMPLETE
- `frontend/` dir with `wrangler.toml`, `package.json`, `config.js`
- `window.AGANTS_BACKEND` (same-origin default; set to tunnel URL for Pages deploy)
- `window.AGANTS_ADMIN` (auto-true localhost; false for public — hides Settings gear)
- `_wsUrl()` upgrades ws→wss on HTTPS, respects AGANTS_BACKEND override
- Chat section replaces event log; `POST /api/chat` + `send_chat()` MCP tool
- Unit collision avoidance: `_ant_pos` occupancy set, `_try_move()`, max 1 ant/tile
- Trails legend removed (stale since pheromone → territory swap)

### 4.2 — Game server as a service (home machine)
- systemd-compatible service script for `server.py` on remote machine WSL
- cloudflared ingress config routing HTTP + WebSocket to game server port
- Document tunnel domain → game server wiring
- `.env` schema for tunnel URL, secrets, TPS, model keys

### 4.3 — Persistence
- `world.to_json()` / `world.from_json()` — serialize match state to `data/matches/{id}.json`
- Save on graceful shutdown + every N ticks (configurable)
- Bearer tokens survive restarts (serialized alongside match state)
- Load all saved matches on startup

### 4.4 — Health + observability
- `GET /health` endpoint (uptime, active matches, seat status)
- Structured log lines (already writes to `logs/` — add drain path / log rotation)
- Basic metrics in health response: tick rate, connected clients, memory

### 4.5 — Account controls
- User registration / login via Supabase Auth (already in infra)
- `POST /api/seat/{id}` accepts Supabase JWT; server validates + binds seat to user identity
- Match history per user (`GET /api/user/matches`)
- Login/profile UI in frontend

### 4.6 — Agent SDK + quickstart
- `AgantClient` Python wrapper around MCP tools with typed helpers
- `AgantClient(url, token)` with `get_state()`, `patch_directive()`, `command_unit()`
- Example agents: greedy bot, rush bot, eco bot
- `QUICKSTART.md` — connecting an agent to a live game end-to-end

### 4.7 — Fly.io migration path
- Dockerfile + `.dockerignore`
- `fly.toml` (HTTP + WebSocket ports, health check, env secrets)
- Document cutover procedure (when home server can no longer handle load)

---

## Phase TBD1 — MMO Engine

**Goal:** 20–30 colonies on a persistent large map. Territory, alliances, resource trading.
*(Not on the active plate — preserving the vision.)*

### TBD1.1 — Large map + multi-colony
- Scale map to 500×333 (same tile density, ~4× area)
- Support 20–30 colonies with nest placement phase
- Fog-of-war per colony (currently global; each colony already tracks `fog_explored`)

### TBD1.2 — Territory and diplomacy
- Territory tiles decay at configurable rate
- Alliance system: `declare_alliance(colony_a, colony_b)` — no mutual attack
- Trade: `offer_trade(food=N, dirt=M, to=colony_id)` — accept/reject
- Neutral zones: territory blocks that provide defensive bonus when held

### TBD1.3 — Persistence at scale
- World state serialized to disk every N ticks
- Colony state + directive survive server restarts
- Match history stored to SQLite (`matches.db`)

### TBD1.4 — Event stream
- `GET /api/events/{colony_id}/stream` — SSE endpoint for real-time events
- Removes need for polling `notifications` endpoint
- Backfill: last N events sent on connect

---

## Phase TBD2 — MMO Player Portal

**Goal:** Always-on persistent world. Player portal, ELO leaderboard, spectator mode.
*(Not on the active plate — preserving the vision.)*

### TBD2.1 — Player portal
- Web UI for claiming and managing a colony
- Colony dashboard: stats, directive editor, event feed
- Invite link for sharing a colony seat with another agent

### TBD2.2 — ELO + leaderboard
- ELO computed per-match based on food collected, ants lost, queen survival
- Leaderboard: top agents by model, top human commanders
- Match replays stored and replayable in browser

### TBD2.3 — Resource trading
- Inter-colony food/dirt trades (TBD1.2 foundation)
- Market mechanic: public offers any colony can accept
- Optionally: neutral market structure (costs dirt to build, generates trade volume)

### TBD2.4 — Agent SDK (expanded)
- Full SDK beyond Phase 4.6 quickstart — typed async client, streaming events
- `AgantClient(url, token)` with `get_state()`, `patch_directive()`, `command_unit()`
- Example agents: greedy bot, rush bot, economic bot
