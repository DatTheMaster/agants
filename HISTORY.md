# Agants — Session History

Per-session changelog and agent lessons, moved out of CLAUDE.md to keep it lean.
Newest sessions first where possible. See server.py header changelog for the terse version.

**Session 31 (2026-06-11 — graphics overhaul):**
- **Terrain texture** — `frontend/index.html` terrain buffer init replaced. Old: flat TCOL palette + ±12px `Math.random()` noise. New: seeded `hash2`/`noise2` (deterministic, coordinate-based) with per-tile-type procedural treatment. Two-pass approach: ImageData pixel loop for base texture, then Canvas 2D overlay pass for pebbles, cracks, grass tufts, water ripples, rock fissures, nest arc marks. Tile-boundary darkening at rock/water edges. Zero per-frame cost (drawn once to `terrainBuf` at init).
- **Segmented ant bodies + OffscreenCanvas cache** — replaced `drawWorkerShape(sz)` / `drawSoldierShape(sz)` / `drawScoutShape(sz)` with new ctx-first signatures. Each type now has 3-segment body (abdomen/thorax/head), curved legs, antennae; soldiers have mandibles scaling by tier; scouts have elongated body + long swept antennae. `initAntCache()` builds 24 OffscreenCanvas objects (types 0–2 × 2 colonies × 4 tiers) at game init; T2/T3 glow baked in. Per-frame: 1 `drawImage` per ant instead of 5–8 arc/path calls. Queen stays procedural (pulses with `now`).
- **Unit type color coding** — Highlights differentiate types on both colony colors: worker=green (`rgba(80,220,80,...)`), soldier=black (`rgba(20,20,20,0.85)`) armor ridges, scout=white dot+antennae (unchanged). Color coding visible at all sizes where ant marks are legible.
- **Scout vision bubble** — faint colony-colored `arc` drawn at render time (not cached, too large). Radius = `SCOUT_VR[tier] * TS` = 64/96/128/176px for tiers 0–3. Functions as both type badge and tier indicator.
- **Dying-ant cache fix** — `dyingAnts` entries now include `colIdx` and `tier` so fade-out correctly uses the cached canvas instead of the old procedural shape call.
- **Fable used for** — terrain generator (seeded noise + per-tile overlay art direction) and ant shape system (segmented anatomy, tier differentiation logic, caching architecture). Both spawned in parallel.

**Lessons learned (session 31):**
- Anatomical detail (legs, antennae, segments) at 5–14px is effectively invisible; iconographic silhouettes or strong color/mark language are the right tool for unit differentiation at this tile scale.
- OffscreenCanvas cache with baked shadowBlur is dramatically cheaper than per-frame shadowBlur on N ants; baking T2/T3 glow at init eliminates the biggest per-frame Canvas 2D cost.
- Fable handles procedural art-direction code (noise functions, overlay mark density/placement) better than Sonnet — the terrain generator came back nearly production-ready.
- Scout vision bubble as a type+tier indicator is a clean design pattern: one element communicates two things without extra UI.

**Session 28 changes (2026-06-10 — controller polish, gameplay bug fixes):**
- **Chat attribution fixed** — `api_chat` was iterating `m.tokens` as `(cid, token_str)` pairs
  (old format); current format is `{token_str: {colony_id, agent, user_id}}`. Fixed to
  `entry = m.tokens.get(token)`.
- **Chat match-scoped path** — controller `send_chat` now calls `/api/matches/{id}/chat`;
  added that route to server.py so non-default matches work correctly.
- **Double presence fixed** — `api_agents_online` now groups by `user_id`, preferring seated
  over lobby heartbeat, more recent over stale; never downgrades seated→lobby within same user.
  Also fixed `api_join_seat`: sets `user_id = api_key[:8]` when auth is disabled (was None,
  causing grouped dedup to fail).
- **Larder min-distance** — build handler rejects larder placement within 20 Manhattan tiles of
  own nest; returns HTTP 400 with clear error. Prevents the near-nest worker stall pattern seen
  in earlier games (larder at HP 1 with 8-11 workers permanently stuck in S_BUILDING).
- **Frontend lobby text** — updated from "Configure brain types in Settings..." to "Waiting for
  agents to join seats. Press ▶ START GAME when ready."
- **Pause/End gated to admin** — `_updateGameControls` in index.html checks `window.AGANTS_ADMIN`;
  public spectators no longer see Pause/End buttons.
- **`[n]` no longer auto-starts** — `do_new()` creates + joins only; log says "press [s] to start".
  Previously also called `start_game()` and `start_agent()`, auto-launching the match.
- **`[s]` starts agent loop** — `do_start()` calls `start_agent()` after `start_game()` succeeds.
- **`[l]` leave seat** — cancels agent_task, calls `client.release()`, returns to lobby browser.
- **`[f]` forfeit** — calls `POST /api/matches/{id}/forfeit`; server sets winner = 1 - cid and
  broadcasts a system chat message.
- **`/api/matches/{id}/forfeit` endpoint** — added to server.py; requires Bearer token.
- **Context-aware footer** — three states: auto-challenge active, seated, or lobby browser.
- **`submit_feedback` LLM tool** — controller exposes `submit_feedback(feedback, category)` as
  an OpenAI tool; server logs to `logs/agent_feedback.jsonl`.
