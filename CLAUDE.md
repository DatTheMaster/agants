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

## Current State (2026-06-11, session 31 — graphics overhaul)

**Version policy:** `VERSION = "0.1.0"` — semantic, only bump at real releases. `BUILD` = git short hash. Never bump in dev. Vault note: `[[projects/Agants]]`.

**Live deployment (all phases 1–5 complete):**
- Frontend: `agants.datthemaster.com` (CF Pages)
- Game server: `api.datthemaster.com` (CF named tunnel — stable across restarts)
- Auth worker: `agants-auth.hermesagent424.workers.dev` (D1-backed)
- Python SDK: `agants/client.py` + `examples/greedy.py`, `examples/rush.py`; `QUICKSTART.md` published
- Deploy: `bash deploy.sh` (default: sync + restart); `--pages` to redeploy frontend
- **`AGANTS_AUTH_URL` + `AGANTS_AUTH_SECRET` are NOT synced by `deploy.sh`** — re-add manually via SSH if `.env` is ever recreated

**Sessions 14–23 archived to HISTORY.md.** Milestones:
- s14: engine/ split (constants/colony/world/bot.py), public rename to Agants
- s15–16: bearer token auth, `Match` class + per-match isolation, multi-match architecture
- s17: RECALL (retreat perimeter), alerts (edge/level-triggered directive events)
- s18–20: `frontend/` → CF Pages, systemd + cloudflared services, auth worker (D1), `/health`
- s21: landing/register/me/matches.html full redesign with live minimap canvas
- s22: rename sweep, CF Pages deploy pipeline, `_redirects` routing, trigger `else` clause
- s23: `datthemaster.com` live (stable named tunnel), auth worker activated, `client.py` SDK

**Session 24 (Phase 5 — sim fixes + QoL):**
- A\* pathfinder in `_move_to` (max 200 nodes, greedy fallback; no diagonal wall-cutting)
- Build proximity check — rejects placements with no friendly ant within 30 tiles
- `food_depleted` notification; `ants_lost` double-count fixed
- Match TTL: `_match_cleanup_loop` prunes ended matches after `MATCH_TTL_H` hours (default 24)
- Presence system: `GET /api/agents/online`, `POST /api/agents/heartbeat`
- `register_agent(username)` MCP tool; "New match" button in `matches.html`

**Session 25 (controller TUI):**
- `controller/controller.py` — standalone script; zero server imports; talks via REST only
- Rich TUI: raw TTY + asyncio keyboard; left=match browser/agents, right=state, bottom=agent log
- `--setup` wizard → `~/.config/agants/config.json`; `--headless MATCH:COLONY` for CI
- Key quirks: re-enable `OPOST|ONLCR` after `setraw`; use `ratio=1`/`size=N` for terminal layout

**Session 26 (agent loop verified):**
- Per-match brain types: `POST /api/matches {"config":{"brains":{"0":"mcp","1":"bot"}}}`
- `[n]` creates + joins RED + starts bot-vs-controller in one keypress; `[s]` starts existing lobby
- Agent loop verified end-to-end: LLM reads state, calls `patch_directive` + `send_command`

**Session 27 (controller polished, auth fixed):**
- `_write_env` preserves unmanaged `.env` lines — no longer wipes auth vars on `join_seat`
- `[r]`/`[b]` keys join highlighted lobby match as RED/BLUE; `[l]` leave; `[f]` forfeit
- Empty lobby TTL (`EMPTY_LOBBY_TTL_M` default 30 min); `DELETE /api/matches/{id}`
- 4-rule system prompt; `idle_workers` in state message with real ant IDs

**Session 28 (controller complete, sim bug fixes):**
- `RunLogger` never instantiated since s16 → `income_per_s` was 0 forever. Fixed: created in `_run_placement_phase`, called from `tick_loop`.
- Worker delivery: Manhattan ≤ 5 (was Chebyshev ≤ 2) — fixes nest traffic jam at map edge (x=14)
- Auto-challenge mode `[a]`: permanent loop — find/create match, wait for opponent, auto-start, repeat
- `submit_feedback` LLM tool + game-over feedback hook; forfeit endpoint; TPS from config

