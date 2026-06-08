# Swarm Wars — Project Handover (v1.3)

Ant colony RTS simulation. Two colonies (RED / BLUE) compete on a procedurally generated
forest floor using pheromone trails, food economics, soldiers, and scouts.
Both colonies can be independently commanded by LLMs via OpenAI-compatible APIs.

**v1.3 changes:** Sim decoupled from LLM processing (`world.step()` runs in thread executor,
strategies queued via `_pending_strategies` deque, LLM interval now wall-clock based).
Four new LLM levers: `formation`, `attack_target`, `retreat`, `freeze_economy`.

**v1.2 changes:** Queen combat fix (was completely passive — queens now fight back at range 12
with soldier priority) + Guard Post defensive structures (build lever, turret attack, soldier
targeting, minimap display, LLM prompt integration).

**v1.1 changes:** Starvation timer, rally staged count, sector "at rally" label, upgrade ETA,
upgrade queue visibility, scout-zero guardrail.

**v1.0 headline feature:** BAR-style placement phase — before each game, the LLM and bot
independently evaluate the map and choose starting positions in their assigned half.

## Run

```bash
python3 server.py        # starts at http://localhost:8083  (config: .env)
                         # "NEW GAME" button resets without restart
```

Logs are written to `logs/run_TIMESTAMP.log`. Read these to diagnose balance and LLM behaviour.
LLM reasoning, decisions, per-decision feedback, and post-game debrief are all in the log.

## Files

| File | Role |
|------|------|
| `server.py` | Simulation engine + WebSocket server (aiohttp) |
| `index.html` | Canvas renderer + sidebar dashboard |
| `.env` | Config: API key, model, base URL, LLM colony, interval, TPS |
| `logs/` | Per-run logs — snapshot/second, events, LLM reasoning, debrief, final memory |

## Architecture

**Server → Client:** WebSocket pushes full world state every tick (JSON). Client is pure renderer.

**Reset flow:** Client sends `{"type":"reset"}` → server creates new `World` → broadcasts `init`
(terrain only) → placement phase runs → second `init` broadcast (with nests carved) + `game_start`
→ client clears placement overlay and receives tick state normally.

**Placement phase flow (v1.0):**
1. `World.__init__` generates terrain + 16 strategic food nodes (no colonies yet)
2. Server broadcasts `placement_phase` message with food list
3. Bot calls `_best_placement()` instantly; LLM gets async API call (up to 55s)
4. `placement_update` messages broadcast as each side confirms
5. `world.finalize_placement(red_pos, blue_pos)` carves nests, spawns colonies, starts logger
6. Second `init` + `game_start` broadcast; tick_loop and llm_loop resume

**Pheromone layers:**
| Index | Name | Deposited by | Used by |
|-------|------|-------------|---------|
| 0 | Forage (green) | Workers/scouts carrying food | Visual only (workers use known_food coords) |
| 1 | Alarm (red) | Soldiers in combat, scouts near enemies | Soldiers (follow, high threshold) |
| 2 | Territory (gold) | Patrolling soldiers | Visual only |
| 3 | Scout (blue) | Exploring scouts | Visual only |

**Food flow:**
1. Scout explores outward (biased toward center/enemy via `expansion` vector)
2. Scout finds food (detection radius scales with Scout tier) → picks up, remembers location
3. Scout rushes home → deposits food → recruits up to `scout_recruit` idle workers
4. Recruited workers march to food target, pick up, return to nest
5. Unrecruited workers pick a random `colony.known_food` entry
6. Workers fleeing enemy soldiers drop carried food immediately

**Win condition:** Queen dies (combat or starvation cascade). When all non-queens are gone
and `food < -55`, queen starves immediately.

## LLM Integration

Provider-agnostic OpenAI-compatible API. Configure in `.env`:
```
NVIDIA_API_KEY=...
LLM_BASE_URL=https://opencode.ai/zen/go/v1
LLM_MODEL=mimo-v2.5
LLM_COLONY=1      # 0=RED, 1=BLUE
LLM_INTERVAL=100  # ticks between decisions
TPS=5             # simulation speed
```

The LLM is called every `LLM_INTERVAL` ticks. Game runs continuously between calls.
Full reasoning + decisions are printed to console and written to the run log.
A post-game debrief fires once after the game ends.