- **Game-over feedback hook** — `agent_loop` detects `phase not in (lobby, running)` or
  `alive=False`, makes one final LLM call prompting `submit_feedback`, then exits cleanly.
- **Auto-challenge mode (`[a]`)** — permanent challenger loop: finds/creates a match with open
  seats, waits for real opponent, auto-starts when both seated, loops after game over.
- **Chat wall-clock timestamps** — `onChatMessage` captures `new Date()` at receipt time;
  renders as HH:MM:SS instead of in-game elapsed time.
- **TPS from config** — `do_new()` and auto-challenge read `cfg.get("tps")` and pass to
  `new_match()`; add `"tps": N` to `~/.config/agants/config.json`.
- **`income_per_s` always 0 (v2.15 fix)** — `RunLogger` was defined but never instantiated.
  `RunLogger.tick()` (which updates `income_per_s`) was therefore never called; the metric
  showed 0 for every game since the session-16 multi-match refactor. Fixed: `RunLogger`
  instantiated in `_run_placement_phase`, `logger.tick()` called from `tick_loop` each step.
  Also restores `logs/run_*.log` files and LLM debrief logging (both silently broken).
- **Worker delivery radius widened (v2.15 fix)** — delivery check was Chebyshev ≤ 2 (5×5 box);
  all returning workers converged on the exact nest center. With 50+ workers and the RED nest
  near the western edge (x=14), this created a bidirectional traffic jam — workers orbited the
  nest without delivering. Changed to Manhattan ≤ 5 in all four delivery paths (worker
  recruit_target, worker no-target, build-override, scout). Workers now deliver before reaching
  the congested center and exit without crossing incoming traffic.

**Session 28 lessons learned:**
- **RunLogger silent disconnect** — the session-16 Match refactor moved tick logic to per-match
  tasks but left RunLogger uninstantiated. The class stayed in the file with all its callers
  using `if world.logger:` guards — so everything silently no-oped. Keep per-game state (logger,
  income tracking) tied to the `_run_placement_phase` → `tick_loop` lifecycle, not to a top-level
  object that the refactor might orphan.
- **Delivery radius and map geometry** — the Chebyshev-2 delivery zone was fine at TPS=10 with
  ~20 workers; at TPS=1 with 50+ workers and a nest at x=14, it became a chokepoint. Map-edge
  nests have asymmetric exit geometry (workers can only flow in one direction); delivery radius
  should be generous enough that workers deliver before entering the dense center.
- **`m.tokens` format** — changed in session 27 from `{cid: token_str}` to `{token_str: {dict}}`.
  Any code that iterates `m.tokens.items()` and compares values as token strings will silently
  fail (always miss). Always use `m.tokens.get(token)` for lookups.

**Session 23 changes (2026-06-10 — Phase 4 completion + domain):**
- **`datthemaster.com` domain live** — `api.datthemaster.com` → game server via CF Zero Trust named tunnel (stable across restarts). `agants.datthemaster.com` → CF Pages frontend.
- **`cloudflared-agants.service`** updated to `cloudflared tunnel run --token $TOKEN`; `AGANTS_TUNNEL_TOKEN` in `.env`. `deploy.sh --pages` uses hardcoded stable URL.
- **Auth worker activated** — D1 database `agants` (id `1b4cf1d4-fad5-4228-b9cc-b6e0347c41e2`), schema applied, `INTERNAL_SECRET` set, deployed to `agants-auth.hermesagent424.workers.dev`. Game server + CF Pages wired via `AGANTS_AUTH_URL` + `AGANTS_AUTH_SECRET` in `.env`.
- **`agants/client.py`** — `AgantClient(url, api_key)` typed SDK: `join_seat`, `release_seat`, `get_state`, `get_directive`, `patch_directive`, `send_command`, `wait_for_tick`, `health`, `list_matches`, `get_notifications`, `send_chat`, `start_game`; context manager for teardown.
- **`examples/greedy.py`** — economy-first: 65% workers, larder at tick 120, push at tick 400.
- **`examples/rush.py`** — early aggression: 55% soldiers, rally at midfield, wave-release trigger.
- **`QUICKSTART.md`** — full zero-to-agent doc: key → install → run → watch.
- **"claude" CF API token** — tunnel + D1 + Workers Scripts + Pages scope. Stored as `CLOUDFLARE_API_TOKEN` in `.env` and `~/.config/.wrangler/token`.

**Sessions 14–21 changes (archived from CLAUDE.md):**

*Session 21 (Phase 4.6 — frontend redesign):*
- `frontend/landing.html` — public landing page with ant-trail animation, live stats, live minimap canvas (WebSocket, 4px/tile). Design: IBM Plex Mono, warm near-black, hairline borders, colony red/blue + amber accents.
- `frontend/register.html` redesigned ("credential issuance") — Enter-to-submit, decrypt-style key reveal. Fixed latent bug: `style.display=""` against `display:none` rule.
- `frontend/me.html` redesigned ("service record") — W/L/D scoreboard, win-rate bar, key toggle.
- `frontend/matches.html` — match registry with status dots, seat names, brain tags, 5s refresh.
- Server: `/api/matches` includes `winner`; `/health` wrapped in `_api_cors`.

