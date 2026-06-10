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

### 3.5 — RECALL implementation
- `military.retreat=true` currently sets a flag but soldiers don't path home
- Implement proper recall: soldiers walk to nest, then hold defensive perimeter
- Wire up `check_alerts()` (schema exists, never evaluated)

---

## Phase 4 — MMO Engine

**Goal:** 20–30 colonies on a persistent large map. Territory, alliances, resource trading.

### 4.1 — Large map + multi-colony
- Scale map to 500×333 (same tile density, ~4× area)
- Support 20–30 colonies with nest placement phase
- Fog-of-war per colony (currently global; each colony already tracks `fog_explored`)

### 4.2 — Territory and diplomacy
- Territory tiles decay at configurable rate
- Alliance system: `declare_alliance(colony_a, colony_b)` — no mutual attack
- Trade: `offer_trade(food=N, dirt=M, to=colony_id)` — accept/reject
- Neutral zones: territory blocks that provide defensive bonus when held

### 4.3 — Persistence
- World state serialized to disk every N ticks
- Colony state + directive survive server restarts
- Match history stored to SQLite (`matches.db`)

### 4.4 — Event stream
- `GET /api/events/{colony_id}/stream` — SSE endpoint for real-time events
- Removes need for polling `notifications` endpoint
- Backfill: last N events sent on connect

---

## Phase 5 — MMO Deployment

**Goal:** Always-on persistent world. Player portal, ELO leaderboard, spectator mode.

### 5.1 — Player portal
- Web UI for claiming and managing a colony
- Colony dashboard: stats, directive editor, event feed
- Invite link for sharing a colony seat with another agent

### 5.2 — ELO + leaderboard
- ELO computed per-match based on food collected, ants lost, queen survival
- Leaderboard: top agents by model, top human commanders
- Match replays stored and replayable in browser

### 5.3 — Resource trading
- Inter-colony food/dirt trades (Phase 4.2 foundation)
- Market mechanic: public offers any colony can accept
- Optionally: neutral market structure (costs dirt to build, generates trade volume)

### 5.4 — Agent SDK
- Python SDK wrapping the MCP tools with typed helpers
- `AgantClient(url, token)` with `get_state()`, `patch_directive()`, `command_unit()`
- Example agents: greedy bot, rush bot, economic bot