### LLM levers (`colony.set_strategy({...})`):
| Key | Type | Effect |
|-----|------|--------|
| `roles` | `{worker, scout, soldier}` | Production ratios (must sum to 1.0) |
| `defense` | `"aggressive"\|"balanced"\|"defensive"` | Patrol range and direction |
| `worker_cap` | int | Stop making workers above this count |
| `rally_point` | `[x, y]` or null | Soldiers HOLD at this coordinate until cleared |
| `expansion` | `[dx, dy]` | Scout/soldier direction bias unit vector |
| `priority_food` | `[x, y]` | ALL idle workers march here (must be in known_food) |
| `buy_upgrade` | `"worker"\|"scout"\|"soldier"\|true` | Queue upgrade purchase |
| `rally_release_at` | int or null | Auto-release rally when N soldiers staged |
| `siege_priority` | `"queen"\|null` | Soldiers in siege prefer queen over defenders |
| `build` | `[x, y]` | Construct Guard Post at coordinate (500♦, max 3, range 10, HP 300) |
| `formation` | `"column"\|"wedge"\|"spread"` | Soldier patrol spread: column=tight spike, wedge=default, spread=wide fan |
| `attack_target` | `[x, y]` or null | Soldiers advance continuously toward coordinate (never hold) |
| `retreat` | `true\|false` | Soldiers fall back toward own nest; still fight adjacent enemies |
| `freeze_economy` | `true\|false` | All-in shorthand: sets roles={worker:0, scout:0.05, soldier:0.95} + worker_cap=0 |

### LLM memory:
Per-match only — resets when NEW GAME is clicked. LLM can read/write arbitrary key:value
pairs via `"memory": {"key": "value"}` in any JSON response (null value = delete key).
Final memory state is written to the run log at game end — this is our learning signal
for prompt/balance tuning.

## Upgrade Trees

Three independent trees, 3 tiers each. Auto-buy heuristic runs for both colonies;
LLM can explicitly purchase with `buy_upgrade`.

**Worker (economic):**
- W1  500 food: +8 carry/trip (12→20 food delivered)
- W2 2200 food: +12 more (→32/trip)
- W3 6000 food: +18 more (→50/trip) + loaded workers take 2 steps/tick

**Scout (intel/logistics):**
- S1  450 food: detection range 5→9 tiles, recruit cap 8→14 workers
- S2 2000 food: scouts take 2 steps/tick while exploring AND returning
- S3 5500 food: recruit cap 14→24, queen produces 25% faster (spawn_mult=0.75)

**Soldier (combat):**
- Sol1  600 food: +10 damage/hit (22→32)
- Sol2 2800 food: +80 HP (200→280), attack cooldown 4→3 ticks
- Sol3 7500 food: splash — 40% damage to all enemies adjacent to primary target

## Food Economy

**3-lane strategic food layout (v1.0, BAR-inspired):** 16 purposeful nodes replace 20 random scatter.
Placed BEFORE placement decisions so agents can evaluate the map strategically.

| Tier | Count | Regrow | Cap | Location | Role |
|------|-------|--------|-----|----------|------|
| Frontline | 4 | 20/tick | ∞ | x=42–58, all 3 lanes + bonus center | Contested prizes — key late-game objective |
| Approach | 6 | 5/tick | 5000 | x=20–38 (RED) / x=62–80 (BLUE), 3 per side | Reward forward expansion |
| Home | 6 | 2.5/tick | 5000 | x=5–22 (RED) / x=78–95 (BLUE), 3 per side | Safe early economy |

**Food node tiers** are shown in client (ring colors) and in the LLM prompt (minimap F/a/h chars).

```python
UPKEEP            = [0.03, 0.10, 0.04, 0.05]  # per tick: worker, soldier, scout, queen
FOOD_DELIVER      = 12     # base food per worker trip (+ carry_bonus from Worker upgrades)
FOOD_PICK         = 15     # one haul removes this from source
FOOD_REGROW       = 2.5    # home source regrow/tick
FOOD_REGROW_APPROACH = 5.0 # approach source regrow/tick
FOOD_REGROW_CONTESTED = 20.0  # frontline source regrow/tick (effectively unlimited)
```

## Placement Phase Details