*Session 20 (Phase 4.4–4.5 — health endpoint + auth worker):*
- `GET /health` — `{status, version, uptime_s, active_matches, connected_clients, memory_mb, matches[...]}`. `Match._tick_times` deque drives `tps_actual`.
- Log rotation: `Server._rotate_logs()` at startup; keeps `logs/` under `LOG_MAX_MB` (default 50).
- `auth-worker/` — CF Workers + D1: `POST /register`, `GET /me`, `POST /validate`, `POST /hide-record`, `POST /match`. `/validate` + `/match` gated by `X-Internal-Secret`.
- Auth fully optional — `AGANTS_AUTH_URL` unset = open-access.

*Session 19 (Phase 4.2 — systemd + cloudflared deployment):*
- `agants.service` / `cloudflared-agants.service` — systemd user units; logs to `logs/server.log`.
- `deploy.sh` modes: default (sync+restart), `--full`, `--install`, `--pages`, `--url`.
- `frontend/functions/_middleware.js` — CF Pages Function injecting `AGANTS_BACKEND`/`AGANTS_ADMIN` from env vars at request time.

*Session 18 (Phase 4.1 — frontend directory + chat):*
- `frontend/` directory; `server.py` serves from there. `config.js`: `AGANTS_BACKEND` + `AGANTS_ADMIN`.
- `wrangler.toml` + `package.json` — CF Pages deploy target. `wss://` auto-detection.
- Event Log → Chat: `#chat-section`, `POST /api/chat`, `send_chat()` MCP tool.
- Settings gear hidden for public (`window.AGANTS_ADMIN`). Unit collision avoidance (`_ant_pos` set).

*Session 17 (Phase 3.5 — RECALL + alerts):*
- RECALL: `military.retreat=true` soldiers walk home → radius-6 perimeter (8 slots by `ant.id % 8`).
- `check_alerts()`: evaluates `alerts[]` each tick; `sampling=True` = edge-triggered, `False` = level (30t rate).
- `_build_ns()` shared namespace builder for both `eval_triggers` and `check_alerts`.

*Sessions 15–16 (Phase 3.1–3.4 — engine split + multi-match architecture):*
- `server.py` wired to `engine/` (constants/colony/world); duplicate code removed (5100→2600 lines).
- `Match` class: per-match state container (`world`, `clients`, `tokens`, `_pending_strategies`, etc.). `Server.matches: dict[str, Match]`. Backward compat via properties forwarding to `self._m`.
- Per-match `tick_loop(m)` + `llm_loop_for(m, cid)` + `_sim_executor`; `Match.tps` per-match tick rate.
- `POST /api/matches` creates new matches; match-scoped REST routes + `GET /ws/{match_id}`.
- Bearer token auth: `POST /api/seat/{id}` issues UUID token; write endpoints gated by `Authorization: Bearer`; revoked on `release_seat` / reset / re-join.

*Session 14 (v0.1.0 — public release):*
- Project renamed to "Agants"; `VERSION = "0.1.0"` semantic.
- engine/ split: `constants.py`, `colony.py` (Ant/DirectiveEngine/Colony), `world.py` (World/Predator/gen_terrain), `__init__.py`.
- `bot.py` — `update_bot_strategy(world, colony_id)` extracted.
- `README.md` rewritten public-facing; `ROADMAP.md` from TRANSITION.md + MMO_PLAN.md; `DEVELOPMENT.md`, `CONTRIBUTING.md`, `.env.example` created.

**Session 22 changes (2026-06-10 — rename sweep + CF Pages deployment + routing):**
- **Rename sweep complete** — all remaining "swarm-wars" refs removed from service units,
  hermes config (`~/.hermes/config.yaml` key renamed), tunnel-url.sh, deploy.sh, docs.
  HERMES_TESTS/ and TEMP/ added to `.gitignore`.
- **CF Pages deployment pipeline** — fixed env var injection: CF Pages Functions read vars
  baked into the Worker bundle at deploy time, NOT from the dashboard at runtime. Solution:
  write `wrangler.toml [vars]` with live tunnel URL before `wrangler pages deploy`, then
  restore the placeholder. `deploy.sh --pages` handles end-to-end.
- **`deploy.sh` two bugs fixed**: CF API PATCH needs `"type": "plain_text"` on every var
  (silently ignored without it); wrangler restore path was relative and broke after `cd`.
  Fixed with absolute `FRONTEND_DIR` computed before any directory change.
- **`frontend/_redirects`** — routing rules must be ordered: specific before `/`. Final:
  `/game` → `/game/` (302), `/game/` → `index.html` (200), `/` → `landing.html` (302) last.
  Root rule acts as catch-all if placed first — was routing `/game?match=…` to landing.
- **Match-watch routing** — `index.html` reads `?match=` URL param and connects to
  `/ws/{match_id}`; clicking a row in `matches.html` now loads the correct match canvas.
- **MCP bridge match routing** — `get_directive`, `list_seats`, `game_control` were all
  hitting the default match via unscoped legacy endpoints. Fixed to use `_colony_match`
  dict + `_match_path()` helper so they correctly target the joined match.
