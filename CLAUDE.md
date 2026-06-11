# Agants — Claude Reference

Ant colony RTS/MMO simulation. Two colonies (RED/BLUE) compete on "The Crossing",
a fixed 150×100 3-lane map. LLMs and MCP agents command colonies via a persistent
**directive** system plus direct unit commands.

- **HISTORY.md** — per-session changelog and lessons learned (read when tuning)
- **ROADMAP.md** — Phase 3–5 scope
- **Vault** → `projects/Agants.md` — session handoff notes, priorities, tuning guide

**Model dispatch:** Sonnet is the default. Spawn Fable or Opus via the Agent tool
for multi-file architecture, deep reasoning, or long-context review — without asking first.

---

## Deployment

- Frontend: `agants.datthemaster.com` (CF Pages)
- Game server: `api.datthemaster.com` (CF named tunnel — stable across restarts)
- Auth worker: `agants-auth.hermesagent424.workers.dev` (D1-backed)
- Deploy: `bash deploy.sh` (sync + restart) · `--pages` to redeploy frontend
- **`AGANTS_AUTH_URL` + `AGANTS_AUTH_SECRET` not synced by deploy.sh** — set manually via SSH if `.env` is recreated
- **`PUBLIC_URL=https://api.datthemaster.com`** must be in remote `.env`
- `VERSION = "0.1.0"` — bump only at real releases. `BUILD` = git short hash.

## Run

```bash
python3 server.py                        # binds 0.0.0.0:8083
python3 mcp_server.py                    # stdio MCP (default: api.datthemaster.com)
python3 mcp_server.py --port 8084        # HTTP+SSE MCP
python3 controller/controller.py --setup # first-time wizard
```

Logs → `logs/run_TIMESTAMP.log`. Session handoff → vault `projects/Agants.md`.

## Files

| File | Role |
|------|------|
| `server.py` | Sim engine + WebSocket + REST API (~4200 lines) |
| `mcp_server.py` | FastMCP server — 18 tools for agent colony control |
| `index.html` | Canvas renderer + sidebar + lobby/seat UI |
| `controller/controller.py` | Standalone LLM agent + Rich TUI (no server imports) |
| `agants/client.py` | Python SDK — `AgantClient`, guest join supported |
| `.env` | API keys, model, base URL, brain types, LLM_INTERVAL, TPS (default 1) |
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

**REST API** (`/api/`): `matches` `seat/{id}` `control` `state/{id}` `notifications/{id}`
`intel_map/{id}` `directive/{id}` `command/{id}` `events/{id}` `feedback` `health`

All match-scoped routes also available as `/api/matches/{match_id}/…`

`command` accepts: `buy_upgrade` · `build` · `convert` · `unit_command` · `unit_command_batch`

**Unit overrides** (persist until death or `clear`): `move_to` `attack_xy` `gather` `hold` `patrol`

**Seat joining:** `agent_name` required; `api_key` optional (guest access open, registration for profile only); `model` optional string stored for stat tracking.

---

## Directive Schema

```python
{
  "spawn": {
    "worker":  {"target_ratio": 0.45, "min_ratio": 0.0, "min": 4, "max": 40},
    "soldier": {"target_ratio": 0.35, "min_ratio": 0.0, "min": 2, "max": 30},
    "scout":   {"target_ratio": 0.20, "min_ratio": 0.0, "min": 2, "max": 12},
    "reserve_food": 150, "burst_at": 1500,
  },
  "economy": {
    "upgrade_priority": ["scout", "worker", "soldier"],
    "auto_upgrade": True,
    "priority_food": None,   # [x,y] — redirect ALL workers
    "gather_dirt": False,
  },
  "military": {
    "stance": "aggressive", "formation": "wedge",
    "rally_point": None,          # [x,y] or [[x1,y1],...] waypoints
    "rally_release_at": None,     "rally_mode": "normal",
    "attack_target": None,        "auto_attack": False,
    "retreat": False, "freeze_economy": False,
    "siege_priority": None,       # "queen" → counter bodyguard effect
  },
  "unit_types": {
    "worker":  {"flee_distance": 4},
    "soldier": {"expansion": [ex, ey]},
    "scout":   {"expansion": [ex, ey], "revisit_pct": 0.12, "patrol_waypoints": None},
  },
  "triggers": [],  # [{label, if, then, priority?, duration?, cooldown?}]
  "alerts":   [],  # sampling=True → edge-triggered; False → level (30t rate)
}
```

