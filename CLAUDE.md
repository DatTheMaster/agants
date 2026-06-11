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

## Current State (2026-06-10, session 29 — income bug fixed, controller hardened)

**Version policy: VERSION = "0.1.0" — semantic, only bump at real releases.
BUILD = git short hash (set at startup). Never bump VERSION in dev — use the server.py
dev changelog and date-stamp entries instead. Vault note: [[projects/Agants]].**

**All phases 1–4 COMPLETE. Project is publicly live and agent-ready.**
- Frontend: `agants.datthemaster.com` (CF Pages)
- Game server API: `api.datthemaster.com` (named CF tunnel, stable)
- Auth worker: `agants-auth.hermesagent424.workers.dev` (D1-backed, live)
- Python SDK: `agants/client.py` + `examples/greedy.py`, `examples/rush.py`
- `QUICKSTART.md` published
- Next: user growth → Phase TBD0 (cloud migration) when home server strains

**Session 23 (2026-06-10 — Phase 4 completion + domain):**
- **`datthemaster.com` domain live** — `api.datthemaster.com` → game server via CF Zero Trust
  named tunnel (replaces trycloudflare Quick Tunnel; URL now stable across restarts).
  `agants.datthemaster.com` → CF Pages frontend.
- **`deploy/cloudflared-agants.service`** updated to `cloudflared tunnel run --token $TOKEN`;
  `AGANTS_TUNNEL_TOKEN` stored in `.env`. `deploy.sh --pages` uses hardcoded stable URL.
- **Auth worker activated** — D1 database `agants` created (id `1b4cf1d4-fad5-4228-b9cc-b6e0347c41e2`),
  schema applied, `INTERNAL_SECRET` set, worker deployed to `agants-auth.hermesagent424.workers.dev`.
  Game server and CF Pages both wired to it (`AGANTS_AUTH_URL` + `AGANTS_AUTH_SECRET` in `.env`).
- **`agants/client.py`** — `AgantClient(url, api_key)` typed SDK: `join_seat`, `release_seat`,
  `get_state`, `get_directive`, `patch_directive`, `send_command`, `wait_for_tick`, `health`,
  `list_matches`, `get_notifications`, `send_chat`, `start_game`; context manager for teardown.
- **`examples/greedy.py`** — economy-first: 65% workers, larder at tick 120, push at tick 400,
  eco-emergency retreat trigger.
- **`examples/rush.py`** — early aggression: 55% soldiers, rally at midfield ridge (x=73/77),
  wave-release trigger at 10 soldiers, rebuild-on-wipe logic.
- **`QUICKSTART.md`** — full zero-to-agent doc: key → install → run → watch; directive schema
  reference, trigger variables, command examples, map constants.
- **"claude" API token** — single Cloudflare token with tunnel + D1 + Workers Scripts + Pages
  scope. Stored as `CLOUDFLARE_API_TOKEN` in `.env` and `~/.config/.wrangler/token`.

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

**Session 24 (2026-06-10 — Phase 5 complete):**
All 7 Phase 5 items delivered:

*Sim bug fixes (5.1):*
- **A* pathfinder** — `engine/world.py` `_move_to` now uses `_astar_step` (Opus-written): finds
  the optimal first step around wall lines (max 200 nodes, falls back to greedy). Diagonal
  corner-cutting through wall gaps is blocked. `import heapq` added. Greedy fallback preserved.
- **Build proximity check** — `server.py` build handler rejects placements where no friendly ant
  is within 30 tiles; returns 400 with clear error.
- **`food_depleted` notification** — `_deplete_food(f)` helper added to World; replaces all 4
  `self.foods.remove(f)` call sites. Notifies any colony with the node in `known_food`.
- **`ants_lost` double-count** — removed the spurious `c.ants_lost += 1` from the aging loop;
  `_kill()` was already incrementing it.

*Quality of life (5.2):*
- **Match TTL** — `Match.ended_at` field added (set in `_save_result`); `_match_cleanup_loop`
  coroutine prunes ended matches older than `MATCH_TTL_H` hours (default 24, env-configurable).
  Runs hourly, skips the default match.
- **`register_agent(username)` MCP tool** — added to `mcp_server.py`; calls `POST /register`
  on `AGANTS_AUTH_URL`; returns `{username, api_key}`. Gracefully returns an info message when
  auth is disabled. `import os` moved to top of file.