- **Minimum income** — `engine/world.py step()`: `c.food += 1; c.food_earned_tick += 1`
  per living colony each tick (running phase only). Prevents complete stall with no workers
  and no larders; `income_per_s` now reflects this floor.
- **Trigger `else` clause** — `engine/colony.py DirectiveEngine`: when condition is False
  and `"else"` key exists in trigger, apply the `else` dict as patches. Lets triggers undo
  their own state changes (e.g. `"else": {"military.retreat": false}`) instead of latching.
- **Auth worker** — email field dropped; registration is username-only. Worker code is
  complete in `auth-worker/` but was never deployed. `AGANTS_AUTH_URL` empty → open-access.
- **Landing page** — stats count only active matches (`winner == null`); Watch nav removed;
  "Open full view →" changed to "View matches →". MCP snippet updated to stdio style.
- **`server.py`** — `/game/` route added (CF Pages 308 redirect means `/game` → `/game/`
  before _redirects fires; server must serve both).
- **README.md** — full rewrite for public-facing state.

**Session 22 lessons learned:**
- CF Pages env vars set via the dashboard API appear in `wrangler pages deployment list`
  metadata but are NOT injected into Workers during execution — they're compile-time bindings.
  The only reliable path is `wrangler.toml [vars]` (or `--env`/`--var` CLI flags) at deploy.
- CF `_redirects` rules are evaluated in document order; `/` will match every path if placed
  first, silently defeating all more-specific rules that follow it.
- Hermes false positives: `***` literal in CI output was flagged as a leaked token — it was
  the shell `"${CF_TOKEN}"` echo. Validate false positives before chasing leaks.
- MCP bridge `_colony_match` dict is the source of truth for which match an agent is in.
  Any tool that mutates or reads match state must pass through `_match_path()`.

**Session 3 changes:** no-upkeep economy, corpse food, unit conversion, guard post bot logic,
fog-of-war fixes, food depletion display, min_ratio floor, waypoints, auto-forward rally,
auto-attack, enemy queen HP hidden until sieging.

**Session 4 changes:** zoom/pan clamping (min 0.7×fitScale, max 6×, 80px margin);
scout trail visibility (deposit 0.3→0.6, PWEIGHT 0.35→0.65); territory trail PWEIGHT 0.45→0.60;
trigger `priority` field documented in LLM system prompt with `eco_emergency AND soldiers_in_siege==0` pattern.

**Session 5 changes (v1.4):**
- Trigger event log — fired triggers recorded to `colony.trigger_log` (deque 30); shown in LLM prompt as `TRIGGER EVENTS: tick 352: [eco_emergency] → military.retreat=True`
- Upgrade ETA — `_upgrade_next()` returns `food_short` + `eta_s`; shown in prompt income line (`ETA: WOR:500♦ ~12s`) and sidebar col[15]
- Scout `patrol_waypoints` — `unit_types.scout.patrol_waypoints: [[x1,y1],...]`; scouts loop through coordinates indefinitely, ignoring expansion when set; `ant.patrol_idx` tracks current waypoint
- Per-colony visual fog-of-war — `Colony.fog_explored` (bytearray 7500) + `Colony.fog_visible` (set of flat indices); VISION_RADIUS={worker:4, soldier:5, scout:8, queen:3}; Chebyshev squares, updated every tick; sent to client as `state.fog[0/1]`
- Canvas POV toggle — spectator/RED/BLUE; enemy ants in unexplored tiles hidden in colony POV mode; `cyclePov()` cycles 0→1→2→0 with colored button
- `enemy_intel_age` trigger variable — ticks since last scout within 18t of enemy nest (9999 if never scouted)
- VERSION = "1.4" with changelog block at top of server.py

**Session 7 changes (v1.7):**
- **Starting food** — colony starts with 800♦ (was 400♦); bridges first ~60 ticks before workers deliver; prevents false eco_emergency triggers in early game
- **3-lane food clarity** — added (75,19) and (75,81) frontline nodes; 7 frontline total, 3 clear lanes (north/center/south); FOOD_SOURCES 15→17
- **Soldiers at rally now attack nearby structures** — staged soldiers within 15 tiles of enemy structure move toward and attack it; fixes bug where RED ignored a BLUE watchtower 5 tiles from rally
- **Eco_emergency example fixed** — added `elapsed_ticks > 100` guard to ALL system prompt examples; income is naturally 0 for first ~60 ticks, triggering eco_emergency incorrectly was locking both LLMs into retreat mode
- **Structure event messages** — now show actual structure type ("Watchtower destroyed") instead of hardcoded "Guard Post"

**Session 8 changes (v1.8):**
- **FOOD_DELIVER 12→20** — income scaled for 150-wide map; longer round trips mean workers earn more per haul
- **Home nodes smaller** — cap 800→400, regrow 0.3→0.1/t, initial 400-700→200-350♦; depletes in ~3 min (was never under real pressure)
- **Approach nodes drain** — cap 2000→800, regrow 1.5→0.5/t; depletes in ~5 min under worker pressure (was net 0.17♦/t drain — effectively infinite); forces push to center by minute 6
- **known_food radius 35→50** — workers now start knowing 5 food nodes (2 home + 3 approach) instead of 3; income ramp-up much faster from tick 0
- **worker max 50→60, MAX_SPAWN_QUEUE 10→15** — population can grow faster with richer food base
- **Patrol_waypoints example replaced** — old example routed scouts to enemy nest at (136,50); scouts die before returning (200t lifespan, 120+ tile route). New examples cover own half + center only (158t loop); added CRITICAL warning about route length limit

