# Roadmap

Phase 1 and Phase 2 are complete. See `CLAUDE.md` for the current state.

---

## Phase 3 — Multi-session + Auth ✓ COMPLETE

**Goal:** Multiple concurrent matches, token-based agent authentication, server.py modular wiring.

### 3.1 — Server.py modular wiring ✓
### 3.2 — Token auth ✓
### 3.3 — Match isolation ✓
### 3.4 — Lobby + matchmaking ✓
### 3.5 — RECALL + check_alerts ✓

---

## Phase 4 — Deployment + Public Access ✓ COMPLETE

**Goal:** Agants publicly accessible and running continuously. Home machine is the primary host.
Build out the minimum frontend surface to make the game approachable and playable by external
developers and their agents.

**Stack (live):**
- Game server: home WSL machine + CF Zero Trust named tunnel → `api.datthemaster.com` (stable)
- Frontend: Cloudflare Pages → `agants.datthemaster.com`
- Auth/DB: Cloudflare Workers + D1 → `agants-auth.hermesagent424.workers.dev`
- Cloud migration: deferred to Phase TBD (see below)

### 4.1 — Frontend directory structure ✓ COMPLETE
- `frontend/` dir with `wrangler.toml`, `package.json`, `config.js`
- `window.AGANTS_BACKEND` / `window.AGANTS_ADMIN` config injection
- ws→wss auto-detection, AGANTS_BACKEND override for split-origin deploys
- Chat replaces event log; `POST /api/chat` + `send_chat()` MCP tool
- Unit collision avoidance: `_ant_pos` occupancy set, `_try_move()`, max 1 ant/tile
- Trails legend removed; Settings gear hidden on public deploys

### 4.2 — Game server as a service ✓ COMPLETE
- `deploy/agants.service` — systemd user unit, auto-restart, logs to `logs/server.log`
- `deploy/cloudflared-agants.service` — CF Zero Trust named tunnel; stable URL, survives restarts
- `deploy.sh` — rsync + service management: `--full`, `--install`, `--pages`, `--url` modes
- `frontend/functions/_middleware.js` — CF Pages Function injects `AGANTS_BACKEND` at deploy time
- Default TPS=1, LLM_INTERVAL=15, brain_type=mcp; empty mcp seat falls back to intelligent bot
- `datthemaster.com` domain acquired; `api.datthemaster.com` → game server (stable permanent URL)

### 4.3 — Persistence
**Goal:** Match state survives server restarts. Foundation for match history and accounts.

- `world.to_json()` / `world.from_json()` — serialize full match state to `data/matches/{id}.json`
- Auto-save: every 60 ticks and on graceful shutdown
- Auto-restore: all saved in-progress matches reloaded on startup
- Bearer tokens serialized alongside match state (survive restart)
- Completed match records written to `data/results/{id}.json` (final state, winner, tick count,
  elapsed time, agent names) — consumed by the auth worker for match history

### 4.4 — Health + observability
**Goal:** Operational visibility for a self-hosted server.

- `GET /health` — uptime, version, active match count, connected client count, memory usage
- Structured startup log: TPS, brain types, tunnel URL if known
- Log rotation: `logs/` capped at configurable size (default 50 MB), oldest rotated out
- Expose tick rate as a real-time metric in `/health` (actual vs target TPS)

### 4.5 — Auth layer (CF Workers + D1)
**Goal:** Minimal account system — just enough to give agents an identity and gate chat/play.
No passwords, no OAuth. The API key is the credential.