- **"New match" button** — `frontend/matches.html` header now has a styled `+ New match` button
  that calls `POST /api/matches` and redirects to `/game?match=<id>` on success.

**Session 24 continued — presence endpoint + controller plan:**
- **`GET /api/agents/online`** — returns agents active within `PRESENCE_TIMEOUT_S` seconds
  (default 90, env-configurable). Presence tracked in `Server._presence` (dict keyed by token).
  Touched on: `api_join_seat`, `_require_token` (all write endpoints), `api_state` and
  `api_notifications` (if Authorization header present). Pruned on `_revoke_colony_token` and
  on each `api_agents_online` call. Response: `{agents:[{agent,user_id,match_id,colony_id,colony,seconds_ago}], count, timeout_s}`.
- **`get_agents_online()` MCP tool** added to `mcp_server.py`.
- **`register_agent(username)` MCP tool** added (calls `POST /register` on auth worker).

**Session 29 (2026-06-10 — income bug fixed, controller hardened):**
- **`income_per_s` double-call fix** — session 28 added `logger.tick()` to `tick_loop` but
  never removed the existing call inside `world.step()` (`engine/world.py`). At tick%10 both
  fired: first call correctly set `income_per_s = food_earned_tick`, second call overwrote it
  with 0 (bucket already cleared). Fixed: removed the `logger.tick()` call from `world.step()`;
  server's `tick_loop` is now the sole caller. Verified: income jumped from 0 to 276/s when
  workers hit frontline node; LLM no longer panic-retreats due to false 0-income.
- **Controller error recovery** — when server restarts, controller held stale `match_id`/
  `token` in a permanent error loop. Fixed: `agent_loop` exits after 8 consecutive fetch
  errors; `auto_challenge_loop` clears `colony_id`/`token`/`match_id` after 5 consecutive
  errors and re-enters matchmaking.
- **Forfeit detection** — `agent_loop` never exited after forfeit because production server
  keeps phase="running" and `queen_alive=True` even after a forfeit winner is set. Fixed:
  `do_forfeit()` sets `client.forfeited=True`; game_over check now includes
  `getattr(client, "forfeited", False)`. Flag cleared on `client.release()`.
- **Controller system prompt improvements:**
  - Added `economy.gather_dirt true/false` to directive levers section
  - Hard rule #3 (larder by tick 200) now says "Set gather_dirt=true to accumulate dirt.
    If dirt=0 at tick 80+, set it immediately."
  - `unit_command` tool description now says "gather (workers only)" and warns against using
    it on scouts/soldiers or dirt deposit coords
  - State message adds `NOTE: income_per_s reads 0 but food_collected shows workers ARE
    delivering` when income=0 and food_collected>50 at tick>80 (residual protection)
  - `idle_workers` line now includes tick number so LLM knows IDs may be stale
  - System prompt warns: only use ant IDs from idle_workers list, never guess/increment
- **Deployed** to `api.datthemaster.com` via `deploy.sh`.

**Session 28 (2026-06-10 — controller complete, sim bug fixes):**
- **`income_per_s` always 0 (root cause)** — `RunLogger` was never instantiated since the
  session-16 multi-match refactor. `RunLogger.tick()` updates `income_per_s` every 10 ticks
  and writes `logs/run_*.log`; without it the metric was 0 forever. Fixed: `RunLogger` now
  created in `_run_placement_phase`; `logger.tick()` called from `tick_loop` after each step.
  Also restores run log files and LLM debrief logging (both silently broken since session 16).
- **Worker delivery radius widened** — delivery check was Chebyshev ≤ 2; all returning workers
  converged on the exact nest center. At TPS=1 with 50+ workers, RED nest at x=14 created a
  bidirectional traffic jam that caused workers to orbit the nest without delivering. Changed to
  Manhattan ≤ 5 across all four delivery paths (worker recruit_target, worker no-target,
  build-override, scout). Workers now deliver before reaching the congested center.
- **Controller fully wired**: `[n]` creates+joins only (no auto-start); `[s]` starts + launches
  agent loop; `[l]` leave; `[f]` forfeit; `[a]` auto-challenge mode; context-aware footer.
- **Auto-challenge mode** — permanent challenger: finds/creates match, waits for real opponent,
  auto-starts when both seated, loops after game over. Runs indefinitely until `q`.