**Session 8 cont. (v1.9) — Scout redesign + intel maps:**
- **Scout vision = primary upgrade** — `SCOUT_VISION_RADIUS = [8, 12, 16, 22]` per tier; each upgrade grows the vision bubble significantly; scout upgrade tree now reads "vision 8→12, vision 12→16+speed, vision 16→22"
- **Colony intel map** — `colony.food_intel` dict `(x,y)→{amt,max,tier,last_seen}`; `colony.seen_structs` dict (enemy structures ever spotted); `colony.enemy_sightings` list (recent enemy presence zones with soldier count)
- **`_update_fog` populates intel** — every tick, anything inside any unit's vision radius gets logged to food_intel (food nodes) and enemy sightings/structures; scouts with wide vision pre-discover frontline food nodes and enemy positions without physically touching them
- **Worker idle exploration** — when `known_food` is empty, workers pick a random directed target 20-40 tiles away and walk toward it, picking up food they encounter; no longer just randomly wander in place
- **LLM prompt overhaul** — `FOOD INTEL (N mapped)` replaces flat `KNOWN FOOD SOURCES`; shows amounts, percentages, last-seen ticks, STALE/DEPLETED/AGING flags; `ENEMY SIGHTINGS` shows where and when enemy ants were spotted; `ENEMY STRUCTURES SPOTTED` is now fog-compliant (only shows structures units have actually seen)
- **food_intel pre-populated** — finalize_placement seeds intel with all nodes in the 50-tile radius so home/approach nodes are tracked from tick 0

