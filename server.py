#!/usr/bin/env python3
"""
Swarm Wars — Ant Colony RTS for LLMs  v0.6

DESIGN:
  Colonies grow by scouting food and recruiting workers at the nest.
  Scouts physically carry food back, depositing a visible trail.
  At the nest, scouts "recruit idle workers" who then follow the trail.
  Workers that reach food carry it back, reinforcing the trail.

  LLM HOOKS:
    colony.set_strategy({
        "roles": {"worker": 0.5, "scout": 0.3, "soldier": 0.2},
        "expansion": (dx, dy),       # direction to push scouts/soldiers
        "defense": "aggressive",      # "aggressive" | "defensive" | "balanced"
        "priority_food": (x, y),      # specific food source to target
        "buy_upgrade": True,          # queue next upgrade (buys ASAP when affordable)
        "rally_point": (x, y),        # stage soldiers at this coordinate before pushing
        "worker_cap": 30,             # stop producing workers above this count
    })
    colony.get_state()   # own state + enemy summary for LLM prompt
    colony.events        # recent event strings

  POPULATION:
    Queen produces ants every tick based on role_allocation and food.
    Ants cost food once at spawn (no per-tick upkeep). Lifespan is the attrition mechanic.
    Workers can be reassigned to scouts/soldiers via strategy.

  WIN CONDITION:
    Colony queen dies → that colony loses.
    If both queens die same tick → draw.
"""

import asyncio, concurrent.futures, json, math, os, random, re, time, uuid
from collections import deque
from datetime import datetime
import aiohttp
from aiohttp import web

# ── Load .env into os.environ (no external dependency) ─────────────────────
_ENV_PATH = ".env"
def _load_dotenv():
    if not os.path.exists(_ENV_PATH):
        return
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            _v = _v.strip().split(" #")[0].strip()
            os.environ[_k] = _v          # .env always wins (override shell env)
_load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

VERSION = "2.10"

# Version changelog (bump VERSION every session that changes gameplay or prompts)
# 1.0 — initial engine: pheromones, food, combat, upgrades, WebSocket renderer
# 1.1 — directive system, DirectiveEngine, trigger evaluator, LLM prompt v1
# 1.2 — no-upkeep economy, corpse food, unit conversion, guard post bot, fog-of-war fixes,
#        food depletion display, min_ratio floor, waypoints, auto-forward rally, auto-attack,
#        enemy queen HP/pos hidden until siege range
# 1.3 — zoom/pan clamped; scout trail deposit 0.3→0.6, weight 0.35→0.65;
#        trigger priority documented in LLM prompt with eco_emergency+siege pattern
# 1.4 — trigger event log in LLM prompt; upgrade ETA in prompt + sidebar;
#        scout patrol_waypoints directive field; per-colony visual fog-of-war with
#        vision radii (worker=4, scout=8, soldier=5, queen=3); canvas POV toggle;
#        enemy_intel_age trigger variable
# 1.7 — starting food 400→800; 2 new frontline nodes (75,19)/(75,81) for 3-lane clarity;
#        soldiers at rally now attack nearby enemy structures (fixes watchtower-on-enemy-side);
#        eco_emergency example updated with elapsed_ticks>100 guard; FOOD_SOURCES 15→17;
#        structure event messages show actual type (not hardcoded "Guard Post")
# 1.8 — FOOD_DELIVER 12→20 (income scaled for 150-wide map); FOOD_MAX_HOME 800→400,
#        FOOD_MAX_APPROACH 2000→800 (nodes drain in 1-5 min, forces center push);
#        FOOD_REGROW_APPROACH 1.5→0.5, FOOD_REGROW_HOME 0.3→0.1 (real depletion pressure);
#        FOOD_INIT_HOME 400-700→200-350, FOOD_INIT_APPR 800-1500→300-600 (smaller initial stock);
#        finalize_placement known_food radius 35→50 (workers start knowing 5+ food nodes);
#        worker max 50→60, MAX_SPAWN_QUEUE 10→15 (faster population growth);
#        patrol_waypoints example replaced with own-half loop (was routing to enemy nest)
# 1.9 — Scout redesign: vision is the primary upgrade (SCOUT_VISION_RADIUS [8,12,16,22] per tier);
#        Colony intel map: food_intel dict (amt+tier+last_seen), seen_structs (enemy structures
#        ever spotted), enemy_sightings (recent enemy presence zones); _update_fog now populates
#        all three from anything in a unit's vision radius — scouts with wide vision auto-discover
#        food nodes and log enemy positions; LLM prompt now shows FOOD INTEL with staleness flags
#        and ENEMY SIGHTINGS instead of flat known_food list; enemy structures only shown when
#        actually spotted (fog-of-war compliant); workers explore directed toward unexplored zones
#        when known_food is empty; scout upgrade tree descriptions changed to vision-focused
# 1.6 — "The Crossing" 150×100 fixed map; fixed spawns RED=(14,50) BLUE=(136,50);
#        placement phase removed (0.5s startup); food tier system (home/approach/frontline);
#        home regrow=0.3/t (cap 800), approach=1.5/t (cap 2000), frontline uncapped;
#        known_food pre-populated within 35t of spawn; dirt resource (col[16]);
#        buildings cost dirt: guard_post=150, watchtower=80, barracks=200, wall=25/seg;
#        watchtower (fog reveal r=12) and barracks (front-line spawner) buildings added;
#        territory bytearray(15000) tracks tile ownership; TERRITORY_DECAY=60t;
#        soldiers_near_enemy_nest trigger variable (20t radius);
#        full LLM fog-of-war compliance (enemy intel only from natural discovery);
#        default spawn ratios W=45% Sol=35% Sc=20%; reserve_food=150, burst_at=1500
# 2.5 — Larder structure (150◆, max 2, 6♦/tick passive income — late-game food sustain);
#        sidebar labels now update when browser connects mid-game (seats_update message on running);
#        buy_upgrade returns meaningful status (queued_waiting_for_food, will_purchase_this_tick,
#          already_maxed) instead of blind ok:true;
#        worker depleted-node abandonment: recruits clear target when node <10♦ at arrival;
#        dirt restored in recruited outbound path: workers pick up dirt on the way to food nodes
# 2.6 — FIX: recruited workers delivered dirt as food (carrying_type ignored in recruited
#          delivery branch) — dirt stockpile stayed 0 forever, so every build was rejected;
#        game_control("end") now adjudicates winner by score instead of hardcoding "draw",
#          never stomps an already-decided winner, and returns winner+scores in the response;
#        combat telemetry: queen_dps_actual (real damage to enemy queen last second) and
#          soldiers_adjacent_queen added to REST combat state; siege_dps is theoretical max —
#          agents were misreading it as actual; siege_hint nudges siege_priority="queen"
#          when soldiers are sieging but the queen is taking no damage (bodyguard effect);
#        game_over notification pushed to both colonies on queen death (victory/defeat/draw)
# 2.7 — FIX worker stranding: priority_food auto-clears when its node depletes (was an
#          infinite reselect→arrive→abandon loop stranding every worker at an empty node);
#        worker selection now filters to viable nodes (amt>10) before the saturation check;
#        saturation counting includes recruit_target commitments (en-route workers), both in
#          worker selection and REST viable_food_nodes.workers_here;
#        unclaimed MCP seats fall back to the bot brain (a game vs an absent agent was
#          running on bare default directive — no builds, no upgrades, no attacks);
#        advisor field in REST state: contextual hints for unspent dirt, idle workers,
#          affordable upgrades, larder timing, and massing attacks;
#        priority_food_cleared notification
# 2.8 — FIX siege DPS: override-path adjacent attack used _nearest_enemy(ant, 1) without
#          queen_focus, so queen got effective_d=d+12 which exceeded radius+1=2 — queen
#          was structurally excluded from the adjacent attack when soldiers had unit overrides;
#          fix: siege=True + queen_focus=(cmd=="attack_xy") on the adjacent check;
#        larder renderer added to canvas (was invisible — else-if chain stopped at barracks);
#        rally_released notification pushed when rally clears;
#        rally fill progress shown in advisor field ("RALLY: 4/8 soldiers at (75,50)");
#        MCP tool command_type(): command all ants of a given type without listing IDs,
#          with optional filter_state to skip units already engaged
# 2.9 — Construction mechanic: structures start inactive (scaffolding), workers build them
#          over time; build rates scale with worker tier (1/2/3/4 work/tick at T0-T3);
#          max 4 workers per site; command_unit("build",x,y) for manual assignment;
#          auto-build: idle workers within 25 tiles walk to nearest incomplete site;
#          structure activates (full HP, functional) when build_progress >= build_required;
#        reserve_food now actually enforced in spawn queue (was declared but never checked);
#        spawn.{type}.pause halts queuing of that unit type (use with upgrade_reserve);
#        economy.upgrade_reserve protects food from spawn queue for a planned upgrade;
#        triggers can fire buy_upgrade as a then-action (not just directive patches);
#        S_BUILDING state added (value=7) — shown by canvas as workers at build site
# 2.10 — FIX upgrade_reserve append-only: DirectiveEngine._merge and patch now treat {}
#           as replace-not-merge, so {"economy.upgrade_reserve": {}} clears the reserve;
#         FIX command_type/command_unit "build" returning "unknown command": _apply_unit_command
#           never had a "build" handler — added with worker-only guard and x,y validation;
#         FIX rally walk-through: soldiers at rally (within 4 tiles) now only react to
#           enemies within 5 tiles (was 15), preventing scouts from pulling them off station

# Vision radii for visual fog-of-war (Chebyshev / square reveal)
VISION_RADIUS = {0: 4, 1: 5, 2: 8, 3: 3}  # worker, soldier, scout, queen (base)
SCOUT_VISION_RADIUS = [8, 12, 16, 22]       # scout vision by tier — the core scout upgrade

# ── Brain config ────────────────────────────────────────────────────────────────
# Each colony is independently "bot" (heuristic) or "llm" (OpenAI-compatible API).
# Stored as module globals; updated live via the dashboard ⚙.

PROVIDERS_PATH = "providers.json"

def _load_providers():
    if not os.path.exists(PROVIDERS_PATH):
        return []
    try:
        with open(PROVIDERS_PATH) as f:
            return json.load(f)
    except Exception:
        return []

def _save_providers(providers):
    with open(PROVIDERS_PATH, "w") as f:
        json.dump(providers, f, indent=2)

PROVIDERS = _load_providers()

LLM_INTERVAL = int(os.environ.get("LLM_INTERVAL", "100"))

