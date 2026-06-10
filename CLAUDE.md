# Agants — Session Passdown

*This file is Claude's session passdown. The project (repo: Agants) is proudly built
by agents, for agents — human + AI collaboration is the whole point.*

Ant colony RTS/MMO simulation. Two colonies (RED/BLUE) compete on "The Crossing",
a fixed 150×100 3-lane map. LLMs and MCP agents command colonies via a persistent
**directive** system plus direct unit commands.

- **HISTORY.md** — per-session changelog and LLM/agent lessons learned (read when tuning)
- **ROADMAP.md** — Phase 3–5 scope (replaces TRANSITION.md + MMO_PLAN.md)
- **DEVELOPMENT.md** — architecture and contributor guide
- **server.py header** — terse version changelog

**Model dispatch:** Claude Sonnet is the default. Spawn Fable or Opus via the Agent tool
when the task is complex enough to justify it (multi-file architecture, deep reasoning,
long-context review). You may do this without asking the user first.

---

## Current State (2026-06-10, session 22 — Phase 4.6 complete)

**Version policy: VERSION = "0.1.0" — semantic, only bump at real releases.
BUILD = git short hash (set at startup). Never bump VERSION in dev — use the server.py
dev changelog and date-stamp entries instead. Vault note: [[projects/Agants]].**

**Phase 1 (directive system) COMPLETE. Phase 2 (MCP surface) COMPLETE. Phase 3 COMPLETE.**
**Phase 4.1–4.6 all COMPLETE (sessions 20–22). Server routing + CF Pages deployment
pipeline working. Frontend live at `agants.pages.dev`. Next: buy domain → stable URL;
activate auth worker when users arrive.**

**Phase 4.6 frontend (session 21 — this session):**
- **`frontend/landing.html`** — public landing page: hero ("TWO COLONIES. ONE MAP. NO HUMANS.")
  with ant-trail ambient animation on the map's 3 lanes; live stats from `/api/matches` +
  `/health` (graceful "—" when unreachable); **live minimap canvas** that connects to `/ws`
  and renders territory/food/structures/ants at 4px/tile (overlay "AWAITING AGENTS" in lobby);
  MCP config snippet + CTA buttons. Design system: IBM Plex Mono, warm near-black, hairline
  borders, corner-bracketed panels, colony red/blue + amber accents, grid + grain backdrop.
- **`frontend/register.html`** redesigned ("credential issuance") — same endpoints/behavior;
  added Enter-to-submit and a decrypt-style key reveal animation. **Fixed latent bug**: result
  pane used `style.display=""` against a CSS `display:none` rule, so the key was never shown.
- **`frontend/me.html`** redesigned ("service record") — scoreboard W/L/D with win-rate bar,
  key show/hide toggle, record-visibility toggle. Same endpoints. Same display-"" bug fixed.
- **`frontend/matches.html`** — match registry: status dot (running=pulsing/lobby=amber/
  ended=hollow), RED vs BLUE seat names + brain tags, phase, tick, outcome; rows link to
  `/game?match={id}`; refreshes every 5 s (paused when tab hidden); active-first sort.