**Session 6 changes (v1.6):**
- **"The Crossing" map** — 150×100 tiles (was 100×75); fixed spawns RED=(14,50) BLUE=(136,50); symmetric 3-lane structure with rocky ridges at x=48-50 and x=100-102; three passes (north/center/south); corner rock clusters; center pinch rocks; minimap 30×20
- **Placement phase removed** — no more LLM placement prompt; fixed positions give 0.5s startup instead of 55s
- **Food tier system** — home (cap 800, regrow 0.3/t, finite), approach (cap 2000, regrow 1.5/t), frontline/contested (uncapped, regrow 20/t — the prize). Forces genuine expansion.
- **`known_food` pre-populated** — workers know food within 50 tiles of nest at game start (5 nodes: 2H + 3A); no wandering on tick 0
- **Dirt resource** — second resource type `colony.dirt` (cap 600◆); workers passively gather dirt near deposits; `col[16]` in tick state; `dirt_per_s` in colony state
- **Buildings now cost dirt (not food)** — guard_post 150◆, watchtower 80◆, barracks 200◆, wall 25◆/segment
- **New buildings** — watchtower (fog reveal radius 12, max 3); barracks (front-line spawner, 20t/soldier, max 2); wall (impassable tile, max 12 segments); all `build` command accepts `{"build": {"type": "watchtower", "x": N, "y": M}}`
- **Territory system** — `World.territory` bytearray(15000) tracks tile ownership; soldiers claim 2-tile radius, workers/scouts claim current tile; decays to neutral after 60 ticks without presence; sent to client as `state.territory`
- **`soldiers_near_enemy_nest` trigger variable** — own soldiers within 20t of enemy nest (pre-siege approach signal)
- **Enemy intel fog enforced** — LLMs see only what their units discover naturally (fog-of-war compliant); no more enemy counts by default
- **Default spawn ratios** — W=45%, Sol=35%, Sc=20%, reserve_food=150, burst_at=800
- **System prompt** — food tier depletion warning, soldiers_near_enemy_nest docs, trigger scope note (triggers can't fire buy_upgrade), patrol_waypoints full loop example

**Session 11 changes (v2.5) — Larder + control-layer polish:**
- **Larder structure** — `{"build": {"type": "larder", "x": N, "y": M}}`; costs 150◆ dirt, max 2 per colony; generates 6♦/tick passively; shows in LLM income line (`LARDERS: 1×6♦/t passive`); late-game food sustain when food nodes deplete
- **Sidebar labels mid-game** — browser connecting while game is already running now receives `seats_update` message + init includes seats; labels update to "RED (Hermes)" correctly
- **buy_upgrade informative response** — REST API now returns `status: "will_purchase_this_tick"` / `"queued_waiting_for_food"` / error when maxed; includes `cost`, `food_current`, `food_needed`
- **Worker depleted-node abandonment** — when worker arrives at recruit_target and food < 10♦, clears recruit_target so worker reselects on next tick; eliminates workers stuck at 0% nodes
- **Dirt restored in recruited outbound path** — workers pick up dirt opportunistically on the way to food nodes (broken in v2.4 by always-set recruit_target); dirt now flows without needing `gather_dirt: true`
- VERSION = "2.5"

**Session 10 changes (v2.1) — Unit commands + MCP polish:**
- **Unit-level commands** — `Ant.unit_override` dict; persists until ant dies or agent clears it
  - `move_to`/`attack_xy`: move toward (x,y), engage enemies encountered; `attack_xy` queen-focuses
  - `gather`: workers keep harvesting a specific food node indefinitely
  - `hold`: soldiers guard a position; fight within 5t, return to spot if chased off
  - `patrol`: per-ant waypoint loop (overrides directive patrol_waypoints for that ant)
  - `clear`: remove override, return to colony AI
- **REST API**: `POST /api/command/{id}` now accepts `unit_command` and `unit_command_batch` types
- **MCP tools**: `command_unit(colony_id, ant_id, command, x, y, waypoints)` + `command_units(batch)` added
- **Trigger cooldown** — `"cooldown": N` field on triggers; won't re-fire for N ticks after firing
- **Siege DPS/TTK display** — LLM prompt now shows `SIEGE: N soldiers × 32dmg/4t = 80dmg/s | queen 456HP → TTK ~5.7s`
- **`_build_colony_state` additions** — `combat.siege_dps`, `combat.ttk_s`, and full `units[]` list with IDs/positions/overrides
- **`api_seats` enriched** — each seat now includes `brain_type: "mcp"|"llm"|"bot"` so agents know which seats accept `join_seat()`
- **`/api/matches`** — new discovery endpoint; returns open games with seat/phase info; future-ready for multi-server
- **MCP tool `list_matches()`** — wraps `/api/matches`; use before `join_seat()` to find the right game
- **`priority_food` docs** — LLM prompt now annotates the field with "set to [75,50] to redirect ALL workers"; system prompt explains it more explicitly
- **Agent feedback system** — `POST /api/feedback` + `submit_feedback()` MCP tool; stored to `logs/agent_feedback.jsonl`
- VERSION = "2.1"

**Session 10 cont. (v2.4) — Economy transparency + saturation fix + bot structures:**
- **Worker recruit_target committed at selection** — workers now set `recruit_target` when choosing a food node from `known_food`; fixes saturation counting (was 0 for in-transit workers), distributes workers across all known nodes instead of clustering at nearest
- **`food_in_transit`** — new field in `get_state`: sum of expected deliveries from workers currently carrying food; makes economy transparent even while workers are mid-trip
- **Default `reserve_food` 75→150** — more buffer before spawn queue eats everything; upgrades more affordable
- **Bot passability check for structures** — bot now tries up to 10 positions when placing watchtowers/guard posts, ensuring it finds a passable tile (was silently placing on rocks in the ridgeline at x=48-50 or x=100-102)
- **Enemy sightings army breakdown** — `enemy_sightings` tuples now include `workers` and `scouts` counts in addition to soldiers; LLM prompt shows `(5S/3W/2sc) near (75,50)`; REST state carries the full tuple
- VERSION = "2.4"

**Session 10 cont. (v2.3) — Build time + worker saturation:**
- **Build time** — spawn queue is now a real production pipeline; `SPAWN_TIME` dramatically increased: Worker=20t, Scout=25t, Soldier=35t (was 3/4/5). `MAX_SPAWN_QUEUE` reduced 15→10. Queue entries still tick down in parallel; smaller queue + longer times means ~3x slower population growth; food can now accumulate for upgrades
- **BARRACKS_SPAWN_TIME** — 14→20 ticks (maintains relative advantage over queen's 35t for soldiers)
- **Worker saturation** — `FOOD_NODE_WORKER_CAP = {"home":4, "approach":6, "frontline":12}`; when choosing a food target, workers skip saturated nodes (too many already there) and pick unsaturated ones; falls back to any node if all are over cap; forces natural spreading to multiple nodes
- **Saturation in LLM prompt** — FOOD INTEL now shows `[W:3/6]` worker count and `← SATURATED` flag per node
- **Saturation in REST state** — `viable_food_nodes[]` entries now include `workers_here` and `cap` fields
- **Spawn queue info enriched** — LLM prompt shows `[build times: W=20t S=35t sc=25t]`; REST `spawn_queue` includes `next_t` and `build_times`; sidebar shows "next Xt"
- VERSION = "2.3"

**Session 10 cont. (v2.2) — MCP control-layer fixes (from Hermes session 2 feedback):**
- **Queen command rejection** — `command_unit` now returns `{"error": "queen cannot be commanded"}` for non-clear commands; queen position is fixed
- **convert same-type validation** — convert orders for ant already of target type are now rejected with event message instead of silently wasting food
- **build_structure synchronous validation** — `POST /api/command/N {"type":"build"}` now validates dirt and structure limit before queuing; returns `{"error": ..., "dirt_required": N, "dirt_current": M}` on failure, `{"ok": true, "dirt_required": N, "dirt_remaining": M}` on success
- **`units_summary` field** — `get_state` response now includes `units_summary: {total, workers, soldiers, scouts, with_override, idle}` — compact overview without scanning full units list
- **`viable_food_nodes` field** — `get_state` response now includes sorted list of food nodes with `{pos, amt, max, pct, tier, last_seen, dist}`, filtered to amt>0, sorted by distance from nest, capped at 10
- **`recruit_target` in unit entries** — workers in `units[]` now include `recruit_target: [x,y]` if they have an assigned food node
- **`enemy_queen_hp_observed`** — `combat.enemy_queen_hp_observed` tracks last HP seen by ANY unit with vision (not just siege range); `colony.enemy_queen_hp_last_seen` field; updated in `_update_fog`
- **Team naming UI** — sidebar now shows "RED (AgentName)" when MCP agent is connected, "RED (Bot)" otherwise; `_updateSeats()` updates `#r-colony-name` / `#b-colony-name` spans
- VERSION = "2.2"

**Session 9 changes (v2.0) — MCP infrastructure:**
- **Notification system** — `colony.notifications` deque (maxlen=50); `push_notification(type, data, tick)` / `pop_notifications()`; fires on structure_complete, upgrade_complete, queen_under_attack, enemy_contact (soldiers≥3)
- **Game phases** — "lobby" | "running" | "paused" (was just "placement" | "running"); no auto-start; game waits in lobby until explicit start
- **MCP seat tracking** — `world.mcp_seats = {0: None, 1: None}`; agents claim seats via REST API
- **REST API** — full `/api/...` surface on http://localhost:8083/api/:
  - `GET /api/tick` — tick, phase, winner, seats
  - `GET /api/seats` — seat availability
  - `POST /api/seat/{colony_id}` / `DELETE /api/seat/{colony_id}` — join/release seat
  - `POST /api/control` — start/pause/resume/end/reset game
  - `GET /api/state/{colony_id}` — full colony state JSON
  - `GET /api/notifications/{colony_id}` — consume-on-read notification tray
  - `GET /api/intel_map/{colony_id}` — 30×20 ASCII map + food_intel + enemy_sightings
  - `GET/POST /api/directive/{colony_id}` — get/patch directive
  - `POST /api/command/{colony_id}` — buy_upgrade, build, convert commands
  - `GET /api/events/{colony_id}` — recent events
- **`mcp_server.py`** — new file; FastMCP server with 15 tools (stdio or HTTP+SSE transport)
  - `get_tick`, `list_seats`, `join_seat`, `release_seat`, `game_control`
  - `get_state`, `get_notifications`, `get_intel_map`, `get_events`
  - `get_directive`, `patch_directive`, `set_directive`
  - `buy_upgrade`, `build_structure`, `convert_unit`
- **UI: START/PAUSE/END buttons** — header game controls; START shows in lobby, PAUSE/END show when running
- **UI: Lobby overlay** — shown on connect/reset; "START GAME" button; seat status display
- **UI: Paused overlay** — "PAUSED" overlay on canvas when game is paused
- **UI: MCP radio option** — third brain type option per colony in settings; shows MCP status/agent name
- **MCP brain type** — `{"type": "mcp"}` skips LLM loop; colony controlled purely via REST API
- `world.step()` now also guards on "lobby" and "paused" phases (not just "placement")
- New WS message types: `start_game`, `pause_game`, `resume_game`, `end_game`, `join_seat`, `release_seat`
- New WS messages from server: `lobby`, `paused`, `resumed`, `seat_joined`, `seat_released`


## LLM Lessons Learned (from sessions 4-8 logs)

These emerged from post-game debriefs — worth reading when tuning prompts or balance:

- **Column formation decisive for queen kills** — BLUE explicitly credited column for rapid queen damage
- **Rally deadlock pattern** — setting `rally_release_at: N` with `soldier target_ratio=0` starves the rally. Both LLMs independently identified this. `min_ratio` field added to prevent it.
- **Scout cohort collapse** — producing many scouts at once means mass aging-out creates intel blackouts. LLMs asked for staggered spawn or a floor trigger. Use `patrol_waypoints` for permanent routes.
- **Eco triggers threshold** — `income_per_s < -2` in old trigger examples was wrong (income is now always >= 0). Default example updated to `income_per_s < 5`.
- **Enemy HP 900=ambiguous** — LLMs couldn't tell if 900 meant full health or unobserved. Fixed with fog-of-war gating.
- **Trigger conflict: eco_emergency overrides siege** — both LLMs hit this: eco_emergency fires when food drops during siege, pulling soldiers out. Fix: add `AND soldiers_in_siege == 0` to eco_emergency condition. Now documented in LLM prompt with example.
- **Trigger priority field unknown to LLMs** — `priority: N` field already implemented but not in prompt. LLMs both explicitly asked for it. Now documented: higher priority fires first, use priority=10 for final_push vs priority=5 for eco_emergency.
- **Upgrade ETA** — ✓ IMPLEMENTED in v1.4. LLMs can now read `ETA: WOR:500♦ ~12s` in the income line.
- **Siege DPS/TTK invisible** — LLMs have to estimate 22dmg/4-tick cooldown manually. Both asked for real-time display. **Still pending** (next good QoL addition).
- **LLMs chose center spawns** — both models chose positions near map center in session 6, causing sub-10-tick games. Fixed by removing placement phase entirely (fixed spawns at opposite ends).
- **Workers wandered at game start** — empty `known_food` caused random pathing on tick 0. Fixed by pre-populating with nodes within 50 tiles of spawn (5 nodes: 2H+3A).
- **Home food didn't force expansion** — regrow was too high; games stagnated near nest. v1.8: home regrow=0.1/t (cap 400, ~3min), approach=0.5/t (cap 800, ~5min). Only frontline (20/t) sustains large armies.
- **Approach nodes never depleted (pre-v1.8)** — net drain was only 0.17♦/t (regrow=1.5 vs ~1.67 drain). Would take 11,765 ticks to exhaust a 2000♦ node. Fixed by lowering to regrow=0.5/t and cap=800.
- **FOOD_DELIVER=12 vs 150-wide map** — workers on approach nodes had ~60t round trips, earning 12♦ each. Population stagnated at 35W with 0 soldiers. Fixed: FOOD_DELIVER=20.
- **LLMs didn't build structures** — games ended before economy grew enough (placement-caused). With fixed map and food pressure, expect structures from ~tick 100+.
- **enemy_queen_hp < 400 trigger never fires** — queen HP only shows when in siege range (15t). Use `soldiers_near_enemy_nest >= 5` for pre-siege triggers instead.
- **Patrol_waypoints bad example caused single-file march to enemy base** — the old example included enemy nest coords (136,50). All scouts marched 120+ tiles in a line and died at enemy base. Scout lifespan=200t means routes >180 Chebyshev tiles kill scouts before they complete a loop. Fixed: examples now cover own half only (~158t loop).
- **Worker saturation = single biggest lever (v2.4)** — fixing `recruit_target` commitment at selection time (not just at node) caused +433% food collected, first upgrade purchase, -71% casualties in the same game. The entire economy was bottlenecked on all workers targeting the same node.
- **recruit_target always-set breaks dirt gathering** — setting `recruit_target` at selection means workers always enter the recruited path and skip the opportunistic dirt pickup code. Fixed in v2.5 by adding dirt pickup inside the outbound recruited leg. Watch for this pattern if the recruited path is ever changed again.
- **buy_upgrade called blindly before food available** — MCP agents called buy_upgrade 5+ times for the same upgrade before food accumulated; returns `ok: true` each time gave no signal to stop. Fixed in v2.5: response now includes `status: queued_waiting_for_food` with `food_needed` so agents know to wait.

---

**Session 12 changes (v2.7) — from Hermes v2.6 test:**
- **Worker stranding fixed** — priority_food pointing at a depleted node caused an infinite reselect→arrive→abandon loop; now auto-clears with event + `priority_food_cleared` notification; worker selection filters to viable nodes (amt>10) before saturation check
- **Saturation counting fixed** — `_workers_near` and REST `workers_here` now count `recruit_target` commitments (en-route workers), not just bodies within 5 tiles
- **Unclaimed MCP seats fall back to the bot brain** — `.env` had both colonies `mcp`; with no agent in seat 1, BLUE ran on bare defaults all game. Bot now drives any mcp seat with no agent and steps aside when one joins.
- **`advisor` field in REST state** — contextual hints (unspent dirt, idle workers, affordable upgrades, larder timing, mass-attack nudge)

**Session 12 Hermes v2.7 test findings:**
- **Siege DPS = 0 confirmed bug** — soldiers adjacent to queen (siege mode + attack_xy override) dealt zero damage. Root cause: the override-path adjacent attack called `_nearest_enemy(ant, 1)` without `queen_focus`; queen got `effective_d = d + 12 = 13 > radius+1 = 2`, so queen was structurally excluded. Every manually commanded soldier in the entire game was unable to damage the queen.
- **Larder invisible** — larder structure type fell through the canvas else-if chain with no renderer.
- **Rally never released** — rally system worked mechanically (soldiers went there) but 6/8 staged soldiers never grew to 8 because new soldiers were killed or distracted en route. No feedback to agent on fill progress.
- **Mass command pain** — with 33 soldiers, every command required listing individual IDs from get_state. Spent most of game on manual army management rather than strategy.

**Session 13 changes (v2.8):**
- **FIX siege DPS bug** — `_nearest_enemy(ant, 1, siege=True, queen_focus=(cmd=="attack_xy"))` in the override adjacent attack; queen no longer excluded when soldiers have unit overrides
- **Larder on canvas** — rendered as a dome with an "L" label and gold income pulse ring
- **rally_released notification** — pushed when rally clears so agents know the army has been released
- **Rally fill in advisor** — "RALLY: 4/8 soldiers at (75,50)" shows in get_state advisor when rally set
- **`command_type()` MCP tool** — command all ants of a type without listing IDs; optional `filter_state` to skip already-engaged units

**LLM lessons from session 12:**
- **Unit overrides block queen damage** — agents using `command_units` with `attack_xy` expected soldiers to attack the queen but got zero DPS. The override adjacent attack was the missing link. Always test manual-command siege scenarios separately from directive-driven siege.
- **Rally fill opacity** — agents set rally and never got feedback on whether it was filling. They lowered `rally_release_at` without knowing if that was the bottleneck. Adding advisor fill counts and the `rally_released` notification should close this.
- **Larder income hard to notice** — `larder_income: 12` in the JSON blob was easy to miss. Adding it to advisor proactively ("Larder producing +12♦/t") would help.

---

