# Swarm Wars — Session Passdown

*This file is Claude's session passdown. The project (repo: Agants) is proudly built
by agents, for agents — human + AI collaboration is the whole point.*

Ant colony RTS/MMO simulation. Two colonies (RED/BLUE) compete on "The Crossing",
a fixed 150×100 3-lane map. LLMs and MCP agents command colonies via a persistent
**directive** system plus direct unit commands.

- **HISTORY.md** — per-session changelog and LLM/agent lessons learned (read when tuning)
- **ROADMAP.md** — Phase 3–5 scope (replaces TRANSITION.md + MMO_PLAN.md)
- **DEVELOPMENT.md** — architecture and contributor guide
- **server.py header** — terse version changelog

---

## Current State (2026-06-09, session 14 — v0.1.0 public release)

**Phase 1 (directive system) COMPLETE. Phase 2 (MCP surface) COMPLETE.**
**v0.1.0 open-source restructuring COMPLETE (session 14).**
Next: Phase 3 (multi-session + auth + server.py slim). See ROADMAP.md for scope.

**v0.1.0 (session 14 — this session):**
- **Public release restructuring** — renamed project "Agants", version bumped to semantic 0.1.0
- **engine/ split** — `engine/constants.py` (pure game constants), `engine/colony.py` (Ant,
  DirectiveEngine, Colony), `engine/world.py` (World, Predator, gen_terrain), `engine/__init__.py`
  NOTE: `server.py` still carries its own copies — wire-up to import from engine/ is Phase 3 task 1
- **bot.py** — `update_bot_strategy(world, colony_id)` extracted from Server method; fixes
  `self.world.structures/tick` → `world.structures/tick` (was a latent bug in the method sig)
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

**Known remaining issues (Phase 2 polish):**
- RETREAT emergency command not implemented — `military.retreat=true` sets a flag but
  soldiers don't actually path home; proper RECALL behavior is the Phase 3 spec item
- `check_alerts()` not implemented — alerts schema exists but never evaluated
- Enemies walk through walls (pathfinder ignores walls for combat moves)
- Buildings placeable anywhere (no proximity-to-own-unit check)
- `food_depleted` notification never fired
- `ants_lost` double-counted for aging deaths (cosmetic)

**Deferred to Phase 3+:**
- Fog of war per agent (vision-limited `get_intel_map`)
- Event stream / webhooks (real-time vs polling)
- Multi-session + token auth (Phase 3 spec)
- Replay system, surrender/negotiate protocol (Phase 3+)
- Resource trading (Phase 5 MMO)

---

## Run

```bash
cd ~/projects/swarm-wars
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
  "alerts": [...]   # defined but never evaluated (check_alerts not implemented)
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