- **`submit_feedback` LLM tool** — controller exposes it; `agent_loop` calls it on game over.
- **Game-over feedback hook** — detects `phase != running` or `alive=False`, makes one final
  LLM call asking for `submit_feedback`, then exits cleanly.
- **Chat attribution fixed** — `api_chat` was using old `{cid: token}` iteration; current
  format is `{token: {dict}}`. Fixed to `m.tokens.get(token)`.
- **Chat wall-clock timestamps** — HH:MM:SS instead of in-game elapsed "00:00" format.
- **Larder min-distance** — build handler rejects larder within 20 Manhattan tiles of own nest.
- **Pause/End buttons** gated to `window.AGANTS_ADMIN`; public spectators can't pause games.
- **`/api/matches/{id}/forfeit`** endpoint added; requires Bearer token scoped to the colony.
- **TPS from config** — `~/.config/agants/config.json` `"tps": N` used for `[n]` and auto-challenge.
- **server.py v2.15** (delivery radius + RunLogger).

**Session 27 (2026-06-10 — controller polished, auth fixed):**
- **`_write_env` root-cause fix** — every `api_join_seat` call triggered `_save_config` →
  `_write_env` which fully overwrote `.env`, silently wiping `AGANTS_AUTH_URL` and
  `AGANTS_AUTH_SECRET`. Fixed: `_write_env` now reads existing file, preserves any lines
  whose keys aren't in `_MANAGED_KEYS`, and re-emits them under `# Other settings`.
- **`AGANTS_AUTH_URL` + `AGANTS_AUTH_SECRET` on remote server** — these are NOT synced by
  `deploy.sh` (`.env` excluded from rsync by design). They were pushed manually to the remote
  `.env`. The `_write_env` fix means they now survive restarts. If the remote `.env` is ever
  recreated from scratch, re-add both lines manually via SSH.
- **Auth enforced on seat join** — bad/missing API key returns 401. Registered username
  (`DatTheMaster` etc.) is returned in join response and stored as `client.agent_name`.
  No config field needed — controller reads name from server response.
- **`POST /api/agents/heartbeat`** — accepts `{api_key}`, validates via auth worker, marks
  agent present as `lobby:{user_id}`. Controller poller pings every 30s so agents appear
  in the online list before joining any match.
- **`api_agents_online`** — `colony` field now handles `null` colony_id (unseated agents).
  Unseated agents visible in TUI online panel in yellow with "lobby" label.
- **Full TUI join** — `r`/`b` keys join highlighted lobby as RED/BLUE directly. No text
  prompts. Dead code removed: `ask()`, `submit_prompt()`, prompt fields on `UI`.
- **Lobby-only match list** — controller shows only `phase==lobby` matches with no winner.
- **`DELETE /api/matches/{id}`** — removes an empty lobby match immediately.
- **Empty lobby TTL** — `_match_cleanup_loop` now runs every 5 min and prunes empty lobbies
  (no seated agents) older than `EMPTY_LOBBY_TTL_M` minutes (default 30).
- **System prompt rewritten** — 4 hard rules with exact directive syntax: (1) retreat cancels
  attack, (2) rally before attacking, (3) larder by tick 200, (4) siege_priority=queen.
- **`unit_command` schema fixed** — tool description now shows flat `command` key (not nested
  `override` dict); system prompt reinforces it.
- **`idle_workers` in state message** — up to 6 idle workers with real ant IDs and positions
  so LLM can issue valid `unit_command` calls.

**Session 26 (2026-06-10 — controller agent loop verified):**
- **Per-match brain types** — `POST /api/matches` now accepts `{"config": {"brains": {"0":"mcp","1":"bot"}}}`.
  `Match.match_brains` overrides global `RED_BRAIN`/`BLUE_BRAIN` for that match. All brain lookups
  in `tick_loop` and `llm_loop_for` use new `_brain_for_match(m, cid)` helper; global `_brain_for`
  retained for backward compat. `api_get_match` seats also reflect per-match types.
- **`GameClient.start_game()`** — calls `POST /api/matches/{match_id}/control {"action":"start"}`.
- **`do_new()` fully wired** — creates match with `brains={"0":"mcp","1":"bot"}`, joins RED,
  starts game, launches agent loop. `n` key now creates a full bot-vs-controller match in one press.
- **`s` key** — starts the current lobby match (`do_start()`); useful after `j` on an existing match.
- **`unit_command` schema fixed** — tool description previously showed `"override":{"type":"..."}` but
  server reads flat `command` key. Fixed in tool description + system prompt. Also added `idle_workers`
  list to `format_state_message` so LLM uses real ant IDs instead of guessing.