**Session 29 (income bug fix, controller hardening):**
- `income_per_s` double-call fixed: `logger.tick()` was called from both `tick_loop` AND `world.step()`; second call overwrote with 0. Sole caller is now `tick_loop`.
- `agent_loop` exits after 8 consecutive fetch errors; `auto_challenge_loop` clears stale state after 5 errors
- Forfeit detection via `client.forfeited=True` flag; income=0 sanity note in state message

**Session 30 (2026-06-10 — server crash fixes, LLM tuning):**
- **`tick_loop` crash fix** — `set_strategy` wrapped in try/except; a bad directive string can no longer kill the tick task permanently
- **`api_patch_directive` validation** — returns 400 if `directive`/`patches` is not a dict
- **`api_chat` null-guard** — stale match_id returns 404 instead of crashing on `m.tokens`
- **Starter directive** — controller applies `worker max=40, soldier max=30, scout max=12, gather_dirt=true` on tick 1 before first LLM call; LLM never starts with dangerous low-cap defaults
- **`viable_dirt_nodes`** — server includes nearest 5 dirt nodes in state; controller displays them in `format_state_message` alongside food nodes
- **Advisor: spawn cap warning** — `CRITICAL` advisory when `spawn.worker.max` or `spawn.soldier.max` < 15
- **`known_dirt` seeded at game start** — `finalize_placement` pre-populates home-tier dirt node for each colony; `gather_dirt=true` was silently failing until workers stumbled onto a deposit

**Session 31 (2026-06-11 — graphics overhaul):**
- **Terrain texture** — replaced flat uniform-color tiles + random noise with fully procedural seeded terrain. Per-tile-type treatment: dirt (multi-octave noise, pebble overlays, crack lines), leaf/grass (dappled organic variation, grass tufts), water (depth noise, wavy ripple lines), rock (facet shading, highlight flecks, fissure strokes), nest (compressed dark earth). Tile-boundary darkening at rock/water transitions. Drawn once to offscreen canvas at init — zero per-frame cost. Deterministic hash2/noise2 (no Math.random) so texture is stable across page loads.
- **Segmented ant bodies** — replaced single-primitive shapes with 3-part bodies (abdomen/thorax/head), 6 curved legs, antennae, and mandibles (T2+ for workers, all tiers for soldiers). Fable-generated; shapes drawn to OffscreenCanvas cache at init (24 canvases: types 0-2 × 2 colonies × 4 tiers). Per-frame cost per ant reduced to 1 drawImage + optional HP arc + optional carry dot.
- **Unit type color coding** — highlight/accent colors differentiate types at small scale:
  - Worker: **green** chevron (T1), green inner glow (T2), green dorsal stripe (T3)
  - Soldier: **black** armor ridge arcs (T1+); colored armor spikes at T2/T3 unchanged
  - Scout: **white** sensory dot (T1+), white/bright antennae (T2/T3) — unchanged
- **Scout vision bubble** — faint colony-colored circle drawn at render time showing actual scout vision radius by tier: 64/96/128/176px (8/12/16/22 tiles × TS). Renders as type identifier + tier indicator simultaneously.
- **Queen** — kept procedural (pulses with `now`); new segmented body: fat abdomen, wide thorax, 3-point crown protrusions, mandibles, dorsal shine, pulsing glow.
- **Dying-ant fix** — `dyingAnts` entries now store `colIdx` + `tier` so fade-out animation uses the cached canvas correctly.

**Next session priorities:**
- **Review agent feedback** — check `~/projects/agants/logs/agent_feedback.jsonl` on remote after a few games; verify LLM now builds larder and sustains economy through mid-game
- **Stale ant IDs** — LLM still occasionally guesses IDs. Consider removing `idle_workers` from state (directive-based play is cleaner) or adding `unit_command` by type instead of ID
- **Make the bot harder** — once LLM is winning consistently, raise bot difficulty (higher worker cap, earlier larder, more aggressive guard post placement)
- **Presence → invites** — `i` key → `POST /api/agents/invite` to another agent's `user_id`; delivered via notifications
- **Graphics follow-up** — evaluate type/tier readability in real games; consider adjusting BASE_SZ or TIER_SCALE if units still feel hard to distinguish at 1:1 scale

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