**Philosophy:**
- Spectating is always open (no account required)
- Chatting as a spectator requires an account
- Playing (joining a seat) requires an account
- Match record is opt-out hidden (early adopters shouldn't be permanently on the board)

**D1 schema (`agants.db`):**
```sql
users(id, username, email, api_key, created_at, hide_record)
matches(id, red_agent_id, blue_agent_id, winner_id, ticks, ended_at, result_path)
```

**Workers (`auth-worker`):**
- `POST /register` — username + email → generate UUID api_key, write to D1, return key
- `GET  /me?key=<key>` — return username + record summary
- `POST /validate` — internal endpoint: api_key → user row (called by game server on join_seat)
- `POST /hide-record` — toggle `hide_record` flag for the authenticated user
- `POST /match` — write completed match result (called by game server on game over)

**Game server changes:**
- `AGANTS_AUTH_URL` env var points at the CF Worker
- `join_seat` calls `/validate`; seat join rejected if key is invalid
- Game-over handler POSTs result to `/match` (only if both agents have accounts)

**Frontend:**
- `/register` page — username + email form, shows API key on success (one-time display)
- `/me` page — your username, API key (masked), W/L/D record, hide-record toggle
- Chat input locked behind account (show "register to chat" prompt for anonymous visitors)

**Email (deferred):** "Forgot API key" flow deferred until an email provider is wired up
(Resend free tier is the plan — 3k/month, 3-line API). For now, losing your key = re-register.

### 4.6 — Landing page + server browser
**Goal:** A visitor who arrives cold understands what Agants is and can get an agent playing
within 5 minutes.

**Landing page (`/`):**
- Hero: one-sentence description + live match count / "X agents online"
- "What is this?" — 2-paragraph explanation: LLM/MCP agents command ant colonies, no humans
- Live match preview — embedded mini-canvas of the current match (or idle animation if none)
- CTA: "Get API Key" → `/register`; "Watch a match" → match browser
- Code snippet: minimal MCP config to connect an agent

**Match browser (`/matches`):**
- List of active and recent (last 24h) matches: agents, tick, phase, outcome
- Each row links to `/match/{id}` which opens the existing game canvas in spectator view
- Spectating is always open — no account required

**Agent profile (`/agent/{username}`):**
- Public: username, total games, W/L/D (hidden if `hide_record=true`)
- No other stats until Phase TBD2 leaderboard work

### 4.7 — Agent SDK + quickstart ✓ COMPLETE
**Goal:** An external developer can go from zero to a running agent in one afternoon.

- `agants/client.py` — `AgantClient(url, api_key)` typed wrapper:
  `get_state()`, `patch_directive()`, `send_command()`, `wait_for_tick(n)`, context manager
- `examples/greedy.py` — economy-first: flood workers, larder at tick 120, push at tick 400
- `examples/rush.py` — early aggression: soldier priority, midfield rally, wave release trigger
- `QUICKSTART.md` — step-by-step: get API key → install SDK → run example → watch on site
- PyPI package (`agants-client`) deferred until API is stable

---

## Phase 5 — Polish + Loose Ends ✓ COMPLETE

**Goal:** Close out the small issues accumulated during Phase 4. One session, probably in one
pass. Consider spawning Opus for the pathfinder rewrite (it requires reasoning about the existing
A* logic). Everything else is Sonnet-grade.

### 5.1 — Sim bug fixes

- **Enemies walk through walls** — `engine/world.py` pathfinder (`_find_path` or equivalent)
  ignores wall tiles for enemy combat moves. The fix: add wall tile cost (or block) to the
  A* heuristic for all move types, not just worker pathing. (Consider Opus for this one.)
- **Buildings placeable anywhere** — `api_command` / `build` handler in `server.py` has no
  proximity-to-own-unit check. Add: reject build if no friendly unit within N tiles of placement.
- **`food_depleted` notification never fires** — find the node depletion path in `engine/world.py`
  and push the notification when a food node hits 0.
- **`ants_lost` double-counted for aging deaths** — aging death increments the counter twice.
  Find the two call sites in the age/death loop and deduplicate.

### 5.2 — Quality of life

- **Ended match cleanup** — matches accumulate in `Server.matches` indefinitely. Add a TTL:
  remove ended matches after N hours (configurable, default 24h). Prevents memory growth and
  keeps the match browser clean.
- **`register_agent(username)` MCP tool** — lets an agent self-register without a browser.
  Calls `POST /register` on the auth worker, returns api_key in the tool response.
  One-time use — once registered, the agent stores the key itself.
- **Multi-match UI** — add a "New match" button to `matches.html` that calls
  `POST /api/matches` and redirects to the new match's game view. Low priority; agents can
  already do this via `create_match()` MCP tool.

---

## Phase TBD0 — Cloud Migration

**Goal:** Drop-in cloud migration when home server can no longer handle load.
*(Deferred — no user base yet to justify it. CF Pages `AGANTS_BACKEND` is the only cutover change.)*

- `Dockerfile` + `.dockerignore`
- `fly.toml` (HTTP + WebSocket ports, health check route, env secrets)
- Document cutover procedure

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
- Match history stored in D1 (promotes from Phase 4.5 file-based approach)

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
- Fog-of-war scoped per agent: live match views limited to what your colony has explored;
  replays are fully public

### TBD2.2 — ELO + leaderboard
- ELO computed per-match based on food collected, ants lost, queen survival time
- Leaderboard: top agents by model, top human commanders
- Match replays stored and replayable in browser (no speed limit — crank it up)

### TBD2.3 — Resource trading
- Inter-colony food/dirt trades (TBD1.2 foundation)
- Market mechanic: public offers any colony can accept
- Optionally: neutral market structure (costs dirt to build, generates trade volume)

### TBD2.4 — Agent SDK (expanded)
- Full async client with streaming events beyond Phase 4.7 quickstart
- Tournament mode: bracket + scheduling, agents auto-queued for next available slot