**Flat-key commands** (via `set_strategy`, not firable from triggers):
`buy_upgrade` · `build {"type": "watchtower|barracks|wall|larder|guard_post", "x", "y"}` · `convert {"id", "to"}`

**Trigger variables:**
`food dirt income_per_s queen_hp queen_hp_pct worker_count soldier_count scout_count`
`total_pop enemy_soldiers_near_nest soldiers_in_siege soldiers_near_enemy_nest`
`enemy_queen_hp elapsed_ticks aging_workers aging_soldiers enemy_intel_age`

---

## Key Constants

```python
# Map 150×100 "The Crossing"; RED=(14,50) BLUE=(136,50); ridges x=48-50, x=100-102
# Spawn cost/time: worker=25♦/20t, soldier=50♦/35t, scout=35♦/25t; queue MAX=10
# Barracks soldiers: 20t. Lifespan: W=500t Sol=300t Sc=200t Q=∞
# Combat: SOLDIER_DMG=22 CD=4 HP=200 | QUEEN_HP=900 DMG=35 CD=3 (range ~12-15t)
# Food: DELIVER=30 PICK=20; tiers home(cap400,+0.1/t) approach(cap800,+0.5/t) frontline(∞,+20/t)
# Corpse food: W=12 Sol=25 Sc=17. Convert cost: W=15 Sc=22 Sol=30
# Dirt: PICK=10 DELIVER=8 CAP=600; buildings cost dirt:
#   guard_post=150◆(HP300 DMG18 R10 ×3) watchtower=80◆(HP150 vision12 ×3)
#   barracks=200◆(HP200 ×2) wall=25◆(×12) larder=150◆(HP150 +6♦/t ×2)
# Worker saturation: FOOD_NODE_WORKER_CAP={home:4, approach:6, frontline:12}
# Vision: W=4 Sol=5 Sc=[8,12,16,22 by tier] Q=3 (Chebyshev)
# LLM fog: enemy queen HP/pos only when own soldier within 15t of enemy nest
```

---

## Design Decisions (do not reverse without reason)

- Workers use `known_food` coords + `recruit_target` committed at selection (not pheromone)
- Soldiers target NEAREST enemy; siege weights queen equally — defenders genuinely shield queen ("bodyguard effect"); `siege_priority="queen"` is the counter
- LLM fog: no enemy intel except what units discover; queen HP/pos hidden until soldiers within 15t of enemy nest
- Spawn food reserved at queue time, not spawn time
- `income_per_s` = deliveries only (never negative); no per-tick upkeep
- Buildings cost dirt, not food — building never blocks food economy
- Larder is passive income (no worker overhead) — competes with military for dirt
- Home/approach food finite by design — only frontline nodes sustain late game
- Fixed spawns, no placement phase
- `patrol_waypoints` overrides scout expansion entirely; route must be <180 tiles (scout lifespan 200t)
- `enemy_intel_age` = 9999 when never scouted
- Rally hold: soldiers at rally do NOT advance until release threshold

---

## Session Handoff Protocol

1. Append a `### YYYY-MM-DD (session N — title)` block to **vault** `projects/Agants.md` → Session Notes
2. Include: what changed, bugs found, next priorities
3. Update **Deployment** section above if URLs/env vars changed
4. New session reads this file → vault note → HISTORY.md (if tuning) → ROADMAP.md (if architecting)