- **server.py (2 small fixes)**: `/api/matches` now includes `winner` per match;
  `/health` response wrapped in `_api_cors` (Pages frontend couldn't read it cross-origin).
- All pages: nav links assume `/` = landing and `/game` = canvas (routing not yet done);
  every page loads `/config.js` and prefixes fetches with `AGANTS_BACKEND`.
- Verified via Playwright screenshots against a live bot match (minimap rendered real
  territory/ants at tick ~100; all auth states stubbed and checked).

**Phase 4.5 (session 20 — this session):**
- **`auth-worker/`** — new CF Workers + D1 project: `wrangler.toml`, `schema.sql`,
  `src/index.js`. Routes: `POST /register`, `GET /me`, `POST /validate` (internal),
  `POST /hide-record`, `POST /match` (internal). `/validate` + `/match` gated by
  `X-Internal-Secret` header (shared with game server via `AGANTS_AUTH_SECRET` env var).
- **`server.py`** — `AGANTS_AUTH_URL` + `AGANTS_AUTH_SECRET` env vars. `_validate_api_key()`
  async helper (calls `/validate`). `api_join_seat` rejects with 401 if auth is enabled and
  `api_key` is invalid; stores `user_id` in token entry; uses registered username.
  `_save_result` calls `_post_match_result()` (fire-and-forget) with colony user IDs.
- **`frontend/register.html`** — registration form; key shown once, stored in `localStorage`.
- **`frontend/me.html`** — profile page: W/L/D record, API key reveal/copy, hide-record toggle.
- **`frontend/index.html`** — `initChatAuth()`: when auth enabled + no stored key, hides
  chat input and shows "Register to chat" link. Verifies stored key against `/me` on load.
- **`frontend/config.js` + `_middleware.js`** — `AGANTS_AUTH_URL` injected from CF Pages env.
- **Auth is fully optional** — if `AGANTS_AUTH_URL` is unset, everything works open-access.

**Phase 4.4 (session 20 — this session):**
- **`GET /health`** — returns `{status, version, uptime_s, active_matches, connected_clients,
  memory_mb, matches[{match_id, phase, tick, tps_target, tps_actual}]}`. Open endpoint (no auth).
- **`Match._tick_times`** — `deque(maxlen=20)` of monotonic timestamps appended after each
  `world.step()`. `tps_actual = (n-1) / (last - first)` when ≥2 samples; `null` in lobby.
- **Structured startup log** — prints `Agants vX.YZ | TPS=N | LLM_INTERVAL=N | RED=X | BLUE=Y`
  and `tunnel → <url>` if `logs/cloudflared.log` contains a trycloudflare URL.
- **Log rotation** — `Server._rotate_logs()` called at startup; deletes oldest `run_*.log` files
  until `logs/` total is under `LOG_MAX_MB` (default 50, configurable via env var).

**Phase 4.2 (session 19 — this session):**
- **`agants.service`** — systemd user unit for `python3 server.py` on remote WSL machine
  (192.168.1.100:2222). Logs to `logs/server.log`. Auto-restarts on failure.
- **`cloudflared-agants.service`** — systemd user unit for Quick Tunnel
  (`cloudflared tunnel --url http://localhost:8083`). Gives a public `*.trycloudflare.com` URL;
  no domain or CF dashboard routing config required. URL captured in `logs/cloudflared.log`.
- **`tunnel-url.sh`** — extracts current tunnel URL from `logs/cloudflared.log`.
- **`deploy.sh` modes**:
  - (default) sync + restart game server
  - `--full` restart cloudflared too, print new URL
  - `--install` first-time service install + enable
  - `--pages` fetch tunnel URL, update CF Pages `AGANTS_BACKEND`, deploy frontend
  - `--url` print current tunnel URL
- **`frontend/functions/_middleware.js`** — CF Pages Function that intercepts `/config.js`
  requests and injects `AGANTS_BACKEND`/`AGANTS_ADMIN` from Pages env vars. No redeploy needed
  when tunnel URL changes — update the CF Pages env var and done.
- **Live deployment**: game server at
  `https://biography-delivering-eliminate-simpsons.trycloudflare.com` (changes on restart);
  frontend at `https://agants.pages.dev`.
- **Tunnel note**: `<uuid>.cfargotunnel.com` URLs are NOT public (CF-internal only, requires
  WARP). Quick Tunnel (`trycloudflare.com`) is the way to get a public URL without a domain.
  When a domain is acquired: configure public hostname in CF Zero Trust, switch service to
  `cloudflared tunnel run --token $TOKEN`, and that URL becomes stable.

**Phase 4.1 (session 18):**
- **`frontend/` directory** — `index.html` moved to `frontend/`; `server.py` serves from there.
- **`frontend/config.js`** — `window.AGANTS_BACKEND` (empty = same-origin; set to tunnel URL
  for Pages deploy) and `window.AGANTS_ADMIN` (auto-true on localhost, false everywhere else).
- **`frontend/wrangler.toml` + `package.json`** — Cloudflare Pages deploy target.
- **ws/wss auto-detection** — `_wsUrl()` upgrades to `wss://` when served over HTTPS;
  respects `AGANTS_BACKEND` override for split-origin (Pages frontend + tunnel backend).
- **Trails legend removed** — stale since pheromone system was replaced with territory.
- **Event Log → Chat** — sidebar `#events-section` replaced with `#chat-section`; game events
  appear as system messages; agents and spectators can send messages; input + Enter-to-send.
- **`POST /api/chat`** — broadcasts to all WebSocket clients; bearer-auth attributes message
  to colony colour; open for spectators (tech demo mode). WS `chat` in-message type handled.
- **`send_chat(message, colony_id?)` MCP tool** — agents can post to game chat.
- **Settings gear hidden for public** — `#btn-config` hidden by default; shown only when
  `window.AGANTS_ADMIN === true` (auto-set for localhost). Settings are local-dev only.
- **Unit collision avoidance** — `_ant_pos` occupancy set rebuilt each tick; `_try_move()`
  helper; `_move_to()` and `_wander()` prefer unoccupied tiles with occupied fallback to
  prevent deadlock. Verified: max pile = 1 ant/tile at tick 30.

**Future (Phase 4 competitive mode note):** When fair competition actually matters, live match
views should be fog-of-war scoped per agent handler; only replay (post-game) is public. Tech
demo mode for now — all matches fully public. ROADMAP.md Phase TBD2 covers the player portal.

**Phase 3.5 (session 17 — this session):**
- **RECALL implemented** — `military.retreat=true` now fully works: soldiers walk home and
  once within 8 tiles of nest they hold a radius-6 defensive perimeter (8 evenly-spaced slots,
  assigned by `ant.id % 8`). Previously soldiers walked to nest coords but had no arrival
  logic — they'd drift off or get pulled by other directives.
- **`check_alerts()` implemented** — `DirectiveEngine.check_alerts(colony, world)` evaluates
  the directive's `alerts[]` array each tick and pushes `{type: "alert", data: {label}}` to
  the colony notification queue. `sampling=True` → edge-triggered (fires only on False→True
  transition); `sampling=False` → level-triggered (fires every 30 ticks while condition holds).
  Both share the same variable namespace as triggers (all trigger variables valid in alert `if`).
- **`_build_ns()` helper extracted** — shared namespace builder used by both `eval_triggers`
  and `check_alerts`; eliminates duplicate colony-stat calculation.

**Phase 3.4 (session 16):**
- **Per-match `tick_loop(m)` and `llm_loop_for(m, colony_id)`** — all loops now take
  an explicit `Match` param; each match runs fully independently.
- **Per-match `_sim_executor`** — `Match.__init__` creates its own `ThreadPoolExecutor`
  so concurrent matches don't serialize simulation steps.
- **`Match.tps`** — per-match tick rate; `POST /api/matches` accepts `{config: {tps: N}}`.
- **`POST /api/matches`** — creates a new match, starts its tasks, returns `match_id` + `ws_url`.
- **`GET /api/matches/{match_id}`** — info for a specific match.
- **Per-match REST routes** — every colony endpoint now has both a legacy path
  (`/api/state/{cid}`) and a match-scoped path (`/api/matches/{mid}/state/{cid}`).
  Same handlers serve both; match resolved via `_get_match_or_default(req)`.
- **Full `_on_ws_for(req, m)`** — shared WebSocket handler used by both `/ws` and
  `/ws/{match_id}`; all in-message commands (`reset`, `start_game`, etc.) correctly
  target the specific match.
- **`_start_match_tasks(m)`** — helper that starts tick_loop + 2 LLM loops for any match.
  Called in `run()` for the default match and in `api_create_match` for new ones.
- **mcp_server.py**: `create_match(tps?)` tool; `join_seat` accepts optional `match_id`;
  `_match_path(colony_id, path)` helper; all write/read tools automatically use
  match-scoped URLs once a seat is joined.

**Phase 3.3 (session 16 — this session):**
- **`Match` class** — `server.py` introduces `Match` as the per-match state container:
  `world`, `clients`, `llm_memories/stats/logs`, `_pending_strategies`, `_step_in_progress`,
  `_placement_*`, `tokens`, `match_id`, `created_at`.
- **`Server.matches: dict[str, Match]`** — replaces the flat `self.world` / `self.clients` / etc.
  A single default match is created at startup; `Server._default_match_id` tracks it.
- **Backward compat via properties** — `Server.world`, `.clients`, `._tokens`, `.llm_memories`,
  `.llm_stats`, `.llm_strategy_logs`, `._pending_strategies`, `._step_in_progress`,
  `._placement_*` are all properties forwarding to `self._m` (the default match). Zero changes
  to the 50+ existing methods that use these attributes.
- **`GET /api/matches`** now returns each match's `match_id`, `ws_url`, `created_at`,
  plus existing `phase`, `tick`, `seats` fields.
- **`POST /api/seat/{id}`** response now includes `match_id`.
- **`GET /ws/{match_id}`** — new match-scoped WebSocket route; validates match_id,
  routes to default match handler for the single default match.
- **mcp_server.py** — `_colony_match: dict[int, str]` stores `match_id` per colony after
  `join_seat`; cleared on `release_seat`.
- **Architecture note**: multiple concurrent matches are now structurally supported. Phase 3.4
  adds `POST /api/matches` creation and scopes `tick_loop` / broadcasts per-match.

**Phase 3.2 (session 15 — this session):**
- **Bearer token auth** — `POST /api/seat/{id}` issues a UUID token, returned in `{token: "..."}`.
  Stored automatically in mcp_server.py's `_colony_tokens`; no agent bookkeeping required.
- **Write endpoints gated**: `POST /api/command/{id}`, `POST /api/directive/{id}`,
  `DELETE /api/seat/{id}` require `Authorization: Bearer <token>` scoped to the colony.
  Read endpoints (`GET /state`, `/notifications`, `/intel_map`, `/events`) stay open.
- **Token revocation**: cleared on `release_seat`, game reset, or new `join_seat` for same colony.
- **mcp_server.py**: `_auth(colony_id)` helper; all write tools pass auth header transparently.

**Phase 3.1 (session 15 — this session):**
- **server.py wired to engine/** — `from engine.constants import *`, `from engine.colony import ...`,
  `from engine.world import ...`, `from bot import update_bot_strategy`
- **Duplicate code removed** from server.py — reduced from ~5100 to ~2600 lines
  (constants, Ant, DirectiveEngine, Colony, Predator, World, _apply_upgrade_effects, gen_terrain)
- **`Server._update_bot_strategy`** removed; now delegates to `bot.update_bot_strategy` (fixes
  the latent `self.world.structures/tick` bug that was still in the Server method copy)
- **engine/constants.py** `VERSION = "0.1.0"` renamed to `PUBLIC_VERSION` to avoid shadowing
  server.py's internal gameplay `VERSION = "2.10"`
- **Agants rename sweep** — all "Swarm Wars" branding updated across server.py, mcp_server.py,
  index.html, HISTORY.md, README.md, CLAUDE.md, engine/__init__.py, .env.example

**v0.1.0 (session 14):**
- **Public release restructuring** — renamed project "Agants", version bumped to semantic 0.1.0
- **engine/ split** — `engine/constants.py` (pure game constants), `engine/colony.py` (Ant,
  DirectiveEngine, Colony), `engine/world.py` (World, Predator, gen_terrain), `engine/__init__.py`
- **bot.py** — `update_bot_strategy(world, colony_id)` extracted from Server method
- **README.md** rewritten as public-facing ("for agents by agents" framing)
- **ROADMAP.md** created from TRANSITION.md + MMO_PLAN.md (stale originals deleted)
- **DEVELOPMENT.md**, **CONTRIBUTING.md** created
- **.gitignore** updated (logs/, data/, .hermes/ now fully excluded)
- **.env.example** created

**v2.12 (this session, latest):**
- **Auto-build engine-level** — `_assign_builders_to_site()` now called on both structure
  creation paths (guard_post legacy + structure_queue). Also runs every 30 ticks as a
  re-check. Works for all colony types (bot, MCP, LLM) — not just bot. Watchtower built
  at tick 62 without manual command_type verified by Hermes.
- **Build override delivery fix** — workers with `cmd=build` override now deliver carried
  food/dirt first before walking to site. Previously food was lost.
- **Military summary** in `get_state`: `{total_soldiers, fighting, patrolling, idle,
  healthy, wounded, avg_hp_pct, building}`. "Game-changer for decision-making" — Hermes.
- **Bot military: rally → wave** — replaced trickle `auto_attack` (soldiers die 1-by-1)
  with rally hold → mass → coordinated release with `attack_target + siege_priority=queen`.
  Bot now builds guard_posts and defends properly before counterattacking.
- **Stale recruit_target fix** — workers with `recruit_target` >50 tiles away now clear
  it and reselect. Prevents workers from marching 105 tiles to an unreachable node.
- **Advisor: build site warnings** — warns when a structure is >40 tiles from nest
  (workers may be killed en route) or has no workers assigned.
- **cancel_spawn MCP tool + REST endpoint** (v2.11)
- **reserve_food default 150→50, worker 35-tile distance preference** (v2.11)
- **`state` field in REST units, filter_state fixed** (v2.11)

**Session 22 — rename sweep + deployment + routing:**
- **Project rename complete** — all "swarm-wars" refs gone from service files, hermes config,
  scripts, docs; HERMES_TESTS/ and TEMP/ added to `.gitignore`.
- **CF Pages deployment fixed** — `wrangler.toml [vars]` injected with tunnel URL before
  `wrangler pages deploy`, restored to placeholder after. Required because CF Pages Functions
  read env vars baked in at deploy time, not from dashboard at runtime.
  `deploy.sh --pages` handles this end-to-end.
- **`frontend/_redirects`** — `/game` → `/game/` (302) → `index.html` (200); `/` → `landing.html`
  (302) last; specific rules must precede `/` or it acts as a catch-all.
- **Match-watch routing** — `index.html` reads `?match=` URL param and opens `/ws/{match_id}`
  instead of the default `/ws`; clicking a match row on `matches.html` lands the right game.
- **Landing page** — stats filter to `winner == null` only (ended matches excluded); Watch
  nav link removed (matches page is the preferred entry); "Open full view →" → "View matches →".
- **Auth worker** — email field dropped; registration is username-only. Worker code is complete
  but NOT deployed (`AGANTS_AUTH_URL` empty; game runs open-access). See memory file for
  activation steps.
- **`deploy.sh`** — fixed CF API PATCH `"type": "plain_text"` requirement; fixed wrangler
  restore path (absolute `FRONTEND_DIR` computed before `cd`).
- **`server.py`** — `/game/` route added; `v2.14` changelog entry.
- **README.md** rewritten for public-facing state (phases 1–4 complete, 18 MCP tools, deploy story).

**Bug-fix sweep (v2.14 — session 22):**
- mcp_server.py `get_directive` / `list_seats` / `game_control` now target the joined
  match (were hitting the default match via legacy unscoped endpoints)
- `income_per_s` includes larder income + a new +1/tick baseline (via `food_earned_tick`)
- Minimum income: every living colony gains +1 food/tick during running phase
  (engine/world.py `step()`) — a 0-worker, 0-larder colony can never permanently stall
- Triggers support an optional `"else"` block (same patch mechanism as `"then"`, applied
  when the condition is False) — lets a trigger undo its own patches instead of latching
  (e.g. `"else": {"military.retreat": false}` clears retreat once the emergency passes)

**Known remaining issues (Phase 2 polish):**
- Enemies walk through walls (pathfinder ignores walls for combat moves)
- Buildings placeable anywhere (no proximity-to-own-unit check)
- `food_depleted` notification never fired
- `ants_lost` double-counted for aging deaths (cosmetic)

**Deferred to Phase 3+:**
- Fog of war per agent (vision-limited `get_intel_map`)
- Event stream / webhooks (real-time vs polling)
- Replay system, surrender/negotiate protocol (Phase TBD1+)
- Resource trading, large map, MMO colonies (Phase TBD1/TBD2)

**Near-term deferred (no userbase yet):**
- Domain purchase → CF Zero Trust public hostname → stable tunnel URL (replaces trycloudflare)
- Auth worker activation (D1 create, `wrangler deploy`) → see memory file for exact steps
- `register_agent(username)` MCP tool for agent-native self-registration
- Multi-match UI (create match button; currently agents only via `create_match()` MCP tool)
- Ended match cleanup (matches accumulate; no expiry yet)

---

## Run

```bash
cd ~/projects/agants
python3 server.py        # http://localhost:8083 — "NEW GAME" button resets

# MCP agent control:
python3 mcp_server.py            # stdio (Claude Code tool use)
python3 mcp_server.py --port 8084  # HTTP+SSE (remote agents)
```

Logs → `logs/run_TIMESTAMP.log` (full events, LLM reasoning, debrief).
Two-agent game: set both colonies to "MCP Agent" in Settings, START GAME, then each
agent `join_seat(0|1, name)` and drives via tools.

## Files

| File | Role |
|------|------|
| `server.py` | Sim engine + WebSocket server + REST API (~4200 lines, monolith) |
| `mcp_server.py` | FastMCP server — 18 tools for agent colony control |
| `index.html` | Canvas renderer + sidebar + lobby/pause/seat UI |
| `.env` | API keys, model, base URL, brain types, LLM_INTERVAL, TPS |
| `providers.json` | Saved LLM provider configs |
| `HISTORY.md` | Session changelogs + lessons learned |

---

## Architecture

**WebSocket → client:** full world state every tick as JSON.

**Colonies array indices** (`col` in JS):
```
[0] id  [1] nx  [2] ny  [3] food  [4] counts[W,S,sc,Q]  [5] directive
[6] known_food[:10]  [7] events  [8] food_collected  [9] ants_lost
[10] alive  [11] tiers[w,sc,sol]  [12] income_per_s  [13] spawn_queue_summary
[14] aging_soon[W,S,sc]  [15] upg_eta  [16] dirt
```

**Ant tuple:** `[id, x, y, prev_x, prev_y, colony, type, state, carrying, hp, max_hp]`
**Fog:** `state.fog[0/1]` flat 15000 ints (0=dark 1=explored 2=visible)
**Territory:** `state.territory` flat 15000 ints (0=neutral 1=RED 2=BLUE)

**REST API** (`http://localhost:8083/api/`): tick, seats, seat/{id}, control,
state/{id}, notifications/{id}, intel_map/{id}, directive/{id}, command/{id},
events/{id}, matches, feedback. `command` accepts buy_upgrade / build / convert /
unit_command / unit_command_batch.

**Unit overrides** (persist until death or `clear`): move_to, attack_xy (queen-focus),
gather, hold, patrol. Queen cannot be commanded.

---

## Directive Schema

```python
{
  "spawn": {
    "worker":  {"target_ratio": 0.45, "min_ratio": 0.0, "min": 4, "max": 40, "birth_config": {}},
    "soldier": {"target_ratio": 0.35, "min_ratio": 0.0, "min": 2, "max": 30, "birth_config": {}},
    "scout":   {"target_ratio": 0.20, "min_ratio": 0.0, "min": 2, "max": 12, "birth_config": {}},
    "reserve_food": 150,
    "burst_at": 1500,
  },
  "economy": {
    "upgrade_priority": ["scout", "worker", "soldier"],
    "auto_upgrade": True,
    "priority_food": None,     # [x,y] — redirect ALL workers
    "gather_dirt": False,      # True = workers actively prioritize dirt
  },
  "military": {
    "stance": "aggressive", "formation": "wedge",
    "rally_point": None,          # [x,y] or [[x1,y1],...] waypoints
    "rally_release_at": None,     # soldier count to auto-release
    "rally_mode": "normal",       # "normal" | "auto_forward"
    "attack_target": None,        # [x,y] continuous advance
    "auto_attack": False,         # advance using fog-of-war intel
    "retreat": False, "freeze_economy": False,
    "siege_priority": None,       # "queen" → soldiers strongly prefer the queen (CRITICAL for sieges)
  },
  "unit_types": {
    "worker":  {"flee_distance": 4},
    "soldier": {"expansion": [ex, ey]},
    "scout":   {"expansion": [ex, ey], "revisit_pct": 0.12, "patrol_waypoints": None},
  },
  "triggers": [],   # [{label, if, then, priority?, duration?, cooldown?}]
  "alerts": [...]   # evaluated each tick; sampling=True=edge-triggered, False=level (30t rate)
}
```

Flat-key commands (`set_strategy` shim, NOT firable from triggers): `buy_upgrade`,
`build` (`{"type": "watchtower"|"barracks"|"wall"|"larder"|..., "x": N, "y": M}`),
`convert` (`{"id": N, "to": type}` — ant within 8t of queen).

**Trigger variables:**
```
food dirt income_per_s queen_hp queen_hp_pct worker_count soldier_count scout_count
total_pop enemy_soldiers_near_nest soldiers_in_siege soldiers_near_enemy_nest
enemy_queen_hp(siege only) elapsed_ticks aging_workers aging_soldiers enemy_intel_age
```

---

## Key Constants

```python
# Map 150×100 "The Crossing"; RED=(14,50) BLUE=(136,50); ridges x=48-50, x=100-102
# Spawn cost/time: worker=25♦/20t, soldier=50♦/35t, scout=35♦/25t; queue MAX=10
# Barracks soldiers: 20t. Lifespan: W=500t Sol=300t Sc=200t Q=∞
# Combat: SOLDIER_DMG=22 CD=4 HP=200 | QUEEN_HP=900 DMG=35 CD=3 (queen range ~12-15!)
# Food: DELIVER=30 PICK=20; tiers home(cap400,+0.1/t) approach(cap800,+0.5/t) frontline(∞,+20/t)
# Corpse food: W=12 Sol=25 Sc=17. Convert cost: W=15 Sc=22 Sol=30
# Dirt: PICK=10 DELIVER=8 CAP=600; buildings cost dirt:
#   guard_post=150◆(HP300 DMG18 R10 ×3) watchtower=80◆(HP150 vision12 ×3)
#   barracks=200◆(HP200 ×2) wall=25◆(×12) larder=150◆(HP150 +6♦/t ×2)
# Worker saturation: FOOD_NODE_WORKER_CAP={home:4,approach:6,frontline:12}
# Vision: W=4 Sol=5 Sc=[8,12,16,22 by tier] Q=3 (Chebyshev)
# LLM fog: enemy queen HP/pos only when own soldier within 15t of enemy nest
# Stalemate: 7200s (effectively never); games end by queen death or end-command adjudication
```

---

## Design Decisions (do not reverse without reason)

- Workers use `known_food` coords + `recruit_target` committed at selection (not pheromone)
- Soldiers target NEAREST enemy; siege weights queen equally — defenders genuinely
  shield the queen ("bodyguard effect"); `siege_priority="queen"` is the counter
- LLM/agent fog of war: no enemy intel except what units discover; queen HP/pos
  hidden until soldiers within 15t of enemy nest
- Spawn food reserved at queue time, not spawn time
- income_per_s = deliveries only (never negative); no per-tick upkeep
- Buildings cost dirt, not food — building never blocks food economy
- Larder is passive income (no worker overhead) — competes with military for dirt
- Home/approach food finite by design — only frontline nodes sustain late game
- Fixed spawns, no placement phase
- `patrol_waypoints` overrides scout expansion entirely; route must be <180 tiles
  (scout lifespan 200t)
- `enemy_intel_age` = 9999 when never scouted
- Rally hold: soldiers at rally do NOT advance until release threshold

---

## Tuning Guide

Read the log first. Key signals:

| Symptom | Likely cause |
|---------|-------------|
| Siege "deals no damage" | Bodyguard effect — set `siege_priority="queen"`; check `queen_dps_actual` vs `siege_dps_potential` |
| Workers idle en masse (pre-v2.7) | priority_food at depleted node — fixed; now auto-clears |
| "Bot" plays passively, never builds | Seat is brain_type=mcp with no agent — pre-v2.7 ran bare defaults; now falls back to bot |
| Armies never meet | Patrol radius too small or expansion wrong |
| Income low/zero | Workers can't find food; check known_food / viable_food_nodes |
| LLM locked in retreat | eco_emergency trigger missing `elapsed_ticks > 100` (income is 0 first ~60t) |
| Trigger overrides siege push | Add `AND soldiers_in_siege == 0` to eco_emergency |
| Rally deadlock | rally_release_at set with soldier target_ratio=0 — use min_ratio |
| Scout intel blackout | Cohort aging collapse; use patrol_waypoints |
| 0 dirt accumulated | Was the v2.4/v2.5 carrying_type bug — fixed in v2.6; check dirt_per_s |
| Build rejected | Read the error body — insufficient dirt or structure limit |
| Late-game income collapse | Build larders before approach nodes deplete (bot does at tick 300) |
| Scouts single-file dying at enemy base | patrol_waypoints route >180 tiles |

Full lessons-learned archive: **HISTORY.md**.

---

## Session Handoff Protocol

Before ending a heavy session:
1. Update "Current State" above (move superseded detail to HISTORY.md — keep this file lean)
2. Note in-progress work and exactly where it stopped
3. List new bugs discovered
4. New session reads CLAUDE.md → HISTORY.md (if tuning) → ROADMAP.md (if architecting)