def _default_llm_brain():
    return {"type": "llm",
            "api_key":  "",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model":    "deepseek-ai/deepseek-r1"}

# Brain configs: 0=RED, 1=BLUE — loaded from .env on startup
RED_BRAIN:  dict = {"type": "bot"}
BLUE_BRAIN: dict = {"type": "bot"}

def _apply_env_config():
    """Load brain config from .env — new-style fields first, legacy fallback."""
    global RED_BRAIN, BLUE_BRAIN, LLM_INTERVAL
    LLM_INTERVAL = int(os.environ.get("LLM_INTERVAL", LLM_INTERVAL))
    # New-style fields (written by _save_config after first use)
    red_type  = os.environ.get("RED_BRAIN_TYPE",  "")
    blue_type = os.environ.get("BLUE_BRAIN_TYPE", "")
    if red_type or blue_type:
        RED_BRAIN  = {"type": red_type  or "bot"}
        BLUE_BRAIN = {"type": blue_type or "bot"}
        if red_type == "llm":
            RED_BRAIN.update({"api_key":  os.environ.get("RED_API_KEY",  ""),
                               "base_url": os.environ.get("RED_BASE_URL", ""),
                               "model":    os.environ.get("RED_MODEL",    "")})
        if blue_type == "llm":
            BLUE_BRAIN.update({"api_key":  os.environ.get("BLUE_API_KEY",  ""),
                                "base_url": os.environ.get("BLUE_BASE_URL", ""),
                                "model":    os.environ.get("BLUE_MODEL",    "")})
        return
    # Legacy fallback: NVIDIA_API_KEY + LLM_COLONY style
    api_key  = os.environ.get("NVIDIA_API_KEY", "")
    base_url = os.environ.get("LLM_BASE_URL",  "https://integrate.api.nvidia.com/v1")
    model    = os.environ.get("LLM_MODEL",     "deepseek-ai/deepseek-r1")
    colony   = int(os.environ.get("LLM_COLONY", "1"))
    if api_key:
        brain = {"type": "llm", "api_key": api_key, "base_url": base_url, "model": model}
        if colony == 0:
            RED_BRAIN  = brain
        else:
            BLUE_BRAIN = brain

_apply_env_config()

def _brain_for(colony_id):
    return RED_BRAIN if colony_id == 0 else BLUE_BRAIN

def _save_config(data):
    """Update brain globals from dict and persist to .env."""
    global RED_BRAIN, BLUE_BRAIN, LLM_INTERVAL, TPS
    if "red_brain" in data:
        RED_BRAIN  = dict(data["red_brain"])
    if "blue_brain" in data:
        BLUE_BRAIN = dict(data["blue_brain"])
    LLM_INTERVAL = max(10, int(data.get("LLM_INTERVAL", LLM_INTERVAL)))
    TPS          = max(1, min(60, int(data.get("TPS", TPS))))
    _write_env()

def _write_env():
    rb, bb = RED_BRAIN, BLUE_BRAIN
    # Derive legacy compat fields from whichever brain is LLM (BLUE preferred)
    llm_brain  = bb if bb["type"] == "llm" else (rb if rb["type"] == "llm" else None)
    legacy_key = llm_brain.get("api_key",  "") if llm_brain else ""
    legacy_url = llm_brain.get("base_url", "") if llm_brain else ""
    legacy_mod = llm_brain.get("model",    "") if llm_brain else ""
    legacy_col = 1 if (bb["type"] == "llm") else 0
    with open(_ENV_PATH, "w") as f:
        f.write("# Swarm Wars — Configuration (auto-generated by dashboard)\n\n")
        f.write("# ── Brain Assignments ────────────────────────────────────────────────────\n")
        f.write(f"RED_BRAIN_TYPE={rb['type']}\n")
        if rb["type"] == "llm":
            f.write(f"RED_API_KEY={rb.get('api_key','')}\n")
            f.write(f"RED_BASE_URL={rb.get('base_url','')}\n")
            f.write(f"RED_MODEL={rb.get('model','')}\n")
        f.write(f"BLUE_BRAIN_TYPE={bb['type']}\n")
        if bb["type"] == "llm":
            f.write(f"BLUE_API_KEY={bb.get('api_key','')}\n")
            f.write(f"BLUE_BASE_URL={bb.get('base_url','')}\n")
            f.write(f"BLUE_MODEL={bb.get('model','')}\n")
        f.write("\n# ── Game Settings ────────────────────────────────────────────────────────\n")
        f.write(f"LLM_INTERVAL={LLM_INTERVAL}\n")
        f.write(f"TPS={TPS}\n")
        f.write("\n# ── Legacy compat (for tools reading old .env format) ────────────────────\n")
        f.write(f"NVIDIA_API_KEY={legacy_key}\n")
        f.write(f"LLM_BASE_URL={legacy_url}\n")
        f.write(f"LLM_MODEL={legacy_mod}\n")
        f.write(f"LLM_COLONY={legacy_col}\n")

def _current_config():
    return {
        "red_brain":    RED_BRAIN,
        "blue_brain":   BLUE_BRAIN,
        "LLM_INTERVAL": LLM_INTERVAL,
        "TPS":          TPS,
        "LLM_ENABLED":  any(b["type"] == "llm" for b in (RED_BRAIN, BLUE_BRAIN)),
    }

async def _fetch_models(base_url: str, api_key: str) -> list:
    """Call /models on an OpenAI-compatible API and return sorted model id list."""
    if not base_url:
        return []
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return []
                body = await r.json(content_type=None)
                models = [m["id"] for m in body.get("data", []) if isinstance(m, dict) and "id" in m]
                return sorted(models)
    except Exception:
        return []

_LLM_SYSTEM_TEMPLATE = """\
You are the strategic commander of the {MY} ant colony in Swarm Wars, a real-time ant colony RTS.

OBJECTIVE: Kill the enemy {EN} queen (900 HP). You win when her HP reaches 0.

HOW IT WORKS:
- You set high-level strategy every ~10 seconds. Ants execute autonomously between your calls.
- Workers gather food from known sources. Scouts paint vision across the map and auto-report discoveries to you. Soldiers fight.
- Scouts are your eyes: their vision radius scales with upgrades (T0=8, T1=12, T2=16, T3=22 tiles).
  Everything within a scout's vision is automatically added to your FOOD INTEL and ENEMY SIGHTINGS.
  Workers explore when all known food is depleted — they wander and report new sources back.
  Without scouts you are blind: stale intel, unknown enemy positions, missed frontline food.
- Food is the only resource. Workers gather it; all units cost food to spawn.
- No recurring upkeep — ants cost food once at spawn. Income shows deliveries per second.
- Dead ants leave harvestable corpses worth half their spawn cost — big battles give the winner a surge.

SPAWN COSTS & TIMES:
  worker=25♦/3t    soldier=50♦/5t    scout=35♦/4t    (queue cap: 15 slots)
  Each worker delivery = 30♦ to treasury. Workers carry 20♦ from each food node per trip.
  Food is RESERVED immediately when an ant is queued — not when it spawns.
  If spawn fails (refunded), you'll see "spawn failed" in events.
LIFESPAN: worker=500t   soldier=300t   scout=200t   queen=∞
  → SPAWN QUEUE in your state shows what is cooking + food already reserved.
  → AGING OUT SOON shows ants in final 20% of lifespan — plan replacements NOW.

FOOD NODE TIERS — expansion is mandatory, not optional:
  home     (cap 400,  regrow 0.1/t)  — depletes in ~3 min. Just a starting cache. Move on fast.
  approach (cap 800,  regrow 0.5/t)  — drains in ~5 min under use. Must push to center to survive.
  contested(uncapped, regrow 20/t)   — INEXHAUSTIBLE. Holding center nodes wins the long game.
  → Home and approach nodes WILL run dry. ONLY contested center nodes sustain a large army.
  → Both sides start on the same home+approach economy. First to contest center gains a decisive edge.
  → Build watchtowers and guard posts at the ridge passes (x≈49, x≈101) to lock down a lane.

RESPONSE FORMAT — you can use either format (both accepted):

OPTION A — DIRECTIVE PATCH (preferred, new format):
  Patch exactly the fields you want to change using nested directive structure.
  {{"directive": {{
    "spawn":    {{"worker": {{"target_ratio": 0.50, "max": 30}}, "soldier": {{"target_ratio": 0.35}}}},
    "military": {{"stance": "aggressive", "rally_point": [42, 35], "rally_release_at": 12}},
    "economy":  {{"priority_food": [22, 38]}},
    "unit_types": {{"scout": {{"expansion": [-1, 0]}}}},
    "triggers": [{{"label": "eco_emergency", "if": "food < 75 AND income_per_s < 5 AND elapsed_ticks > 100 AND soldiers_in_siege == 0",
                   "then": {{"military.retreat": true, "spawn.soldier.target_ratio": 0.05}}}}]
  }}}}
  — Only fields you include are changed. Anything omitted stays as-is.
  — Dot-notation also works: "military.stance" as a key patches that single field.

OPTION B — LEGACY FLAT KEYS (backward compat):
  "roles": {{"worker": W, "scout": S, "soldier": M}}   — production ratios, must sum to 1.0
  "defense": "aggressive" | "balanced" | "defensive"
  "worker_cap": <int>
  "rally_point": [x, y] | null
  "rally_release_at": <int> | null
  "expansion": [dx, dy]
  "priority_food": [x, y] | null
  "siege_priority": "queen" | null
  "formation": "column" | "wedge" | "spread"
  "attack_target": [x, y] | null
  "retreat": true | false
  "freeze_economy": true | false

SIDE-EFFECT COMMANDS (always flat, work with either format above):
  "cancel_spawn": "worker" | "soldier" | "scout" | "all"
      — cancel queued spawn entries of that type (or all) and refund reserved food immediately.
        Use before buying an upgrade to free up food. Response includes food_refunded.
  "buy_upgrade": "worker" | "scout" | "soldier" | true
      — queue a specific upgrade tree (or true = buy cheapest available)
      WORKER TREE (economic):
        W1   500 food → workers carry +8/trip (12→20)
        W2  2200 food → carry +12 more (→32/trip)
        W3  6000 food → carry +18 more (→50/trip) + loaded workers move faster
      SCOUT TREE (vision/intel):
        S1   450 food → vision radius 8→12 tiles (scouts reveal 2.25× map area per tick)
        S2  2000 food → vision radius 12→16, double explore speed
        S3  5500 food → vision radius 16→22 (see across an entire pass from one unit)
      SOLDIER TREE (combat):
        Sol1  600 food → +10 damage per hit (22→32)
        Sol2 2800 food → +80 HP (200→280), attack cooldown 4→3
        Sol3 7500 food → splash: 40% damage to all enemies adjacent to primary target
  "build": [x, y]
      — construct a Guard Post at coordinate [x, y]. Cost: 500 food. Max 3 per colony.
        Stats: 300 HP, 18 dmg/shot, range 10 tiles, 3-tick cooldown. Shown as 'T' on minimap.
  "convert": {{"id": <ant_id>, "to": "worker"|"scout"|"soldier"}}
      — convert an ant to a different type. Ant must be within 8 tiles of the queen.
        Cost: worker=15♦  scout=22♦  soldier=30♦. Resets HP and lifespan. Instant.
        Use ant IDs from the UNITS section. Example: convert a surplus worker to a soldier.

TRIGGER SYSTEM — set-and-forget autonomous policy:
  Triggers in "directive.triggers" evaluate every tick and auto-patch the directive.
  Variables: food, dirt, income_per_s, queen_hp, queen_hp_pct, worker_count, soldier_count,
             scout_count, total_pop, soldiers_in_siege, soldiers_near_enemy_nest,
             enemy_soldiers_near_nest, queen_under_attack (bool), enemy_queen_hp,
             elapsed_ticks, aging_workers, aging_soldiers,
             enemy_intel_age  (ticks since last scout near enemy nest; 9999 if never scouted)
    soldiers_in_siege      = own soldiers within 12t of enemy nest (queen visible, dealing damage)
    soldiers_near_enemy_nest = own soldiers within 20t of enemy nest (advancing, not yet in siege)
    enemy_intel_age        = use this! if > 50, send scouts. if > 100, you're flying blind.
  NOTE: income_per_s is food delivered in the last 10 ticks (window resets every 10t), smoothed over 5 windows.
  Syntax: "if": "food < 100 AND income_per_s < 5"  (AND/OR, <, >, <=, >=, ==)
  Action: "then": {{"military.retreat": true}}   (dot-notation patches)
    Special then-action: "buy_upgrade": "worker"|"scout"|"soldier" — trigger fires the upgrade purchase.
    Example: {{"label": "buy_worker_t1", "if": "food > 650", "then": {{"buy_upgrade": "worker"}}, "cooldown": 600}}
  Priority: "priority": N (higher N fires first and wins conflicts). Default 0.
    CRITICAL: use priority to prevent low-priority triggers from overriding high-priority ones.
    ALWAYS add "AND soldiers_in_siege == 0" to eco_emergency to prevent retreat during active siege.
    ALWAYS add "AND queen_under_attack == False" to siege triggers to auto-defend when attacked.
  Cooldown: "cooldown": N (ticks between fires). Without it, trigger re-applies every tick it's true.
    Use cooldown on noisy triggers: eco_emergency cooldown=50 prevents spam, burst_mode cooldown=100.
  NOTE: triggers can patch directive fields AND fire buy_upgrade. build/convert must be direct commands.
  Example triggers:
    eco_emergency: if food < 75 AND income_per_s < 5 AND elapsed_ticks > 100 AND soldiers_in_siege == 0 AND queen_under_attack == False → retreat, priority=5
      *** income_per_s is naturally 0 for the first ~60 ticks (workers haven't delivered yet). ALWAYS use elapsed_ticks > 100 in eco_emergency to avoid false triggers. ***
    queen_defense: if queen_under_attack == True AND soldiers_in_siege < 3 → retreat=true, priority=8
    push_detected: if soldiers_near_enemy_nest >= 5 → stance=aggressive, siege_priority=queen, priority=7
    siege_push:    if soldiers_in_siege >= 8 AND enemy_queen_hp < 600 → siege_priority=queen, priority=10
    final_push:    if soldiers_in_siege >= 1 AND enemy_queen_hp < 300 → retreat=false, priority=15
    aging_refresh: if aging_soldiers > 5 → keep soldier ratio up to replace losses

BUILDINGS — use dirt (◆) to construct; workers collect dirt passively near deposits.
  CONSTRUCTION: dirt is deducted immediately; structure starts INACTIVE (scaffolding, not functional).
  Workers within 2 tiles auto-contribute (T0=1/tick, T1=2, T2=3, T3=4; max 4 workers).
  Structure activates when build_progress reaches build_required. Speed it up:
    command_type(colony_id, "worker", "build", x=N, y=M) — send all workers to site
  Guard Post ({GUARD_POST_COST}◆, max {GUARD_POST_MAX}): attacks enemies in range {GUARD_POST_RANGE} tiles. [build 100 work]
  Watchtower  ({WATCHTOWER_COST}◆, max {WATCHTOWER_MAX}): permanent fog-of-war reveal radius {WATCHTOWER_VISION}. [build 60 work]
  Barracks    ({BARRACKS_COST}◆, max {BARRACKS_MAX}): spawns soldiers at front-line location. [build 150 work]
  Wall        ({WALL_COST}◆/tile, max {WALL_MAX}): impassable tile. Channel enemy movement. [build 25 work]
  Larder      ({LARDER_COST}◆, max {LARDER_MAX}): generates {LARDER_INCOME}♦/tick passively. [build 120 work]
  Command: {{"build": {{"type": "watchtower", "x": 45, "y": 30}}}}
  Enable active dirt gathering: {{"directive": {{"economy": {{"gather_dirt": true}}}}}}

DIRECTIVE QUICK REFERENCE:
  spawn.worker.target_ratio / spawn.scout.target_ratio / spawn.soldier.target_ratio
  spawn.worker.min_ratio / spawn.scout.min_ratio / spawn.soldier.min_ratio  ← floor, survives triggers
  spawn.worker.max (= worker_cap)
  spawn.reserve_food — food floor below which NO units are queued (default 50)
  spawn.{type}.pause — set true to stop queuing that type entirely (use to save for upgrades)
  economy.upgrade_reserve — {{"worker"|"scout"|"soldier": amount}} protects food from spawn queue
    Pattern to buy an upgrade without being blocked by spawn queue:
      {{"spawn": {{"scout": {{"pause": true}}}}, "economy": {{"upgrade_reserve": {{"scout": 450}}}}}}
      → then buy_upgrade("scout") once food ≥ 450; clear reserve + unpause after purchase
  economy.priority_food — [x,y] redirects ALL workers to this node; null = workers choose freely
    Use [75,50] for sustained income once home/approach food drains. This is the most impactful
    single directive change when income drops mid-game.
  economy.gather_dirt (bool)
  military.stance ("aggressive"|"balanced"|"defensive")
  military.formation ("column"|"wedge"|"spread")
  military.rally_point / military.rally_release_at / military.rally_mode
  military.attack_target / military.auto_attack / military.retreat / military.siege_priority
  unit_types.scout.expansion / unit_types.soldier.expansion
  unit_types.scout.patrol_waypoints: [[x1,y1],[x2,y2],...] — scouts loop FOREVER through these.
    USE THIS to prevent intel blackouts. Without it, scouts die/age and leave you blind for 80+ ticks.
    CRITICAL: Scout lifespan=200t. Keep total Chebyshev route length under 180 tiles or scouts die mid-route.
    NEVER route scouts to enemy nest — they die before returning. Cover YOUR half + center food zone.
    Example (RED): {{"directive": {{"unit_types": {{"scout": {{"patrol_waypoints":
      [[14,50],[45,20],[62,19],[75,50],[62,81],[45,80],[14,50]]
    }}}}}}}}
    Example (BLUE): {{"directive": {{"unit_types": {{"scout": {{"patrol_waypoints":
      [[136,50],[105,20],[88,19],[75,50],[88,81],[105,80],[136,50]]
    }}}}}}}}
    Route: home → north approach → north frontline food → center food → south frontline food → south approach → home (~158t loop)
    Scouts still pick up food intel they pass. Set this EARLY and leave it running all game.

TACTICAL NOTES:
  defense stance:
    aggressive = soldiers patrol 45-90 tiles toward enemy nest (push/attack)
    balanced   = soldiers patrol center territory (50/50)
    defensive  = soldiers stay 15-35 tiles from own nest (hold the line)
  formation (aggressive mode only):
    column = single-file spike — punches through chokepoints
    wedge  = default (0.15 rad spread) — balanced advance
    spread = wide fan — encircles defenders
  rally_point: soldiers HOLD at coordinate until staged count hits rally_release_at.
    Single point: [x, y]. Waypoints: [[x1,y1],[x2,y2],[x3,y3]] — advance through them in sequence.
    rally_mode: "normal" (clear on release) | "auto_forward" (advance rally toward enemy on release)
  attack_target: soldiers advance continuously — do NOT hold when they arrive.
  auto_attack: true — soldiers automatically target enemy nest/queen using only fog-of-war-known info.
    Queen exact pos only revealed once soldiers reach siege range; otherwise advances to enemy nest.
  min_ratio: production floor for each unit type (0.0 = no floor). Useful to prevent zeroing out soldiers/scouts during eco emergencies. E.g. spawn.soldier.min_ratio=0.05 always keeps soldier trickle.
  freeze_economy legacy key: atomically sets soldier=0.95, worker=0, worker_cap=0.

MAP: {MAP_W}×{MAP_H} tiles — "The Crossing". Three-lane structure with rocky ridges.
  Rocky ridges run N-S at ~x=49 and ~x=101 with three passes: north (y≈19), center (y≈50), south (y≈80).
  # = rock (impassable). Passes are chokepoints — good spots for walls, guard posts, watchtowers.
  Your nest is fixed at game start — no placement phase.
Use own_queen_pos and enemy_nest from state for actual coordinates.
SIEGE MODE: When your soldiers reach within 12 tiles of the enemy nest they automatically
hunt the queen directly. `soldiers_in_siege` in your state tells you exactly how many
soldiers are already in siege range right now. Enemy queen_hp only revealed when sieging.

FOG OF WAR:
- You do NOT have omniscient knowledge of the enemy. You only know what your scouts
  and soldiers have observed near the enemy nest (within ~18 tiles).
- Enemy army counts shown are from your LAST SCOUT — they may be stale or unknown.
- CRITICAL: enemy queen_hp shows 900 when no friendly ant is near the enemy nest.
  A value of 900 means unobserved (not necessarily full health). A drop BACK to 900
  after it was lower means your siege broke and ants retreated out of range — the
  queen did NOT regenerate. Queens do NOT have HP regeneration.
- Enemy food and income are NEVER visible. Infer their economic state from:
  pressure (do their soldiers keep coming?), tier upgrades you've seen, and activity.
- Keep scouts active to maintain fresh intel on enemy forces.

IMPORTANT RULES:
- NEVER set "roles" with 0 workers manually unless you have 500+ food banked AND are
  actively sieging. Use "freeze_economy": true for intentional all-in — it sets workers=0
  safely. Zero workers = starvation in 20-30 seconds at normal army sizes.
- Scouts reveal the map as they move. Every food node, enemy structure, and enemy unit group
  they see is auto-logged to your FOOD INTEL and ENEMY SIGHTINGS. Higher tier = wider vision.
  Without scouts, your FOOD INTEL goes stale, you miss frontline nodes, and you fight blind.
  If you must cut production keep at least scout min_ratio=0.05 so coverage doesn't die out.
- "retreat" pulls soldiers home but does NOT reduce upkeep — retreating soldiers still cost
  food. Use it to regroup, not to save food. Reduce roles.soldier to actually cut upkeep.
- "attack_target" overrides patrol direction but NOT rally_point — clear rally first if set.
- rally_point should be set to a staging tile NEAR the enemy nest, not ON it.
  Use own_queen_pos and enemy_queen_pos from state to pick real coordinates.
  Good staging point: midpoint between nests, or 15-20 tiles short of enemy nest.
- If soldiers_in_siege > 3 and enemy queen_hp < 600, PUSH — do not hold back.
  That is a winning position. Set defense="aggressive", clear rally_point to let
  soldiers advance freely into siege range.

MEMORY: Per-match scratch pad — resets when a new game starts. Shown each turn under
YOUR MEMORY. Update it by adding "memory": {{"key": "value"}} to your JSON.
  - Keys and values are arbitrary strings you choose. Null a key to delete it.
  - Use it to track strategic plans, discovered coordinates, and lessons THIS game.
  - Keep entries concise — there's a ~800 char budget. Curate ruthlessly.
  Example: "memory": {{"rally": "(28,28) works as staging point", "focus": "eco first"}}

MAP: The minimap (shown each turn) uses 30×20 cells (each = 5×5 tiles).
  B=your nest  R=enemy nest  T=your Guard Post  t=enemy Guard Post
  f=known food  S=your soldiers (2+ in cell)
  E=enemy last-scouted position  ~=water  #=rock (ridge)

SCOUT DIRECTION: "expansion": [dx, dy] biases BOTH scout exploration AND soldier
  patrol direction. To gather intel near enemy: set expansion toward enemy nest.
  Example: if enemy is at (15,15) and you're at (84,59), use "expansion": [-1,-1].

Reason through the current situation carefully, then output ONLY a JSON object — \
no markdown fences, no extra text outside the think block.
Format: <think>your reasoning here</think>{{"key": value, ...}}
If no change is needed, respond: <think>reasoning</think>{{}}

REQUIRED FIELDS (always include both):
  "feedback": "What game state data was unclear, missing, or misleading this turn? \
What lever or info would have changed your decision? Be specific — cite the exact \
data gap (e.g. 'no visibility into enemy upgrade timing', 'income spike confused me', \
'needed projected starvation time'). This field is read by developers every run \
and directly shapes future versions of this game."
  "memory": {{"key": "value", ...}}  — updates your persistent scratch pad
"""

def _make_llm_system_prompt(my_color: str, enemy_color: str) -> str:
    return _LLM_SYSTEM_TEMPLATE.format(
        MY=my_color, EN=enemy_color,
        MAP_W=MAP_W, MAP_H=MAP_H,
        GUARD_POST_COST=GUARD_POST_COST, GUARD_POST_MAX=GUARD_POST_MAX,
        GUARD_POST_RANGE=GUARD_POST_RANGE,
        WATCHTOWER_COST=WATCHTOWER_COST, WATCHTOWER_MAX=WATCHTOWER_MAX,
        WATCHTOWER_VISION=WATCHTOWER_VISION,
        BARRACKS_COST=BARRACKS_COST, BARRACKS_MAX=BARRACKS_MAX,
        WALL_COST=WALL_COST, WALL_MAX=WALL_MAX,
        LARDER_COST=LARDER_COST, LARDER_MAX=LARDER_MAX, LARDER_INCOME=LARDER_INCOME,
    )

LLM_DEBRIEF_SYSTEM = """\
You are reviewing your performance as the strategic commander of an ant colony RTS.
Respond in free text — NOT JSON. Be specific: cite tick numbers, exact decisions, and
numbers from the stats. Brutal self-assessment is more useful than flattery.
"""

MAP_W, MAP_H = 150, 100
MAP_NAME   = "The Crossing"
RED_SPAWN  = (14, 50)
BLUE_SPAWN = (136, 50)
TILE = 8
TPS = int(os.environ.get("TPS", "10"))

TERRITORY_DECAY = 60   # ticks without ant presence before tile reverts to neutral

# Dirt — construction material resource (separate from food)
DIRT_PICK            = 10     # dirt gathered per ant trip
DIRT_DELIVER         = 8      # dirt deposited to colony per trip
DIRT_REGROW          = 0.8    # per tick (slow — dirt is scarce)
DIRT_REGROW_FRONT    = 2.0    # frontline dirt nodes regrow faster
DIRT_MAX             = 200    # max per scattered node
DIRT_FRONT_MAX       = 999999 # frontline nodes effectively unlimited
DIRT_INIT_MIN        = 80
DIRT_INIT_MAX        = 160
DIRT_CAP             = 600    # colony dirt storage cap

# Building costs (dirt) and stats
WATCHTOWER_COST      = 80    # dirt
WATCHTOWER_HP        = 150
WATCHTOWER_VISION    = 12    # Chebyshev radius of permanent fog-of-war reveal
WATCHTOWER_MAX       = 3

BARRACKS_COST        = 200   # dirt
BARRACKS_HP          = 200
BARRACKS_SPAWN_TIME  = 20    # ticks between soldier spawns (still faster than queen at 35t)
BARRACKS_MAX         = 2

WALL_COST            = 25    # dirt per segment
WALL_HP              = 500
WALL_MAX             = 12    # max wall segments per colony

LARDER_COST          = 150   # dirt to build
LARDER_HP            = 150
LARDER_MAX           = 2     # per colony
LARDER_INCOME        = 6     # food per tick (passive income)

MEMORY_MAX_CHARS = 800

def _trim_memory(mem):
    """Drop oldest keys until memory is within the char budget."""
    while True:
        text = json.dumps(mem, indent=None)
        if len(text) <= MEMORY_MAX_CHARS or not mem:
            break
        oldest = next(iter(mem))
        del mem[oldest]
    return mem

def apply_memory_update(current, update):
    """Merge an LLM memory update dict into current memory. Null values delete keys."""
    if not isinstance(update, dict):
        return current
    for k, v in update.items():
        if v is None:
            current.pop(k, None)
        else:
            current[str(k)] = str(v)
    return current

# Terrain
T_DIRT, T_LEAF, T_WATER, T_ROCK, T_NEST = range(5)

# Ant types
A_WORKER, A_SOLDIER, A_SCOUT, A_QUEEN = range(4)

# Ant states
S_IDLE, S_FORAGING, S_RETURNING, S_EXPLORING, S_FIGHTING, S_PATROLLING, S_RECRUITED, S_BUILDING = range(8)

# Upkeep removed — units have one-time spawn cost only; lifespan is the attrition mechanic

# Food
FOOD_SOURCES   = 17   # 4H + 6A + 7F (added north/south center frontline nodes)
FOOD_MAX_HOME      = 400    # home nodes drain in ~3 min — just a starting cache
FOOD_MAX_APPROACH  = 800    # approach nodes drain in ~5-6 min — bridge to center
FOOD_MAX           = FOOD_MAX_APPROACH  # legacy alias
FOOD_INIT_HOME_MIN = 200;  FOOD_INIT_HOME_MAX = 350
FOOD_INIT_APPR_MIN = 300;  FOOD_INIT_APPR_MAX = 600
FOOD_INIT_FRONT_MIN= 2000; FOOD_INIT_FRONT_MAX= 4000
FOOD_INIT_MIN  = FOOD_INIT_APPR_MIN   # legacy alias
FOOD_INIT_MAX  = FOOD_INIT_APPR_MAX
FOOD_PICK      = 20     # how much one ant haul removes from the node
FOOD_DELIVER   = 30     # how much one haul adds to colony food (scaled up for 150-wide map)
FOOD_NODE_WORKER_CAP = {"home": 4, "approach": 6, "frontline": 12}  # workers before saturation kicks in
FOOD_REGROW    = 0.1    # home nodes barely regenerate — use up and move on
FOOD_CONTESTED_MIN_DIST = 28
FOOD_REGROW_CONTESTED   = 20.0  # contested center nodes: inexhaustible — fight to control them
FOOD_REGROW_APPROACH    = 0.5   # approach drains in 5-6 min — forces push to center

# Stalemate resolution: if neither queen dies within this many real seconds, winner by score
STALEMATE_TIMEOUT = 7200  # 2 hours — effectively never fires; games end by queen death

# Corpse harvesting
CORPSE_FOOD  = [12, 25, 17, 0]  # half of spawn cost: worker=25/2, soldier=50/2, scout=35/2
CORPSE_DECAY = 0.4              # units lost per tick (~30 ticks for soldier, ~150 for queen)

# Queen combat
QUEEN_DMG = 35         # queen hits harder than soldiers
QUEEN_CD  = 3          # faster attack rate than soldiers

# Guard Post defensive structures
GUARD_POST_COST  = 150   # dirt to construct (was 500 food)
GUARD_POST_HP    = 300   # structure hit points
GUARD_POST_DMG   = 18    # damage per shot
GUARD_POST_CD    = 3     # ticks between shots
GUARD_POST_RANGE = 10    # attack range in tiles (Manhattan)
GUARD_POST_MAX   = 3     # max guard posts per colony

# Construction — workers build structures over time instead of instant placement
BUILD_WORK_REQUIRED = {"guard_post": 100, "watchtower": 60, "barracks": 150, "larder": 120, "wall": 25}
BUILD_WORKER_CAP    = 4      # max concurrent builders contributing per site per tick
BUILD_RATE          = [1, 2, 3, 4]   # work units added per worker per tick, indexed by worker tier
BUILD_RANGE         = 2      # Manhattan distance from site center that counts as "at site"

# Per-unit upgrade trees — 3 tiers each
WORKER_UPGRADE_COSTS  = [500,  2200, 6000]
SCOUT_UPGRADE_COSTS   = [450,  2000, 5500]
SOLDIER_UPGRADE_COSTS = [600,  2800, 7500]
UPGRADE_LABELS = {
    "worker":  ["Foraging I",    "Foraging II",     "Foraging III"],
    "scout":   ["Pathfinding",   "Swift",            "Master Recruiter"],
    "soldier": ["Combat I",      "Combat II",        "Combat III"],
}
UPGRADE_EFFECTS = {
    "worker":  ["+8 carry (→20/trip)", "+12 more (→32/trip)", "+18 more (→50/trip) + fast return"],
    "scout":   ["vision 8→12 tiles",          "vision 12→16 + double speed", "vision 16→22 tiles"],
    "soldier": ["+10 damage (→32)",   "+80 HP + cooldown 4→3",  "splash 40% to adjacent enemies"],
}

def _apply_upgrade_effects(c, unit_type, tier):
    """Update colony bonuses when unit_type reaches a new tier (1-3)."""
    if unit_type == "worker":
        c.carry_bonus = [0, 8, 20, 38][tier]
        c.worker_fast = (tier >= 3)
    elif unit_type == "scout":
        # Vision radius handled in _update_fog via SCOUT_VISION_RADIUS[c.scout_tier]
        c.scout_detect  = [5, 9, 9, 14][tier]   # food pickup range (minor bonus)
        c.scout_recruit = [8, 12, 12, 16][tier]  # recruit workers on food return (minor bonus)
        c.scout_fast    = (tier >= 2)            # double explore speed at T2+
    elif unit_type == "soldier":
        c.dmg_bonus          = 10 if tier >= 1 else 0
        c.soldier_hp_bonus   = 80 if tier >= 2 else 0
        c.soldier_fast_cd    = SOLDIER_CD - (1 if tier >= 2 else 0)
        c.soldier_splash     = (tier >= 3)

# Combat
SOLDIER_DMG    = 22
SOLDIER_CD     = 4     # ticks between attacks
SOLDIER_HP     = 200
WORKER_HP      = 55    # fragile — soldiers kill in 2 hits, raids are decisive
SCOUT_HP       = 45    # fragile — scouts die fast if caught
QUEEN_HP       = 900   # queens are killable but very tanky

MAX_EVENTS = 12   # events kept per colony for the dashboard

# ═══════════════════════════════════════════════════════════════════════════════
# Terrain Generation
# ═══════════════════════════════════════════════════════════════════════════════

def _rock_patch(t, cx, cy, r):
    for dy in range(-r, r+1):
        for dx in range(-r, r+1):
            if dx*dx + dy*dy <= r*r:
                x, y = cx+dx, cy+dy
                if 0 <= x < MAP_W and 0 <= y < MAP_H:
                    t[y][x] = T_ROCK

def gen_terrain():
    """'The Crossing' — symmetric 3-lane map with rocky ridges and chokepoint passes."""
    t = [[T_DIRT] * MAP_W for _ in range(MAP_H)]

    # Leaf patches for visual texture (purely cosmetic, still passable)
    rng = random.Random(42)
    for _ in range(20):
        cx, cy = rng.randint(5, MAP_W-6), rng.randint(5, MAP_H-6)
        r = rng.randint(2, 4)
        for dy in range(-r, r+1):
            for dx in range(-r, r+1):
                if dx*dx + dy*dy <= r*r:
                    x, y = cx+dx, cy+dy
                    if 0 <= x < MAP_W and 0 <= y < MAP_H:
                        if t[y][x] == T_DIRT:
                            t[y][x] = T_LEAF

    # Rocky ridges at x=48-50 and x=100-102 (3 tiles wide)
    # Three passes: north y=[13,25], center y=[44,56], south y=[74,86]
    PASSES = [(13, 25), (44, 56), (74, 86)]
    for rx in (48, 49, 50, 100, 101, 102):
        for y in range(MAP_H):
            if not any(y0 <= y <= y1 for y0, y1 in PASSES):
                t[y][rx] = T_ROCK

    # Corner rock clusters — creates defensive texture near each base
    _rock_patch(t,   6,  7, 3)   # RED NW
    _rock_patch(t,   6, 92, 3)   # RED SW
    _rock_patch(t, 144,  7, 3)   # BLUE NE
    _rock_patch(t, 144, 92, 3)   # BLUE SE

    # Center pinch rocks — slight constriction at center lane flanks
    _rock_patch(t, 71, 34, 2)
    _rock_patch(t, 79, 66, 2)

    return t

# ═══════════════════════════════════════════════════════════════════════════════
# Ant
# ═══════════════════════════════════════════════════════════════════════════════

class Ant:
    _id = 0
    # Lifespan in ticks (None = infinite, for queen)
    LIFESPAN = {0: 500, 1: 300, 2: 200, 3: None}  # worker, soldier, scout, queen

    def __init__(self, x, y, colony, ant_type, born_tick=0):
        Ant._id += 1
        self.id = Ant._id
        self.x = x
        self.y = y
        self.colony = colony
        self.type = ant_type
        self.state = S_IDLE
        self.carrying = False
        self.carrying_type = "food"  # "food" | "dirt"
        self.hp = {A_WORKER: WORKER_HP, A_SOLDIER: SOLDIER_HP,
                   A_SCOUT: SCOUT_HP, A_QUEEN: QUEEN_HP}[ant_type]
        self.max_hp = self.hp
        self.prev_x = x
        self.prev_y = y
        self.tx = None
        self.ty = None
        self.cooldown = 0
        self.recruit_target = None  # (food_x, food_y) set by scout recruitment
        # Lifespan
        self.born_tick = born_tick
        self.lifespan = self.LIFESPAN.get(ant_type)  # None for queen
        self.age = 0  # incremented each tick
        # Per-unit behavior config (individual > birth_config > directive.unit_types > defaults)
        self.behavior_config = {}   # individual override via set_unit_config
        self.birth_config    = {}   # injected at spawn time by spawner birth_config
        self.unit_override   = None # MCP direct command: {cmd, x, y, waypoints, ...}

    def resolved_config(self, colony_directive):
        """Merge: individual override > birth_config > directive.unit_types[type] > hardcoded defaults."""
        type_name = ["worker", "soldier", "scout", "queen"][self.type]
        defaults = {
            "worker":  {"flee_distance": 4},
            "soldier": {"expansion": [1, 1]},
            "scout":   {"expansion": [1, 1], "revisit_pct": 0.12},
            "queen":   {},
        }[type_name]
        cfg = {**defaults}
        cfg.update(colony_directive.get("unit_types", {}).get(type_name, {}))
        cfg.update(self.birth_config)
        cfg.update(self.behavior_config)
        return cfg

# ═══════════════════════════════════════════════════════════════════════════════
# DirectiveEngine — colony policy management
# ═══════════════════════════════════════════════════════════════════════════════

class DirectiveEngine:
    """Manages colony directive: apply, patch (dot-notation), trigger eval, alert checking."""

    @staticmethod
    def default_directive(expansion_x, expansion_y):
        ex, ey = expansion_x, expansion_y
        return {
            "spawn": {
                "worker":  {"target_ratio": 0.45, "min_ratio": 0.0, "min": 4,  "max": 40, "pause": False, "birth_config": {}},
                "soldier": {"target_ratio": 0.35, "min_ratio": 0.0, "min": 2,  "max": 30, "pause": False, "birth_config": {}},
                "scout":   {"target_ratio": 0.20, "min_ratio": 0.0, "min": 2,  "max": 12, "pause": False, "birth_config": {}},
                "reserve_food": 50,
                "burst_at": 800,
            },
            "economy": {
                "upgrade_priority": ["scout", "worker", "soldier"],
                "auto_upgrade": True,
                "priority_food": None,
                "gather_dirt": False,   # set True to have workers actively collect dirt for building
                "upgrade_reserve": {},  # {unit_type: food_amount} — food protected from spawn queue for a planned upgrade
            },
            "military": {
                "stance": "aggressive",
                "formation": "wedge",
                "rally_point": None,
                "rally_release_at": None,
                "rally_mode": "normal",
                "attack_target": None,
                "auto_attack": False,
                "retreat": False,
                "freeze_economy": False,
                "siege_priority": None,
            },
            "unit_types": {
                "worker":  {"flee_distance": 4},
                "soldier": {"expansion": [ex, ey]},
                "scout":   {"expansion": [ex, ey], "revisit_pct": 0.12, "patrol_waypoints": None},
            },
            "triggers": [],
            "alerts": [
                {"label": "queen_critical",  "if": "queen_hp_pct < 0.30", "sampling": True},
                {"label": "low_income", "if": "food < 80 AND income_per_s < 5", "sampling": False},
            ],
        }

    @staticmethod
    def patch(colony, partial):
        """Deep-merge partial into colony.directive. Supports dot-notation keys.
        Empty dict {} replaces rather than merges (allows clearing fields like upgrade_reserve)."""
        d = colony.directive
        for key, value in partial.items():
            if "." in key:
                parts = key.split(".")
                target = d
                for part in parts[:-1]:
                    if part not in target or not isinstance(target[part], dict):
                        target[part] = {}
                    target = target[part]
                target[parts[-1]] = value
            elif isinstance(value, dict) and value and key in d and isinstance(d[key], dict):
                DirectiveEngine._merge(d[key], value)
            else:
                d[key] = value  # {} replaces, non-dict replaces, new key set

    @staticmethod
    def _merge(base, override):
        for k, v in override.items():
            if isinstance(v, dict) and v and k in base and isinstance(base[k], dict):
                DirectiveEngine._merge(base[k], v)
            else:
                base[k] = v  # {} clears a nested dict cleanly

    @staticmethod
    def eval_triggers(colony, world):
        """Evaluate triggers; auto-patch directive when conditions are met."""
        triggers = colony.directive.get("triggers", [])
        if not triggers:
            return
        counts = [0, 0, 0, 0]
        for a in colony.ants:
            counts[a.type] += 1
        queen = next((a for a in colony.ants if a.type == A_QUEEN), None)
        queen_hp = queen.hp if queen else 0
        soldiers_in_siege = 0
        soldiers_near_enemy_nest = 0
        enemy_soldiers_near_nest = 0
        enemy_queen_hp = float(QUEEN_HP)  # default: assume full HP until seen
        if colony.enemy:
            for a in colony.ants:
                if a.type == A_SOLDIER:
                    d_enemy = abs(a.x - colony.enemy.nx) + abs(a.y - colony.enemy.ny)
                    if d_enemy <= 12:
                        soldiers_in_siege += 1
                    if d_enemy <= 20:
                        soldiers_near_enemy_nest += 1
            for a in colony.enemy.ants:
                if a.type == A_SOLDIER and abs(a.x - colony.nx) + abs(a.y - colony.ny) <= 15:
                    enemy_soldiers_near_nest += 1
            # Queen HP only known when our soldiers are in siege range
            if soldiers_in_siege > 0:
                eq = next((a for a in colony.enemy.ants if a.type == A_QUEEN), None)
                if eq:
                    enemy_queen_hp = eq.hp
        aging_workers  = sum(1 for a in colony.ants
                             if a.type == A_WORKER  and a.lifespan and a.age >= int(a.lifespan * 0.80))
        aging_soldiers = sum(1 for a in colony.ants
                             if a.type == A_SOLDIER and a.lifespan and a.age >= int(a.lifespan * 0.80))
        enemy_intel_age = (world.tick - colony.enemy_scouted_tick
                           if hasattr(colony, 'enemy_scouted_tick') else 9999)
        ns = {
            "food": colony.food,
            "dirt": colony.dirt,
            "income_per_s": colony.income_smooth if colony.income_history else colony.income_per_s,
            "queen_hp": queen_hp,
            "queen_hp_pct": queen_hp / QUEEN_HP if QUEEN_HP else 0,
            "worker_count": counts[0],
            "soldier_count": counts[1],
            "scout_count": counts[2],
            "total_pop": len(colony.ants),
            "enemy_soldiers_near_nest": enemy_soldiers_near_nest,
            "queen_under_attack": enemy_soldiers_near_nest >= 1,
            "soldiers_in_siege": soldiers_in_siege,
            "soldiers_near_enemy_nest": soldiers_near_enemy_nest,
            "enemy_queen_hp": enemy_queen_hp,
            "elapsed_ticks": world.tick,
            "aging_workers": aging_workers,
            "aging_soldiers": aging_soldiers,
            "enemy_intel_age": enemy_intel_age,  # ticks since last scout near enemy nest
        }
        if not hasattr(colony, "trigger_cooldowns"):
            colony.trigger_cooldowns = {}
        for trigger in sorted(triggers, key=lambda t: t.get("priority", 0), reverse=True):
            condition = trigger.get("if", "")
            if not condition:
                continue
            label = trigger.get("label", "?")
            # Cooldown: skip if this trigger fired within cooldown ticks
            cooldown = trigger.get("cooldown", 0)
            if cooldown > 0:
                last = colony.trigger_cooldowns.get(label, -(cooldown + 1))
                if world.tick - last < cooldown:
                    continue
            try:
                expr = condition.replace(" AND ", " and ").replace(" OR ", " or ")
                if eval(expr, {"__builtins__": {}}, ns):  # noqa: S307 — restricted namespace
                    then = trigger.get("then", {})
                    if then:
                        # Separate special actions from directive patches
                        buy_upg = then.get("buy_upgrade")
                        patch_keys = {k: v for k, v in then.items() if k != "buy_upgrade"}
                        if patch_keys:
                            DirectiveEngine.patch(colony, patch_keys)
                        # buy_upgrade trigger action: mark unit type upgrade as pending
                        if buy_upg and buy_upg in ("worker", "scout", "soldier"):
                            setattr(colony, f"{buy_upg}_upgrade_pending", True)
                        if cooldown > 0:
                            colony.trigger_cooldowns[label] = world.tick
                        # Log: only once per 5-tick burst per label (still deduplicate even without cooldown)
                        recent = [e for e in colony.trigger_log
                                  if e["label"] == label and world.tick - e["tick"] < 5]
                        if not recent:
                            colony.trigger_log.append({
                                "tick":    world.tick,
                                "label":   label,
                                "patches": then,
                            })
            except Exception:
                pass  # malformed trigger condition — skip silently

# ═══════════════════════════════════════════════════════════════════════════════
# Colony — with directive-driven strategy
# ═══════════════════════════════════════════════════════════════════════════════

class Colony:
    def __init__(self, cid, nx, ny):
        self.id = cid
        self.nx = nx
        self.ny = ny
        self.ants = []
        self.food = 800.0   # enough to bridge first ~60 ticks before workers deliver
        self.prod_timer = 0
        self.alive = True
        self.food_collected = 0   # lifetime stat
        self.ants_lost = 0        # lifetime stat

        # ── Directive — persistent policy document driving all colony behaviour ──
        # Expansion toward enemy (horizontal on The Crossing; no vertical bias if spawning center)
        ex = 1 if nx < MAP_W // 2 else -1
        ey = 0 if ny == MAP_H // 2 else (1 if ny < MAP_H // 2 else -1)
        self.directive = DirectiveEngine.default_directive(ex, ey)
        # Bot default: worker cap at 50 (LLM can override)
        self.directive["spawn"]["worker"]["max"] = 60
        self.build_queue = []      # [(x, y)] — pending guard post construction orders
        self.structure_queue = []  # [{"type": str, "x": int, "y": int}] — new structure types
        self.convert_queue = []   # [{"id": ant_id, "to": type_name}] — pending conversions
        self.known_food = []       # list of (x, y) discovered by scouts/vision
        self.known_dirt = []       # list of (x, y) of known dirt node locations
        # Queen's intel map — populated by scout vision and worker discovery
        self.food_intel = {}       # (x,y) → {amt, max, tier, last_seen}
        self.seen_structs = {}     # (x,y) → {type, hp_approx, last_seen} — enemy structures ever spotted
        self.enemy_sightings = []  # [(cx, cy, soldiers, total, tick, workers, scouts)] — recent enemy presence, cap 15
        self.dirt = 0              # current dirt stockpile
        self.dirt_earned_tick = 0.0
        self.dirt_per_s = 0.0
        self.events = deque(maxlen=MAX_EVENTS)  # recent events for LLM + dashboard
        self.notifications = deque(maxlen=50)  # MCP notification tray (consume-on-read)
        # Fog of war: track last time a friendly ant was near enemy nest
        self.enemy_scouted_tick   = -9999  # tick when we last had eyes near enemy nest
        self.enemy_scouted_counts = [0, 0, 0, 0]  # [W, S, sc, Q] as of last scout
        # Visual fog-of-war: per-tile seen/visible tracking
        self.fog_explored = bytearray(MAP_W * MAP_H)  # 1 = ever seen
        self.fog_visible  = set()                      # (x,y) currently in unit sight
        # Trigger event log: recent fires shown in LLM prompt
        self.trigger_log      = deque(maxlen=30)
        self.trigger_cooldowns = {}  # {label: last_fired_tick} — enforces cooldown field
        self.enemy_queen_hp_last_seen = None  # last observed enemy queen HP (any unit with vision)
        self.queen_dmg_dealt_tick = 0.0  # damage dealt to enemy queen this 10-tick window
        self.queen_dps_actual = 0.0      # damage to enemy queen per second (rolled each second)
        self.log_queue = []        # drained each tick by RunLogger
        self.food_prev = self.food # kept for compatibility
        self.food_earned_tick = 0.0  # food delivered this 10-tick window (deliveries only)
        self.income_per_s = 0.0
        self.income_smooth = 0.0          # 5-sample rolling avg — filters upgrade cost spikes
        self.income_history = deque(maxlen=5)
        self.income_neg_warned = False
        self.peak_pop = 0
        self.peak_pop_tick = 0

        # Per-unit upgrade tiers (0 = base, 1/2/3 = upgraded)
        self.worker_tier  = 0
        self.scout_tier   = 0
        self.soldier_tier = 0
        self.worker_upgrade_pending  = False
        self.scout_upgrade_pending   = False
        self.soldier_upgrade_pending = False
        # Worker bonuses
        self.carry_bonus  = 0      # extra food per trip (Worker T1/T2/T3)
        self.worker_fast  = False  # loaded workers take 2 steps/tick (Worker T3)
        # Scout bonuses
        self.scout_detect  = 5     # food detection radius in tiles
        self.scout_recruit = 8     # max workers recruited per scout return
        self.scout_fast    = False  # scouts take 2 steps/tick (Scout T2)
        self.spawn_mult    = 1.0   # queen production interval multiplier (Scout T3)
        # Soldier bonuses
        self.dmg_bonus         = 0          # extra damage per hit (Soldier T1)
        self.soldier_hp_bonus  = 0          # extra HP for newly spawned soldiers (Soldier T2)
        self.soldier_fast_cd   = SOLDIER_CD  # attack cooldown ticks (Soldier T2: 4→3)
        self.soldier_splash    = False       # splash 40% to adjacent enemies (Soldier T3)

        # Enemy reference (set by World after both colonies created)
        self.enemy = None

        # ── Spawn queue (explicit, not instant) ──
        # Each entry: (ant_type, ticks_remaining, food_cost)
        self.spawn_queue = []
        self.SPAWN_COST = {A_WORKER: 25, A_SOLDIER: 50, A_SCOUT: 35}
        self.SPAWN_TIME = {A_WORKER: 20, A_SOLDIER: 35, A_SCOUT: 25}  # real build times; forces production planning
        self.MAX_SPAWN_QUEUE = 10  # smaller queue = less pre-reserved food, more surplus for upgrades

        # ── Emergency commands (one-shot overrides) ──
        # Set by LLM, executed once, then cleared
        self.emergency_command = None  # {"type": "RECALL"|"FREEZE"|"FOCUS"|"RETREAT"|"ALL_IN", ...}
        self.emergency_ticks_left = 0  # how many ticks the emergency lasts

    def push_event(self, msg):
        self.events.appendleft(msg)
        self.log_queue.append(msg)

    def push_notification(self, notif_type: str, data: dict = None, tick: int = 0):
        self.notifications.append({"type": notif_type, "tick": tick, "data": data or {}})

    def pop_notifications(self):
        notifs = list(self.notifications)
        self.notifications.clear()
        return notifs

    def drain_events(self):
        ev = list(self.events)
        self.events.clear()
        return ev

    def set_strategy(self, s):
        """Apply strategy. Accepts new directive-patch format OR old flat strategy keys."""
        if "directive" in s:
            DirectiveEngine.patch(self, s["directive"])
        else:
            self._apply_legacy_strategy(s)
        # Side-effect actions (not in directive schema, handled separately)
        if "buy_upgrade" in s:
            v = s["buy_upgrade"]
            if v == "worker":    self.worker_upgrade_pending = True
            elif v == "scout":   self.scout_upgrade_pending  = True
            elif v == "soldier": self.soldier_upgrade_pending = True
            elif v is True:      self._queue_cheapest_upgrade()
        if "build" in s:
            pos = s["build"]
            if pos is None:
                pass
            elif isinstance(pos, dict) and "type" in pos and "x" in pos and "y" in pos:
                # New dict format: {"type": "watchtower", "x": 45, "y": 30}
                entry = {"type": str(pos["type"]), "x": int(pos["x"]), "y": int(pos["y"])}
                if entry not in self.structure_queue:
                    self.structure_queue.append(entry)
            elif isinstance(pos, (list, tuple)) and len(pos) == 2:
                # Legacy: [x, y] → guard post (backward compat)
                x, y = int(pos[0]), int(pos[1])
                if [x, y] not in self.build_queue:
                    self.build_queue.append([x, y])
        if "convert" in s:
            cv = s["convert"]
            if isinstance(cv, dict) and "id" in cv and "to" in cv:
                self.convert_queue.append({"id": int(cv["id"]), "to": str(cv["to"])})
        if "cancel_spawn" in s:
            unit_str = s["cancel_spawn"]
            _type_map = {"worker": A_WORKER, "soldier": A_SOLDIER, "scout": A_SCOUT}
            if unit_str == "all":
                for _, _, cost in self.spawn_queue:
                    self.food += cost
                self.spawn_queue = []
            elif unit_str in _type_map:
                t_int = _type_map[unit_str]
                for t, _, cost in self.spawn_queue:
                    if t == t_int:
                        self.food += cost
                self.spawn_queue = [e for e in self.spawn_queue if e[0] != t_int]

    def _apply_legacy_strategy(self, s):
        """Map old flat strategy keys into the directive schema."""
        d = self.directive
        if "roles" in s:
            r = s["roles"]
            total = sum(r.values())
            if total > 0:
                d["spawn"]["worker"]["target_ratio"]  = r.get("worker",  0.55) / total
                d["spawn"]["scout"]["target_ratio"]   = r.get("scout",   0.25) / total
                d["spawn"]["soldier"]["target_ratio"] = r.get("soldier", 0.20) / total
        if "expansion" in s:
            v = list(s["expansion"]) if isinstance(s["expansion"], tuple) else list(s["expansion"])
            d["unit_types"]["scout"]["expansion"]   = v
            d["unit_types"]["soldier"]["expansion"] = v
        if "defense" in s:
            d["military"]["stance"] = s["defense"]
        if "priority_food" in s:
            pf_val = s["priority_food"]
            if pf_val is None:
                d["economy"]["priority_food"] = None
            elif isinstance(pf_val, (list, tuple)) and len(pf_val) == 2:
                d["economy"]["priority_food"] = list(pf_val)
            # else: ignore bad value (not a 2-element coord)
        if "rally_point" in s:
            rp = s["rally_point"]
            d["military"]["rally_point"] = list(rp) if isinstance(rp, tuple) else rp
        if "worker_cap" in s:
            d["spawn"]["worker"]["max"] = s["worker_cap"] if s["worker_cap"] is not None else 40
        if "rally_release_at" in s:
            d["military"]["rally_release_at"] = s["rally_release_at"]
        if "rally_mode" in s and s["rally_mode"] in ("normal", "auto_forward"):
            d["military"]["rally_mode"] = s["rally_mode"]
        if "siege_priority" in s:
            d["military"]["siege_priority"] = s["siege_priority"]
        if "auto_attack" in s:
            d["military"]["auto_attack"] = bool(s["auto_attack"])
        if "formation" in s and s["formation"] in ("column", "wedge", "spread"):
            d["military"]["formation"] = s["formation"]
        if "attack_target" in s:
            pos = s["attack_target"]
            d["military"]["attack_target"] = (
                [int(pos[0]), int(pos[1])] if isinstance(pos, (list, tuple)) and len(pos) == 2
                else None
            )
        if "retreat" in s:
            d["military"]["retreat"] = bool(s["retreat"]) if s["retreat"] is not None else False
        if "freeze_economy" in s:
            if s["freeze_economy"]:
                d["spawn"]["worker"]["target_ratio"]  = 0.0
                d["spawn"]["scout"]["target_ratio"]   = 0.05
                d["spawn"]["soldier"]["target_ratio"] = 0.95
                d["spawn"]["worker"]["max"] = 0
            else:
                d["spawn"]["worker"]["max"] = 50

    def _queue_cheapest_upgrade(self):
        options = []
        if self.worker_tier  < 3: options.append(("worker",  WORKER_UPGRADE_COSTS[self.worker_tier]))
        if self.scout_tier   < 3: options.append(("scout",   SCOUT_UPGRADE_COSTS[self.scout_tier]))
        if self.soldier_tier < 3: options.append(("soldier", SOLDIER_UPGRADE_COSTS[self.soldier_tier]))
        if options:
            cheapest = min(options, key=lambda x: x[1])[0]
            setattr(self, f"{cheapest}_upgrade_pending", True)

    def get_state(self, world_tick=0):
        """Return colony state dict for LLM prompt — enemy intel gated by fog of war."""
        counts = [0, 0, 0, 0]
        for a in self.ants: counts[a.type] += 1
        queen = next((a for a in self.ants if a.type == A_QUEEN), None)

        # How many own soldiers are currently in siege range of enemy nest
        soldiers_in_siege = 0
        if self.enemy:
            for a in self.ants:
                if a.type == A_SOLDIER:
                    if abs(a.x - self.enemy.nx) + abs(a.y - self.enemy.ny) <= 12:
                        soldiers_in_siege += 1

        own_queen_pos    = (queen.x, queen.y) if queen else None
        enemy_queen      = next((a for a in self.enemy.ants if a.type == A_QUEEN), None) if self.enemy else None
        enemy_queen_pos  = (enemy_queen.x, enemy_queen.y) if enemy_queen else None

        # Enemy summary (always available — colonies know the map)
        enemy_counts = [0, 0, 0, 0]
        enemy_queen_hp = 0
        if self.enemy:
            for a in self.enemy.ants: enemy_counts[a.type] += 1
            eq = next((a for a in self.enemy.ants if a.type == A_QUEEN), None)
            enemy_queen_hp = eq.hp if eq else 0

        own_soldiers  = counts[1]

        # Fog of war: enemy army info only known if we had scouts/soldiers near their nest recently
        # Food and income are NEVER visible — must be inferred from behavior
        ticks_since_recon = (world_tick - self.enemy_scouted_tick) if hasattr(self, 'enemy_scouted_tick') else 9999
        sc = self.enemy_scouted_counts if hasattr(self, 'enemy_scouted_counts') else [0,0,0,0]
        if ticks_since_recon < 150:
            intel_status   = "fresh"          # < ~15-30s ago
            intel_soldiers = sc[1]
        elif ticks_since_recon < 500:
            intel_status   = "stale"          # < ~50-100s ago
            intel_soldiers = sc[1]
        else:
            intel_status   = "unknown"
            intel_soldiers = 0

        def _upgrade_next(unit, costs, tier):
            if tier >= 3: return None
            cost = costs[tier]
            income = max(0.5, self.income_smooth if self.income_history else self.income_per_s)
            short = max(0, cost - int(self.food))
            eta_s = round(short / income, 1) if short > 0 else 0.0
            return {"label": UPGRADE_LABELS[unit][tier],
                    "effect": UPGRADE_EFFECTS[unit][tier],
                    "cost": costs[tier],
                    "food_short": short,
                    "eta_s": eta_s}
        return {
            # Own state
            "food": int(self.food),
            "dirt": int(self.dirt),
            "income_per_s": round(self.income_smooth if self.income_history else self.income_per_s, 1),
            "workers": counts[0], "soldiers": counts[1],
            "scouts": counts[2], "queen_hp": queen.hp if queen else 0,
            "total": len(self.ants),
            "upgrades": {
                "worker":  {"tier": self.worker_tier,  "next": _upgrade_next("worker",  WORKER_UPGRADE_COSTS,  self.worker_tier)},
                "scout":   {"tier": self.scout_tier,   "next": _upgrade_next("scout",   SCOUT_UPGRADE_COSTS,   self.scout_tier)},
                "soldier": {"tier": self.soldier_tier, "next": _upgrade_next("soldier", SOLDIER_UPGRADE_COSTS, self.soldier_tier)},
            },
            "trigger_log": [{"tick": e["tick"], "label": e["label"],
                             "patches": e["patches"]} for e in list(self.trigger_log)[-10:]],
            "known_food": self.known_food[:10],
            "directive": self.directive,
            "food_collected": self.food_collected,
            "ants_lost": self.ants_lost,
            # Positional awareness (enemy queen pos only revealed when soldiers are in siege range)
            "own_queen_pos":    own_queen_pos,
            "enemy_queen_pos":  enemy_queen_pos if soldiers_in_siege > 0 else None,
            "soldiers_in_siege": soldiers_in_siege,
            "active_food_sources": len(self.known_food),
            # Strategic assessment — derived from own observations, not omniscience
            "army_ratio": round(own_soldiers / max(1, intel_soldiers), 2) if intel_soldiers else None,
            "pressure": (
                "overwhelmed" if intel_soldiers > own_soldiers * 2.0 else
                "losing"      if intel_soldiers > own_soldiers * 1.25 else
                "even"        if intel_soldiers > own_soldiers * 0.77 else
                "dominant"    if intel_soldiers > own_soldiers * 0.5 else
                "crushing"
            ) if intel_status != "unknown" else "unknown",
            # Enemy intel — limited by fog of war
            "enemy": {
                "intel": intel_status,        # "fresh" | "stale" | "unknown"

                "ticks_since_recon": ticks_since_recon,
                "workers":  sc[0] if intel_status != "unknown" else None,
                "soldiers": sc[1] if intel_status != "unknown" else None,
                "scouts":   sc[2] if intel_status != "unknown" else None,
                "queen_hp": enemy_queen_hp if soldiers_in_siege > 0 else None,  # fog-of-war: only known when sieging
                "tiers": ([self.enemy.worker_tier, self.enemy.scout_tier, self.enemy.soldier_tier]
                          if (self.enemy and intel_status != "unknown") else None),
            },
        }

    def spawn_initial(self):
        self.ants.append(Ant(self.nx, self.ny, self.id, A_QUEEN))
        for _ in range(6):
            self.ants.append(Ant(self.nx+random.randint(-3,3), self.ny+random.randint(-3,3), self.id, A_WORKER))
        for _ in range(2):
            self.ants.append(Ant(self.nx+random.randint(-3,3), self.ny+random.randint(-3,3), self.id, A_SOLDIER))
        for _ in range(2):
            self.ants.append(Ant(self.nx+random.randint(-3,3), self.ny+random.randint(-3,3), self.id, A_SCOUT))

# ═══════════════════════════════════════════════════════════════════════════════
# LLM helpers
# ═══════════════════════════════════════════════════════════════════════════════

_MAP_COLS, _MAP_ROWS = 30, 20   # ASCII minimap grid size (150/5=30, 100/5=20 — each cell = 5×5 tiles)
_CW = MAP_W / _MAP_COLS         # tiles per cell, x
_CH = MAP_H / _MAP_ROWS         # tiles per cell, y

def build_minimap(world, colony):
    """Return a 20×15 ASCII minimap string showing terrain, nests, food, soldiers."""
    grid = [['.' for _ in range(_MAP_COLS)] for _ in range(_MAP_ROWS)]

    # Terrain — mark cells that are majority water or rock
    for row in range(_MAP_ROWS):
        for col in range(_MAP_COLS):
            tx0, tx1 = int(col * _CW), int((col + 1) * _CW)
            ty0, ty1 = int(row * _CH), int((row + 1) * _CH)
            water = rock = total = 0
            for ty in range(ty0, ty1):
                for tx in range(tx0, tx1):
                    t = world.terrain[ty][tx]
                    if t == T_WATER: water += 1
                    elif t == T_ROCK: rock += 1
                    total += 1
            if water > total * 0.3:  grid[row][col] = '~'
            elif rock > total * 0.3: grid[row][col] = '#'

    # Known food sources
    for fx, fy in colony.known_food:
        col = min(_MAP_COLS-1, int(fx / _CW))
        row = min(_MAP_ROWS-1, int(fy / _CH))
        if grid[row][col] == '.': grid[row][col] = 'f'

    # Own soldier clusters
    soldier_cells = {}
    for ant in colony.ants:
        if ant.type == A_SOLDIER:
            col = min(_MAP_COLS-1, int(ant.x / _CW))
            row = min(_MAP_ROWS-1, int(ant.y / _CH))
            soldier_cells[(row, col)] = soldier_cells.get((row, col), 0) + 1
    for (row, col), count in soldier_cells.items():
        if grid[row][col] not in ('B', 'R'):
            grid[row][col] = 'S' if count >= 2 else 's'

    # Enemy last-known position (from fog-of-war intel)
    ticks_since = world.tick - getattr(colony, 'enemy_scouted_tick', -9999)
    if ticks_since < 500 and colony.enemy:
        eq = next((a for a in colony.enemy.ants if a.type == A_QUEEN), None)
        if eq:
            col = min(_MAP_COLS-1, int(eq.x / _CW))
            row = min(_MAP_ROWS-1, int(eq.y / _CH))
            if grid[row][col] not in ('B',):
                grid[row][col] = 'E'

    # Guard posts — own='T' enemy='t'
    for struct in world.structures:
        sc = min(_MAP_COLS-1, int(struct["x"] / _CW))
        sr = min(_MAP_ROWS-1, int(struct["y"] / _CH))
        if grid[sr][sc] not in ('B', 'R'):
            grid[sr][sc] = 'T' if struct["colony"] == colony.id else 't'

    # Nests — highest priority
    bc = min(_MAP_COLS-1, int(colony.nx / _CW))
    br = min(_MAP_ROWS-1, int(colony.ny / _CH))
    grid[br][bc] = 'B'
    if colony.enemy:
        rc = min(_MAP_COLS-1, int(colony.enemy.nx / _CW))
        rr = min(_MAP_ROWS-1, int(colony.enemy.ny / _CH))
        grid[rr][rc] = 'R'

    rows = ["  " + "".join(grid[r]) for r in range(_MAP_ROWS)]
    rows.append("  B=you  R=enemy  T=your post  t=enemy post  f=food  S=soldiers(2+)  s=soldier(1)  E=enemy(last seen)  ~=water")
    return "\n".join(rows)


def soldier_sectors(colony):
    """Summarise where own soldiers are relative to own nest, rally point, and enemy nest."""
    if not colony.enemy:
        return ""
    rally    = colony.directive["military"]["rally_point"]
    at_rally = defending = patrolling = forward = 0
    for ant in colony.ants:
        if ant.type != A_SOLDIER: continue
        d = abs(ant.x - colony.nx) + abs(ant.y - colony.ny)
        dist_to_enemy = abs(ant.x - colony.enemy.nx) + abs(ant.y - colony.enemy.ny)
        _rr = rally[0] if rally and isinstance(rally[0], (list, tuple)) else rally
        if rally and _rr and abs(ant.x - _rr[0]) + abs(ant.y - _rr[1]) <= 4:
            at_rally += 1
        elif dist_to_enemy <= 20:
            forward   += 1
        elif d <= 25:
            defending += 1
        else:
            patrolling += 1
    parts = []
    if at_rally:   parts.append(f"{at_rally} at rally")
    if defending:  parts.append(f"{defending} near home")
    if patrolling: parts.append(f"{patrolling} midfield")
    if forward:    parts.append(f"{forward} near enemy")
    return ", ".join(parts) if parts else "none"


def build_llm_prompt(colony, tick, world=None, memory=None,
                     my_color="BLUE", enemy_color="RED"):
    s = colony.get_state(world_tick=tick)
    events = list(colony.events)[:12]
    pressure_map = {
        "overwhelmed": "CRITICAL — enemy has 2x+ your soldiers (last intel)",
        "losing":      "BEHIND — enemy has more soldiers (last intel)",
        "even":        "EVEN — armies roughly matched (last intel)",
        "dominant":    "AHEAD — you outnumber enemy soldiers (last intel)",
        "crushing":    "DOMINANT — you have 2x+ enemy soldiers (last intel)",
        "unknown":     "UNKNOWN — send scouts near enemy nest for intel",
    }
    siege     = s.get("soldiers_in_siege", 0)
    own_qpos  = s.get("own_queen_pos")
    ene_qpos  = s.get("enemy_queen_pos")

    if siege > 0:
        _dmg_per_tick = (SOLDIER_DMG + colony.dmg_bonus) / colony.soldier_fast_cd
        _dps = siege * _dmg_per_tick * TPS
        _raw_qhp = s["enemy"]["queen_hp"]
        if _raw_qhp is not None:
            _ttk = _raw_qhp / _dps if _dps > 0 else 9999
            siege_line = (f"SIEGE: {siege} soldiers in range × {SOLDIER_DMG + colony.dmg_bonus}dmg"
                          f"/{colony.soldier_fast_cd}t = {_dps:.0f}dmg/s"
                          f" | enemy queen {int(_raw_qhp)}/900 HP → TTK ~{_ttk:.1f}s")
        else:
            siege_line = (f"SIEGE: {siege} soldiers in range × {SOLDIER_DMG + colony.dmg_bonus}dmg"
                          f"/{colony.soldier_fast_cd}t = {_dps:.0f}dmg/s | enemy queen HP unknown")
    else:
        siege_line = "SIEGE: 0 soldiers in enemy nest range"

    # Build fog-of-war enemy intel line
    ei = s["enemy"]
    intel = ei.get("intel", "unknown")
    raw_qhp = ei["queen_hp"]
    qhp_str = f"{int(raw_qhp)}" if raw_qhp is not None else "unknown (no soldiers in siege range)"
    if intel == "unknown":
        enemy_line = (f"ENEMY ({enemy_color}): intel UNKNOWN — no recent scouts near enemy nest. "
                      f"enemy_nest={ene_qpos or '?'}  queen_hp={qhp_str}")
    else:
        since = ei['ticks_since_recon']
        freshness = (f"observed {since} ticks ago" if intel == "fresh"
                     else f"STALE — last seen {since} ticks ago")
        if ei.get("tiers"):
            et = ei["tiers"]
            tier_str = f"  upgrades=W{et[0]}/Sc{et[1]}/Sol{et[2]}"
        else:
            tier_str = ""
        enemy_line = (f"ENEMY ({enemy_color}): {ei['workers']}W {ei['soldiers']}S {ei['scouts']}sc "
                      f"({freshness}){tier_str}  queen_hp={qhp_str} pos={ene_qpos}")

    army_ratio_str = (f"ARMY RATIO (you/last intel): {s['army_ratio']}"
                      if s.get("army_ratio") else "ARMY RATIO: unknown (no intel)")

    sectors = soldier_sectors(colony) if world else ""
    soldiers_line = (f"YOUR SOLDIERS: {s['soldiers']} total — {sectors}"
                     if sectors else f"YOUR SOLDIERS: {s['soldiers']} total")

    upg = s["upgrades"]
    upg_summary = (f"W{upg['worker']['tier']} Sc{upg['scout']['tier']} "
                   f"Sol{upg['soldier']['tier']}")
    _pending_flags = {
        "worker":  colony.worker_upgrade_pending,
        "scout":   colony.scout_upgrade_pending,
        "soldier": colony.soldier_upgrade_pending,
    }
    upg_available = []
    for utype, ucosts in [("worker", WORKER_UPGRADE_COSTS), ("scout", SCOUT_UPGRADE_COSTS),
                           ("soldier", SOLDIER_UPGRADE_COSTS)]:
        nxt = upg[utype]["next"]
        if nxt:
            needed = nxt["cost"] - s["food"]
            if needed <= 0:
                status = "READY"
            else:
                inc = s["income_per_s"]
                if inc > 0:
                    eta = int(needed / inc)
                    status = f"need {needed:+d} more (~{eta}s)"
                else:
                    status = f"need {needed:+d} more"
            queued = " (QUEUED)" if _pending_flags.get(utype) else ""
            upg_available.append(f'{utype.upper()[:3]} T{upg[utype]["tier"]+1} '
                                  f'{nxt["label"]} ({nxt["cost"]}♦ {status}){queued}')

    all_maxed = (colony.worker_tier >= 3 and colony.scout_tier >= 3 and colony.soldier_tier >= 3)
    surplus_hint = (f"  💰 ALL UPGRADES MAXED — {s['food']} food idle. "
                    f"Lower worker_cap to slow accumulation; excess food cannot be spent."
                    if all_maxed and s["food"] > 8000 else "")

    # Upgrade ETA: show time-to-next for each pending upgrade
    _upg_eta_parts = []
    for uname in ("worker", "scout", "soldier"):
        upg = s["upgrades"].get(uname, {})
        nxt = upg.get("next")
        if nxt and nxt.get("food_short", 0) > 0:
            _upg_eta_parts.append(f"{uname.upper()[:3]}:{nxt['cost']}♦ ~{nxt['eta_s']}s")
        elif nxt and nxt.get("food_short", 0) == 0:
            _upg_eta_parts.append(f"{uname.upper()[:3]}:{nxt['cost']}♦ READY NOW")
    _eta_str = ("  |  ETA: " + "  ".join(_upg_eta_parts)) if _upg_eta_parts else ""

    own_larders = sum(1 for st in world.structures if st["colony"] == colony.id and st.get("type") == "larder")
    larder_str = f"  LARDERS: {own_larders}×{LARDER_INCOME}♦/t passive" if own_larders else ""
    income_line = (f"FOOD: {s['food']}  INCOME: {s['income_per_s']:+.0f}/s (5s avg){larder_str}  "
                   f"DIRT: {colony.dirt}  UPGRADES: {upg_summary}{_eta_str}")
    if s['income_per_s'] < 5:
        income_line += "  *** LOW INCOME — workers may be stuck or food depleted ***"

    # Spawn queue summary
    sq = colony.spawn_queue
    if sq:
        sw_  = sum(1 for t, _, _ in sq if t == A_WORKER)
        ss_  = sum(1 for t, _, _ in sq if t == A_SOLDIER)
        sc_  = sum(1 for t, _, _ in sq if t == A_SCOUT)
        reserved = sum(cost for _, _, cost in sq)
        min_t = min(tr for _, tr, _ in sq)
        sparts = [f"{v}{k}" for k, v in [("W", sw_), ("S", ss_), ("sc", sc_)] if v]
        spawn_queue_line = (f"SPAWN QUEUE: {len(sq)}/{colony.MAX_SPAWN_QUEUE} pending "
                            f"({' '.join(sparts)} · next in {min_t}t · {reserved}♦ reserved) "
                            f"[build times: W={colony.SPAWN_TIME[A_WORKER]}t S={colony.SPAWN_TIME[A_SOLDIER]}t sc={colony.SPAWN_TIME[A_SCOUT]}t]")
    else:
        spawn_queue_line = (f"SPAWN QUEUE: empty — "
                            f"build times: W={colony.SPAWN_TIME[A_WORKER]}t  S={colony.SPAWN_TIME[A_SOLDIER]}t  sc={colony.SPAWN_TIME[A_SCOUT]}t")

    # Aging ants — those in final 20% of lifespan
    _TNAME = ["W", "S", "sc"]
    aging_counts = [0, 0, 0]
    aging_min_rem = [9999, 9999, 9999]
    for a in colony.ants:
        if a.type < 3 and a.lifespan and a.age >= int(a.lifespan * 0.80):
            aging_counts[a.type] += 1
            aging_min_rem[a.type] = min(aging_min_rem[a.type], a.lifespan - a.age)
    aging_parts = [f"{aging_counts[t]}{_TNAME[t]} (~{aging_min_rem[t]}t left)"
                   for t in range(3) if aging_counts[t]]
    aging_line = f"AGING OUT SOON: {' · '.join(aging_parts)}" if aging_parts else ""

    # Convert reminder — show always; especially useful with aging ants near queen
    ants_near_queen = [a for a in colony.ants
                       if a.type != 3 and abs(a.x - colony.nx) + abs(a.y - colony.ny) <= 8]
    if ants_near_queen:
        convert_ids = ", ".join(str(a.id) for a in ants_near_queen[:4])
        convert_hint = (f"CONVERT: ants near queen (IDs: {convert_ids}{'...' if len(ants_near_queen)>4 else ''}) "
                        f"→ {{\"convert\": {{\"id\": N, \"to\": \"worker\"|\"scout\"|\"soldier\"}}}} "
                        f"cost 15/22/30♦, resets HP+lifespan")
    else:
        convert_hint = "CONVERT: rally ants back near queen to convert types (15/22/30♦, resets HP+lifespan)"

    lines = [
        f"=== TICK {tick} ({tick//10//60:02d}:{tick//10%60:02d}) ===",
        income_line,
        f"ARMY: {s['workers']}W {s['soldiers']}S {s['scouts']}sc  total={s['total']}",
        spawn_queue_line,
        *(([aging_line]) if aging_line else []),
        convert_hint,
        f"QUEEN HP: yours={int(s['queen_hp'])}/900  pos={own_qpos}",
        *(([surplus_hint]) if surplus_hint else []),
        f"PRESSURE: {pressure_map.get(s.get('pressure','unknown'), s.get('pressure','unknown'))}",
        army_ratio_str,
        soldiers_line,
        f"",
        enemy_line,
        f"",
        siege_line,
        f"",
        f"",
    ]
    nx, ny = colony.nx, colony.ny
    # Food intel: rich display from queen's map
    if colony.food_intel:
        tier_order = {"home": 0, "approach": 1, "frontline": 2}
        sorted_intel = sorted(colony.food_intel.items(),
                              key=lambda kv: (tier_order.get(kv[1]["tier"], 1),
                                              abs(kv[0][0]-nx)+abs(kv[0][1]-ny)))
        food_lines = []
        _SAT_CAPS = {"home": 4, "approach": 6, "frontline": 12}
        for (fx, fy), info in sorted_intel[:12]:
            amt = int(info["amt"])
            mx = info.get("max", 0)
            tier = info.get("tier", "home")
            age = tick - info.get("last_seen", 0)
            if tier == "frontline":
                amt_str = f"∞"
            elif mx > 0:
                pct = int(amt / mx * 100)
                amt_str = f"{amt}/{mx}♦ {pct}%"
            else:
                amt_str = f"{amt}♦"
            stale = "  ← STALE" if age > 80 else ("  ← AGING" if age > 30 else "")
            depleted = "  ← DEPLETED" if amt <= 0 else ""
            # Worker saturation count
            workers_at = sum(1 for a in colony.ants if a.type == A_WORKER
                             and abs(a.x - fx) + abs(a.y - fy) <= 5)
            cap = _SAT_CAPS.get(tier, 6)
            sat_str = f"  [W:{workers_at}/{cap}]" + (" ← SATURATED" if workers_at >= cap else "")
            food_lines.append(f"  ({fx},{fy}) {tier:10s} {amt_str:15s} seen t{info['last_seen']}{sat_str}{depleted}{stale}")
        lines.append(f"FOOD INTEL ({len(colony.food_intel)} mapped):\n" + "\n".join(food_lines))
    else:
        lines.append("FOOD INTEL: none yet — workers and scouts must explore")
    if world:
        own_structs  = [st for st in world.structures if st["colony"] == colony.id]
        def _fmt_structs(lst):
            if not lst: return "none"
            return ", ".join(f"({st['x']},{st['y']}) {st.get('type','GP')[0].upper()} HP={st['hp']}" for st in lst)
        # Count by type
        own_gp  = sum(1 for st in own_structs if st.get("type") == "guard_post")
        own_wt  = sum(1 for st in own_structs if st.get("type") == "watchtower")
        own_br  = sum(1 for st in own_structs if st.get("type") == "barracks")
        own_wl  = sum(1 for st in own_structs if st.get("type") == "wall")
        own_lr  = sum(1 for st in own_structs if st.get("type") == "larder")
        # Enemy structures: only what scouts/units have actually spotted (fog-of-war compliant)
        seen_enemy = sorted(colony.seen_structs.items(),
                            key=lambda kv: kv[1]["last_seen"], reverse=True)
        enemy_struct_str = ", ".join(
            f"({x},{y}) {v['type'].replace('_',' ')} ~HP={v['hp_approx']} t{v['last_seen']}"
            for (x, y), v in seen_enemy[:6]
        ) if seen_enemy else "none spotted"
        lines.append(
            f"YOUR STRUCTURES: {_fmt_structs(own_structs)}\n"
            f"  counts: guard_post {own_gp}/{GUARD_POST_MAX}  watchtower {own_wt}/{WATCHTOWER_MAX}  "
            f"barracks {own_br}/{BARRACKS_MAX}  wall {own_wl}/{WALL_MAX}  larder {own_lr}/{LARDER_MAX}\n"
            f"ENEMY STRUCTURES SPOTTED: {enemy_struct_str}"
        )
        # Enemy unit sightings
        if colony.enemy_sightings:
            recent = colony.enemy_sightings[-5:]
            sig_parts = []
            for s in reversed(recent):
                cx, cy, sol, tot, tk = s[0], s[1], s[2], s[3], s[4]
                wk = s[5] if len(s) > 5 else "?"
                sc = s[6] if len(s) > 6 else "?"
                sig_parts.append(f"{tot} ants ({sol}S/{wk}W/{sc}sc) near ({cx},{cy}) t{tk}")
            lines.append(f"ENEMY SIGHTINGS: {' · '.join(sig_parts)}")
        else:
            lines.append("ENEMY SIGHTINGS: none — send scouts into center/enemy territory")
        # Known dirt deposits
        dirt_info = []
        for dx, dy in colony.known_dirt[:6]:
            dn = next((d for d in world.dirt_nodes if d["x"] == dx and d["y"] == dy), None)
            amt_str = f" {int(dn['amt'])}◆" if dn else ""
            dist = abs(dx - colony.nx) + abs(dy - colony.ny)
            dirt_info.append(f"({dx},{dy}) d={dist}{amt_str}")
        lines.append(f"KNOWN DIRT DEPOSITS: {', '.join(dirt_info) if dirt_info else 'none found yet'}")
        lines.append(
            f"BUILDING COSTS (dirt): guard_post={GUARD_POST_COST}◆  watchtower={WATCHTOWER_COST}◆  "
            f"barracks={BARRACKS_COST}◆  wall={WALL_COST}◆/segment  larder={LARDER_COST}◆\n"
            f"  BUILD: {{\"build\": {{\"type\": \"watchtower\", \"x\": 45, \"y\": 30}}}}  "
            f"or guard_post [x,y] legacy form\n"
            f"  Workers gather dirt automatically when near deposits; set economy.gather_dirt:true to prioritize"
        )
    if upg_available:
        lines.append(f"AVAILABLE UPGRADES (buy_upgrade: \"worker\"|\"scout\"|\"soldier\"|true):")
        for ua in upg_available:
            lines.append(f"  {ua}")
    d = s["directive"]
    sp  = d["spawn"]
    mil = d["military"]
    eco = d["economy"]
    ut  = d["unit_types"]
    min_r = (f" [floors: W≥{sp['worker'].get('min_ratio',0):.0%} "
             f"Sc≥{sp['scout'].get('min_ratio',0):.0%} "
             f"Sol≥{sp['soldier'].get('min_ratio',0):.0%}]"
             if any(sp[k].get("min_ratio", 0) > 0 for k in ("worker","scout","soldier")) else "")
    roles_str = (f"W={sp['worker']['target_ratio']:.0%} "
                 f"Sc={sp['scout']['target_ratio']:.0%} "
                 f"Sol={sp['soldier']['target_ratio']:.0%}{min_r}")
    scout_exp = ut.get("scout", {}).get("expansion", "?")
    scout_patrol = ut.get("scout", {}).get("patrol_waypoints")
    lines += ["", "CURRENT DIRECTIVE:"]
    lines.append(f"  spawn: {roles_str}  worker_cap={sp['worker']['max']}")
    lines.append(f"  military: stance={mil['stance']}  formation={mil['formation']}"
                 f"  retreat={mil['retreat']}  auto_attack={mil.get('auto_attack',False)}")
    rally_pt   = mil["rally_point"]
    release_at = mil["rally_release_at"]
    rally_mode = mil.get("rally_mode", "normal")
    if rally_pt:
        # Normalize waypoints vs single point
        if isinstance(rally_pt[0], (list, tuple)):
            rx, ry = int(rally_pt[0][0]), int(rally_pt[0][1])
            wpt_str = f" → {len(rally_pt)} waypoints"
        else:
            rx, ry = int(rally_pt[0]), int(rally_pt[1])
            wpt_str = ""
        staged = sum(1 for a in colony.ants
                     if a.type == A_SOLDIER
                     and abs(a.x - rx) + abs(a.y - ry) <= 4)
        rally_str = (f"({rx},{ry}){wpt_str} [{staged} staged → release at {release_at}, mode={rally_mode}]"
                     if release_at else f"({rx},{ry}){wpt_str} [{staged} staged, mode={rally_mode}]")
    else:
        rally_str = "null"
    lines.append(f"  rally={rally_str}  attack_target={mil['attack_target']}"
                 f"  siege_priority={mil['siege_priority']}")
    lines.append(f"  economy: priority_food={eco['priority_food']}"
               f"  ← set to [75,50] to redirect ALL workers to contested center food")
    if scout_patrol:
        lines.append(f"  unit_types.scout.patrol_waypoints={scout_patrol}  (expansion ignored)")
    else:
        lines.append(f"  unit_types.scout.expansion={scout_exp}")
    if d.get("triggers"):
        trig_labels = [t.get("label", "?") for t in d["triggers"]]
        lines.append(f"  triggers: {', '.join(trig_labels)} ({len(d['triggers'])} active)")
    # Trigger event log — only show events from last 50 ticks (prevents stale confusion)
    tlog = s.get("trigger_log", [])
    recent_tlog = [e for e in tlog if tick - e["tick"] <= 50]
    if recent_tlog:
        lines.append(f"TRIGGER EVENTS (last 50 ticks, tick {tick-50}–{tick}):")
        for entry in recent_tlog[-8:]:
            patches_str = ", ".join(f"{k}={v}" for k, v in entry["patches"].items())
            lines.append(f"  tick {entry['tick']:4d}: [{entry['label']}] → {patches_str}")
    else:
        lines.append(f"TRIGGER EVENTS: none fired in last 50 ticks (current tick {tick})")
    lines += ["",
              "RESPOND with JSON using the directive patch format or legacy flat keys:",
              '  New: {"directive": {"military": {"stance": "aggressive"}, "spawn": {"soldier": {"target_ratio": 0.40}}}}',
              '  Legacy (still works): {"roles": {"worker": 0.4, "scout": 0.2, "soldier": 0.4}, "defense": "aggressive"}',
              "  You can also set per-unit triggers: "
              '{"directive": {"triggers": [{"label": "eco_emergency", "if": "food < 75 AND income_per_s < 5 AND elapsed_ticks > 100 AND soldiers_in_siege == 0", '
              '"then": {"military.retreat": true}}]}}',
              ""]

    if world:
        lines += ["", "MAP OVERVIEW (NW=top-left, SE=bottom-right):"]
        lines.append(build_minimap(world, colony))

    if memory:
        lines += ["", "YOUR MEMORY (this match only — update via \"memory\": {...}):"]
        for k, v in memory.items():
            lines.append(f"  {k}: {v}")
    else:
        lines += ["", "YOUR MEMORY: (empty — no notes yet this match)"]

    if events:
        lines += ["", "RECENT EVENTS (newest first):"]
        for ev in events:
            lines.append(f"  {ev}")
    lines += [
        "",
        "What is your strategy? Think, then respond with JSON.",
        "ALWAYS include \"feedback\": \"...\" — what data was unclear or missing THIS turn?",
    ]
    return "\n".join(lines)


def parse_llm_response(text):
    """Extract (<think> reasoning, strategy dict) from LLM output."""
    reasoning = ""
    think_match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    if think_match:
        reasoning = think_match.group(1).strip()
        text = text[think_match.end():]

    # Strip markdown fences if present
    text = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()

    strategy = {}
    feedback = ""
    memory_update = None
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        try:
            raw = json.loads(json_match.group())
            feedback       = raw.pop("feedback", "")     or ""
            memory_update  = raw.pop("memory",   None)      # dict or None
            # Normalize legacy flat keys (lists→tuples for coord fields)
            # New directive format uses lists throughout — skip normalization when directive key present
            if "directive" not in raw:
                for key in ("rally_point", "expansion", "priority_food"):
                    if key in raw and isinstance(raw[key], list):
                        raw[key] = tuple(raw[key])
            strategy = raw
        except json.JSONDecodeError:
            pass

    return reasoning, strategy, feedback, memory_update


def _read_key_events(log_path, max_lines=50):
    """Pull macro events out of the run log for the debrief context."""
    events = []
    try:
        with open(log_path) as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                if ('★' in s or '[RED]' in s or '[BLUE]' in s
                        or 'INCOME' in s or 'QUEEN' in s
                        or 'siege' in s.lower() or 'combat surge' in s.lower()
                        or 'tier' in s.lower() or 'upgrade' in s.lower()):
                    events.append(s)
    except Exception:
        pass
    return events[-max_lines:]


def build_debrief_prompt(world, colony_id, strategy_log, memory=None):
    """Build the post-game reflection prompt sent to the LLM after the game ends."""
    c   = world.colonies[colony_id]
    ec  = world.colonies[1 - colony_id]
    name  = "RED" if colony_id == 0 else "BLUE"
    ename = "BLUE" if colony_id == 0 else "RED"
    elapsed = round(time.time() - world.start_time)
    mm, ss = elapsed // 60, elapsed % 60

    def ant_counts(col):
        ct = [0, 0, 0, 0]
        for a in col.ants: ct[a.type] += 1
        return ct

    ct_own = ant_counts(c)
    ct_en  = ant_counts(ec)
    winner = world.winner

    if winner == "draw":
        result_str = f"DRAW at {mm:02d}:{ss:02d}"
    elif winner == colony_id:
        result_str = f"{name} WINS (your colony!) at {mm:02d}:{ss:02d}"
    else:
        result_str = f"{ename} WINS (you lost) at {mm:02d}:{ss:02d}"

    lines = [
        "=== POST-GAME DEBRIEF REQUEST ===",
        f"You commanded {name}. The game has ended.",
        f"RESULT: {result_str}",
        "",
        "FINAL STATS:",
        f"  {name} (you): {len(c.ants)} ants ({ct_own[0]}W {ct_own[1]}S {ct_own[2]}sc)  "
        f"food={int(c.food)}  collected={c.food_collected}  lost={c.ants_lost}  "
        f"upgrades=W{c.worker_tier}/Sc{c.scout_tier}/Sol{c.soldier_tier}  peak_pop={c.peak_pop}",
        f"  {ename} (enemy): {len(ec.ants)} ants ({ct_en[0]}W {ct_en[1]}S {ct_en[2]}sc)  "
        f"food={int(ec.food)}  collected={ec.food_collected}  lost={ec.ants_lost}  "
        f"upgrades=W{ec.worker_tier}/Sc{ec.scout_tier}/Sol{ec.soldier_tier}  peak_pop={ec.peak_pop}",
        "",
    ]

    if strategy_log:
        lines.append("YOUR STRATEGY DECISIONS (tick → what you set):")
        for tick, strat in strategy_log:
            lines.append(f"  tick={tick:5d}: {json.dumps(strat) if strat else '(no change)'}")
        lines.append("")

    key_events = _read_key_events(world.logger.path)
    if key_events:
        lines.append("KEY EVENTS FROM THIS RUN:")
        for ev in key_events:
            lines.append(f"  {ev}")
        lines.append("")

    if memory:
        lines += ["YOUR MEMORY (entries saved during this match):"]
        for k, v in memory.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    lines += [
        "Respond in FREE TEXT (not JSON). Be specific: cite tick numbers and exact decisions.",
        "Address all five points:",
        "1. WHAT WORKED — strategic decisions with positive impact and why",
        "2. WHAT FAILED — mistakes, which ticks, and what you should have done instead",
        "3. MISSING INFO — game state data that was absent or unclear",
        "4. NEW LEVERS — strategic commands or mechanics you wish existed",
        "5. REPLAY PLAN — if you played this same map again, what would you do from tick 1",
        "",
        "After the debrief, if you want to update your persistent memory, output a final",
        "section starting exactly with the line: MEMORY UPDATE:",
        "followed by one key: value pair per line. Example:",
        "  staging: rally at [28,28] for BLUE vs NW nest works well",
        "  eco_rules: never drop worker_cap below 40 while soldiers > 30",
    ]
    return "\n".join(lines)


def build_minimap_placement(world):
    """ASCII minimap for placement phase — terrain + food tiers, no ants."""
    grid = [['.' for _ in range(_MAP_COLS)] for _ in range(_MAP_ROWS)]
    for row in range(_MAP_ROWS):
        for col in range(_MAP_COLS):
            tx0 = int(col * _CW); tx1 = int((col+1) * _CW)
            ty0 = int(row * _CH); ty1 = int((row+1) * _CH)
            water = rock = total = 0
            for ty in range(ty0, ty1):
                for tx in range(tx0, tx1):
                    t = world.terrain[ty][tx]
                    if t == T_WATER: water += 1
                    elif t == T_ROCK: rock += 1
                    total += 1
            if water > total * 0.3:   grid[row][col] = '~'
            elif rock  > total * 0.3: grid[row][col] = '#'
    tier_chars = {"frontline": "F", "approach": "a", "home": "h"}
    for f in world.foods:
        col = min(_MAP_COLS-1, int(f["x"] / _CW))
        row = min(_MAP_ROWS-1, int(f["y"] / _CH))
        if grid[row][col] not in ('~', '#'):
            ch = tier_chars.get(f.get("tier", "home"), "h")
            if grid[row][col] == '.': grid[row][col] = ch
    mid_col = _MAP_COLS // 2
    for row in range(_MAP_ROWS):
        if grid[row][mid_col] == '.': grid[row][mid_col] = '|'
    rows = ["MAP OVERVIEW (NW=top-left, SE=bottom-right):"]
    rows += ["  " + "".join(grid[r]) for r in range(_MAP_ROWS)]
    rows.append("  F=frontline  a=approach  h=home  ~=water  #=rock  |=half-boundary")
    rows.append("  LEFT half = RED territory  |  RIGHT half = BLUE territory")
    return "\n".join(rows)


def build_placement_prompt(world, half):
    """One-shot placement decision prompt sent before the game starts."""
    is_blue = (half == "blue")
    name  = "BLUE" if is_blue else "RED"
    _buf  = 12  # minimum tiles from center (matches _valid_placement)
    x_min_b = MAP_W // 2 + _buf       # BLUE must be >= this x
    x_max_r = MAP_W // 2 - _buf - 1   # RED  must be <= this x
    x_con = f"x >= {x_min_b}" if is_blue else f"x <= {x_max_r}"

    frontline = [(f["x"], f["y"], f["kind"]) for f in world.foods if f.get("tier") == "frontline"]
    approach  = [(f["x"], f["y"], f["kind"]) for f in world.foods if f.get("tier") == "approach"
                 and ((is_blue and f["x"] > MAP_W//2) or (not is_blue and f["x"] <= MAP_W//2))]
    home      = [(f["x"], f["y"], f["kind"]) for f in world.foods if f.get("tier") == "home"
                 and ((is_blue and f["x"] > MAP_W//2) or (not is_blue and f["x"] <= MAP_W//2))]

    lines = [
        f"=== COLONY PLACEMENT — {name} ({x_con}) ===",
        "",
        f"Map is {MAP_W}×{MAP_H} tiles. Choose where to place your queen's nest.",
        f"Constraint: {x_con}  (must be passable — not water '~' or rock '#')",
        "",
        "FRONTLINE NODES (contested — inexhaustible regrow, key late-game prize):",
    ]
    for fx, fy, kind in sorted(frontline, key=lambda n: n[1]):
        lines.append(f"  ({fx:3d},{fy:3d})  {kind}")

    lines += ["", "YOUR APPROACH NODES (medium regrow — reward expanding toward center):"]
    for fx, fy, kind in sorted(approach, key=lambda n: n[1]):
        lines.append(f"  ({fx:3d},{fy:3d})  {kind}")

    lines += ["", "YOUR HOME NODES (safe early economy — close to starting zones):"]
    for fx, fy, kind in sorted(home, key=lambda n: n[1]):
        lines.append(f"  ({fx:3d},{fy:3d})  {kind}")

    lines += ["", build_minimap_placement(world), ""]

    ctr  = f"x=55–70" if is_blue else f"x=30–45"
    corn = f"x=80–90" if is_blue else f"x=10–20"
    lines += [
        f"TRADEOFFS for {name}:",
        f"  Aggressive center ({ctr}):",
        f"    + Reach approach nodes in ~20 tile march; frontline in ~35",
        f"    - Queen exposed — enemy can reach you ~30 tiles sooner",
        f"  Safe corner ({corn}):",
        f"    + Home nodes within 15 tiles; strong early economy",
        f"    + Enemy needs 70+ tiles to raid your queen",
        f"    - Long march to midfield; enemy claims frontline nodes first",
        "",
        f"ENEMY: Their position is UNKNOWN — restricted to the opposite half.",
        "Scouts will reveal them. No recon data yet.",
        "",
        f"Output JSON: {{\"x\": <int>, \"y\": <int>, \"reason\": \"<brief note>\"}}",
    ]
    return "\n".join(lines)


class RunLogger:
    """Writes a human+Claude-readable log of every run to logs/run_TIMESTAMP.log"""

    NAMES = ["RED", "BLUE"]

    def __init__(self, world):
        os.makedirs("logs", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = f"logs/run_{ts}.log"
        self.world = world
        self.last_lost = [0, 0]
        self.finished = False
        self._write_header()
        print(f"📋  logging → {self.path}")

    def _fmt(self, tick=None):
        t = (self.world.tick if tick is None else tick) // 10
        return f"{t//60:02d}:{t%60:02d}"

    def _counts(self, c):
        ct = [0, 0, 0, 0]
        for a in c.ants: ct[a.type] += 1
        return ct

    def log_placement(self, red_pos, red_reason, blue_pos, blue_reason, errors=None):
        with open(self.path, "a") as f:
            f.write("=== PLACEMENT PHASE ===\n")
            f.write(f"  RED  ({red_pos[0]:3d},{red_pos[1]:3d})  {red_reason}\n")
            f.write(f"  BLUE ({blue_pos[0]:3d},{blue_pos[1]:3d})  {blue_reason}\n")
            if errors:
                for err in errors:
                    f.write(f"  ⚠  {err}\n")
            f.write("\n")

    def _write_header(self):
        w = self.world
        c0, c1 = w.colonies
        n_food = len(w.foods)
        n_front = sum(1 for f in w.foods if f.get("tier") == "frontline")
        n_appr  = sum(1 for f in w.foods if f.get("tier") == "approach")
        n_home  = sum(1 for f in w.foods if f.get("tier") == "home")
        with open(self.path, "w") as f:
            f.write(f"=== SWARM WARS v{VERSION}  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            f.write(f"map={MAP_W}x{MAP_H} | food={n_food} ({n_front}F/{n_appr}A/{n_home}H) | "
                    f"food_max={FOOD_MAX} | regrow={FOOD_REGROW}/tick | pick={FOOD_PICK}\n")
            f.write(f"no-upkeep | corpse=[W12,Sol25,Sc17]♦ | "
                    f"soldier: dmg={SOLDIER_DMG} cd={SOLDIER_CD} hp={SOLDIER_HP} | "
                    f"queen_hp={QUEEN_HP} dmg={QUEEN_DMG} cd={QUEEN_CD}\n")
            f.write(f"RED nest=({c0.nx},{c0.ny})   BLUE nest=({c1.nx},{c1.ny})\n")
            llm_lines = []
            for cid, cname in ((0, "RED"), (1, "BLUE")):
                b = _brain_for(cid)
                if b["type"] == "llm":
                    llm_lines.append(f"{cname}={b.get('model','?')} @ {b.get('base_url','?')}")
            if llm_lines:
                interval_s = LLM_INTERVAL / TPS
                f.write(f"LLM  interval={LLM_INTERVAL} ticks ({interval_s:.1f}s real-time)  |  " + "  ".join(llm_lines) + "\n")
            else:
                f.write("LLM=disabled (bot vs bot)\n")
            f.write("\n")

    def tick(self):
        if self.finished:
            return
        w = self.world
        if w.tick % 10 != 0:
            return

        lines = []

        # Update per-second income and peak population
        for i, c in enumerate(w.colonies):
            c.income_per_s = c.food_earned_tick  # deliveries in this 10-tick window
            c.food_earned_tick = 0.0
            c.dirt_per_s = c.dirt_earned_tick
            c.dirt_earned_tick = 0.0
            c.queen_dps_actual = c.queen_dmg_dealt_tick
            c.queen_dmg_dealt_tick = 0.0
            c.income_history.append(c.income_per_s)
            c.income_smooth = sum(c.income_history) / len(c.income_history)
            pop = len(c.ants)
            if pop > c.peak_pop:
                c.peak_pop = pop
                c.peak_pop_tick = w.tick

        # Macro: income dropped low (no deliveries in this window = workers starved/stuck)
        for i, c in enumerate(w.colonies):
            if c.income_per_s < 5 and not c.income_neg_warned:
                c.income_neg_warned = True
                lines.append(f"  ★ {self.NAMES[i]} LOW INCOME "
                              f"({c.income_per_s:+.0f}/s) — workers may be stuck or depleted")
            elif c.income_per_s > 20 and c.income_neg_warned:
                c.income_neg_warned = False
                lines.append(f"  ★ {self.NAMES[i]} income recovered ({c.income_per_s:+.0f}/s)")

        # Macro: combat surge (≥3 kills in this 1s window)
        for i, c in enumerate(w.colonies):
            delta = c.ants_lost - self.last_lost[i]
            if delta >= 3:
                lines.append(f"  ★ {self.NAMES[i]} lost {delta} ants this second — combat surge")
            self.last_lost[i] = c.ants_lost

        # Drain colony log queues
        for i, c in enumerate(w.colonies):
            for ev in c.log_queue:
                lines.append(f"  [{self.NAMES[i]}] {ev}")
            c.log_queue.clear()

        # Snapshot line
        c0, c1 = w.colonies
        ct0, ct1 = self._counts(c0), self._counts(c1)
        def tier_tag(c):
            parts = []
            if c.worker_tier  > 0: parts.append(f"W{c.worker_tier}")
            if c.scout_tier   > 0: parts.append(f"Sc{c.scout_tier}")
            if c.soldier_tier > 0: parts.append(f"Sol{c.soldier_tier}")
            return " [" + "|".join(parts) + "]" if parts else "     "
        snap = (f"{self._fmt()}"
                f"  RED{tier_tag(c0)} {ct0[0]:3d}W {ct0[1]:2d}S {ct0[2]:2d}sc"
                f"  pop={len(c0.ants):3d}  food={int(c0.food):6d}  inc={c0.income_per_s:+5.0f}/s"
                f"   │"
                f"  BLUE{tier_tag(c1)} {ct1[0]:3d}W {ct1[1]:2d}S {ct1[2]:2d}sc"
                f"  pop={len(c1.ants):3d}  food={int(c1.food):6d}  inc={c1.income_per_s:+5.0f}/s")

        with open(self.path, "a") as f:
            f.write(snap + "\n")
            for l in lines:
                f.write(l + "\n")

    def log_llm(self, colony_id, reasoning, strategy, prompt=None, feedback=""):
        name = self.NAMES[colony_id]
        with open(self.path, "a") as f:
            f.write(f"\n{'─'*90}\n")
            f.write(f"[LLM {name}]  tick={self.world.tick}\n")
            if prompt:
                f.write(f"  PROMPT:\n")
                for line in prompt.splitlines():
                    f.write(f"    {line}\n")
                f.write("\n")
            if reasoning:
                for line in reasoning.splitlines():
                    f.write(f"  THINK: {line}\n")
                f.write("\n")
            if strategy:
                f.write(f"  DECISION: {json.dumps(strategy)}\n")
            else:
                f.write(f"  DECISION: (no change)\n")
            if feedback:
                f.write(f"  FEEDBACK: {feedback}\n")
            f.write(f"{'─'*90}\n\n")

    def log_debrief(self, colony_id, thinking, debrief):
        name = self.NAMES[colony_id]
        with open(self.path, "a") as f:
            f.write(f"\n{'═'*90}\n")
            f.write(f"POST-GAME DEBRIEF — LLM {name}\n")
            f.write(f"{'═'*90}\n")
            if thinking:
                f.write("[THINKING]\n")
                for line in thinking.splitlines():
                    f.write(f"  {line}\n")
                f.write("\n")
            f.write("[DEBRIEF]\n")
            for line in debrief.splitlines():
                f.write(f"  {line}\n")
            f.write(f"{'═'*90}\n\n")

    def log_memory_snapshot(self, memory):
        if not memory: return
        with open(self.path, "a") as f:
            f.write(f"\n  FINAL LLM MEMORY STATE:\n")
            for k, v in memory.items():
                f.write(f"    {k}: {v}\n")

    def finish(self, winner):
        if self.finished:
            return
        self.finished = True
        w = self.world
        c0, c1 = w.colonies
        ct0, ct1 = self._counts(c0), self._counts(c1)
        with open(self.path, "a") as f:
            f.write(f"\n{'═'*90}\n")
            f.write(f"RESULT  {self._fmt()}\n")
            if winner == "draw":
                f.write("DRAW — both queens fell simultaneously\n\n")
            else:
                f.write(f"{'RED' if winner == 0 else 'BLUE'} WINS\n\n")
            for i, (c, ct) in enumerate([(c0, ct0), (c1, ct1)]):
                f.write(f"  {self.NAMES[i]:4s}  "
                        f"{len(c.ants):3d} ants ({ct[0]}W {ct[1]}S {ct[2]}sc)  "
                        f"food={int(c.food):6d}  "
                        f"collected={c.food_collected:6d}  "
                        f"lost={c.ants_lost:3d}  "
                        f"upgrades=W{c.worker_tier}/Sc{c.scout_tier}/Sol{c.soldier_tier}  "
                        f"peak_pop={c.peak_pop} at {self._fmt(c.peak_pop_tick)}\n")
            for ls in w._llm_stats_list:
                if ls:
                    ptok, ctok = ls['prompt_tok'], ls['completion_tok']
                    f.write(f"\n  LLM  model={ls['model']}  colony={ls['colony']}\n")
                    f.write(f"       calls={ls['calls']}  errors={ls['errors']}  "
                            f"tokens: {ptok}p + {ctok}c = {ptok+ctok} total\n")
        print(f"📋  run complete → {self.path}")
        for ls in w._llm_stats_list:
            if ls:
                ptok, ctok = ls['prompt_tok'], ls['completion_tok']
                print(f"🤖  LLM [{ls['colony']}]: {ls['calls']} calls, "
                      f"{ptok}p + {ctok}c = {ptok+ctok} tokens, "
                      f"{ls['errors']} errors")


# ═══════════════════════════════════════════════════════════════════════════════
# Predator
# ═══════════════════════════════════════════════════════════════════════════════

class Predator:
    _id = 0
    def __init__(self, x, y):
        Predator._id += 1
        self.id = Predator._id
        self.x = x; self.y = y
        self.prev_x = x; self.prev_y = y
        self.hp = 400; self.max_hp = 400
        self.cooldown = 0
        self.tx = None; self.ty = None

    def update(self, world):
        self.prev_x, self.prev_y = self.x, self.y
        if self.cooldown > 0: self.cooldown -= 1
        nearest, nearest_d = None, 15
        for c in world.colonies:
            for a in c.ants:
                if a.type == A_QUEEN: continue
                d = abs(a.x - self.x) + abs(a.y - self.y)
                if d < nearest_d:
                    nearest_d = d; nearest = a
        if nearest:
            world._move_prey(self, nearest.x, nearest.y)
            if abs(self.x - nearest.x) <= 1 and abs(self.y - nearest.y) <= 1:
                if self.cooldown <= 0:
                    nearest.hp -= 60; self.cooldown = 5
                    if nearest.hp <= 0:
                        col = world.colonies[nearest.colony]
                        col.push_event(f"predator killed a {['worker','soldier','scout','queen'][nearest.type]}")
                        world._kill(nearest)
        else:
            if self.tx is None or random.random() < 0.1:
                self.tx = random.randint(5, MAP_W-6)
                self.ty = random.randint(5, MAP_H-6)
            world._move_prey(self, self.tx, self.ty)
            if self.tx is not None and abs(self.x-self.tx) <= 1 and abs(self.y-self.ty) <= 1:
                self.tx = None

# ═══════════════════════════════════════════════════════════════════════════════
# World
# ═══════════════════════════════════════════════════════════════════════════════

class World:
    def __init__(self):
        self.tick = 0
        self.start_time = None   # set by finalize_placement when the game actually begins
        self.terrain = gen_terrain()
        # Territory: 0=neutral, 1=RED, 2=BLUE — replaces pheromone trails
        self.territory     = bytearray(MAP_W * MAP_H)
        self.territory_age = bytearray(MAP_W * MAP_H)
        self.foods = []
        self.dirt_nodes = []  # [{"x","y","amt","max","regrow","tier"}]
        self.corpses = []     # [{"x", "y", "amt"}] — harvestable by workers
        self.structures = [] # [{"x","y","colony","hp","max_hp","cd","type",...}]
        self.colonies = []
        self.winner = None    # None | 0 | 1 | "draw"
        self.logger = None
        self._llm_stats_list = [None, None]  # per-colony stats; set by Server after each LLM call
        self.phase = "lobby"       # "lobby" | "placement" | "running" | "paused"
        self.mcp_seats = {0: None, 1: None}  # agent_name or None per colony
        self._build_map()

    def _build_map(self):
        """'The Crossing' — hand-crafted symmetric 3-lane resource layout."""
        kinds = ["seeds", "beetle", "leaf", "honeydew"]

        _INIT = {
            "home":      (FOOD_INIT_HOME_MIN,  FOOD_INIT_HOME_MAX,  FOOD_MAX_HOME),
            "approach":  (FOOD_INIT_APPR_MIN,  FOOD_INIT_APPR_MAX,  FOOD_MAX_APPROACH),
            "frontline": (FOOD_INIT_FRONT_MIN, FOOD_INIT_FRONT_MAX, 999999),
        }
        def food(x, y, tier, regrow):
            imin, imax, cap = _INIT[tier]
            self.foods.append({
                "x": x, "y": y,
                "amt": float(random.randint(imin, imax)),
                "max": float(cap),
                "regrow": regrow,
                "kind": random.choice(kinds),
                "contested": (tier == "frontline"),
                "tier": tier,
            })

        def dirt(x, y, tier, regrow, max_amt):
            self.dirt_nodes.append({
                "x": x, "y": y,
                "amt": float(random.randint(DIRT_INIT_MIN, DIRT_INIT_MAX)),
                "max": float(max_amt),
                "regrow": regrow,
                "tier": tier,
            })

        # ── Food nodes — 15 total, symmetric ──
        # RED heartland — finite, barely regroups. Mine early then EXPAND.
        food(10, 32, "home",     FOOD_REGROW)
        food(10, 68, "home",     FOOD_REGROW)
        # RED approach zone — drains under moderate pressure. Push to center to sustain.
        food(30, 20, "approach", FOOD_REGROW_APPROACH)
        food(32, 50, "approach", FOOD_REGROW_APPROACH)
        food(30, 80, "approach", FOOD_REGROW_APPROACH)
        # Center contested — inexhaustible (20/tick). WIN THE GAME by holding these.
        # 3 clear lanes: north (y≈19), center (y≈50), south (y≈81)
        food(62, 19, "frontline", FOOD_REGROW_CONTESTED)
        food(75, 19, "frontline", FOOD_REGROW_CONTESTED)  # north lane center
        food(88, 19, "frontline", FOOD_REGROW_CONTESTED)
        food(75, 50, "frontline", FOOD_REGROW_CONTESTED)  # true center
        food(62, 81, "frontline", FOOD_REGROW_CONTESTED)
        food(75, 81, "frontline", FOOD_REGROW_CONTESTED)  # south lane center
        food(88, 81, "frontline", FOOD_REGROW_CONTESTED)
        # BLUE approach zone (mirror)
        food(120, 20, "approach", FOOD_REGROW_APPROACH)
        food(118, 50, "approach", FOOD_REGROW_APPROACH)
        food(120, 80, "approach", FOOD_REGROW_APPROACH)
        # BLUE heartland (mirror)
        food(140, 32, "home",     FOOD_REGROW)
        food(140, 68, "home",     FOOD_REGROW)

        # ── Dirt nodes — 10 total, symmetric ──
        # Left frontline (at ridge pass approaches — ideal tower/wall placement)
        dirt(44, 19, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(44, 50, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(44, 81, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        # Right frontline (mirror)
        dirt(106, 19, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(106, 50, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(106, 81, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        # Center flanking (near contested food, rewards holding center)
        dirt(75, 27, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(75, 73, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        # Home dirt (safe early resource)
        dirt(22, 50, "home", DIRT_REGROW, DIRT_MAX)
        dirt(128, 50, "home", DIRT_REGROW, DIRT_MAX)

    def finalize_placement(self, red_pos, blue_pos):
        """Carve nests, spawn colonies, start the logger. Called once both sides choose."""
        for nx, ny in [red_pos, blue_pos]:
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    if abs(dx)+abs(dy) <= 3:
                        tx, ty = nx+dx, ny+dy
                        if 0 <= tx < MAP_W and 0 <= ty < MAP_H:
                            self.terrain[ty][tx] = T_NEST
        for i, (nx, ny) in enumerate([red_pos, blue_pos]):
            c = Colony(i, nx, ny)
            c.spawn_initial()
            # Pre-populate home-zone food so workers don't wander on tick 1
            for f in self.foods:
                if abs(f["x"] - nx) + abs(f["y"] - ny) <= 50:
                    key = (f["x"], f["y"])
                    c.known_food.append(key)
                    c.food_intel[key] = {
                        "amt": f["amt"], "max": int(f.get("max", FOOD_MAX_APPROACH)),
                        "tier": f.get("tier", "home"), "last_seen": 0
                    }
            self.colonies.append(c)
        self.colonies[0].enemy = self.colonies[1]
        self.colonies[1].enemy = self.colonies[0]
        self.start_time = time.time()
        self.phase = "running"
        self.logger = RunLogger(self)

    # ── Main Tick ──

    def step(self):
        if self.phase in ("lobby", "placement", "paused"):
            return   # frozen until game starts / while paused
        if self.winner is not None:
            return   # frozen after game over

        self.tick += 1

        # Update territory ownership (replaces pheromone trails)
        self._update_territory()

        # Decay corpses
        self.corpses = [c for c in self.corpses if c["amt"] > CORPSE_DECAY]
        for c in self.corpses:
            c["amt"] -= CORPSE_DECAY

        # Process unit conversion orders (ant must be near queen; costs 60% of target spawn cost)
        _CONVERT_COST = {A_WORKER: 15, A_SCOUT: 22, A_SOLDIER: 30}
        _CONVERT_HP   = {A_WORKER: WORKER_HP, A_SCOUT: SCOUT_HP, A_SOLDIER: SOLDIER_HP}
        _NAME_TO_TYPE = {"worker": A_WORKER, "scout": A_SCOUT, "soldier": A_SOLDIER}
        _TYPE_LIFESPAN = {A_WORKER: 500, A_SCOUT: 200, A_SOLDIER: 300}
        for c in self.colonies:
            if not c.alive or not c.convert_queue: continue
            queen = next((a for a in c.ants if a.type == A_QUEEN), None)
            remaining = []
            for order in c.convert_queue:
                target_type = _NAME_TO_TYPE.get(order["to"])
                if target_type is None:
                    continue
                ant = next((a for a in c.ants if a.id == order["id"]), None)
                if ant is None or ant.type == A_QUEEN:
                    continue
                if ant.type == target_type:
                    c.push_event(f"convert failed: ant {order['id']} is already a {order['to']}")
                    continue
                if queen and abs(ant.x - queen.x) + abs(ant.y - queen.y) > 8:
                    remaining.append(order)  # not close enough yet — keep trying
                    continue
                cost = _CONVERT_COST.get(target_type, 60)
                if c.food < cost:
                    c.push_event(f"convert failed: need {cost}♦ to convert to {order['to']}")
                    continue
                c.food -= cost
                old_name = ["worker","soldier","scout"][ant.type]
                ant.type     = target_type
                ant.hp       = _CONVERT_HP[target_type]
                ant.max_hp   = ant.hp
                ant.lifespan = _TYPE_LIFESPAN[target_type]
                ant.age      = 0
                ant.state    = S_IDLE
                ant.birth_config = {}
                ant.behavior_config = {}
                c.push_event(f"★ converted {old_name}→{order['to']} (cost {cost}♦)")
            c.convert_queue = remaining

        # Process guard post build orders (legacy path — still uses dirt)
        for c in self.colonies:
            if not c.alive or not c.build_queue: continue
            x, y = c.build_queue.pop(0)
            own_gp = sum(1 for st in self.structures
                         if st["colony"] == c.id and st["type"] == "guard_post")
            if (c.dirt >= GUARD_POST_COST
                    and own_gp < GUARD_POST_MAX
                    and self._passable(x, y)
                    and not any(st["x"] == x and st["y"] == y for st in self.structures)):
                c.dirt -= GUARD_POST_COST
                c.dirt = max(0, c.dirt)
                self.structures.append({"x": x, "y": y, "colony": c.id, "type": "guard_post",
                                        "hp": 1, "max_hp": GUARD_POST_HP, "cd": 0,
                                        "active": False, "build_progress": 0,
                                        "build_required": BUILD_WORK_REQUIRED["guard_post"]})
                c.push_event(f"★ Guard Post foundation laid at ({x},{y}) — workers needed to build")
                self._assign_builders_to_site(c, x, y)

        # Process new structure build orders (watchtower, barracks, wall)
        _STRUCT_LIMITS = {"watchtower": WATCHTOWER_MAX, "barracks": BARRACKS_MAX, "wall": WALL_MAX, "larder": LARDER_MAX}
        _STRUCT_COSTS  = {"watchtower": WATCHTOWER_COST, "barracks": BARRACKS_COST, "wall": WALL_COST, "larder": LARDER_COST}
        _STRUCT_HP     = {"watchtower": WATCHTOWER_HP, "barracks": BARRACKS_HP, "wall": WALL_HP, "larder": LARDER_HP}
        for c in self.colonies:
            if not c.alive or not c.structure_queue: continue
            order = c.structure_queue.pop(0)
            stype, x, y = order["type"], order["x"], order["y"]
            if stype not in _STRUCT_COSTS:
                continue
            cost  = _STRUCT_COSTS[stype]
            limit = _STRUCT_LIMITS[stype]
            hp    = _STRUCT_HP[stype]
            own_of_type = sum(1 for st in self.structures
                              if st["colony"] == c.id and st["type"] == stype)
            tile_clear = not any(st["x"] == x and st["y"] == y for st in self.structures)
            passable_req = stype != "wall"  # walls go in passable spots too, but block after
            if (c.dirt >= cost
                    and own_of_type < limit
                    and (0 <= x < MAP_W and 0 <= y < MAP_H)
                    and tile_clear
                    and (self._passable(x, y) or stype == "wall")):
                c.dirt -= cost
                c.dirt = max(0, c.dirt)
                entry = {"x": x, "y": y, "colony": c.id, "type": stype,
                         "hp": 1, "max_hp": hp, "cd": 0,
                         "active": False, "build_progress": 0,
                         "build_required": BUILD_WORK_REQUIRED.get(stype, 60)}
                if stype == "barracks":
                    entry["spawn_timer"] = 0
                self.structures.append(entry)
                sname = stype.replace("_", " ").title()
                c.push_event(f"★ {sname} foundation laid at ({x},{y}) — workers needed to build")
                self._assign_builders_to_site(c, x, y)

        # Periodic re-check: top up builder count for any stalled incomplete sites
        if self.tick % 30 == 0:
            for c in self.colonies:
                if not c.alive: continue
                for site in self.structures:
                    if site.get("active", True) or site["colony"] != c.id: continue
                    self._assign_builders_to_site(c, site["x"], site["y"])

        # Evaluate directive triggers before behavior runs (auto-patch when conditions fire)
        for c in self.colonies:
            if c.alive:
                DirectiveEngine.eval_triggers(c, self)

        # Update ants (queens included — _behavior_queen handles queen defense)
        for c in self.colonies:
            if not c.alive: continue
            for ant in list(c.ants):
                self._update_ant(ant)

        # Construction: check if any build site is now complete
        for struct in self.structures:
            if not struct.get("active", True) and struct.get("build_progress", 0) >= struct.get("build_required", 1):
                struct["active"] = True
                struct["hp"] = struct["max_hp"]
                c = self.colonies[struct["colony"]]
                sname = struct.get("type", "structure").replace("_", " ").title()
                c.push_event(f"★ {sname} at ({struct['x']},{struct['y']}) construction COMPLETE!")
                c.push_notification("structure_complete",
                                    {"type": struct.get("type"), "x": struct["x"], "y": struct["y"]},
                                    tick=self.tick)

        # Structure actions — guard posts attack, barracks spawn, watchtowers contribute vision
        dead_structs = []
        for struct in self.structures:
            stype = struct.get("type", "guard_post")
            # Remove destroyed structures
            if struct["hp"] <= 0:
                dead_structs.append(struct)
                continue
            # Skip inactive (under construction) structures
            if not struct.get("active", True):
                continue

            if stype == "guard_post":
                if struct["cd"] > 0:
                    struct["cd"] -= 1
                else:
                    best, best_d = None, GUARD_POST_RANGE + 1
                    for ec in self.colonies:
                        if ec.id == struct["colony"]: continue
                        for e in ec.ants:
                            d = abs(e.x - struct["x"]) + abs(e.y - struct["y"])
                            if d < best_d:
                                best_d = d; best = e
                    if best:
                        struct["cd"] = GUARD_POST_CD
                        old_hp = max(0, int(best.hp))
                        best.hp -= GUARD_POST_DMG
                        new_hp = max(0, int(best.hp))
                        owner = self.colonies[struct["colony"]]
                        owner.push_event(f"Guard Post ({struct['x']},{struct['y']}) hit {['worker','soldier','scout','queen'][best.type]}! HP {old_hp}→{new_hp}")
                        if best.hp <= 0:
                            self._kill(best)

            elif stype == "larder":
                c = self.colonies[struct["colony"]]
                if c.alive:
                    c.food += LARDER_INCOME
                    c.food_earned_tick += LARDER_INCOME

            elif stype == "barracks":
                struct["spawn_timer"] = struct.get("spawn_timer", 0) + 1
                if struct["spawn_timer"] >= BARRACKS_SPAWN_TIME:
                    struct["spawn_timer"] = 0
                    c = self.colonies[struct["colony"]]
                    cost = c.SPAWN_COST[A_SOLDIER]
                    if c.alive and c.food >= cost:
                        c.food -= cost
                        new_sol = Ant(struct["x"] + random.randint(-1, 1),
                                     struct["y"] + random.randint(-1, 1),
                                     c.id, A_SOLDIER, born_tick=self.tick)
                        if c.soldier_hp_bonus > 0:
                            new_sol.hp     += c.soldier_hp_bonus
                            new_sol.max_hp += c.soldier_hp_bonus
                        c.ants.append(new_sol)
                        c.push_event(f"Barracks ({struct['x']},{struct['y']}) spawned soldier")

        for st in dead_structs:
            ec = self.colonies[st["colony"]]
            ec.push_event(f"★ {st.get('type','structure').title()} at ({st['x']},{st['y']}) DESTROYED!")
            self.structures.remove(st)

        # Colony production, upkeep, spawn queue & lifespan
        for c in self.colonies:
            if not c.alive: continue

            # ── Age-based death (lifespan) ──
            for ant in list(c.ants):
                if ant.lifespan is not None:
                    ant.age += 1
                    if ant.age >= ant.lifespan:
                        c.ants_lost += 1
                        c.push_event(f"aged out: {['worker','soldier','scout'][ant.type]} died at age {ant.age}")
                        self._kill(ant)

            # Starvation: lose ONE ant per tick when food deeply negative (shouldn't happen normally)
            if c.food < -50:
                non_queens = [a for a in c.ants if a.type != A_QUEEN]
                if non_queens:
                    victim = random.choice(non_queens)
                    c.push_event(f"starved: lost a {['worker','soldier','scout'][victim.type]}")
                    self._kill(victim)
                else:
                    if c.food < -55:
                        c.push_event("queen starved — colony extinct")
                        c.ants.clear()
                        c.alive = False

            # ── Spawn queue processing ──
            # Tick down all entries in the queue
            for i in range(len(c.spawn_queue)):
                entry = c.spawn_queue[i]
                c.spawn_queue[i] = (entry[0], entry[1] - 1, entry[2])
            # Spawn any that reached 0
            ready = [e for e in c.spawn_queue if e[1] <= 0]
            c.spawn_queue = [e for e in c.spawn_queue if e[1] > 0]
            for ant_type, _, food_cost in ready:
                queen = next((a for a in c.ants if a.type == A_QUEEN), None)
                if queen:
                    # Food was already reserved at queue time — just spawn.
                    new_ant = Ant(queen.x + random.randint(-2, 2),
                                  queen.y + random.randint(-2, 2), c.id, ant_type,
                                  born_tick=self.tick)
                    if ant_type == A_SOLDIER and c.soldier_hp_bonus > 0:
                        new_ant.hp     += c.soldier_hp_bonus
                        new_ant.max_hp += c.soldier_hp_bonus
                    c.ants.append(new_ant)
                else:
                    # Queen died during spawn delay — refund the reservation.
                    c.food += food_cost

            # ── Auto-queue production based on role ratios ──
            # Only queue if queue isn't full and we have a queen
            if len(c.spawn_queue) < c.MAX_SPAWN_QUEUE:
                queen = next((a for a in c.ants if a.type == A_QUEEN), None)
                if queen:
                    sp = c.directive["spawn"]
                    # Apply min_ratio floor — triggers can't zero out a type with min_ratio set
                    w_paused   = sp["worker"].get("pause", False)
                    sc_paused  = sp["scout"].get("pause", False)
                    sol_paused = sp["soldier"].get("pause", False)
                    wshare   = 0.0 if w_paused   else max(sp["worker"]["target_ratio"],  sp["worker"].get("min_ratio", 0.0))
                    sshare   = 0.0 if sc_paused  else max(sp["scout"]["target_ratio"],   sp["scout"].get("min_ratio", 0.0))
                    solshare = 0.0 if sol_paused else max(sp["soldier"]["target_ratio"], sp["soldier"].get("min_ratio", 0.0))
                    total_sh = wshare + sshare + solshare
                    if total_sh > 0:
                        wshare /= total_sh; sshare /= total_sh; solshare /= total_sh
                    worker_max = sp["worker"]["max"]
                    n_workers = sum(1 for a in c.ants if a.type == A_WORKER)
                    worker_capped = worker_max is not None and n_workers >= worker_max

                    # Food floor: reserve_food keeps food above a minimum; upgrade_reserve
                    # additionally protects food earmarked for a planned upgrade purchase.
                    reserve_floor = sp.get("reserve_food", 150) + sum(
                        c.directive["economy"].get("upgrade_reserve", {}).values())

                    # Determine what to queue next
                    r = random.random()
                    if total_sh == 0:
                        t = None  # all types paused
                    elif worker_capped:
                        non_w = sshare + solshare
                        t = A_SCOUT if (r < sshare / max(non_w, 0.01)) else A_SOLDIER
                    elif r < wshare:
                        t = A_WORKER
                    elif r < wshare + sshare:
                        t = A_SCOUT
                    else:
                        t = A_SOLDIER

                    if t is not None:
                        cost = c.SPAWN_COST[t]
                        spawn_time = c.SPAWN_TIME[t]
                        # Enforce reserve_floor: only spawn if food will still cover the reserve
                        if c.food - cost >= reserve_floor:
                            c.food -= cost  # reserve food at queue time
                            c.spawn_queue.append((t, spawn_time, cost))

            # Upgrade shop — three independent trees (worker / scout / soldier)
            # Bot auto-buy: graduated buffer (T1 = 1.5×, T2 = 1.3×, T3 = 1.2×)
            # LLM explicit buy: set_strategy({"buy_upgrade": "worker"|"scout"|"soldier"|true})
            _UNIT_TREES = [
                ("worker",  c.worker_tier,  c.worker_upgrade_pending,  WORKER_UPGRADE_COSTS),
                ("scout",   c.scout_tier,   c.scout_upgrade_pending,   SCOUT_UPGRADE_COSTS),
                ("soldier", c.soldier_tier, c.soldier_upgrade_pending, SOLDIER_UPGRADE_COSTS),
            ]
            for unit_type, cur_tier, pending, costs in _UNIT_TREES:
                if cur_tier >= 3: continue
                cost = costs[cur_tier]
                buf  = [1.5, 1.3, 1.2][cur_tier]
                bot_ready = c.food >= cost * buf
                if (pending or bot_ready) and c.food >= cost:
                    c.food -= cost
                    setattr(c, f"{unit_type}_upgrade_pending", False)
                    new_tier = cur_tier + 1
                    setattr(c, f"{unit_type}_tier", new_tier)
                    _apply_upgrade_effects(c, unit_type, new_tier)
                    label  = UPGRADE_LABELS[unit_type][cur_tier]
                    effect = UPGRADE_EFFECTS[unit_type][cur_tier]
                    c.push_event(f"★ {unit_type.upper()} T{new_tier} {label}: {effect}")
                    c.push_notification("upgrade_complete", {"unit": unit_type, "tier": new_tier, "label": label, "effect": effect}, tick=self.tick)

        # Food regrowth — per-source rate (contested mid-map sources regrow much faster)
        for f in self.foods:
            if f["amt"] < f["max"]:
                f["amt"] = min(f["max"], f["amt"] + f.get("regrow", FOOD_REGROW))

        # Dirt regrowth
        for dn in self.dirt_nodes:
            if dn["amt"] < dn["max"]:
                dn["amt"] = min(dn["max"], dn["amt"] + dn.get("regrow", DIRT_REGROW))

        # Update per-colony visual fog-of-war (includes watchtower vision)
        self._update_fog()

        # Log this tick's state + events
        if self.logger:
            self.logger.tick()

        # Check win condition
        self._check_win()

    def _check_win(self):
        if self.winner is not None:
            return
        # Colony is dead if it has no queen OR no ants at all (starvation extinction)
        dead = [c for c in self.colonies
                if not c.alive or not any(a.type == A_QUEEN for a in c.ants)]
        for c in dead:
            c.alive = False
        if len(dead) == 2:
            self.winner = "draw"
            if self.logger: self.logger.finish("draw")
        elif len(dead) == 1:
            loser = dead[0]
            self.winner = 1 - loser.id
            if self.logger: self.logger.finish(self.winner)
        if self.winner is not None:
            for c in self.colonies:
                outcome = ("draw" if self.winner == "draw"
                           else "victory" if self.winner == c.id else "defeat")
                c.push_notification("game_over", {"winner": self.winner, "outcome": outcome,
                                                  "reason": "queen_death"}, tick=self.tick)

        # Stalemate: no queen died after STALEMATE_TIMEOUT real seconds → win by score
        if self.winner is None and self.start_time:
            elapsed = time.time() - self.start_time
            if elapsed >= STALEMATE_TIMEOUT:
                _val = {A_WORKER: 5, A_SOLDIER: 20, A_SCOUT: 8}
                scores = []
                for c in self.colonies:
                    army_val = sum(_val.get(a.type, 0) for a in c.ants if a.type != A_QUEEN)
                    scores.append(c.food_collected + army_val + max(0, int(c.food)))
                if scores[0] > scores[1]:
                    self.winner = 0
                elif scores[1] > scores[0]:
                    self.winner = 1
                else:
                    self.winner = "draw"
                winner_name = ("RED" if self.winner == 0 else
                               "BLUE" if self.winner == 1 else "DRAW")
                sc_str = f"RED {scores[0]} vs BLUE {scores[1]}"
                for c in self.colonies:
                    c.push_event(f"STALEMATE — {winner_name} wins by score ({sc_str})")
                if self.logger: self.logger.finish(self.winner)

    # ── Ant Behavior ──

    def _update_ant(self, ant):
        ant.prev_x, ant.prev_y = ant.x, ant.y
        if ant.cooldown > 0: ant.cooldown -= 1

        # ── Emergency command override ──
        c = self.colonies[ant.colony]
        if c.emergency_command and ant.type != A_QUEEN:
            cmd = c.emergency_command
            if cmd["type"] == "RECALL":
                # All non-queen ants move to nest
                ant.tx, ant.ty = c.nx, c.ny
                self._wander(ant)
                return
            elif cmd["type"] == "FREEZE":
                # All ants stay put
                return
            elif cmd["type"] == "RETREAT":
                # Combat units flee to nest, workers keep working
                if ant.type in (A_SOLDIER, A_SCOUT):
                    ant.tx, ant.ty = c.nx, c.ny
                    self._wander(ant)
                    return
            elif cmd["type"] == "FOCUS" and ant.type == A_SOLDIER:
                # Soldiers move to target coordinates
                tx, ty = cmd.get("target", (c.nx, c.ny))
                ant.tx, ant.ty = tx, ty
                self._wander(ant)
                return
            elif cmd["type"] == "ALL_IN" and ant.type == A_SOLDIER:
                # Soldiers rush toward enemy
                if c.enemy:
                    eq = next((a for a in c.enemy.ants if a.type == A_QUEEN), None)
                    if eq:
                        ant.tx, ant.ty = eq.x, eq.y
                        self._wander(ant)
                        return

        if ant.type == A_WORKER:   self._behavior_worker(ant)
        elif ant.type == A_SOLDIER: self._behavior_soldier(ant)
        elif ant.type == A_SCOUT:   self._behavior_scout(ant)
        elif ant.type == A_QUEEN:   self._behavior_queen(ant)
        # Fog of war: if this ant is near the enemy nest, update scout intel
        c = self.colonies[ant.colony]
        if c.enemy and abs(ant.x - c.enemy.nx) + abs(ant.y - c.enemy.ny) <= 18:
            c.enemy_scouted_tick = self.tick
            c.enemy_scouted_counts = [sum(1 for a in c.enemy.ants if a.type == t)
                                      for t in range(4)]

    def _behavior_worker(self, ant):
        c = self.colonies[ant.colony]

        # Unit override: MCP direct command takes priority (except flee — safety always wins)
        ov = ant.unit_override
        if ov:
            cmd = ov.get("cmd")
            if cmd == "gather":
                # Re-assert food target every tick so it survives the delivery reset
                ant.recruit_target = (int(ov["x"]), int(ov["y"]))
            elif cmd == "build":
                # Manual build assignment: walk to site and contribute work each tick.
                # Override clears automatically when the structure is complete.
                bx, by = int(ov["x"]), int(ov["y"])
                # Deliver any carried food/dirt before heading to the build site
                if ant.carrying:
                    if abs(ant.x - c.nx) <= 2 and abs(ant.y - c.ny) <= 2:
                        ant.carrying = False
                        if getattr(ant, "carrying_type", "food") == "dirt":
                            c.dirt = min(DIRT_CAP, c.dirt + DIRT_DELIVER)
                            c.dirt_earned_tick += DIRT_DELIVER
                        else:
                            earned = FOOD_DELIVER + c.carry_bonus
                            c.food += earned
                            c.food_collected += earned
                            c.food_earned_tick += earned
                        ant.state = S_IDLE
                        self._dep(ant.x, ant.y, 0, 1.0)
                    else:
                        self._move_to(ant, c.nx, c.ny, 0)
                        if c.worker_fast:
                            self._move_to(ant, c.nx, c.ny, 0)
                        ant.state = S_RETURNING
                    return
                site = next((st for st in self.structures
                             if st["x"] == bx and st["y"] == by
                             and st["colony"] == ant.colony
                             and not st.get("active", True)), None)
                if site is None:
                    # Structure complete or not found — clear override and resume normal behavior
                    ant.unit_override = None
                else:
                    d = abs(ant.x - bx) + abs(ant.y - by)
                    if d > BUILD_RANGE:
                        self._move_to(ant, bx, by, 0)
                        ant.state = S_FORAGING
                    else:
                        # At site — contribute work (up to cap)
                        builders = sum(1 for a in c.ants if a.type == A_WORKER
                                       and abs(a.x - bx) + abs(a.y - by) <= BUILD_RANGE)
                        if builders <= BUILD_WORKER_CAP:
                            site["build_progress"] = site.get("build_progress", 0) + BUILD_RATE[c.worker_tier]
                        ant.state = S_BUILDING
                    return
            elif cmd in ("move_to", "hold"):
                tx, ty = int(ov["x"]), int(ov["y"])
                if abs(ant.x - tx) + abs(ant.y - ty) <= 2:
                    ant.state = S_IDLE
                else:
                    self._move_to(ant, tx, ty, 0)
                    ant.state = S_FORAGING
                return

        # FLEE: drop food and run home if an enemy soldier is within 4 tiles
        for ec in self.colonies:
            if ec.id == ant.colony: continue
            for e in ec.ants:
                if e.type == A_SOLDIER and abs(ant.x-e.x)+abs(ant.y-e.y) <= 4:
                    if ant.carrying:
                        ant.carrying = False   # food dropped — raid disrupted the haul
                        ant.recruit_target = None
                    ant.state = S_RETURNING
                    self._move_to(ant, c.nx, c.ny, 0)
                    return

        # RECRUITED: scout gave us a food target — go get it
        if ant.recruit_target:
            fx, fy = ant.recruit_target
            if ant.carrying:
                if abs(ant.x-c.nx) <= 2 and abs(ant.y-c.ny) <= 2:
                    ant.carrying = False
                    if ant.carrying_type == "dirt":
                        # Opportunistic dirt picked up on the outbound leg — credit dirt,
                        # keep recruit_target so the worker resumes its food trip
                        c.dirt = min(DIRT_CAP, c.dirt + DIRT_DELIVER)
                        c.dirt_earned_tick += DIRT_DELIVER
                        ant.carrying_type = "food"
                        ant.state = S_IDLE
                        self._dep(ant.x, ant.y, 0, 1.0)
                        return
                    earned = FOOD_DELIVER + c.carry_bonus
                    c.food += earned
                    c.food_collected += earned
                    c.food_earned_tick += earned
                    # Keep override-assigned food target; only clear autonomous recruits
                    if ov and ov.get("cmd") == "gather":
                        ant.recruit_target = (int(ov["x"]), int(ov["y"]))
                    else:
                        ant.recruit_target = None
                    ant.state = S_IDLE
                    self._dep(ant.x, ant.y, 0, 1.0)
                else:
                    self._move_to(ant, c.nx, c.ny, 0)
                    if c.worker_fast:
                        self._move_to(ant, c.nx, c.ny, 0)   # Worker T3: fast return
                    self._dep(ant.x, ant.y, 0, 0.9)
            else:
                f = self._food_nearby(ant.x, ant.y, 3)
                if f and f["amt"] > 10:
                    f["amt"] -= FOOD_PICK
                    if f["amt"] <= 0: self.foods.remove(f)
                    ant.carrying = True
                    ant.carrying_type = "food"
                    ant.state = S_RETURNING
                    self._dep(ant.x, ant.y, 0, 1.0)
                elif abs(ant.x - fx) + abs(ant.y - fy) <= 4:
                    # Arrived at node but depleted — abandon so worker reselects next tick
                    ant.recruit_target = None
                    ant.state = S_IDLE
                elif abs(ant.x - fx) + abs(ant.y - fy) > 50:
                    # Target is unreachably far — worker would age out before arriving.
                    # Clear and reselect so the 35-tile cap applies.
                    ant.recruit_target = None
                    ant.state = S_IDLE
                else:
                    # Outbound: opportunistic dirt pickup if passing near a deposit
                    dn = self._dirt_nearby(ant.x, ant.y, 2)
                    if dn and c.dirt < DIRT_CAP:
                        dn["amt"] -= DIRT_PICK
                        ant.carrying = True
                        ant.carrying_type = "dirt"
                        ant.state = S_RETURNING
                        if (dn["x"], dn["y"]) not in c.known_dirt:
                            c.known_dirt.append((dn["x"], dn["y"]))
                            if len(c.known_dirt) > 12: c.known_dirt.pop(0)
                        return
                    self._move_to(ant, fx, fy, 0)
                    self._dep(ant.x, ant.y, 0, 0.6)
            return

        # NOT RECRUITED: carrying → return home (deliver food or dirt)
        if ant.carrying:
            if abs(ant.x-c.nx) <= 2 and abs(ant.y-c.ny) <= 2:
                ant.carrying = False
                if ant.carrying_type == "dirt":
                    gained = DIRT_DELIVER
                    c.dirt = min(DIRT_CAP, c.dirt + gained)
                    c.dirt_earned_tick += gained
                    ant.carrying_type = "food"
                else:
                    earned = FOOD_DELIVER + c.carry_bonus
                    c.food += earned
                    c.food_collected += earned
                    c.food_earned_tick += earned
                ant.state = S_IDLE
            else:
                self._move_to(ant, c.nx, c.ny, 0)
                if c.worker_fast:
                    self._move_to(ant, c.nx, c.ny, 0)   # Worker T3: fast return while loaded
            return

        # Check if food or corpse is directly reachable
        for corp in self.corpses:
            if abs(ant.x - corp["x"]) + abs(ant.y - corp["y"]) <= 2 and corp["amt"] >= 1:
                corp["amt"] -= FOOD_PICK
                if corp["amt"] <= 0: self.corpses.remove(corp)
                ant.carrying = True
                ant.carrying_type = "food"
                ant.state = S_RETURNING
                return
        f = self._food_nearby(ant.x, ant.y, 2)
        if f and f["amt"] > 10:
            f["amt"] -= FOOD_PICK
            if f["amt"] <= 0: self.foods.remove(f)
            ant.carrying = True
            ant.carrying_type = "food"
            ant.state = S_RETURNING
            return

        # Opportunistic dirt pickup when right next to a node and colony needs dirt
        dn = self._dirt_nearby(ant.x, ant.y, 2)
        if dn and c.dirt < DIRT_CAP:
            dn["amt"] -= DIRT_PICK
            ant.carrying = True
            ant.carrying_type = "dirt"
            ant.state = S_RETURNING
            if (dn["x"], dn["y"]) not in c.known_dirt:
                c.known_dirt.append((dn["x"], dn["y"]))
                if len(c.known_dirt) > 12: c.known_dirt.pop(0)
            return

        # Navigate outbound: prefer safe-side corpses, then known food, then dirt if directed
        if self.corpses:
            enemy = c.enemy
            safe = [cr for cr in self.corpses
                    if enemy is None or
                    abs(cr["x"]-c.nx)+abs(cr["y"]-c.ny) <
                    abs(cr["x"]-enemy.nx)+abs(cr["y"]-enemy.ny)]
            if safe:
                best_corp = min(safe, key=lambda cr: abs(ant.x-cr["x"])+abs(ant.y-cr["y"]))
                if abs(ant.x - best_corp["x"]) + abs(ant.y - best_corp["y"]) <= 20:
                    ant.state = S_FORAGING
                    self._move_to(ant, best_corp["x"], best_corp["y"], 0)
                    return

        # Dirt gathering: active when gather_dirt directive set AND colony needs it
        gather_dirt = c.directive["economy"].get("gather_dirt", False)
        if gather_dirt and c.dirt < DIRT_CAP and c.known_dirt and random.random() < 0.4:
            target_dn = min(c.known_dirt, key=lambda p: abs(ant.x-p[0])+abs(ant.y-p[1]))
            ant.state = S_FORAGING
            self._move_to(ant, target_dn[0], target_dn[1], 0)
            return

        # Auto-construction: workers within BUILD_RANGE of an own incomplete site always build,
        # even if food is available. Prevents structures from stalling when workers pass nearby.
        for site in self.structures:
            if site.get("active", True): continue
            if site["colony"] != ant.colony: continue
            d = abs(ant.x - site["x"]) + abs(ant.y - site["y"])
            if d <= BUILD_RANGE:
                builders = sum(1 for a in c.ants if a.type == A_WORKER
                               and abs(a.x - site["x"]) + abs(a.y - site["y"]) <= BUILD_RANGE)
                if builders <= BUILD_WORKER_CAP:
                    site["build_progress"] = site.get("build_progress", 0) + BUILD_RATE[c.worker_tier]
                ant.state = S_BUILDING
                return

        if c.known_food:
            def _node_amt(pos):
                f = next((fd for fd in self.foods if (fd["x"], fd["y"]) == pos), None)
                return f["amt"] if f else 0
            pf = c.directive["economy"]["priority_food"]
            pf_t = tuple(pf) if isinstance(pf, (list, tuple)) and len(pf) == 2 else None
            if pf is not None and pf_t is None:
                # Bad value (not a 2-element list/tuple) — clear it silently
                c.directive["economy"]["priority_food"] = None
            if pf_t and pf_t in c.known_food and _node_amt(pf_t) <= 10:
                # Priority node is depleted — auto-clear so workers don't strand
                c.directive["economy"]["priority_food"] = None
                pf_t = None
                c.push_event(f"priority_food ({pf[0]},{pf[1]}) depleted — cleared, workers spreading")
                c.push_notification("priority_food_cleared",
                                    {"x": pf[0], "y": pf[1], "reason": "depleted"}, tick=self.tick)
            if pf_t and pf_t in c.known_food:
                target = pf_t
            else:
                def _workers_near(pos):
                    return sum(1 for a in c.ants if a.type == A_WORKER
                               and (a.recruit_target == pos
                                    or abs(a.x - pos[0]) + abs(a.y - pos[1]) <= 5))
                viable = [p for p in c.known_food if _node_amt(p) > 10]
                pool = viable if viable else c.known_food
                # Prefer nodes within 35 tiles — avoids workers pathing deep into enemy territory
                close = [p for p in pool if abs(ant.x - p[0]) + abs(ant.y - p[1]) <= 35]
                use_pool = close if close else pool
                unsaturated = [p for p in use_pool
                               if _workers_near(p) < FOOD_NODE_WORKER_CAP.get(
                                   c.food_intel.get(p, {}).get("tier", "approach"), 6)]
                target = random.choice(unsaturated if unsaturated else (close if close else pool))
            ant.recruit_target = target  # commit so saturation count includes en-route workers
            ant.state = S_FORAGING
            self._move_to(ant, target[0], target[1], 0)
            return

        # Auto-construction fallback: when no food known, walk toward nearby incomplete site
        for site in self.structures:
            if site.get("active", True): continue
            if site["colony"] != ant.colony: continue
            d = abs(ant.x - site["x"]) + abs(ant.y - site["y"])
            if d <= 25:
                self._move_to(ant, site["x"], site["y"], 0)
                ant.state = S_FORAGING
                return

        # No known food — explore toward unexplored areas to discover new sources
        ant.state = S_EXPLORING
        if ant.tx is None or abs(ant.x - ant.tx) + abs(ant.y - ant.ty) <= 3:
            a = random.uniform(0, 2 * math.pi)
            d = random.randint(20, 40)
            ant.tx = max(0, min(MAP_W - 1, ant.x + int(math.cos(a) * d)))
            ant.ty = max(0, min(MAP_H - 1, ant.y + int(math.sin(a) * d)))
        self._move_to(ant, ant.tx, ant.ty, 0)
        # Pick up any food discovered during exploration
        f2 = self._food_nearby(ant.x, ant.y, 2)
        if f2 and f2["amt"] > 5:
            f2["amt"] -= FOOD_PICK
            if f2["amt"] <= 0: self.foods.remove(f2)
            ant.carrying = True; ant.carrying_type = "food"; ant.tx = ant.ty = None
            key2 = (f2["x"], f2["y"])
            if key2 not in c.known_food:
                c.known_food.append(key2)
                c.push_event(f"worker explored — found {f2['kind']} at ({f2['x']},{f2['y']})")
                if len(c.known_food) > 25: c.known_food.pop(0)
            ant.state = S_RETURNING

    def _behavior_scout(self, ant):
        c = self.colonies[ant.colony]

        # Unit override: per-ant patrol or move command
        ov = ant.unit_override
        if ov and not ant.carrying:
            cmd = ov.get("cmd")
            if cmd == "patrol":
                wps = ov.get("waypoints", [])
                if wps and len(wps) >= 2:
                    if not hasattr(ant, "patrol_idx"): ant.patrol_idx = 0
                    idx = ant.patrol_idx % len(wps)
                    wx, wy = int(wps[idx][0]), int(wps[idx][1])
                    if abs(ant.x - wx) + abs(ant.y - wy) <= 2:
                        ant.patrol_idx = (ant.patrol_idx + 1) % len(wps)
                    ant.state = S_EXPLORING
                    self._move_to(ant, wx, wy, 3)
                    self._dep(ant.x, ant.y, 3, 0.6)
                    return
            elif cmd == "move_to":
                tx, ty = int(ov["x"]), int(ov["y"])
                ant.state = S_EXPLORING
                self._move_to(ant, tx, ty, 3)
                self._dep(ant.x, ant.y, 3, 0.6)
                return

        # Patrol waypoints: loop through a fixed sequence of coordinates
        # Only active when not carrying food (intel mission, not foraging)
        if not ant.carrying:
            wps = ant.resolved_config(c.directive).get("patrol_waypoints")
            if wps and len(wps) >= 2:
                if not hasattr(ant, "patrol_idx"): ant.patrol_idx = 0
                idx = ant.patrol_idx % len(wps)
                wx, wy = int(wps[idx][0]), int(wps[idx][1])
                if abs(ant.x - wx) + abs(ant.y - wy) <= 2:
                    ant.patrol_idx = (ant.patrol_idx + 1) % len(wps)
                ant.state = S_EXPLORING
                self._move_to(ant, wx, wy, 3)
                self._dep(ant.x, ant.y, 3, 0.6)
                return

        # Carrying food: rush home (scouts are fast — single extra step), recruit workers on arrival
        if ant.carrying:
            if abs(ant.x-c.nx) <= 2 and abs(ant.y-c.ny) <= 2:
                ant.carrying = False
                earned = FOOD_DELIVER + c.carry_bonus
                c.food += earned
                c.food_collected += earned
                c.food_earned_tick += earned
                self._dep(ant.x, ant.y, 0, 1.0)
                # RECRUIT: find nearby workers and send them to food
                if ant.tx is not None:
                    recruited = 0
                    for w in c.ants:
                        if (w.type == A_WORKER
                                and w.state in (S_IDLE, S_FORAGING)
                                and not w.carrying and not w.recruit_target
                                and abs(w.x-c.nx) <= 10 and abs(w.y-c.ny) <= 10):
                            w.recruit_target = (ant.tx, ant.ty)
                            w.state = S_RECRUITED
                            recruited += 1
                            if recruited >= c.scout_recruit:
                                break
                    ant.tx = None
            else:
                # scouts move one extra step when loaded (intentional speed boost)
                self._move_to(ant, c.nx, c.ny, 0)
                self._move_to(ant, c.nx, c.ny, 0)
                self._dep(ant.x, ant.y, 0, 1.0)
            return

        # Find food: pick it up (detection radius scales with scout tier)
        f = self._food_nearby(ant.x, ant.y, c.scout_detect)
        if f and f["amt"] > 10:
            f["amt"] -= 30
            if f["amt"] <= 0: self.foods.remove(f)
            ant.carrying = True
            ant.tx, ant.ty = f["x"], f["y"]
            if (f["x"], f["y"]) not in c.known_food:
                c.known_food.append((f["x"], f["y"]))
                c.push_event(f"scout found {f['kind']} at ({f['x']},{f['y']})")
                if len(c.known_food) > 20: c.known_food.pop(0)
            return

        # Discover dirt nodes nearby
        dn = self._dirt_nearby(ant.x, ant.y, c.scout_detect)
        if dn and (dn["x"], dn["y"]) not in c.known_dirt:
            c.known_dirt.append((dn["x"], dn["y"]))
            c.push_event(f"scout found dirt deposit at ({dn['x']},{dn['y']})")
            if len(c.known_dirt) > 12: c.known_dirt.pop(0)

        # Spot enemy — lay alarm
        enemy = self._nearest_enemy(ant, 12)
        if enemy:
            self._dep(ant.x, ant.y, 1, 1.0)

        # Explore outward
        if ant.tx is None or random.random() < 0.02:
            known = c.known_food
            # Low revisit rate: scouts should push into unexplored areas
            if known and random.random() < 0.12:
                target = random.choice(known)
                ant.tx, ant.ty = target
            else:
                _exp = ant.resolved_config(c.directive).get("expansion", [1, 1])
                ex, ey = _exp[0], _exp[1]
                a = math.atan2(ey, ex) + random.gauss(0, 0.45)
                # Range scales with colony size, capped at near full-map diagonal
                d = random.randint(35, min(90, 45 + len(c.ants)))
                ant.tx = max(0, min(MAP_W-1, c.nx + int(math.cos(a)*d)))
                ant.ty = max(0, min(MAP_H-1, c.ny + int(math.sin(a)*d)))
            ant.state = S_EXPLORING

        self._move_to(ant, ant.tx, ant.ty, 3)
        if c.scout_fast:
            self._move_to(ant, ant.tx, ant.ty, 3)   # Scout T2: double explore speed
        if ant.tx is not None and abs(ant.x-ant.tx) <= 1 and abs(ant.y-ant.ty) <= 1:
            ant.tx = None
        self._dep(ant.x, ant.y, 3, 0.6)

    def _behavior_soldier(self, ant):
        c = self.colonies[ant.colony]

        # Unit override: MCP direct command — move/attack/hold/patrol
        ov = ant.unit_override
        if ov:
            cmd = ov.get("cmd")
            if cmd in ("move_to", "attack_xy"):
                tx, ty = int(ov["x"]), int(ov["y"])
                near = self._nearest_enemy(ant, 8, queen_focus=(cmd == "attack_xy"))
                if near:
                    self._move_to(ant, near.x, near.y, 1)
                    ant.state = S_FIGHTING
                else:
                    self._move_to(ant, tx, ty, 1)
                    ant.state = S_PATROLLING
                self._dep(ant.x, ant.y, 1, 0.5)
            elif cmd == "hold":
                tx, ty = int(ov["x"]), int(ov["y"])
                near = self._nearest_enemy(ant, 5)
                if near:
                    self._move_to(ant, near.x, near.y, 1)
                    ant.state = S_FIGHTING
                elif abs(ant.x - tx) + abs(ant.y - ty) > 2:
                    self._move_to(ant, tx, ty, 1)
                self._dep(ant.x, ant.y, 1, 0.3)
            elif cmd == "patrol":
                wps = ov.get("waypoints", [])
                if wps:
                    if not hasattr(ant, "patrol_idx"): ant.patrol_idx = 0
                    idx = ant.patrol_idx % len(wps)
                    wx, wy = int(wps[idx][0]), int(wps[idx][1])
                    if abs(ant.x - wx) + abs(ant.y - wy) <= 2:
                        ant.patrol_idx = (ant.patrol_idx + 1) % len(wps)
                    near = self._nearest_enemy(ant, 6)
                    if near:
                        self._move_to(ant, near.x, near.y, 1)
                        ant.state = S_FIGHTING
                    else:
                        self._move_to(ant, wx, wy, 1)
                        ant.state = S_PATROLLING
                    self._dep(ant.x, ant.y, 1, 0.3)
            # All override modes: still attack any adjacent enemy.
            # siege=True removes the queen's +12 avoidance penalty so soldiers can hit her
            # when adjacent; queen_focus amplifies this for attack_xy commands.
            adj = self._nearest_enemy(ant, 1, siege=True, queen_focus=(cmd == "attack_xy"))
            if adj and ant.cooldown <= 0:
                dmg = SOLDIER_DMG + c.dmg_bonus
                old_hp = int(adj.hp)
                adj.hp -= dmg; ant.cooldown = c.soldier_fast_cd
                new_hp = max(0, int(adj.hp))
                if adj.type == A_QUEEN:
                    c.queen_dmg_dealt_tick += dmg
                    ec = self.colonies[adj.colony]
                    ec.push_event(f"★ QUEEN UNDER ATTACK! HP: {old_hp}→{new_hp} (dealt {dmg})")
                    ec.push_notification("queen_under_attack", {"hp": new_hp, "old_hp": old_hp, "dmg": dmg}, tick=self.tick)
                    c.push_event(f"★ SIEGE — striking enemy queen! HP: {old_hp}→{new_hp} (dealt {dmg})")
                if c.soldier_splash:
                    splash_dmg = max(1, int(dmg * 0.4))
                    for ec in self.colonies:
                        if ec.id == ant.colony: continue
                        for stgt in list(ec.ants):
                            if stgt is adj: continue
                            if abs(stgt.x - adj.x) + abs(stgt.y - adj.y) <= 1:
                                stgt.hp -= splash_dmg
                                if stgt.hp <= 0: self._kill(stgt)
                if adj.hp <= 0:
                    ec = self.colonies[adj.colony]
                    ec.push_event(f"{['worker','soldier','scout','queen'][adj.type]} killed in battle!")
                    c.push_event(f"killed enemy {['worker','soldier','scout','queen'][adj.type]}")
                    self._kill(adj)
            return

        # Siege mode: near enemy nest, hunt queen directly instead of mopping up workers
        in_siege = (c.enemy is not None
                    and abs(ant.x - c.enemy.nx) + abs(ant.y - c.enemy.ny) <= 12)
        queen_focus = c.directive["military"]["siege_priority"] == "queen"

        # Soldiers at rally hold their position — only react to close threats (5 tiles) so
        # they don't chase scouts through the staging point and break formation.
        _rally = c.directive["military"]["rally_point"]
        _at_rally = False
        if _rally and not in_siege:
            _rp = _rally[0] if isinstance(_rally[0], (list, tuple)) else _rally
            _at_rally = abs(ant.x - int(_rp[0])) + abs(ant.y - int(_rp[1])) <= 4

        detect_range = 5 if _at_rally else 15
        enemy = self._nearest_enemy(ant, detect_range, siege=in_siege, queen_focus=queen_focus)
        if enemy:
            self._move_to(ant, enemy.x, enemy.y, 1)
            ant.state = S_FIGHTING
            self._dep(ant.x, ant.y, 1, 1.0)
            steps = max(1, abs(ant.x - c.nx) + abs(ant.y - c.ny))
            for i in range(min(steps, 20)):
                t = i / min(steps, 20)
                x = int(ant.x + (c.nx - ant.x) * t)
                y = int(ant.y + (c.ny - ant.y) * t)
                self._dep(x, y, 1, 0.9 * (1 - t * 0.5))
            if abs(ant.x-enemy.x) <= 1 and abs(ant.y-enemy.y) <= 1:
                if ant.cooldown <= 0:
                    dmg = SOLDIER_DMG + c.dmg_bonus
                    old_hp = int(enemy.hp)
                    enemy.hp -= dmg; ant.cooldown = c.soldier_fast_cd
                    new_hp = max(0, int(enemy.hp))
                    if enemy.type == A_QUEEN:
                        c.queen_dmg_dealt_tick += dmg
                        ec = self.colonies[enemy.colony]
                        ec.push_event(f"★ QUEEN UNDER ATTACK! HP: {old_hp}→{new_hp} (dealt {dmg})")
                        ec.push_notification("queen_under_attack", {"hp": int(new_hp), "old_hp": int(old_hp), "dmg": dmg}, tick=self.tick)
                        c.push_event(f"★ SIEGE — striking enemy queen! HP: {old_hp}→{new_hp} (dealt {dmg})")
                    # Soldier T3: splash 40% to all enemies adjacent to primary target
                    if c.soldier_splash:
                        splash_dmg = max(1, int(dmg * 0.4))
                        for ec in self.colonies:
                            if ec.id == ant.colony: continue
                            for stgt in list(ec.ants):
                                if stgt is enemy: continue
                                if abs(stgt.x - enemy.x) + abs(stgt.y - enemy.y) <= 1:
                                    stgt.hp -= splash_dmg
                                    if stgt.hp <= 0:
                                        self._kill(stgt)
                    if enemy.hp <= 0:
                        ec = self.colonies[enemy.colony]
                        ec.push_event(f"{['worker','soldier','scout','queen'][enemy.type]} killed in battle!")
                        c.push_event(f"killed enemy {['worker','soldier','scout','queen'][enemy.type]}")
                        self._kill(enemy)
        else:
            # RETREAT: pull all soldiers toward own nest — only fight if an enemy is right here
            if c.directive["military"]["retreat"] and not in_siege:
                ant.state = S_PATROLLING
                self._move_to(ant, c.nx, c.ny, 2)
                self._dep(ant.x, ant.y, 2, 0.3)
                return

            # Rally point takes priority over alarm pheromone — soldiers with staging orders
            # march through active combat zones to the staging point, fighting only direct threats.
            # rally_point can be [x,y] (single) or [[x1,y1],[x2,y2],...] (waypoints).
            rally = c.directive["military"]["rally_point"]
            if rally and not in_siege:
                # Normalize: waypoints list vs single point
                if rally and isinstance(rally[0], (list, tuple)):
                    rx, ry = int(rally[0][0]), int(rally[0][1])
                else:
                    rx, ry = int(rally[0]), int(rally[1])
                if abs(ant.x - rx) + abs(ant.y - ry) > 4:
                    ant.state = S_PATROLLING
                    self._move_to(ant, rx, ry, 2)
                    self._dep(ant.x, ant.y, 2, 0.3)
                else:
                    # At rally — hold position; attack any enemy structure in reach
                    ant.state = S_PATROLLING
                    self._dep(ant.x, ant.y, 2, 0.3)
                    near_struct = None
                    near_struct_d = 15
                    for struct in self.structures:
                        if struct["colony"] == ant.colony: continue
                        sd = abs(ant.x - struct["x"]) + abs(ant.y - struct["y"])
                        if sd < near_struct_d:
                            near_struct_d = sd; near_struct = struct
                    if near_struct:
                        self._move_to(ant, near_struct["x"], near_struct["y"], 2)
                        if near_struct_d <= 1 and ant.cooldown <= 0:
                            dmg = SOLDIER_DMG + c.dmg_bonus
                            near_struct["hp"] -= dmg
                            ant.cooldown = c.soldier_fast_cd
                            stype = near_struct.get("type", "guard_post").replace("_", " ").title()
                            if near_struct["hp"] <= 0:
                                ec = self.colonies[near_struct["colony"]]
                                ec.push_event(f"★ {stype} at ({near_struct['x']},{near_struct['y']}) DESTROYED!")
                                c.push_event(f"Destroyed enemy {stype} at ({near_struct['x']},{near_struct['y']})!")
                                self.structures.remove(near_struct)
                    # Auto-release when rally_release_at threshold is met
                    release_n = c.directive["military"]["rally_release_at"]
                    if release_n:
                        staged = sum(1 for a in c.ants
                                     if a.type == A_SOLDIER
                                     and abs(a.x - rx) + abs(a.y - ry) <= 4)
                        if staged >= release_n:
                            rally_mode = c.directive["military"].get("rally_mode", "normal")
                            current_rally = c.directive["military"]["rally_point"]
                            if isinstance(current_rally[0], (list, tuple)) and len(current_rally) > 1:
                                # Waypoint: advance to next point in sequence
                                c.directive["military"]["rally_point"] = current_rally[1:]
                                c.push_event(f"★ WAYPOINT ({rx},{ry}) reached — next: {current_rally[1]}")
                            elif rally_mode == "auto_forward" and c.enemy:
                                # Auto-forward: advance rally toward enemy 40% of remaining distance
                                ex, ey = c.enemy.nx, c.enemy.ny
                                dx = ex - rx; dy = ey - ry
                                dist = math.sqrt(dx*dx + dy*dy)
                                if dist > 8:
                                    step = max(6, int(dist * 0.4))
                                    nrx = max(2, min(MAP_W-2, int(rx + dx/dist * step)))
                                    nry = max(2, min(MAP_H-2, int(ry + dy/dist * step)))
                                    c.directive["military"]["rally_point"] = [nrx, nry]
                                    c.push_event(f"★ AUTO-FORWARD: rally ({rx},{ry})→({nrx},{nry})")
                                else:
                                    c.directive["military"]["rally_point"] = None
                                    c.directive["military"]["rally_release_at"] = None
                                    c.push_event(f"★ AUTO-FORWARD: at enemy — releasing {staged} soldiers!")
                            else:
                                c.directive["military"]["rally_point"] = None
                                c.directive["military"]["rally_release_at"] = None
                                c.push_event(f"★ RALLY RELEASED — {staged} soldiers advancing!")
                                c.push_notification("rally_released", {"staged": staged, "from": [rx, ry]}, tick=self.tick)
                return

            # ATTACK_TARGET: continuous advance toward a specific coordinate — never hold.
            # Soldiers keep pushing toward the target, engaging any enemies encountered en route.
            attack_tgt = c.directive["military"]["attack_target"]
            if not attack_tgt and c.directive["military"].get("auto_attack") and c.enemy:
                # AUTO_ATTACK: target enemy queen (fog-of-war safe).
                # Queen position only known if own soldiers are in siege range.
                soldiers_in_siege_now = sum(1 for a in c.ants
                                            if a.type == A_SOLDIER
                                            and abs(a.x - c.enemy.nx) + abs(a.y - c.enemy.ny) <= 15)
                if soldiers_in_siege_now > 0:
                    eq = next((a for a in c.enemy.ants if a.type == A_QUEEN), None)
                    attack_tgt = [eq.x, eq.y] if eq else [c.enemy.nx, c.enemy.ny]
                else:
                    attack_tgt = [c.enemy.nx, c.enemy.ny]  # nest coords always known
            if attack_tgt and not in_siege:
                ax, ay = int(attack_tgt[0]), int(attack_tgt[1])
                ant.state = S_PATROLLING
                self._move_to(ant, ax, ay, 2)
                self._dep(ant.x, ant.y, 2, 0.3)
                return

            # No rally — follow fresh alarm pheromone toward active combat.
            # Do NOT re-deposit alarm here: that keeps stale pools alive and traps soldiers.
            if self._follow(ant, 1):
                ant.state = S_FIGHTING
            else:
                # Target enemy guard posts before falling back to random patrol
                nearest_struct = None
                nearest_struct_d = 25
                for struct in self.structures:
                    if struct["colony"] == ant.colony: continue
                    d = abs(ant.x - struct["x"]) + abs(ant.y - struct["y"])
                    if d < nearest_struct_d:
                        nearest_struct_d = d; nearest_struct = struct
                if nearest_struct:
                    self._move_to(ant, nearest_struct["x"], nearest_struct["y"], 2)
                    ant.state = S_PATROLLING
                    if nearest_struct_d <= 1 and ant.cooldown <= 0:
                        dmg = SOLDIER_DMG + c.dmg_bonus
                        nearest_struct["hp"] -= dmg
                        ant.cooldown = c.soldier_fast_cd
                        stype = nearest_struct.get("type", "guard_post").replace("_", " ").title()
                        if nearest_struct["hp"] <= 0:
                            ec = self.colonies[nearest_struct["colony"]]
                            ec.push_event(f"★ {stype} at ({nearest_struct['x']},{nearest_struct['y']}) DESTROYED!")
                            c.push_event(f"Destroyed enemy {stype} at ({nearest_struct['x']},{nearest_struct['y']})!")
                            self.structures.remove(nearest_struct)
                    return
                if ant.tx is None or random.random() < 0.03:
                    defense   = c.directive["military"]["stance"]
                    formation = c.directive["military"].get("formation", "wedge")
                    if defense == "aggressive" and c.enemy:
                        # Aggressive: converge on enemy queen; formation controls spread angle
                        eq = next((a for a in c.enemy.ants if a.type == A_QUEEN), None)
                        enx = eq.x if eq else c.enemy.nx
                        eny = eq.y if eq else c.enemy.ny
                        spread = {"column": 0.06, "spread": 0.40}.get(formation, 0.15)
                        a = math.atan2(eny - c.ny, enx - c.nx) + random.gauss(0, spread)
                        d = random.randint(60, 95)
                    elif defense == "defensive":
                        # Defensive: stay close to own nest
                        _exp = ant.resolved_config(c.directive).get("expansion", [1, 1])
                        ex, ey = _exp[0], _exp[1]
                        a = math.atan2(ey, ex) + random.gauss(0, 1.0)
                        d = random.randint(15, 35)
                    else:
                        # Balanced: patrol toward center with moderate spread
                        _exp = ant.resolved_config(c.directive).get("expansion", [1, 1])
                        ex, ey = _exp[0], _exp[1]
                        a = math.atan2(ey, ex) + random.gauss(0, 0.7)
                        d = random.randint(35, 65)
                    ant.tx = max(0, min(MAP_W-1, c.nx + int(math.cos(a)*d)))
                    ant.ty = max(0, min(MAP_H-1, c.ny + int(math.sin(a)*d)))
                    ant.state = S_PATROLLING
                self._move_to(ant, ant.tx, ant.ty, 2)
                if ant.tx is not None and abs(ant.x-ant.tx) <= 1 and abs(ant.y-ant.ty) <= 1:
                    ant.tx = None
                self._dep(ant.x, ant.y, 2, 0.3)

    # ── Movement ──

    def _passable(self, x, y):
        if not (0 <= x < MAP_W and 0 <= y < MAP_H): return False
        if self.terrain[y][x] in (T_WATER, T_ROCK): return False
        # Walls are impassable (but only to enemy ants — own ants path through for now)
        for st in self.structures:
            if st.get("type") == "wall" and st["x"] == x and st["y"] == y:
                return False
        return True

    def _assign_builders_to_site(self, colony, sx, sy):
        """Assign up to BUILD_WORKER_CAP idle workers from colony to build the site at (sx,sy)."""
        already = sum(1 for a in colony.ants if a.type == A_WORKER and a.unit_override
                      and a.unit_override.get("cmd") == "build"
                      and a.unit_override.get("x") == sx and a.unit_override.get("y") == sy)
        slots = BUILD_WORKER_CAP - already
        if slots <= 0:
            return
        # Prefer workers not carrying (can start walking immediately), sorted by distance
        candidates = sorted(
            [a for a in colony.ants if a.type == A_WORKER and not a.unit_override and not a.carrying],
            key=lambda a: abs(a.x - sx) + abs(a.y - sy)
        )
        for ant in candidates[:slots]:
            ant.unit_override = {"cmd": "build", "x": sx, "y": sy}

    def _move_to(self, ant, tx, ty, layer):
        dx = (1 if tx > ant.x else -1) if tx != ant.x else 0
        dy = (1 if ty > ant.y else -1) if ty != ant.y else 0
        cands = [(ant.x+dx, ant.y+dy)]
        if dx and dy: cands += [(ant.x+dx, ant.y), (ant.x, ant.y+dy)]
        random.shuffle(cands)
        for nx, ny in cands:
            if self._passable(nx, ny):
                ant.x, ant.y = nx, ny; return
        # All primary directions blocked (water/rock) — try remaining 8 neighbors
        # sorted by distance to target so ants route around obstacles
        tried = set(cands)
        fallback = sorted(
            [(ant.x+ddx, ant.y+ddy) for ddx in (-1, 0, 1) for ddy in (-1, 0, 1)
             if (ddx, ddy) != (0, 0) and (ant.x+ddx, ant.y+ddy) not in tried],
            key=lambda p: abs(p[0]-tx) + abs(p[1]-ty)
        )
        for nx, ny in fallback:
            if self._passable(nx, ny):
                ant.x, ant.y = nx, ny; return

    def _move_prey(self, prey, tx, ty):
        dx = (1 if tx > prey.x else -1) if tx != prey.x else 0
        dy = (1 if ty > prey.y else -1) if ty != prey.y else 0
        cands = [(prey.x+dx, prey.y+dy)]
        if dx and dy: cands += [(prey.x+dx, prey.y), (prey.x, prey.y+dy)]
        random.shuffle(cands)
        for nx, ny in cands:
            if self._passable(nx, ny):
                prey.x, prey.y = nx, ny; return

    def _wander(self, ant):
        dirs = [(-1,-1),(-1,0),(-1,1),(0,-1),(0,1),(1,-1),(1,0),(1,1)]
        random.shuffle(dirs)
        for dx, dy in dirs:
            if self._passable(ant.x+dx, ant.y+dy):
                ant.x, ant.y = ant.x+dx, ant.y+dy; return

    def _follow(self, ant, layer, radius=2, threshold=None):
        return False  # pheromone trails removed; territory system replaces them

    # ── Queries ──

    def _dep(self, x, y, layer, val):
        pass  # pheromone deposition removed

    def _get_p(self, x, y, layer):
        return 0  # pheromone system removed

    def _dirt_nearby(self, x, y, radius=3):
        best, best_d = None, radius + 1
        for dn in self.dirt_nodes:
            d = abs(dn["x"] - x) + abs(dn["y"] - y)
            if d < best_d and dn["amt"] >= DIRT_PICK:
                best_d = d; best = dn
        return best

    def _food_nearby(self, x, y, radius=3):
        best, best_d = None, radius+1
        for f in self.foods:
            d = abs(f["x"]-x)+abs(f["y"]-y)
            if d < best_d: best_d = d; best = f
        return best

    def _behavior_queen(self, ant):
        """Queen defends herself — priority targets soldiers, then scouts, then workers."""
        c = self.colonies[ant.colony]
        if ant.cooldown > 0:
            return
        # Scan range 12 (matches siege range). Soldiers get effective distance bonus so
        # the queen prioritises the most dangerous intruders first.
        best, best_eff = None, 12
        for ec in self.colonies:
            if ec.id == ant.colony: continue
            for e in ec.ants:
                d = abs(ant.x-e.x) + abs(ant.y-e.y)
                # Priority bonus: soldiers=-4, scouts=-2, workers=0
                priority_bonus = {A_SOLDIER: -4, A_SCOUT: -2}.get(e.type, 0)
                eff = d + priority_bonus
                if eff < best_eff:
                    best_eff = eff; best = e
        if best:
            ant.state = S_FIGHTING
            ant.cooldown = QUEEN_CD
            old_hp = max(0, int(best.hp))
            best.hp -= QUEEN_DMG
            new_hp = max(0, int(best.hp))
            self._dep(ant.x, ant.y, 1, 1.0)  # alarm pheromone
            if best.type == A_SOLDIER:
                c.push_event(f"★ QUEEN fighting back! Killed/hit soldier HP {old_hp}→{new_hp}")
            if best.hp <= 0:
                ec = self.colonies[best.colony]
                ec.push_event(f"queen killed a {['worker','soldier','scout','queen'][best.type]}!")
                c.push_event(f"queen defended nest! Killed {['worker','soldier','scout','queen'][best.type]}")
                self._kill(best)
        else:
            ant.state = S_IDLE

    def _nearest_enemy(self, ant, radius, siege=False, queen_focus=False):
        best, best_d = None, radius+1
        for c in self.colonies:
            if c.id == ant.colony: continue
            for o in c.ants:
                d = abs(ant.x-o.x)+abs(ant.y-o.y)
                if queen_focus and o.type == A_QUEEN:
                    effective_d = d - 8   # strongly prefer queen over any nearby defenders
                elif siege:
                    effective_d = d       # siege: queen equally weighted with defenders
                else:
                    effective_d = d + (12 if o.type == A_QUEEN else 0)  # normal: mop up defenders first
                if effective_d < best_d: best_d = effective_d; best = o
        return best

    def _kill(self, ant):
        for c in self.colonies:
            if ant in c.ants:
                c.ants.remove(ant)
                c.ants_lost += 1
                self.corpses.append({"x": ant.x, "y": ant.y, "amt": float(CORPSE_FOOD[ant.type])})
                for ally in c.ants:
                    if abs(ally.x-ant.x) + abs(ally.y-ant.y) <= 8:
                        self._dep(ally.x, ally.y, 1, 1.0)
                return

    # ── Territory ──

    def _update_territory(self):
        """Claim tiles by ant presence; decay unclaimed tiles back to neutral."""
        ter = self.territory
        age = self.territory_age
        # Claim tiles around each ant
        for c in self.colonies:
            cid = c.id + 1  # 1=RED, 2=BLUE
            for ant in c.ants:
                r = 2 if ant.type == A_SOLDIER else (3 if ant.type == A_QUEEN else 1)
                ax, ay = ant.x, ant.y
                for dy in range(-r, r + 1):
                    for dx in range(-r, r + 1):
                        tx, ty = ax + dx, ay + dy
                        if 0 <= tx < MAP_W and 0 <= ty < MAP_H:
                            idx = ty * MAP_W + tx
                            ter[idx] = cid
                            age[idx] = 0
        # Decay: unclaimed tiles age out and revert to neutral
        for i in range(MAP_W * MAP_H):
            if ter[i] != 0:
                a = age[i] + 1
                if a >= TERRITORY_DECAY:
                    ter[i] = 0
                    age[i] = 0
                else:
                    age[i] = a

    # ── Visual fog-of-war ──

    def _update_fog(self):
        """Recompute each colony's visible tile set, explored mask, and intel maps."""
        for c in self.colonies:
            vis = set()
            for ant in c.ants:
                if ant.type == A_SCOUT:
                    r = SCOUT_VISION_RADIUS[c.scout_tier]
                else:
                    r = VISION_RADIUS.get(ant.type, 4)
                ax, ay = ant.x, ant.y
                x0, x1 = max(0, ax - r), min(MAP_W - 1, ax + r)
                y0, y1 = max(0, ay - r), min(MAP_H - 1, ay + r)
                for ty in range(y0, y1 + 1):
                    base = ty * MAP_W
                    for tx in range(x0, x1 + 1):
                        vis.add(base + tx)
            # Watchtowers permanently reveal their area
            for st in self.structures:
                if st.get("type") == "watchtower" and st["colony"] == c.id:
                    r = WATCHTOWER_VISION
                    x0, x1 = max(0, st["x"] - r), min(MAP_W - 1, st["x"] + r)
                    y0, y1 = max(0, st["y"] - r), min(MAP_H - 1, st["y"] + r)
                    for ty in range(y0, y1 + 1):
                        base = ty * MAP_W
                        for tx in range(x0, x1 + 1):
                            vis.add(base + tx)
            c.fog_visible = vis
            exp = c.fog_explored
            for idx in vis:
                exp[idx] = 1

            # ── Populate intel maps from what's visible this tick ──

            # Food intel: any food node in vision gets logged with current amount
            for f in self.foods:
                idx = f["y"] * MAP_W + f["x"]
                if idx in vis:
                    key = (f["x"], f["y"])
                    c.food_intel[key] = {
                        "amt": f["amt"], "max": int(f.get("max", FOOD_MAX_APPROACH)),
                        "tier": f.get("tier", "home"), "last_seen": self.tick
                    }
                    if key not in c.known_food:
                        c.known_food.append(key)
                        if len(c.known_food) > 25: c.known_food.pop(0)

            # Enemy structure intel: any enemy structure in vision
            for st in self.structures:
                if st["colony"] == c.id: continue
                idx = st["y"] * MAP_W + st["x"]
                if idx in vis:
                    c.seen_structs[(st["x"], st["y"])] = {
                        "type": st.get("type", "guard_post"),
                        "hp_approx": st["hp"],
                        "last_seen": self.tick
                    }

            # Enemy unit sightings: bucket nearby visible enemy ants
            if c.enemy and (self.tick % 5 == 0):  # sample every 5 ticks for performance
                spotted = [(ea.x, ea.y, ea.type) for ea in c.enemy.ants
                           if ea.y * MAP_W + ea.x in vis]
                if spotted:
                    cx = sum(x for x, y, t in spotted) // len(spotted)
                    cy = sum(y for x, y, t in spotted) // len(spotted)
                    workers  = sum(1 for x, y, t in spotted if t == A_WORKER)
                    soldiers = sum(1 for x, y, t in spotted if t == A_SOLDIER)
                    scouts   = sum(1 for x, y, t in spotted if t == A_SCOUT)
                    c.enemy_sightings.append((cx, cy, soldiers, len(spotted), self.tick, workers, scouts))
                    if len(c.enemy_sightings) > 15:
                        c.enemy_sightings.pop(0)
                    if soldiers >= 3:
                        c.push_notification("enemy_contact", {"cx": cx, "cy": cy, "soldiers": soldiers, "total": len(spotted)}, tick=self.tick)

            # Track enemy queen HP from fog-of-war (updated whenever any unit sees the queen)
            if c.enemy:
                eq = next((a for a in c.enemy.ants if a.type == A_QUEEN), None)
                if eq and eq.y * MAP_W + eq.x in vis:
                    c.enemy_queen_hp_last_seen = int(eq.hp)

            # Remove food_intel entries for nodes that no longer exist
            dead_nodes = [k for k in c.food_intel if not any(
                f["x"] == k[0] and f["y"] == k[1] for f in self.foods)]
            for k in dead_nodes:
                c.food_intel[k] = {**c.food_intel[k], "amt": 0}  # mark depleted, keep history

    # ── Serialization ──

    def serialize_tick(self):
        ants = []
        for c in self.colonies:
            for a in c.ants:
                ants.append([a.id, a.x, a.y, a.prev_x, a.prev_y, a.colony,
                             a.type, a.state, int(a.carrying), a.hp, a.max_hp])
        # Index 4 = tier: "home" | "approach" | "frontline" (used by client for color)
        foods = [[f["x"], f["y"], int(f["amt"]), f["kind"], f.get("tier","home")]
                 for f in self.foods]
        corpses = [[int(c["x"]), int(c["y"]), int(c["amt"])] for c in self.corpses]
        cols = []
        for c in self.colonies:
            counts = [0, 0, 0, 0]
            for a in c.ants: counts[a.type] += 1
            # Spawn queue summary for UI [13]
            sq_summary = {"w": 0, "so": 0, "sc": 0, "reserved": 0, "next_t": None}
            for ant_type, ticks_rem, cost in c.spawn_queue:
                sq_summary[["w", "so", "sc"][ant_type]] += 1
                sq_summary["reserved"] += cost
            if c.spawn_queue:
                sq_summary["next_t"] = min(tr for _, tr, _ in c.spawn_queue)
            # Aging soon counts for UI [14] — ants in final 20% of lifespan
            aging_soon = [0, 0, 0]
            for a in c.ants:
                if a.type < 3 and a.lifespan and a.age >= int(a.lifespan * 0.80):
                    aging_soon[a.type] += 1
            # Upgrade ETA for UI [15] — seconds to next upgrade at current income
            _all_costs = {
                "worker": WORKER_UPGRADE_COSTS, "scout": SCOUT_UPGRADE_COSTS,
                "soldier": SOLDIER_UPGRADE_COSTS,
            }
            _tiers = {"worker": c.worker_tier, "scout": c.scout_tier, "soldier": c.soldier_tier}
            income = max(0.5, c.income_per_s)
            upg_eta = {}
            for uname, costs in _all_costs.items():
                tier = _tiers[uname]
                if tier < 3:
                    short = max(0, costs[tier] - int(c.food))
                    upg_eta[uname] = round(short / income, 1) if short > 0 else 0.0
                else:
                    upg_eta[uname] = None
            cols.append([c.id, c.nx, c.ny, int(c.food), counts,
                         c.directive, c.known_food[:10],
                         list(c.events), c.food_collected, c.ants_lost, int(c.alive),
                         [c.worker_tier, c.scout_tier, c.soldier_tier],
                         round(c.income_per_s, 1),
                         sq_summary,    # [13]
                         aging_soon,    # [14]
                         upg_eta,       # [15]
                         int(c.dirt)])  # [16] dirt stockpile
        # Territory flat list (replaces pheromone): 0=neutral,1=RED,2=BLUE
        territory = list(self.territory)
        # Dirt nodes: [x, y, amt, tier]
        dirt = [[dn["x"], dn["y"], int(dn["amt"]), dn["tier"]] for dn in self.dirt_nodes]
        structs = [[st["x"], st["y"], st["colony"], st["hp"], st["max_hp"], st.get("type","guard_post"),
                    1 if st.get("active", True) else 0,
                    st.get("build_progress", 0), st.get("build_required", 0)]
                   for st in self.structures]
        # Fog-of-war bitmasks for each colony: 0=unseen, 1=explored, 2=currently visible
        fog = []
        for c in self.colonies:
            arr = bytearray(c.fog_explored)   # 1 for explored
            for idx in c.fog_visible:
                arr[idx] = 2                  # 2 for currently visible (overwrites 1)
            fog.append(list(arr))
        return {
            "tick": self.tick,
            "phase": self.phase,
            "elapsed_s": round(time.time() - self.start_time, 1) if self.start_time else 0,
            "ants": ants, "food": foods,
            "corpses": corpses,
            "structures": structs,
            "colonies": cols, "territory": territory,
            "dirt": dirt,
            "fog": fog,
            "predators": [],
            "winner": self.winner,
        }

# ═══════════════════════════════════════════════════════════════════════════════
# Server
# ═══════════════════════════════════════════════════════════════════════════════

class Server:
    def __init__(self):
        self.world = World()
        self.clients = set()
        # Per-colony LLM state [0]=RED, [1]=BLUE — reset each game
        self.llm_memories      = [{}, {}]
        self.llm_strategy_logs = [[], []]
        self.llm_stats = [
            {"calls": 0, "prompt_tok": 0, "completion_tok": 0, "errors": 0},
            {"calls": 0, "prompt_tok": 0, "completion_tok": 0, "errors": 0},
        ]
        # Placement phase state (for late-joining clients)
        self._placement_food    = []
        self._placement_updates = []
        self._placement_start_t = None   # monotonic time when phase started
        # Sim decoupling: strategy queue + step-in-progress flag
        self._pending_strategies: deque = deque()   # (colony_id, strategy_dict)
        self._step_in_progress: bool = False
        self._sim_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sim"
        )

    def _make_init_msg(self):
        return json.dumps({
            "type": "init",
            "map": {"w": MAP_W, "h": MAP_H, "tile": TILE},
            "terrain": [self.world.terrain[y][x] for y in range(MAP_H) for x in range(MAP_W)],
            "seats": {str(k): v for k, v in self.world.mcp_seats.items()},
        })

    async def _broadcast(self, msg):
        dead = set()
        for ws in self.clients:
            try: await ws.send_str(msg)
            except: dead.add(ws)
        self.clients -= dead

    async def _reset(self):
        for cid in (0, 1):
            if self.llm_memories[cid] and self.world.logger:
                self.world.logger.log_memory_snapshot(self.llm_memories[cid])
        Ant._id = 0
        self.world = World()
        self.llm_memories      = [{}, {}]
        self.llm_strategy_logs = [[], []]
        self.llm_stats = [
            {"calls": 0, "prompt_tok": 0, "completion_tok": 0, "errors": 0},
            {"calls": 0, "prompt_tok": 0, "completion_tok": 0, "errors": 0},
        ]
        self._pending_strategies.clear()
        # Send new terrain and lobby state — game starts when client sends "start_game"
        await self._broadcast(self._make_init_msg())
        await self._broadcast(json.dumps({"type": "lobby", "seats": {"0": None, "1": None}}))
        print("🔄  game reset to lobby — click START to begin")

    async def _run_placement_phase(self):
        """Fixed spawn positions on The Crossing — no placement decision needed."""
        world = self.world
        red_pos, blue_pos = RED_SPAWN, BLUE_SPAWN

        print(f"\n{'═'*70}")
        print(f"🗺️  {MAP_NAME} — {MAP_W}×{MAP_H}  RED@{red_pos} vs BLUE@{blue_pos}")
        print(f"{'═'*70}")

        food_data = [[f["x"], f["y"], int(f["amt"]), f["kind"], f.get("tier","home")]
                     for f in world.foods]
        self._placement_food    = food_data
        self._placement_updates = []

        await self._broadcast(json.dumps({
            "type": "placement_phase",
            "food": food_data,
            "timeout": 2,
        }))
        await self._broadcast(json.dumps({"type": "placement_update", "colony": 0,
                                          "pos": list(red_pos),  "label": "RED",  "score": 0}))
        await self._broadcast(json.dumps({"type": "placement_update", "colony": 1,
                                          "pos": list(blue_pos), "label": "BLUE", "score": 0}))
        await self._broadcast(json.dumps({"type": "placement_ready"}))
        await asyncio.sleep(0.5)

        world.finalize_placement(red_pos, blue_pos)
        if world.logger:
            world.logger.log_placement(red_pos, "fixed spawn", blue_pos, "fixed spawn")
        self._placement_food    = []
        self._placement_updates = []

        await self._broadcast(self._make_init_msg())
        await self._broadcast(json.dumps({
            "type": "game_start",
            "red":  list(red_pos),
            "blue": list(blue_pos),
        }))
        print(f"🎮  GAME START — RED@{red_pos} vs BLUE@{blue_pos}")
        print(f"{'═'*70}\n")

    def _update_bot_strategy(self, world, colony_id):
        """Adaptive heuristic strategy for a bot colony."""
        c = world.colonies[colony_id]
        if not c.alive: return
        workers  = sum(1 for a in c.ants if a.type == A_WORKER)
        soldiers = sum(1 for a in c.ants if a.type == A_SOLDIER)
        food     = c.food
        income   = c.income_per_s

        if (food < 150 and workers < 20) or (income < 5 and food < 100):
            # Emergency: nearly broke and low workers — dump everything into workers
            roles, cap, defense = {"worker": 0.65, "scout": 0.15, "soldier": 0.20}, 60, "defensive"
        elif food > 1800 and workers >= 40:
            # Flush with cash and workers — mass army
            roles, cap, defense = {"worker": 0.25, "scout": 0.10, "soldier": 0.65}, 45, "aggressive"
        elif food > 1000 and workers >= 30:
            # Good position — ramp soldiers
            roles, cap, defense = {"worker": 0.35, "scout": 0.12, "soldier": 0.53}, 45, "aggressive"
        elif food > 500 and workers >= 20:
            # Building up
            roles, cap, defense = {"worker": 0.45, "scout": 0.15, "soldier": 0.40}, 50, "balanced"
        else:
            # Lean early — grow workers first
            roles, cap, defense = {"worker": 0.55, "scout": 0.20, "soldier": 0.25}, 55, "balanced"

        # Military: rally to mass, then release as a wave — avoids 1-by-1 trickle deaths
        rally_update = {}
        mil = c.directive["military"]
        if c.enemy and c.alive:
            # Rally point: 40% of the way toward enemy (safe staging area, inside own ridgeline)
            rx = int(c.nx + (c.enemy.nx - c.nx) * 0.40)
            ry = int(c.ny + (c.enemy.ny - c.ny) * 0.40)
            if soldiers < 3:
                # Army wiped — clear attack state and rebuild
                if mil.get("attack_target") or mil.get("rally_point"):
                    rally_update = {"rally_point": None, "rally_release_at": None,
                                    "attack_target": None, "siege_priority": None, "auto_attack": False}
            elif soldiers >= 3 and not mil.get("rally_point") and not mil.get("attack_target"):
                # Start massing — hold at rally until release threshold
                release_at = max(8, min(soldiers + 4, 18))
                rally_update = {"rally_point": [rx, ry], "rally_release_at": release_at,
                                "rally_mode": "normal", "siege_priority": "queen"}
            elif mil.get("rally_point"):
                # Check if enough staged to release
                _rp = mil["rally_point"]
                _rx, _ry = (int(_rp[0][0]), int(_rp[0][1])) if isinstance(_rp[0], (list, tuple)) else (int(_rp[0]), int(_rp[1]))
                staged = sum(1 for a in c.ants if a.type == A_SOLDIER
                             and abs(a.x - _rx) + abs(a.y - _ry) <= 6)
                release_at = mil.get("rally_release_at", 8)
                if staged >= release_at or (soldiers >= release_at and world.tick > 400):
                    # Wave ready — release and advance
                    rally_update = {"rally_point": None, "rally_release_at": None,
                                    "attack_target": [c.enemy.nx, c.enemy.ny],
                                    "siege_priority": "queen", "auto_attack": False}

        c.set_strategy({**{"roles": roles, "worker_cap": cap, "defense": defense}, **rally_update})

        # Build structures from dirt when stable
        dirt = c.dirt
        own_structs = [st for st in world.structures if st["colony"] == c.id]
        own_gp = sum(1 for st in own_structs if st.get("type") == "guard_post")
        own_wt = sum(1 for st in own_structs if st.get("type") == "watchtower")
        if c.enemy:
            def _bot_find_passable(bx, by, spread=6):
                for _ in range(10):
                    tx = max(2, min(MAP_W-2, bx + random.randint(-spread, spread)))
                    ty = max(2, min(MAP_H-2, by + random.randint(-spread, spread)))
                    if world._passable(tx, ty):
                        return tx, ty
                return None, None
            # Watchtower first (cheap, great intel value)
            if own_wt < WATCHTOWER_MAX and dirt >= WATCHTOWER_COST and workers >= 8:
                bx = int(c.nx + (c.enemy.nx - c.nx) * 0.25)
                by = int(c.ny + (c.enemy.ny - c.ny) * 0.25)
                wx, wy = _bot_find_passable(bx, by)
                if wx is not None:
                    entry = {"type": "watchtower", "x": wx, "y": wy}
                    if entry not in c.structure_queue:
                        c.structure_queue.append(entry)
            # Guard post when have enough dirt and workers
            elif own_gp < GUARD_POST_MAX and dirt >= GUARD_POST_COST and workers >= 15:
                bx = int(c.nx + (c.enemy.nx - c.nx) * 0.33)
                by = int(c.ny + (c.enemy.ny - c.ny) * 0.33)
                gx, gy = _bot_find_passable(bx, by, spread=4)
                if gx is not None and [gx, gy] not in c.build_queue:
                    c.build_queue.append([gx, gy])
            # Larder for late-game food sustain — build near nest when approach nodes depleting
            own_lr = sum(1 for st in self.world.structures if st["colony"] == c.id and st.get("type") == "larder")
            if own_lr < LARDER_MAX and dirt >= LARDER_COST and self.world.tick > 300:
                lx, ly = _bot_find_passable(c.nx + 5, c.ny, spread=4)
                if lx is not None:
                    entry = {"type": "larder", "x": lx, "y": ly}
                    if entry not in c.structure_queue:
                        c.structure_queue.append(entry)

        # Queue upgrades across all tiers — buy when comfortable buffer exists
        _BUFFERS = [1.5, 1.3, 1.2]
        for unit_type, tier_attr, costs in [
            ("scout",   c.scout_tier,   SCOUT_UPGRADE_COSTS),
            ("worker",  c.worker_tier,  WORKER_UPGRADE_COSTS),
            ("soldier", c.soldier_tier, SOLDIER_UPGRADE_COSTS),
        ]:
            if tier_attr >= 3: continue
            cost = costs[tier_attr]
            buf  = _BUFFERS[tier_attr]
            if food >= cost * buf:
                setattr(c, f"{unit_type}_upgrade_pending", True)

    async def tick_loop(self):
        bot_last_tick = 0
        loop = asyncio.get_event_loop()
        _last_idle_bc = 0.0
        while True:
            t0 = time.monotonic()
            phase = self.world.phase
            if phase in ("lobby", "paused"):
                # Broadcast idle state once per second so clients stay updated
                if t0 - _last_idle_bc >= 1.0:
                    seats = {str(k): v for k, v in self.world.mcp_seats.items()}
                    await self._broadcast(json.dumps({"type": phase, "seats": seats}))
                    _last_idle_bc = t0
                await asyncio.sleep(max(0, 1.0/TPS - (time.monotonic()-t0)))
                continue
            # Drain queued strategy updates before stepping (main-thread, safe)
            while self._pending_strategies:
                cid, strat = self._pending_strategies.popleft()
                if self.world.colonies[cid].alive:
                    self.world.colonies[cid].set_strategy(strat)
            # Bot decisions (tick-based, still safe here)
            if phase == "running" and self.world.winner is None:
                if self.world.tick - bot_last_tick >= LLM_INTERVAL:
                    bot_last_tick = self.world.tick
                    for cid in (0, 1):
                        btype = _brain_for(cid)["type"]
                        # Unclaimed MCP seats fall back to the bot brain so an absent
                        # agent doesn't leave the colony running on bare defaults;
                        # the bot steps aside the moment an agent joins the seat.
                        if btype == "bot" or (btype == "mcp"
                                              and self.world.mcp_seats.get(cid) is None):
                            self._update_bot_strategy(self.world, cid)
            # Run step() in a thread so the event loop stays responsive during heavy ticks
            self._step_in_progress = True
            await loop.run_in_executor(self._sim_executor, self.world.step)
            self._step_in_progress = False
            if self.world.phase == "running":
                state = self.world.serialize_tick()
                await self._broadcast(json.dumps(state))
            await asyncio.sleep(max(0, 1.0/TPS - (time.monotonic()-t0)))

    async def llm_loop_for(self, colony_id: int):
        """LLM decision loop for one colony. Reads brain config live; skips if type=='bot'."""
        try:
            import openai as _openai
        except ImportError:
            print("⚠️  openai package not installed — run: pip install openai")
            return

        client          = None
        last_key        = None
        last_url        = None
        last_world      = None
        # Wall-clock time of last call — interval is enforced in real seconds, not ticks.
        # This ensures both colonies get equal call frequency regardless of sim TPS.
        last_call_time  = time.monotonic()  # start: wait one full interval before first call
        debriefed       = set()
        name            = "RED"  if colony_id == 0 else "BLUE"
        enemy_color     = "BLUE" if colony_id == 0 else "RED"
        # Stagger: BLUE fires half an interval later so both don't call simultaneously
        if colony_id == 1:
            last_call_time -= (LLM_INTERVAL / TPS) / 2

        while True:
            await asyncio.sleep(0.1)

            brain = _brain_for(colony_id)
            if brain["type"] != "llm" or not brain.get("api_key"):
                await asyncio.sleep(1)
                continue

            api_key  = brain["api_key"]
            base_url = brain["base_url"]
            model    = brain["model"]

            if api_key != last_key or base_url != last_url:
                client   = _openai.AsyncOpenAI(base_url=base_url, api_key=api_key)
                last_key = api_key
                last_url = base_url
                print(f"🤖  LLM → {model} @ {base_url} (colony {name})")

            world = self.world
            if world is not last_world:
                last_world     = world
                last_call_time = time.monotonic()  # reset on new game

            if world.winner is not None:
                wid = id(world) * 10 + colony_id
                if wid not in debriefed:
                    debriefed.add(wid)
                    await self._llm_debrief(world, client, colony_id)
                continue

            # Colonies don't exist during placement phase — wait for game to start
            if world.phase != "running":
                continue

            # Wall-clock interval: LLM_INTERVAL ticks converted to seconds at current TPS
            llm_interval_secs = LLM_INTERVAL / TPS
            if time.monotonic() - last_call_time < llm_interval_secs:
                continue

            # Wait for the sim thread to finish its current step before reading world state.
            # This prevents data races when build_llm_prompt iterates colony/food lists.
            while self._step_in_progress:
                await asyncio.sleep(0)

            colony = world.colonies[colony_id]
            if not colony.alive:
                last_call_time = time.monotonic()
                continue

            # Snapshot world state synchronously (no await → no step() can start mid-read)
            stats  = self.llm_stats[colony_id]
            prompt = build_llm_prompt(colony, world.tick, world=world,
                                      memory=self.llm_memories[colony_id],
                                      my_color=name, enemy_color=enemy_color)
            system_prompt = _make_llm_system_prompt(name, enemy_color)
            print(f"\n{'─'*70}")
            print(f"🤖  [{name}  tick={world.tick}]  {model}  ({len(prompt)} chars)")

            last_call_time = time.monotonic()
            reasoning, strategy, feedback, memory_update = "", {}, "", None
            t_call = time.monotonic()

            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": prompt},
                    ],
                    temperature=0.6,
                )
                # Yield immediately so tick_loop can process ticks before we do heavy work
                await asyncio.sleep(0)

                elapsed = time.monotonic() - t_call
                raw_text = resp.choices[0].message.content or ""
                if hasattr(resp.choices[0].message, "reasoning_content"):
                    rc = resp.choices[0].message.reasoning_content
                    if rc and "<think>" not in raw_text:
                        raw_text = f"<think>{rc}</think>" + raw_text

                usage = resp.usage
                ptok = usage.prompt_tokens     if usage else 0
                ctok = usage.completion_tokens if usage else 0
                ttok = usage.total_tokens      if usage else 0
                stats["calls"]       += 1
                stats["prompt_tok"]  += ptok
                stats["completion_tok"] += ctok
                print(f"    ⏱  {elapsed:.2f}s  |  {ptok}p + {ctok}c = {ttok} tok  "
                      f"(total: {stats['prompt_tok']}p + {stats['completion_tok']}c, "
                      f"{stats['calls']} calls)")

                reasoning, strategy, feedback, memory_update = parse_llm_response(raw_text)

                if reasoning:
                    print(f"\n[REASONING]")
                    for line in reasoning.splitlines():
                        print(f"  {line}")
                print(f"\n[DECISION]  {json.dumps(strategy) if strategy else '(no change)'}")
                if feedback:
                    print(f"[FEEDBACK]  {feedback}")
                if memory_update:
                    print(f"[MEMORY ↑]  {json.dumps(memory_update)}")
                print(f"{'─'*70}")

            except Exception as e:
                elapsed = time.monotonic() - t_call
                reasoning = f"[API error: {e}]"
                stats["errors"] += 1
                print(f"    ❌ error after {elapsed:.2f}s: {e}  (errors: {stats['errors']})")
                print(f"{'─'*70}")

            if world.winner is not None or world is not self.world:
                continue

            if strategy:
                # Queue strategy for the tick loop to apply atomically before next step()
                self._pending_strategies.append((colony_id, strategy))
                colony.push_event(f"[LLM] strategy → {json.dumps(strategy)}")

            if memory_update:
                self.llm_memories[colony_id] = _trim_memory(
                    apply_memory_update(self.llm_memories[colony_id], memory_update)
                )

            self.llm_strategy_logs[colony_id].append((world.tick, strategy))
            world.logger.log_llm(colony_id, reasoning, strategy, prompt=prompt, feedback=feedback)
            world._llm_stats_list[colony_id] = {
                "model":   model, "colony": name,
                "calls":   stats["calls"], "errors": stats["errors"],
                "prompt_tok": stats["prompt_tok"], "completion_tok": stats["completion_tok"],
            }


    async def _llm_debrief(self, world, client, colony_id: int):
        """Post-game reflection call — fires once per LLM colony when game ends."""
        if client is None: return
        name  = "RED" if colony_id == 0 else "BLUE"
        brain = _brain_for(colony_id)
        model = brain.get("model", "?")
        print(f"\n{'═'*70}")
        print(f"🎓  [{name}] post-game debrief → {model}")
        print(f"{'═'*70}")
        prompt = build_debrief_prompt(world, colony_id,
                                      self.llm_strategy_logs[colony_id],
                                      memory=self.llm_memories[colony_id])
        t0 = time.monotonic()
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": LLM_DEBRIEF_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.7,
            )
            elapsed = time.monotonic() - t0
            raw = resp.choices[0].message.content or ""

            thinking = ""
            m = re.search(r"<think>(.*?)</think>", raw, re.DOTALL)
            if m:
                thinking = m.group(1).strip()
                raw = raw[m.end():].strip()

            usage = resp.usage
            ptok = usage.prompt_tokens if usage else 0
            ctok = usage.completion_tokens if usage else 0
            print(f"    ⏱  {elapsed:.2f}s  |  {ptok}p + {ctok}c tokens\n")

            if thinking:
                print(f"[THINKING]")
                for line in thinking.splitlines(): print(f"  {line}")
                print()
            print(f"[DEBRIEF]")
            for line in raw.splitlines(): print(f"  {line}")
            print(f"\n{'═'*70}\n")

            world.logger.log_debrief(colony_id, thinking, raw)

            debrief_mem = {}
            if "MEMORY UPDATE:" in raw:
                for line in raw.split("MEMORY UPDATE:", 1)[1].splitlines():
                    line = line.strip()
                    if line and ":" in line:
                        k, _, v = line.partition(":")
                        debrief_mem[k.strip()] = v.strip()
            if debrief_mem:
                self.llm_memories[colony_id] = _trim_memory(
                    apply_memory_update(self.llm_memories[colony_id], debrief_mem)
                )
                print(f"[MEMORY ↑ debrief]  {json.dumps(debrief_mem)}")
            if world.logger:
                world.logger.log_memory_snapshot(self.llm_memories[colony_id])

        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"    ❌ debrief error after {elapsed:.2f}s: {e}\n")

    async def on_index(self, req):
        return web.FileResponse("./index.html")

    async def on_ws(self, req):
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        self.clients.add(ws)
        try:
            await ws.send_str(self._make_init_msg())
            # Send current phase state to newly connected client
            seats_now = {str(k): v for k, v in self.world.mcp_seats.items()}
            if self.world.phase == "lobby":
                await ws.send_str(json.dumps({"type": "lobby", "seats": seats_now}))
            elif self.world.phase == "paused":
                await ws.send_str(json.dumps({"type": "paused", "seats": seats_now}))
            elif self.world.phase == "running":
                await ws.send_str(json.dumps({"type": "seats_update", "seats": seats_now}))
            # Replay placement state for clients that connect mid-phase
            if self.world.phase == "placement" and self._placement_food:
                remaining = 2
                await ws.send_str(json.dumps({
                    "type": "placement_phase",
                    "food": self._placement_food,
                    "timeout": remaining,
                }))
                for upd in self._placement_updates:
                    await ws.send_str(json.dumps(upd))
                # If all placements already done, tell the rejoining client immediately
                if len(self._placement_updates) >= 2:
                    await ws.send_str(json.dumps({"type": "placement_ready"}))
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "reset":
                            await self._reset()
                        elif data.get("type") == "get_config":
                            await ws.send_str(json.dumps({
                                "type": "config", "data": _current_config()
                            }))
                        elif data.get("type") == "set_config":
                            _save_config(data.get("data", {}))
                            await ws.send_str(json.dumps({
                                "type": "config_saved", "data": _current_config()
                            }))
                        elif data.get("type") == "get_providers":
                            await ws.send_str(json.dumps({
                                "type": "providers", "data": PROVIDERS
                            }))
                        elif data.get("type") == "save_provider":
                            p = data.get("data", {})
                            pid = p.get("id") or p.get("base_url", "")
                            existing = next((x for x in PROVIDERS if x.get("id") == pid), None)
                            if existing:
                                existing.update(p)
                            else:
                                p.setdefault("id", pid)
                                PROVIDERS.append(p)
                            _save_providers(PROVIDERS)
                            await ws.send_str(json.dumps({
                                "type": "providers", "data": PROVIDERS
                            }))
                        elif data.get("type") == "delete_provider":
                            pid = data.get("id")
                            PROVIDERS[:] = [x for x in PROVIDERS if x.get("id") != pid]
                            _save_providers(PROVIDERS)
                            await ws.send_str(json.dumps({
                                "type": "providers", "data": PROVIDERS
                            }))
                        elif data.get("type") == "get_models":
                            models = await _fetch_models(
                                data.get("base_url", ""),
                                data.get("api_key", ""),
                            )
                            await ws.send_str(json.dumps({
                                "type": "models", "data": models,
                                "request_id": data.get("request_id"),
                            }))
                        elif data.get("type") == "set_brains":
                            d = data.get("data", {})
                            if "red_brain" in d:
                                _save_config({"red_brain": d["red_brain"]})
                            if "blue_brain" in d:
                                _save_config({"blue_brain": d["blue_brain"]})
                            await ws.send_str(json.dumps({
                                "type": "config_saved", "data": _current_config()
                            }))
                        elif data.get("type") == "start_game":
                            if self.world.phase in ("lobby", "placement"):
                                asyncio.create_task(self._run_placement_phase())
                        elif data.get("type") == "pause_game":
                            if self.world.phase == "running":
                                self.world.phase = "paused"
                                await self._broadcast(json.dumps({"type": "paused", "seats": {str(k): v for k, v in self.world.mcp_seats.items()}}))
                        elif data.get("type") == "resume_game":
                            if self.world.phase == "paused":
                                self.world.phase = "running"
                                await self._broadcast(json.dumps({"type": "resumed"}))
                        elif data.get("type") == "end_game":
                            if self.world.phase in ("running", "paused"):
                                self.world.winner = "draw"
                                self.world.phase = "running"  # let tick_loop handle win broadcast
                        elif data.get("type") == "join_seat":
                            cid = data.get("colony_id")
                            agent = data.get("agent_name", "MCP Agent")
                            if cid in (0, 1) and self.world.mcp_seats.get(cid) is None:
                                self.world.mcp_seats[cid] = agent
                                key = "red_brain" if cid == 0 else "blue_brain"
                                _save_config({key: {"type": "mcp", "agent": agent}})
                                await self._broadcast(json.dumps({"type": "seat_joined", "colony_id": cid, "agent": agent}))
                        elif data.get("type") == "release_seat":
                            cid = data.get("colony_id")
                            if cid in (0, 1):
                                self.world.mcp_seats[cid] = None
                                key = "red_brain" if cid == 0 else "blue_brain"
                                _save_config({key: {"type": "bot"}})
                                await self._broadcast(json.dumps({"type": "seat_released", "colony_id": cid}))
                    except Exception:
                        pass
        finally:
            self.clients.discard(ws)
        return ws

    # ─── REST API handlers ────────────────────────────────────────────────────

    async def _api_cors(self, resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    async def api_options(self, req):
        return await self._api_cors(web.Response(status=204))

    async def api_tick(self, req):
        w = self.world
        elapsed = round(time.time() - w.start_time, 1) if w.start_time else 0
        return await self._api_cors(web.json_response({
            "tick": w.tick, "phase": w.phase, "winner": w.winner,
            "elapsed_s": elapsed,
            "seats": {str(k): v for k, v in w.mcp_seats.items()},
        }))

    async def api_seats(self, req):
        seats = {}
        for k, agent in self.world.mcp_seats.items():
            brain = RED_BRAIN if k == 0 else BLUE_BRAIN
            seats[str(k)] = {"agent": agent, "brain_type": brain.get("type", "bot")}
        return await self._api_cors(web.json_response({"seats": seats}))

    async def api_matches(self, req):
        """Match discovery endpoint — lists open games for agents to find seats."""
        seats = {}
        for k, agent in self.world.mcp_seats.items():
            brain = RED_BRAIN if k == 0 else BLUE_BRAIN
            seats[str(k)] = {"agent": agent, "brain_type": brain.get("type", "bot")}
        return await self._api_cors(web.json_response({
            "matches": [{
                "game_url": "http://localhost:8083",
                "phase": self.world.phase,
                "tick": self.world.tick,
                "map": "The Crossing (150×100)",
                "seats": seats,
            }]
        }))

    async def api_join_seat(self, req):
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        try:
            body = await req.json()
        except Exception:
            body = {}
        agent = body.get("agent_name", f"MCP-{cid}")
        if self.world.mcp_seats.get(cid) is not None:
            return await self._api_cors(web.json_response({"error": "seat occupied", "agent": self.world.mcp_seats[cid]}, status=409))
        self.world.mcp_seats[cid] = agent
        key = "red_brain" if cid == 0 else "blue_brain"
        _save_config({key: {"type": "mcp", "agent": agent}})
        await self._broadcast(json.dumps({"type": "seat_joined", "colony_id": cid, "agent": agent}))
        return await self._api_cors(web.json_response({"ok": True, "colony_id": cid, "agent": agent}))

    async def api_release_seat(self, req):
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        self.world.mcp_seats[cid] = None
        key = "red_brain" if cid == 0 else "blue_brain"
        _save_config({key: {"type": "bot"}})
        await self._broadcast(json.dumps({"type": "seat_released", "colony_id": cid}))
        return await self._api_cors(web.json_response({"ok": True, "colony_id": cid}))

    async def api_control(self, req):
        try:
            body = await req.json()
        except Exception:
            return await self._api_cors(web.json_response({"error": "bad json"}, status=400))
        action = body.get("action", "")
        if action == "start":
            if self.world.phase in ("lobby", "placement"):
                asyncio.create_task(self._run_placement_phase())
                return await self._api_cors(web.json_response({"ok": True, "phase": "starting"}))
        elif action == "pause":
            if self.world.phase == "running":
                self.world.phase = "paused"
                await self._broadcast(json.dumps({"type": "paused", "seats": {str(k): v for k, v in self.world.mcp_seats.items()}}))
                return await self._api_cors(web.json_response({"ok": True, "phase": "paused"}))
        elif action == "resume":
            if self.world.phase == "paused":
                self.world.phase = "running"
                await self._broadcast(json.dumps({"type": "resumed"}))
                return await self._api_cors(web.json_response({"ok": True, "phase": "running"}))
        elif action == "end":
            if self.world.phase in ("running", "paused"):
                w = self.world
                scores = None
                if w.winner is None:
                    # Adjudicate by score (same formula as stalemate resolution)
                    _val = {A_WORKER: 5, A_SOLDIER: 20, A_SCOUT: 8}
                    scores = []
                    for c in w.colonies:
                        army_val = sum(_val.get(a.type, 0) for a in c.ants if a.type != A_QUEEN)
                        scores.append(c.food_collected + army_val + max(0, int(c.food)))
                    w.winner = (0 if scores[0] > scores[1] else
                                1 if scores[1] > scores[0] else "draw")
                    if w.logger: w.logger.finish(w.winner)
                w.phase = "running"
                return await self._api_cors(web.json_response({
                    "ok": True, "phase": "ending", "winner": w.winner,
                    **({"scores": {"red": scores[0], "blue": scores[1]}} if scores else {}),
                }))
        elif action == "reset":
            asyncio.create_task(self._reset())
            return await self._api_cors(web.json_response({"ok": True, "phase": "lobby"}))
        return await self._api_cors(web.json_response({"error": f"invalid action '{action}' for phase '{self.world.phase}'"}, status=400))

    def _build_colony_state(self, cid: int) -> dict:
        w = self.world
        if not w.colonies or cid >= len(w.colonies):
            return {"error": "game not started"}
        c = w.colonies[cid]
        enemy = c.enemy
        counts = [0, 0, 0, 0]
        for a in c.ants:
            counts[a.type] += 1
        queen = next((a for a in c.ants if a.type == A_QUEEN), None)
        queen_hp = queen.hp if queen else 0
        soldiers_in_siege = 0
        soldiers_near_enemy_nest = 0
        enemy_soldiers_near_nest = 0
        enemy_queen_hp = None
        if enemy:
            for a in c.ants:
                if a.type == A_SOLDIER:
                    d = abs(a.x - enemy.nx) + abs(a.y - enemy.ny)
                    if d <= 12: soldiers_in_siege += 1
                    if d <= 20: soldiers_near_enemy_nest += 1
            for a in enemy.ants:
                if a.type == A_SOLDIER and abs(a.x - c.nx) + abs(a.y - c.ny) <= 15:
                    enemy_soldiers_near_nest += 1
            if soldiers_in_siege > 0:
                eq = next((a for a in enemy.ants if a.type == A_QUEEN), None)
                enemy_queen_hp = eq.hp if eq else None
        soldiers_adjacent_queen = 0
        if enemy and soldiers_in_siege > 0:
            eq = next((a for a in enemy.ants if a.type == A_QUEEN), None)
            if eq:
                soldiers_adjacent_queen = sum(
                    1 for a in c.ants if a.type == A_SOLDIER
                    and abs(a.x - eq.x) <= 1 and abs(a.y - eq.y) <= 1)
        sq = c.spawn_queue
        sq_summary = {
            "w": sum(1 for e in sq if e[0] == A_WORKER),
            "so": sum(1 for e in sq if e[0] == A_SOLDIER),
            "sc": sum(1 for e in sq if e[0] == A_SCOUT),
            "reserved": sum(e[2] for e in sq),
            "next_t": min(tr for _, tr, _ in sq) if sq else None,
            "build_times": {"worker": c.SPAWN_TIME[A_WORKER], "soldier": c.SPAWN_TIME[A_SOLDIER],
                            "scout": c.SPAWN_TIME[A_SCOUT]},
        }
        own_structs = [{"type": st.get("type","guard_post"), "x": st["x"], "y": st["y"],
                        "hp": st["hp"], "max_hp": st["max_hp"]} for st in w.structures if st["colony"] == cid]
        aging_soon = [
            sum(1 for a in c.ants if a.type == A_WORKER  and a.lifespan and a.age >= int(a.lifespan * 0.80)),
            sum(1 for a in c.ants if a.type == A_SOLDIER and a.lifespan and a.age >= int(a.lifespan * 0.80)),
            sum(1 for a in c.ants if a.type == A_SCOUT   and a.lifespan and a.age >= int(a.lifespan * 0.80)),
        ]
        # Advisor: contextual nudges toward neglected game levers. Agents reliably act
        # on hints that live in state; they rarely re-read tool docs mid-game.
        advisor = []
        _struct_counts = {}
        for st in w.structures:
            if st["colony"] == cid:
                _struct_counts[st.get("type", "guard_post")] = _struct_counts.get(st.get("type", "guard_post"), 0) + 1
        if c.dirt >= LARDER_COST:
            affordable = [f"{s} ({co}◆)" for s, co, mx in
                          [("larder", LARDER_COST, LARDER_MAX), ("guard_post", GUARD_POST_COST, GUARD_POST_MAX),
                           ("watchtower", WATCHTOWER_COST, WATCHTOWER_MAX), ("barracks", BARRACKS_COST, BARRACKS_MAX)]
                          if c.dirt >= co and _struct_counts.get(s, 0) < mx]
            if affordable:
                advisor.append(f"{int(c.dirt)}◆ dirt unspent — build_structure available: {', '.join(affordable)}")
        idle_workers = sum(1 for a in c.ants if a.type == A_WORKER and a.state == S_IDLE)
        if counts[0] >= 8 and idle_workers > counts[0] * 0.3:
            advisor.append(f"{idle_workers}/{counts[0]} workers idle — check viable_food_nodes; "
                           f"clear economy.priority_food if set to a depleted node")
        _next_costs = [(u, costs[t]) for u, t, costs in
                       [("worker", c.worker_tier, WORKER_UPGRADE_COSTS),
                        ("scout", c.scout_tier, SCOUT_UPGRADE_COSTS),
                        ("soldier", c.soldier_tier, SOLDIER_UPGRADE_COSTS)] if t < 3]
        affordable_upg = [(u, co) for u, co in _next_costs if c.food >= co * 1.1]
        if affordable_upg:
            u, co = min(affordable_upg, key=lambda x: x[1])
            advisor.append(f"food {int(c.food)} covers {u} upgrade ({co}♦) — buy_upgrade('{u}') "
                           f"(permanent, usually beats more units)")
        if (w.tick > 250 and _struct_counts.get("larder", 0) < LARDER_MAX
                and c.dirt >= LARDER_COST
                and not any(v["amt"] > 100 and v["tier"] in ("home", "approach")
                            for v in c.food_intel.values())):
            advisor.append("home/approach food nearly gone — larders (6♦/t passive) sustain the late game")
        if (counts[1] >= 10 and not c.directive["military"].get("rally_point")
                and not c.directive["military"].get("auto_attack")):
            advisor.append(f"{counts[1]} soldiers without rally or auto_attack — they trickle in 1-by-1 and die; "
                           f"set military.rally_point + rally_release_at to attack as a mass")
        _rally = c.directive["military"].get("rally_point")
        _release_at = c.directive["military"].get("rally_release_at")
        if _rally and _release_at:
            _rp = _rally[0] if isinstance(_rally[0], (list, tuple)) else _rally
            _rx, _ry = int(_rp[0]), int(_rp[1])
            _staged = sum(1 for a in c.ants if a.type == A_SOLDIER
                          and abs(a.x - _rx) + abs(a.y - _ry) <= 4)
            if _staged < _release_at:
                advisor.append(f"RALLY: {_staged}/{_release_at} soldiers at ({_rx},{_ry}) — "
                               f"release triggers at {_release_at}; {_release_at - _staged} more needed")
        # Warn about incomplete structures with workers assigned but far from nest
        for st in w.structures:
            if st["colony"] != cid or st.get("active", True): continue
            dist = abs(st["x"] - c.nx) + abs(st["y"] - c.ny)
            assigned = sum(1 for a in c.ants if a.type == A_WORKER and a.unit_override
                           and a.unit_override.get("cmd") == "build"
                           and a.unit_override.get("x") == st["x"]
                           and a.unit_override.get("y") == st["y"])
            pct = round(st.get("build_progress", 0) / max(st.get("build_required", 1), 1) * 100)
            if dist > 40 and assigned > 0:
                advisor.append(f"{st['type']} at ({st['x']},{st['y']}) is {dist} tiles from nest — "
                               f"workers may be killed en route ({pct}% built, {assigned} assigned); "
                               f"consider placing structures within 35 tiles of nest")
            elif assigned == 0 and pct < 100:
                advisor.append(f"{st['type']} at ({st['x']},{st['y']}) stalled at {pct}% — "
                               f"no workers assigned; use command_type('{cid}','worker','build',x={st['x']},y={st['y']})")
        return {
            "tick": w.tick, "phase": w.phase, "colony_id": cid,
            "nest": [c.nx, c.ny],
            "food": int(c.food), "dirt": int(c.dirt),
            "food_in_transit": sum(FOOD_DELIVER + c.carry_bonus for a in c.ants
                                   if a.type == A_WORKER and a.carrying
                                   and getattr(a, "carrying_type", "food") == "food"),
            "income_per_s": round(c.income_per_s, 2),
            "larder_income": LARDER_INCOME * sum(1 for st in w.structures if st["colony"] == cid and st.get("type") == "larder"),
            "dirt_per_s": round(c.dirt_per_s, 2),
            "counts": {"workers": counts[0], "soldiers": counts[1], "scouts": counts[2], "queen": counts[3]},
            "tiers": {"worker": c.worker_tier, "scout": c.scout_tier, "soldier": c.soldier_tier},
            "spawn_queue": sq_summary,
            "aging_soon": {"workers": aging_soon[0], "soldiers": aging_soon[1], "scouts": aging_soon[2]},
            "directive": c.directive,
            "trigger_log": list(c.trigger_log)[-10:],
            "events": [list(c.events)[i] for i in range(min(20, len(c.events)))],
            "food_intel": {f"{k[0]},{k[1]}": v for k, v in c.food_intel.items()},
            "enemy_sightings": list(c.enemy_sightings),
            "seen_structs": {f"{k[0]},{k[1]}": v for k, v in c.seen_structs.items()},
            "own_structures": own_structs,
            "combat": {
                "soldiers_in_siege": soldiers_in_siege,
                "soldiers_adjacent_queen": soldiers_adjacent_queen,
                "soldiers_near_enemy_nest": soldiers_near_enemy_nest,
                "enemy_soldiers_near_nest": enemy_soldiers_near_nest,
                "enemy_queen_hp": enemy_queen_hp,
                "enemy_queen_hp_observed": c.enemy_queen_hp_last_seen,
                "queen_dps_actual": round(c.queen_dps_actual, 1),
                # Theoretical max if every siege soldier were adjacent to the queen and hitting her.
                # Compare with queen_dps_actual — a large gap means soldiers are fighting defenders.
                "siege_dps_potential": round(soldiers_in_siege * (SOLDIER_DMG + c.dmg_bonus) / c.soldier_fast_cd * TPS, 1) if soldiers_in_siege > 0 else 0,
                "ttk_s": round(enemy_queen_hp / (soldiers_in_siege * (SOLDIER_DMG + c.dmg_bonus) / c.soldier_fast_cd * TPS), 1) if (soldiers_in_siege > 0 and enemy_queen_hp) else None,
                **({"siege_hint": "soldiers are engaging defenders, not the queen — "
                                  "set military.siege_priority='queen' to focus her"}
                   if (soldiers_in_siege >= 3 and c.queen_dps_actual == 0
                       and c.directive["military"].get("siege_priority") != "queen") else {}),
            },
            "advisor": advisor,
            "queen_hp": queen_hp,
            "queen_alive": c.alive,
            "food_collected": c.food_collected,
            "ants_lost": c.ants_lost,
            "peak_pop": c.peak_pop,
            "units_summary": {
                "total": len(c.ants),
                "workers": counts[0], "soldiers": counts[1], "scouts": counts[2],
                "with_override": sum(1 for a in c.ants if a.unit_override),
                "idle": sum(1 for a in c.ants if a.state == S_IDLE),
            },
            "military_summary": {
                "total_soldiers": counts[1],
                "fighting": sum(1 for a in c.ants if a.type == A_SOLDIER and a.state == S_FIGHTING),
                "patrolling": sum(1 for a in c.ants if a.type == A_SOLDIER and a.state in (S_PATROLLING, S_FORAGING)),
                "idle": sum(1 for a in c.ants if a.type == A_SOLDIER and a.state == S_IDLE),
                "healthy": sum(1 for a in c.ants if a.type == A_SOLDIER and a.hp >= a.max_hp * 0.75),
                "wounded": sum(1 for a in c.ants if a.type == A_SOLDIER and a.hp < a.max_hp * 0.5),
                "avg_hp_pct": round(sum(a.hp / a.max_hp for a in c.ants if a.type == A_SOLDIER) / counts[1] * 100) if counts[1] else 0,
                "building": sum(1 for a in c.ants if a.unit_override and a.unit_override.get("cmd") == "build"),
            },
            "viable_food_nodes": sorted(
                [{"pos": list(k), "amt": v["amt"], "max": v["max"],
                  "pct": round(v["amt"] / max(v["max"], 1) * 100),
                  "tier": v["tier"], "last_seen": v["last_seen"],
                  "dist": abs(k[0] - c.nx) + abs(k[1] - c.ny),
                  "workers_here": sum(1 for a in c.ants if a.type == A_WORKER
                                      and (a.recruit_target == k
                                           or abs(a.x - k[0]) + abs(a.y - k[1]) <= 5)),
                  "cap": FOOD_NODE_WORKER_CAP.get(v["tier"], 6)}
                 for k, v in c.food_intel.items() if v["amt"] > 0],
                key=lambda n: n["dist"]
            )[:10],
            "units": [
                {"id": a.id, "type": ["worker","soldier","scout","queen"][a.type],
                 "x": a.x, "y": a.y, "hp": int(a.hp), "max_hp": a.max_hp,
                 "age": a.age, "lifespan": a.lifespan, "carrying": a.carrying,
                 "state": ["idle","foraging","returning","exploring","fighting","patrolling","recruited","building"][a.state] if a.state < 8 else "idle",
                 "override": a.unit_override,
                 **({"recruit_target": list(a.recruit_target)} if a.type == A_WORKER and a.recruit_target else {})}
                for a in c.ants
            ],
        }

    async def api_state(self, req):
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        return await self._api_cors(web.json_response(self._build_colony_state(cid)))

    async def api_notifications(self, req):
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        if not self.world.colonies:
            return await self._api_cors(web.json_response({"notifications": []}))
        notifs = self.world.colonies[cid].pop_notifications()
        return await self._api_cors(web.json_response({"notifications": notifs, "count": len(notifs)}))

    async def api_intel_map(self, req):
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        if not self.world.colonies:
            return await self._api_cors(web.json_response({"error": "game not started"}))
        c = self.world.colonies[cid]
        w = self.world
        COLS, ROWS = 30, 20
        cell_w = MAP_W / COLS
        cell_h = MAP_H / ROWS
        grid = [['·'] * COLS for _ in range(ROWS)]
        # Rock overlay
        for col in range(COLS):
            for row in range(ROWS):
                tx = int(col * cell_w + cell_w / 2)
                ty = int(row * cell_h + cell_h / 2)
                if tx < MAP_W and ty < MAP_H and not w._passable(tx, ty):
                    grid[row][col] = '#'
        # Food intel
        for (fx, fy), info in c.food_intel.items():
            col = min(COLS - 1, int(fx / cell_w))
            row = min(ROWS - 1, int(fy / cell_h))
            if grid[row][col] in ('·', '#'):
                tier = info.get("tier", "home")
                ch = 'F' if tier == "frontline" else ('a' if tier == "approach" else 'h')
                if info.get("amt", 1) <= 0: ch = ch.lower() + '!' if tier == "frontline" else 'x'
                grid[row][col] = ch if grid[row][col] == '·' else grid[row][col]
        # Enemy sightings
        for sighting in c.enemy_sightings:
            cx_s, cy_s, soldiers, total, tick = sighting[0], sighting[1], sighting[2], sighting[3], sighting[4]
            col = min(COLS - 1, int(cx_s / cell_w))
            row = min(ROWS - 1, int(cy_s / cell_h))
            ch = '!' if soldiers >= 3 else '?'
            if grid[row][col] not in ('R', 'B', 'W', 'G', 'K', '#'):
                grid[row][col] = ch
        # Own structures
        for st in w.structures:
            if st["colony"] == cid:
                col = min(COLS - 1, int(st["x"] / cell_w))
                row = min(ROWS - 1, int(st["y"] / cell_h))
                ch = {'watchtower': 'W', 'guard_post': 'G', 'barracks': 'K', 'wall': '|'}.get(st.get("type",""), 'S')
                grid[row][col] = ch
        # Enemy structures seen
        for (sx, sy), info in c.seen_structs.items():
            col = min(COLS - 1, int(sx / cell_w))
            row = min(ROWS - 1, int(sy / cell_h))
            ch = {'watchtower': 'w', 'guard_post': 'g', 'barracks': 'k', 'wall': ':'}.get(info.get("type",""), 's')
            if grid[row][col] not in ('R', 'B', 'W', 'G', 'K', '#'):
                grid[row][col] = ch
        # Nest positions (always known)
        for colony in w.colonies:
            col = min(COLS - 1, int(colony.nx / cell_w))
            row = min(ROWS - 1, int(colony.ny / cell_h))
            grid[row][col] = 'R' if colony.id == 0 else 'B'
        map_rows = [''.join(row) for row in grid]
        # Annotate with column coords
        header = ''.join(str(i % 10) for i in range(COLS))
        annotated = [f"{i:02d}|{row}|" for i, row in enumerate(map_rows)]
        return await self._api_cors(web.json_response({
            "map": map_rows,
            "annotated_map": [f"   {header}"] + annotated,
            "food_intel": {f"{k[0]},{k[1]}": v for k, v in c.food_intel.items()},
            "enemy_sightings": list(c.enemy_sightings),
            "seen_structs": {f"{k[0]},{k[1]}": v for k, v in c.seen_structs.items()},
            "legend": "R/B=nests  h=home food  a=approach  F=frontline  x=depleted  #=rock  W/G/K=own structures  w/g/k=enemy spotted  !=enemy soldiers>=3  ?=enemy contact",
            "cell_size_tiles": f"{cell_w:.1f}x{cell_h:.1f}",
        }))

    async def api_directive(self, req):
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        if not self.world.colonies:
            return await self._api_cors(web.json_response({"error": "game not started"}))
        return await self._api_cors(web.json_response({"directive": self.world.colonies[cid].directive}))

    async def api_patch_directive(self, req):
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        if not self.world.colonies:
            return await self._api_cors(web.json_response({"error": "game not started"}))
        try:
            body = await req.json()
        except Exception:
            return await self._api_cors(web.json_response({"error": "bad json"}, status=400))
        c = self.world.colonies[cid]
        if "directive" in body:
            self._pending_strategies.append((cid, {"directive": body["directive"]}))
        elif "patches" in body:
            self._pending_strategies.append((cid, {"directive": body["patches"]}))
        else:
            self._pending_strategies.append((cid, {"directive": body}))
        return await self._api_cors(web.json_response({"ok": True}))

    def _apply_unit_command(self, cid: int, body: dict) -> dict:
        """Apply a unit-level override command to a specific ant. Returns result dict."""
        if not self.world.colonies:
            return {"error": "game not started"}
        c = self.world.colonies[cid]
        ant_id = body.get("ant_id")
        command = body.get("command", "")
        if ant_id is None:
            return {"error": "missing ant_id"}
        ant = next((a for a in c.ants if a.id == ant_id), None)
        if ant is None:
            return {"error": f"ant {ant_id} not found in colony {cid}"}
        if ant.type == A_QUEEN and command != "clear":
            return {"error": "queen cannot be commanded — queen position is fixed"}
        if command == "clear":
            ant.unit_override = None
            return {"ok": True, "ant_id": ant_id, "override": None}
        elif command in ("move_to", "attack_xy"):
            x, y = body.get("x"), body.get("y")
            if x is None or y is None:
                return {"error": "move_to/attack_xy requires x and y"}
            x, y = max(0, min(MAP_W-1, int(x))), max(0, min(MAP_H-1, int(y)))
            ant.unit_override = {"cmd": command, "x": x, "y": y}
        elif command == "gather":
            x, y = body.get("x"), body.get("y")
            if x is None or y is None:
                return {"error": "gather requires x and y"}
            if ant.type != A_WORKER:
                return {"error": "gather only applies to workers"}
            x, y = max(0, min(MAP_W-1, int(x))), max(0, min(MAP_H-1, int(y)))
            ant.unit_override = {"cmd": "gather", "x": x, "y": y}
            ant.recruit_target = (x, y)
        elif command == "build":
            x, y = body.get("x"), body.get("y")
            if x is None or y is None:
                return {"error": "build requires x and y (the build site coordinates)"}
            if ant.type != A_WORKER:
                return {"error": "build only applies to workers"}
            x, y = max(0, min(MAP_W-1, int(x))), max(0, min(MAP_H-1, int(y)))
            ant.unit_override = {"cmd": "build", "x": x, "y": y}
        elif command == "hold":
            ant.unit_override = {"cmd": "hold", "x": ant.x, "y": ant.y}
        elif command == "patrol":
            wps = body.get("waypoints")
            if not wps or len(wps) < 2:
                return {"error": "patrol requires waypoints list with at least 2 points"}
            ant.unit_override = {"cmd": "patrol", "waypoints": wps, "idx": 0}
            ant.patrol_idx = 0
        else:
            return {"error": f"unknown unit command '{command}'. Valid: move_to, attack_xy, gather, build, hold, patrol, clear"}
        return {"ok": True, "ant_id": ant_id, "type": ant.type, "override": ant.unit_override}

    async def api_command(self, req):
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        if not self.world.colonies:
            return await self._api_cors(web.json_response({"error": "game not started"}))
        try:
            body = await req.json()
        except Exception:
            return await self._api_cors(web.json_response({"error": "bad json"}, status=400))
        cmd_type = body.get("type", "")
        if cmd_type == "buy_upgrade":
            unit = body.get("unit", True)
            c = self.world.colonies[cid]
            _UPGRADE_COSTS_MAP = {
                "worker":  WORKER_UPGRADE_COSTS,
                "scout":   SCOUT_UPGRADE_COSTS,
                "soldier": SOLDIER_UPGRADE_COSTS,
            }
            _UPGRADE_TIERS_MAP = {
                "worker":  c.worker_tier,
                "scout":   c.scout_tier,
                "soldier": c.soldier_tier,
            }
            if unit is True:
                self._pending_strategies.append((cid, {"buy_upgrade": unit}))
                return await self._api_cors(web.json_response({"ok": True, "status": "queued_cheapest"}))
            elif unit in _UPGRADE_COSTS_MAP:
                tier  = _UPGRADE_TIERS_MAP[unit]
                costs = _UPGRADE_COSTS_MAP[unit]
                if tier >= len(costs):
                    return await self._api_cors(web.json_response(
                        {"ok": False, "error": f"{unit} already at max tier (T{tier})"}, status=400))
                cost = costs[tier]
                self._pending_strategies.append((cid, {"buy_upgrade": unit}))
                if c.food >= cost:
                    return await self._api_cors(web.json_response({
                        "ok": True, "status": "will_purchase_this_tick",
                        "unit": unit, "tier_target": tier + 1, "cost": cost,
                        "food_current": int(c.food),
                    }))
                else:
                    return await self._api_cors(web.json_response({
                        "ok": True, "status": "queued_waiting_for_food",
                        "unit": unit, "tier_target": tier + 1, "cost": cost,
                        "food_current": int(c.food), "food_needed": cost - int(c.food),
                    }))
        elif cmd_type == "build":
            b = body.get("build", {})
            c = self.world.colonies[cid]
            # Synchronous dirt validation before queuing
            _BUILD_COSTS = {"guard_post": GUARD_POST_COST, "watchtower": WATCHTOWER_COST,
                            "barracks": BARRACKS_COST, "wall": WALL_COST, "larder": LARDER_COST}
            _BUILD_LIMITS = {"guard_post": GUARD_POST_MAX, "watchtower": WATCHTOWER_MAX,
                             "barracks": BARRACKS_MAX, "wall": WALL_MAX, "larder": LARDER_MAX}
            stype = "guard_post" if isinstance(b, list) else b.get("type", "guard_post")
            dirt_cost = _BUILD_COSTS.get(stype, GUARD_POST_COST)
            limit = _BUILD_LIMITS.get(stype, GUARD_POST_MAX)
            own_count = sum(1 for st in self.world.structures if st["colony"] == cid and st.get("type", "guard_post") == stype)
            if c.dirt < dirt_cost:
                return await self._api_cors(web.json_response({
                    "error": f"insufficient dirt for {stype}: need {dirt_cost}◆, have {int(c.dirt)}◆",
                    "dirt_required": dirt_cost, "dirt_current": int(c.dirt)
                }, status=400))
            if own_count >= limit:
                return await self._api_cors(web.json_response({
                    "error": f"{stype} limit reached ({own_count}/{limit})",
                    "limit": limit, "current_count": own_count
                }, status=400))
            self._pending_strategies.append((cid, {"build": b}))
            return await self._api_cors(web.json_response({
                "ok": True, "dirt_required": dirt_cost,
                "dirt_remaining": int(c.dirt - dirt_cost)
            }))
        elif cmd_type == "convert":
            self._pending_strategies.append((cid, {"convert": body.get("convert", {})}))
        elif cmd_type == "cancel_spawn":
            unit_type = body.get("unit_type", "all")
            if unit_type not in {"worker", "soldier", "scout", "all"}:
                return await self._api_cors(web.json_response(
                    {"error": "unit_type must be 'worker', 'soldier', 'scout', or 'all'"}, status=400))
            c = self.world.colonies[cid]
            _type_map = {"worker": A_WORKER, "soldier": A_SOLDIER, "scout": A_SCOUT}
            if unit_type == "all":
                cancelled = len(c.spawn_queue)
                refund = sum(cost for _, _, cost in c.spawn_queue)
            else:
                t_int = _type_map[unit_type]
                cancelled = sum(1 for t, _, _ in c.spawn_queue if t == t_int)
                refund = sum(cost for t, _, cost in c.spawn_queue if t == t_int)
            self._pending_strategies.append((cid, {"cancel_spawn": unit_type}))
            return await self._api_cors(web.json_response({
                "ok": True, "cancelled": cancelled, "food_refunded": refund
            }))
        elif cmd_type == "unit_command":
            result = self._apply_unit_command(cid, body)
            return await self._api_cors(web.json_response(result, status=400 if "error" in result else 200))
        elif cmd_type == "unit_command_batch":
            cmds = body.get("commands", [])
            results = [self._apply_unit_command(cid, c) for c in cmds]
            errors = [r for r in results if "error" in r]
            return await self._api_cors(web.json_response({"ok": len(results) - len(errors), "errors": errors}))
        else:
            return await self._api_cors(web.json_response({"error": f"unknown command '{cmd_type}'"}, status=400))
        return await self._api_cors(web.json_response({"ok": True}))

    async def api_events(self, req):
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        if not self.world.colonies:
            return await self._api_cors(web.json_response({"events": []}))
        since_tick = int(req.rel_url.query.get("since_tick", 0))
        c = self.world.colonies[cid]
        events = list(c.events)  # newest first
        return await self._api_cors(web.json_response({
            "events": events[:30], "tick": self.world.tick
        }))

    async def api_feedback(self, req):
        """Receive agent feedback about the game and append to logs/agent_feedback.jsonl."""
        try:
            body = await req.json()
        except Exception:
            return await self._api_cors(web.json_response({"error": "bad json"}, status=400))
        feedback_text = body.get("feedback", "").strip()
        if not feedback_text:
            return await self._api_cors(web.json_response({"error": "feedback field required"}, status=400))
        entry = {
            "ts": datetime.now().isoformat(),
            "tick": self.world.tick,
            "phase": self.world.phase,
            "colony_id": body.get("colony_id"),
            "agent": body.get("agent"),
            "category": body.get("category", "general"),  # general|ux|missing_data|bug|balance
            "feedback": feedback_text,
        }
        os.makedirs("logs", exist_ok=True)
        with open("logs/agent_feedback.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"💬  Agent feedback [{entry['category']}]: {feedback_text[:80]}{'...' if len(feedback_text)>80 else ''}")
        return await self._api_cors(web.json_response({"ok": True, "stored": entry}))

    async def run(self):
        app = web.Application()
        app.router.add_get("/", self.on_index)
        app.router.add_get("/ws", self.on_ws)
        # REST API routes
        app.router.add_get("/api/tick", self.api_tick)
        app.router.add_get("/api/seats", self.api_seats)
        app.router.add_get("/api/matches", self.api_matches)
        app.router.add_post("/api/seat/{colony_id}", self.api_join_seat)
        app.router.add_delete("/api/seat/{colony_id}", self.api_release_seat)
        app.router.add_post("/api/control", self.api_control)
        app.router.add_get("/api/state/{colony_id}", self.api_state)
        app.router.add_get("/api/notifications/{colony_id}", self.api_notifications)
        app.router.add_get("/api/intel_map/{colony_id}", self.api_intel_map)
        app.router.add_get("/api/directive/{colony_id}", self.api_directive)
        app.router.add_post("/api/directive/{colony_id}", self.api_patch_directive)
        app.router.add_post("/api/command/{colony_id}", self.api_command)
        app.router.add_get("/api/events/{colony_id}", self.api_events)
        app.router.add_post("/api/feedback", self.api_feedback)
        # CORS preflight
        app.router.add_route("OPTIONS", "/api/{path_info:.*}", self.api_options)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", 8083).start()
        print("🐝  Swarm Wars — http://localhost:8083")
        print("🔌  MCP REST API — http://localhost:8083/api/")
        asyncio.create_task(self.llm_loop_for(0))
        asyncio.create_task(self.llm_loop_for(1))
        await self.tick_loop()

if __name__ == "__main__":
    asyncio.run(Server().run())