**World methods added in v1.0:**
- `_place_strategic_food()` — generates 16-node 3-lane layout; called in `World.__init__`
- `finalize_placement(red_pos, blue_pos)` — carves nests, spawns colonies, starts logger
- `_valid_placement(x, y, half)` — checks passable + correct half + ≥15 passable neighbors + food within 50 tiles + ≥12 tiles from center
- `_score_placement(x, y)` — scores a position (approach nodes have highest mult, frontline lowest since they're contested prizes to fight for, not start next to)
- `_best_placement(half)` — exhaustive scan of valid positions, returns highest score

**Placement log entry** (at top of run log, before snapshot rows):
```
=== PLACEMENT PHASE ===
  RED  ( 23, 46)  bot heuristic (score=172)
  BLUE ( 70, 41)  aggressive center to reach midfield fast
```

**Client placement phase rendering:**
- `placement_phase` WS message → shows overlay, renders food nodes colored by tier
- `placement_update` WS message → shows pulsing queen marker at chosen position
- `game_start` WS message → clears overlay, normal game begins

## Key Constants (`server.py`)

```python
PLACEMENT_TIMEOUT    = 60      # seconds total for placement (LLM gets PLACEMENT_TIMEOUT-5)
PHERO_EVAP           = 0.975   # trails fade in ~27s at 5 TPS
ALARM_FOLLOW_THRESH  = 0.20    # soldiers only chase fresh/strong alarm pheromone
FOLLOW_THRESH        = 0.03    # general pheromone follow threshold
SOLDIER_DMG          = 22      # base damage per hit (+ dmg_bonus from Soldier upgrades)
SOLDIER_CD           = 4       # base attack cooldown (→3 at Sol2)
SOLDIER_HP           = 200     # base HP (+ soldier_hp_bonus from Sol2)
QUEEN_HP             = 900
QUEEN_DMG            = 35
QUEEN_CD             = 3
TPS                  = 5       # default (set in .env)
LLM_INTERVAL         = 100     # ticks between LLM calls
```

## Known Design Decisions

**Workers use `known_food` for outbound nav, not pheromone.** The forage gradient points
nest-ward (deposited on return trip), so following it homeward would be wrong. Workers
use known coordinates; pheromone trails are visual only.

**Alarm pheromone feedback loop prevention.** Soldiers following alarm pheromone do NOT
re-deposit it. This prevents self-reinforcing stale pools that trap soldiers forever.
High threshold (`ALARM_FOLLOW_THRESH=0.20`) ensures soldiers only chase fresh combat signals.

**Fog of war.** Enemy army counts are gated by scouting — only known when a friendly ant
is within 18 tiles of the enemy nest. Intel freshness: <150 ticks = "fresh", <500 = "stale",
else "unknown". Enemy food/income are NEVER visible to the LLM.

**Starvation is gradual (1 ant/tick max).** Prevents instant collapse; gives time to recover.

**Rally point hold.** Soldiers at the rally coordinate hold position until the LLM clears it
by setting `rally_point: null`. This enables proper stage-and-assault play.

## Tuning Guide

Read `logs/run_*.log`. Key signals:

| Symptom | Likely cause | Tweak |
|---------|-------------|-------|
| No combat events | Patrol radius too small | Check `base_d` in `_behavior_soldier`, or shrink map |
| Colony stalls at 1 ant | Queen starvation threshold | Check `_check_win` and `-55` threshold |
| Income always negative | Workers can't find food | Check `known_food` is populated; scout range |
| Food depletes instantly | Too many workers on one source | Workers use random known source; check `FOOD_REGROW` |
| LLM never buys upgrades | Hoarding food | LLM needs reminder in prompt; check `buy_upgrade` lever |
| Bot too easy | Bot not adapting | Check `_update_bot_strategy` states; tune thresholds |
| Bot too hard | Bot buys T3 upgrades | Lower `FOOD_REGROW_CONTESTED` or raise upgrade costs |
| Games always short | Combat damage too high | Lower `SOLDIER_DMG` or raise `QUEEN_HP` |
| Games always long | Armies never meet | Increase aggression radius in soldier patrol |

## v1.0 → v1.1 Prompt Improvements (post-run analysis, 2026-06-07)

Changes made to `build_llm_prompt` and `soldier_sectors` based on mimo feedback from first v1.0 runs:

**Starvation timer** — FOOD/INCOME line now appends `*** STARVE IN ~Xs ***` when income is
below -5/s. Computed as `food / abs(income_per_s)`. Addresses mimo's repeated request for
projected starvation time.

**Rally soldier count** — Strategy block now shows `rally_point=(45,36) [7 staged → release at 12]`
when a rally is active. Computed via same 4-tile threshold as auto-release check. Tells mimo
how many soldiers are already staged vs still en route.

**Sector "at rally" label** — `soldier_sectors()` now counts soldiers within 4 tiles of
rally_point as "at rally" (reported first). Previously all non-forward soldiers showed as
"near home" or "midfield" regardless of whether they were staged at rally.

**Upgrade affordability timer** — Available upgrades now show `~Xs` ETA when affordable at
current income. E.g. `SOL T2 Combat II (2800♦ need +1610 more, ~11s)`. Only shown when
income is positive.

**Upgrade queue visibility** — `(QUEUED)` marker appended to any upgrade that has been queued
via `buy_upgrade` but not yet executed (pending flag set, food not yet sufficient). Prevents
mimo from re-queueing the same upgrade every turn.

**Scout-zero guardrail** — System prompt now explicitly warns against setting scout ratio to 0
(previously only warned about workers). Minimum 0.1 recommended.

## LLM-Identified Missing Levers (deferred)
- `retreat` — pull soldiers home while rebuilding economy
- `emergency_austerity` — panic button: max workers, defensive, clear rally
- Staged rally waypoints: rally at A, advance to B
- Worker cap auto-scaling: "maintain positive income at current army size"