- **Agent loop verified** — headless test confirms LLM reads state, reasons, calls `patch_directive` and
  `send_command` with correct structure. Bot opponent active on BLUE, game advances at 1 TPS.

**Session 25 (2026-06-10 — controller TUI built):**
- **`controller/controller.py`** — standalone ~680-line script, zero game-server imports.
  Talks to the game server over REST only. Distributable standalone with own `requirements.txt`.
- **`controller/requirements.txt`** — `openai>=1.0`, `rich>=13.0`, `httpx>=0.25`
- **`controller/README.md`** — install/configure/run docs, keyboard reference, tool list
- **`controller/config.example.json`** — example config shape
- **`--setup` wizard** — writes `~/.config/agants/config.json` interactively; local
  `./controller.json` takes precedence. Config: `game_url`, `api_key`, `llm.{base_url,api_key,model}`.
- **`--headless MATCH:COLONY`** — non-TTY mode for CI/pipes; prints agent log to stdout.
- **Rich TUI** — raw TTY + asyncio `loop.add_reader` keyboard (no blocking threads).
  Left: match browser (list + online agents). Right: colony state (tick/food/army/income/events).
  Bottom: agent log (last N lines of LLM reasoning + tool calls). Footer: key legend.
  Redraw loop: `\033[H` home + `console.capture()` + direct stdout write (no `rich.Live`).
- **Key fixes made during session:**
  - `agent_name` (not `name`) in join POST body — matched server expectation
  - Empty `api_key` crash in OpenAI client — patched with `or "no-llm-key-set"` fallback
  - Terminal corruption on exit — caused by raw-mode thread whose `finally` never ran;
    fixed by moving all TTY restore to main `finally` block with `loop.add_reader`
  - `tty.setraw()` disables `OPOST` flag → `\n` no longer translates to `\r\n` → every
    Rich output line starts at the wrong column. Fixed by re-enabling `OPOST | ONLCR`
    immediately after `setraw`.
  - Layout footer/log invisible — root `Layout(size=N)` ignored by `console.print()`;
    `top` section consumed all terminal lines. Fixed: `top` uses `ratio=1`, log+footer
    use fixed `size`. Now fills terminal height exactly.
- **Status:** TUI renders correctly; match list visible; join works (verified on website);
  key legend visible; `n` creates new match. **Agent loop not yet verified end-to-end**
  — no path to start a game against a bot from the controller yet (see next session).

**Next session priorities:**
- **Watch a full game to completion with income working** — income_per_s is now accurate;
  run a full game and verify the LLM makes better economic decisions (larder by tick 200,
  no false panic-retreats from income=0). Review feedback from production server logs (SSH
  to desktop and check `~/projects/agants/logs/agent_feedback.jsonl` and `logs/run_*.log`).
- **Gather_dirt reliability** — current game shows LLM turns off gather_dirt when confused
  (tick 279). Consider adding a trigger: "if dirt < 50 AND elapsed_ticks < 200, set gather_dirt=True"
  to the LLM's suggested starter directives in the system prompt.
- **Stale ant IDs** — LLM still guesses/increments ant IDs despite the system prompt warning.
  Root fix: remove `idle_workers` from state message entirely (directive-based play is better),
  OR add a server-side `unit_command_batch` that accepts a type+command rather than specific IDs.
- **Presence → invites** — online panel shows unseated agents in lobby. Next: `i` key → POST
  invite to another agent's user_id via `POST /api/agents/invite`; delivered via notifications.
- **Forfeit tool for `mcp_server.py`** — server endpoint exists (`POST /api/matches/{id}/forfeit`),
  controller has `[f]`; add the MCP tool if MCP agents need it.

**Next session — TBD0 (cloud migration) or TBD1 (MMO engine) when load warrants.**
If userbase grows: check home server load first (use `/health` actual vs target TPS).

**Deferred beyond Phase 5:**
- Fog of war per agent, event stream, replay system (Phase TBD1+)
- Resource trading, large map, MMO colonies (Phase TBD1/TBD2)
- Cloud migration / Fly.io (Phase TBD0 — wait for load)
- PyPI package (`agants-client`) — wait for API stability

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
| `controller/controller.py` | Standalone AI agent + Rich TUI (no server imports) |
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
