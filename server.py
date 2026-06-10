#!/usr/bin/env python3
"""
Agants — Ant Colony RTS for LLMs  v0.1.0

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

import asyncio, concurrent.futures, json, math, os, random, re, resource, time, uuid
from collections import deque
from datetime import datetime
import aiohttp
from aiohttp import web

from engine.constants import *
from engine.colony import Ant, DirectiveEngine, Colony, _apply_upgrade_effects
from engine.world import gen_terrain, Predator, World
from bot import update_bot_strategy

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

VERSION = "0.1.0"   # semantic; bump only at real releases
try:
    import subprocess as _sp
    BUILD = _sp.check_output(["git", "rev-parse", "--short", "HEAD"],
                              stderr=_sp.DEVNULL, text=True).strip()
except Exception:
    BUILD = "dev"

# Internal dev changelog (date-stamped, not tied to VERSION)
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
# 2.11 — Phase 3.5: RECALL + check_alerts
#         RECALL: military.retreat=true now properly holds a defensive perimeter — soldiers
#           walk home and once within 8 tiles of nest orbit a radius-6 ring (8 slots) instead
#           of drifting; previously retreat just sent them toward the nest with no arrival logic
#         check_alerts: DirectiveEngine.check_alerts() evaluates the directive's alerts[] array
#           each tick and pushes "alert" notifications to the colony notification queue;
#           sampling=True → edge-triggered (only on False→True transition, no spam);
#           sampling=False → level-triggered (fires every 30 ticks while condition holds);
#           namespace is identical to triggers (all trigger variables available in alert conditions)
# 2.13 — Phase 4.4: GET /health (uptime/version/matches/clients/memory/actual TPS);
#         structured startup log (TPS, brain types, tunnel URL if present);
#         log rotation: logs/ capped at LOG_MAX_MB (default 50 MB), oldest run_*.log deleted;
#         _tick_times deque on Match for actual-vs-target TPS measurement
# 2.12 — Default TPS 10→1, LLM_INTERVAL 100→15, default brain type bot→mcp
#         TPS=1 makes live matches watchable and gives agents time for real decision cycles;
#         LLM_INTERVAL=15 keeps bot/LLM updates on a 15-second cadence at 1 TPS;
#         default brain type is now "mcp" so both seats advertise as agent-ready out of the box;
#         empty mcp seat already falls back to update_bot_strategy() — the intelligent planning
#         bot fills in automatically when only one agent is connected (no dumb dummy opponent)
# 2.14 — Bug-fix sweep (6 issues):
#         mcp_server.py: get_directive / list_seats / game_control now target the JOINED match
#           (were hitting the default match unconditionally via legacy unscoped endpoints);
#         income_per_s now includes larder income (already accumulated into food_earned_tick)
#           and a new baseline of +1 food/tick;
#         minimum income: every living colony gains +1 food/tick during the running phase
#           (engine/world.py step()) so a 0-worker, 0-larder colony can never permanently stall;
#         DirectiveEngine triggers support an optional "else" block — applied (same patch
#           mechanism as "then") when the condition is False, so a trigger can undo its own
#           patches (e.g. clear military.retreat) instead of latching forever

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

LLM_INTERVAL = int(os.environ.get("LLM_INTERVAL", "15"))
LOG_MAX_MB        = int(os.environ.get("LOG_MAX_MB", "50"))
AGANTS_AUTH_URL    = os.environ.get("AGANTS_AUTH_URL", "")      # e.g. https://agants-auth.workers.dev
AGANTS_AUTH_SECRET = os.environ.get("AGANTS_AUTH_SECRET", "")  # shared secret for /validate + /match

def _default_llm_brain():
    return {"type": "llm",
            "api_key":  "",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model":    "deepseek-ai/deepseek-r1"}

# Brain configs: 0=RED, 1=BLUE — loaded from .env on startup
RED_BRAIN:  dict = {"type": "mcp"}
BLUE_BRAIN: dict = {"type": "mcp"}

def _apply_env_config():
    """Load brain config from .env — new-style fields first, legacy fallback."""
    global RED_BRAIN, BLUE_BRAIN, LLM_INTERVAL
    LLM_INTERVAL = int(os.environ.get("LLM_INTERVAL", LLM_INTERVAL))
    # New-style fields (written by _save_config after first use)
    red_type  = os.environ.get("RED_BRAIN_TYPE",  "")
    blue_type = os.environ.get("BLUE_BRAIN_TYPE", "")
    if red_type or blue_type:
        RED_BRAIN  = {"type": red_type  or "mcp"}
        BLUE_BRAIN = {"type": blue_type or "mcp"}
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
        f.write("# Agants — Configuration (auto-generated by dashboard)\n\n")
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
You are the strategic commander of the {MY} ant colony in Agants, a real-time ant colony RTS.

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

TPS = int(os.environ.get("TPS", "1"))

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
            f.write(f"=== AGANTS v{VERSION} ({BUILD})  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
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
            c.income_per_s = c.food_earned_tick  # food earned this 10-tick window (deliveries + larders + baseline)
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
# Match — per-match state container
# ═══════════════════════════════════════════════════════════════════════════════

class Match:
    """All mutable state for one game. Server.matches holds one or more of these."""
    def __init__(self, tps: float = None):
        self.match_id: str                    = str(uuid.uuid4())[:8]
        self.tps: float                       = tps or TPS
        self.world: World                     = World()
        self.clients: set                     = set()
        self.llm_memories: list               = [{}, {}]
        self.llm_strategy_logs: list          = [[], []]
        self.llm_stats: list                  = [
            {"calls": 0, "prompt_tok": 0, "completion_tok": 0, "errors": 0},
            {"calls": 0, "prompt_tok": 0, "completion_tok": 0, "errors": 0},
        ]
        self._placement_food: list            = []
        self._placement_updates: list         = []
        self._placement_start_t               = None
        self._pending_strategies: deque       = deque()
        self._step_in_progress: bool          = False
        self.tokens: dict                     = {}
        self.created_at: float                = time.time()
        self._sim_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"sim-{self.match_id[:4]}"
        )
        self._tick_times: deque = deque(maxlen=20)  # monotonic timestamps of recent steps

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_dict(self):
        return {
            "match_id":   self.match_id,
            "tps":        self.tps,
            "created_at": self.created_at,
            "tokens":     self.tokens,
            "world":      self.world.to_dict(),
        }

    @classmethod
    def from_dict(cls, d):
        m = cls.__new__(cls)
        m.match_id   = d["match_id"]
        m.tps        = d.get("tps", TPS)
        m.created_at = d.get("created_at", time.time())
        m.tokens     = d.get("tokens", {})
        m.world      = World.from_dict(d["world"])
        m.clients    = set()
        m.llm_memories      = [{}, {}]
        m.llm_strategy_logs = [[], []]
        m.llm_stats = [
            {"calls": 0, "prompt_tok": 0, "completion_tok": 0, "errors": 0},
            {"calls": 0, "prompt_tok": 0, "completion_tok": 0, "errors": 0},
        ]
        m._placement_food     = []
        m._placement_updates  = []
        m._placement_start_t  = None
        m._pending_strategies = deque()
        m._step_in_progress   = False
        m._sim_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"sim-{m.match_id[:4]}"
        )
        m._tick_times = deque(maxlen=20)
        return m


# ═══════════════════════════════════════════════════════════════════════════════
# Server
# ═══════════════════════════════════════════════════════════════════════════════

class Server:
    SAVE_DIR    = "data/matches"
    RESULTS_DIR = "data/results"
    SAVE_INTERVAL = 60  # ticks between autosaves

    def __init__(self):
        self.matches: dict[str, Match] = {}
        self._startup_time = time.monotonic()
        os.makedirs(self.SAVE_DIR,    exist_ok=True)
        os.makedirs(self.RESULTS_DIR, exist_ok=True)
        self._rotate_logs()
        saved = self._load_saved_matches()
        if saved:
            m = saved[0]
            self.matches[m.match_id] = m
            self._default_match_id = m.match_id
            print(f"♻️   Restored match {m.match_id} at tick {m.world.tick} (phase={m.world.phase})")
        else:
            m = self._new_match()
            self._default_match_id = m.match_id

    # ── Persistence ────────────────────────────────────────────────────────────

    def _save_match(self, m: Match):
        path = os.path.join(self.SAVE_DIR, f"{m.match_id}.json")
        tmp  = path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(m.to_dict(), f)
            os.replace(tmp, path)
        except Exception as e:
            print(f"⚠️  save_match {m.match_id}: {e}")

    def _delete_save(self, m: Match):
        path = os.path.join(self.SAVE_DIR, f"{m.match_id}.json")
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def _save_result(self, m: Match):
        """Write a compact completed-match record for future match history."""
        w = m.world
        agents = {str(k): v for k, v in w.mcp_seats.items()}
        # Collect user_ids from active tokens for each colony
        user_ids = {e["colony_id"]: e.get("user_id") for e in m.tokens.values()}
        rec = {
            "match_id":   m.match_id,
            "created_at": m.created_at,
            "ended_at":   time.time(),
            "ticks":      w.tick,
            "winner":     w.winner,
            "red_agent":  agents.get("0"),
            "blue_agent": agents.get("1"),
            "food_collected": [c.food_collected for c in w.colonies],
            "ants_lost":      [c.ants_lost      for c in w.colonies],
        }
        path = os.path.join(self.RESULTS_DIR, f"{m.match_id}.json")
        try:
            with open(path, "w") as f:
                json.dump(rec, f)
        except Exception as e:
            print(f"⚠️  save_result {m.match_id}: {e}")
        # Determine winner user_id
        winner_uid = None
        if w.winner == 0:
            winner_uid = user_ids.get(0)
        elif w.winner == 1:
            winner_uid = user_ids.get(1)
        self._post_match_result(rec, user_ids.get(0), user_ids.get(1), winner_uid)

    def _load_saved_matches(self) -> list:
        """Load in-progress matches from disk. Returns list of Match objects."""
        loaded = []
        for fname in os.listdir(self.SAVE_DIR):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.SAVE_DIR, fname)
            try:
                with open(path) as f:
                    d = json.load(f)
                m = Match.from_dict(d)
                # Only restore non-finished matches still in a playable phase
                if m.world.phase in ("running", "paused", "lobby"):
                    loaded.append(m)
            except Exception as e:
                print(f"⚠️  load_match {fname}: {e}")
        loaded.sort(key=lambda m: m.created_at, reverse=True)
        return loaded

    # ── Match management ───────────────────────────────────────────────────────

    def _new_match(self, tps: float = None) -> Match:
        m = Match(tps=tps)
        self.matches[m.match_id] = m
        return m

    def _get_match_or_default(self, req) -> "Match | None":
        """Extract match_id from request path info; fall back to default match."""
        mid = req.match_info.get("match_id")
        if mid:
            return self.matches.get(mid)  # None → caller returns 404
        return self._m

    def _start_match_tasks(self, m: Match):
        """Spawn tick_loop + LLM loops for a newly created match."""
        asyncio.create_task(self.tick_loop(m))
        asyncio.create_task(self.llm_loop_for(m, 0))
        asyncio.create_task(self.llm_loop_for(m, 1))

    @property
    def _m(self) -> Match:
        return self.matches[self._default_match_id]

    # Properties that forward to the default match (backward compat)
    @property
    def world(self): return self._m.world
    @world.setter
    def world(self, v): self._m.world = v

    @property
    def clients(self): return self._m.clients

    @property
    def _tokens(self): return self._m.tokens
    @_tokens.setter
    def _tokens(self, v): self._m.tokens = v

    @property
    def llm_memories(self): return self._m.llm_memories
    @llm_memories.setter
    def llm_memories(self, v): self._m.llm_memories = v

    @property
    def llm_strategy_logs(self): return self._m.llm_strategy_logs
    @llm_strategy_logs.setter
    def llm_strategy_logs(self, v): self._m.llm_strategy_logs = v

    @property
    def llm_stats(self): return self._m.llm_stats
    @llm_stats.setter
    def llm_stats(self, v): self._m.llm_stats = v

    @property
    def _pending_strategies(self): return self._m._pending_strategies

    @property
    def _step_in_progress(self): return self._m._step_in_progress
    @_step_in_progress.setter
    def _step_in_progress(self, v): self._m._step_in_progress = v

    @property
    def _placement_food(self): return self._m._placement_food
    @_placement_food.setter
    def _placement_food(self, v): self._m._placement_food = v

    @property
    def _placement_updates(self): return self._m._placement_updates
    @_placement_updates.setter
    def _placement_updates(self, v): self._m._placement_updates = v

    @property
    def _placement_start_t(self): return self._m._placement_start_t
    @_placement_start_t.setter
    def _placement_start_t(self, v): self._m._placement_start_t = v

    def _make_init_msg(self, m: Match = None):
        w = (m or self._m).world
        return json.dumps({
            "type": "init",
            "map": {"w": MAP_W, "h": MAP_H, "tile": TILE},
            "terrain": [w.terrain[y][x] for y in range(MAP_H) for x in range(MAP_W)],
            "seats": {str(k): v for k, v in w.mcp_seats.items()},
        })

    async def _broadcast(self, msg, m: Match = None):
        clients = (m or self._m).clients
        dead = set()
        for ws in clients:
            try: await ws.send_str(msg)
            except: dead.add(ws)
        clients -= dead

    async def _reset(self, m: Match = None):
        m = m or self._m
        for cid in (0, 1):
            if m.llm_memories[cid] and m.world.logger:
                m.world.logger.log_memory_snapshot(m.llm_memories[cid])
        Ant._id = 0
        m.world                = World()
        m.llm_memories         = [{}, {}]
        m.llm_strategy_logs    = [[], []]
        m.llm_stats            = [
            {"calls": 0, "prompt_tok": 0, "completion_tok": 0, "errors": 0},
            {"calls": 0, "prompt_tok": 0, "completion_tok": 0, "errors": 0},
        ]
        m._pending_strategies.clear()
        m.tokens.clear()
        # Send new terrain and lobby state — game starts when client sends "start_game"
        await self._broadcast(self._make_init_msg(m), m=m)
        await self._broadcast(json.dumps({"type": "lobby", "seats": {"0": None, "1": None}}), m=m)
        print(f"🔄  [{m.match_id}] game reset to lobby — click START to begin")

    async def _run_placement_phase(self, m: Match = None):
        """Fixed spawn positions on The Crossing — no placement decision needed."""
        m = m or self._m
        world = m.world
        red_pos, blue_pos = RED_SPAWN, BLUE_SPAWN

        print(f"\n{'═'*70}")
        print(f"🗺️  [{m.match_id}] {MAP_NAME} — {MAP_W}×{MAP_H}  RED@{red_pos} vs BLUE@{blue_pos}")
        print(f"{'═'*70}")

        food_data = [[f["x"], f["y"], int(f["amt"]), f["kind"], f.get("tier","home")]
                     for f in world.foods]
        m._placement_food    = food_data
        m._placement_updates = []

        await self._broadcast(json.dumps({
            "type": "placement_phase",
            "food": food_data,
            "timeout": 2,
        }), m=m)
        await self._broadcast(json.dumps({"type": "placement_update", "colony": 0,
                                          "pos": list(red_pos),  "label": "RED",  "score": 0}), m=m)
        await self._broadcast(json.dumps({"type": "placement_update", "colony": 1,
                                          "pos": list(blue_pos), "label": "BLUE", "score": 0}), m=m)
        await self._broadcast(json.dumps({"type": "placement_ready"}), m=m)
        await asyncio.sleep(0.5)

        world.finalize_placement(red_pos, blue_pos)
        if world.logger:
            world.logger.log_placement(red_pos, "fixed spawn", blue_pos, "fixed spawn")
        m._placement_food    = []
        m._placement_updates = []

        await self._broadcast(self._make_init_msg(m), m=m)
        await self._broadcast(json.dumps({
            "type": "game_start",
            "red":  list(red_pos),
            "blue": list(blue_pos),
        }), m=m)
        print(f"🎮  [{m.match_id}] GAME START — RED@{red_pos} vs BLUE@{blue_pos}")
        print(f"{'═'*70}\n")

    async def tick_loop(self, m: Match):
        bot_last_tick = 0
        loop = asyncio.get_event_loop()
        _last_idle_bc = 0.0
        while True:
            t0 = time.monotonic()
            phase = m.world.phase
            if phase in ("lobby", "paused"):
                # Broadcast idle state once per second so clients stay updated
                if t0 - _last_idle_bc >= 1.0:
                    seats = {str(k): v for k, v in m.world.mcp_seats.items()}
                    await self._broadcast(json.dumps({"type": phase, "seats": seats}), m=m)
                    _last_idle_bc = t0
                await asyncio.sleep(max(0, 1.0/m.tps - (time.monotonic()-t0)))
                continue
            # Drain queued strategy updates before stepping (main-thread, safe)
            while m._pending_strategies:
                cid, strat = m._pending_strategies.popleft()
                if m.world.colonies[cid].alive:
                    m.world.colonies[cid].set_strategy(strat)
            # Bot decisions (tick-based, still safe here)
            if phase == "running" and m.world.winner is None:
                if m.world.tick - bot_last_tick >= LLM_INTERVAL:
                    bot_last_tick = m.world.tick
                    for cid in (0, 1):
                        btype = _brain_for(cid)["type"]
                        # Unclaimed MCP seats fall back to the bot brain so an absent
                        # agent doesn't leave the colony running on bare defaults;
                        # the bot steps aside the moment an agent joins the seat.
                        if btype == "bot" or (btype == "mcp"
                                              and m.world.mcp_seats.get(cid) is None):
                            update_bot_strategy(m.world, cid)
            # Run step() in a thread so the event loop stays responsive during heavy ticks
            m._step_in_progress = True
            await loop.run_in_executor(m._sim_executor, m.world.step)
            m._step_in_progress = False
            m._tick_times.append(time.monotonic())
            tick = m.world.tick
            if m.world.phase == "running":
                state = m.world.serialize_tick()
                await self._broadcast(json.dumps(state), m=m)
            # Autosave every SAVE_INTERVAL ticks; save+record on game over
            if m.world.winner is not None and tick > 0:
                self._save_result(m)
                self._delete_save(m)
            elif tick > 0 and tick % self.SAVE_INTERVAL == 0:
                asyncio.get_event_loop().run_in_executor(None, self._save_match, m)
            await asyncio.sleep(max(0, 1.0/m.tps - (time.monotonic()-t0)))

    async def llm_loop_for(self, m: Match, colony_id: int):
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
            last_call_time -= (LLM_INTERVAL / m.tps) / 2

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

            world = m.world
            if world is not last_world:
                last_world     = world
                last_call_time = time.monotonic()  # reset on new game

            if world.winner is not None:
                wid = id(world) * 10 + colony_id
                if wid not in debriefed:
                    debriefed.add(wid)
                    await self._llm_debrief(m, world, client, colony_id)
                continue

            # Colonies don't exist during placement phase — wait for game to start
            if world.phase != "running":
                continue

            # Wall-clock interval: LLM_INTERVAL ticks converted to seconds at current TPS
            llm_interval_secs = LLM_INTERVAL / m.tps
            if time.monotonic() - last_call_time < llm_interval_secs:
                continue

            # Wait for the sim thread to finish its current step before reading world state.
            # This prevents data races when build_llm_prompt iterates colony/food lists.
            while m._step_in_progress:
                await asyncio.sleep(0)

            colony = world.colonies[colony_id]
            if not colony.alive:
                last_call_time = time.monotonic()
                continue

            # Snapshot world state synchronously (no await → no step() can start mid-read)
            stats  = m.llm_stats[colony_id]
            prompt = build_llm_prompt(colony, world.tick, world=world,
                                      memory=m.llm_memories[colony_id],
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

            if world.winner is not None or world is not m.world:
                continue

            if strategy:
                # Queue strategy for the tick loop to apply atomically before next step()
                m._pending_strategies.append((colony_id, strategy))
                colony.push_event(f"[LLM] strategy → {json.dumps(strategy)}")

            if memory_update:
                m.llm_memories[colony_id] = _trim_memory(
                    apply_memory_update(m.llm_memories[colony_id], memory_update)
                )

            m.llm_strategy_logs[colony_id].append((world.tick, strategy))
            world.logger.log_llm(colony_id, reasoning, strategy, prompt=prompt, feedback=feedback)
            world._llm_stats_list[colony_id] = {
                "model":   model, "colony": name,
                "calls":   stats["calls"], "errors": stats["errors"],
                "prompt_tok": stats["prompt_tok"], "completion_tok": stats["completion_tok"],
            }


    async def _llm_debrief(self, m: Match, world, client, colony_id: int):
        """Post-game reflection call — fires once per LLM colony when game ends."""
        if client is None: return
        name  = "RED" if colony_id == 0 else "BLUE"
        brain = _brain_for(colony_id)
        model = brain.get("model", "?")
        print(f"\n{'═'*70}")
        print(f"🎓  [{name}] post-game debrief → {model}")
        print(f"{'═'*70}")
        prompt = build_debrief_prompt(world, colony_id,
                                      m.llm_strategy_logs[colony_id],
                                      memory=m.llm_memories[colony_id])
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
                m.llm_memories[colony_id] = _trim_memory(
                    apply_memory_update(m.llm_memories[colony_id], debrief_mem)
                )
                print(f"[MEMORY ↑ debrief]  {json.dumps(debrief_mem)}")
            if world.logger:
                world.logger.log_memory_snapshot(m.llm_memories[colony_id])

        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"    ❌ debrief error after {elapsed:.2f}s: {e}\n")

    async def on_index(self, req):
        return web.FileResponse("./frontend/landing.html")

    async def on_game(self, req):
        return web.FileResponse("./frontend/index.html")

    async def on_static_html(self, req):
        name = req.match_info["page"]
        allowed = {"register.html", "me.html", "matches.html"}
        if name not in allowed:
            return web.Response(status=404)
        return web.FileResponse(f"./frontend/{name}")

    async def on_config_js(self, req):
        return web.FileResponse("./frontend/config.js")

    async def on_ws(self, req):
        return await self._on_ws_for(req, self._m)

    async def on_ws_match(self, req):
        """Match-scoped WebSocket — validates match_id, then delegates to shared handler."""
        match_id = req.match_info["match_id"]
        m = self.matches.get(match_id)
        if m is None:
            return web.Response(status=404, text=f"match {match_id!r} not found")
        return await self._on_ws_for(req, m)

    async def _on_ws_for(self, req, m: Match):
        """Shared WebSocket handler scoped to a specific match."""
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        m.clients.add(ws)
        try:
            await ws.send_str(self._make_init_msg(m))
            # Send current phase state to newly connected client
            seats_now = {str(k): v for k, v in m.world.mcp_seats.items()}
            if m.world.phase == "lobby":
                await ws.send_str(json.dumps({"type": "lobby", "seats": seats_now}))
            elif m.world.phase == "paused":
                await ws.send_str(json.dumps({"type": "paused", "seats": seats_now}))
            elif m.world.phase == "running":
                await ws.send_str(json.dumps({"type": "seats_update", "seats": seats_now}))
            # Replay placement state for clients that connect mid-phase
            if m.world.phase == "placement" and m._placement_food:
                await ws.send_str(json.dumps({
                    "type": "placement_phase",
                    "food": m._placement_food,
                    "timeout": 2,
                }))
                for upd in m._placement_updates:
                    await ws.send_str(json.dumps(upd))
                if len(m._placement_updates) >= 2:
                    await ws.send_str(json.dumps({"type": "placement_ready"}))
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "reset":
                            await self._reset(m)
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
                            if m.world.phase in ("lobby", "placement"):
                                asyncio.create_task(self._run_placement_phase(m))
                        elif data.get("type") == "pause_game":
                            if m.world.phase == "running":
                                m.world.phase = "paused"
                                await self._broadcast(json.dumps({"type": "paused", "seats": {str(k): v for k, v in m.world.mcp_seats.items()}}), m=m)
                        elif data.get("type") == "resume_game":
                            if m.world.phase == "paused":
                                m.world.phase = "running"
                                await self._broadcast(json.dumps({"type": "resumed"}), m=m)
                        elif data.get("type") == "end_game":
                            if m.world.phase in ("running", "paused"):
                                m.world.winner = "draw"
                                m.world.phase = "running"
                        elif data.get("type") == "join_seat":
                            cid = data.get("colony_id")
                            agent = data.get("agent_name", "MCP Agent")
                            if cid in (0, 1) and m.world.mcp_seats.get(cid) is None:
                                m.world.mcp_seats[cid] = agent
                                key = "red_brain" if cid == 0 else "blue_brain"
                                _save_config({key: {"type": "mcp", "agent": agent}})
                                await self._broadcast(json.dumps({"type": "seat_joined", "colony_id": cid, "agent": agent}), m=m)
                        elif data.get("type") == "release_seat":
                            cid = data.get("colony_id")
                            if cid in (0, 1):
                                m.world.mcp_seats[cid] = None
                                key = "red_brain" if cid == 0 else "blue_brain"
                                _save_config({key: {"type": "bot"}})
                                await self._broadcast(json.dumps({"type": "seat_released", "colony_id": cid}), m=m)
                        elif data.get("type") == "chat":
                            msg_text = (data.get("msg") or "").strip()[:200]
                            if msg_text:
                                sender = data.get("name") or "spectator"
                                await self._broadcast(json.dumps({
                                    "type": "chat", "colony": None,
                                    "name": str(sender)[:32], "msg": msg_text,
                                    "cls": "human", "tick": m.world.tick,
                                }), m=m)
                    except Exception:
                        pass
        finally:
            m.clients.discard(ws)
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
        """Match discovery endpoint — lists all games with seat availability."""
        result = []
        for m in self.matches.values():
            seats = {}
            for k, agent in m.world.mcp_seats.items():
                brain = RED_BRAIN if k == 0 else BLUE_BRAIN
                seats[str(k)] = {"agent": agent, "brain_type": brain.get("type", "bot")}
            result.append({
                "match_id": m.match_id,
                "game_url": "http://localhost:8083",
                "ws_url":   f"ws://localhost:8083/ws/{m.match_id}",
                "phase":    m.world.phase,
                "tick":     m.world.tick,
                "map":      "The Crossing (150×100)",
                "seats":    seats,
                "winner":   m.world.winner,
                "created_at": m.created_at,
            })
        return await self._api_cors(web.json_response({"matches": result}))

    def _require_token(self, req, cid: int, m: Match = None):
        """Validate Bearer token. Returns (True, None) or (False, error_response)."""
        auth = req.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False, web.json_response({"error": "missing Authorization: Bearer <token>"}, status=401)
        token = auth[7:]
        tokens = (m or self._m).tokens
        entry = tokens.get(token)
        if entry is None:
            return False, web.json_response({"error": "invalid or expired token"}, status=401)
        if entry["colony_id"] != cid:
            return False, web.json_response({"error": "token not scoped to this colony"}, status=403)
        return True, None

    def _revoke_colony_token(self, cid: int, m: Match = None):
        """Remove any token currently held for the given colony."""
        tokens = (m or self._m).tokens
        to_del = [t for t, e in tokens.items() if e["colony_id"] == cid]
        for t in to_del:
            del tokens[t]

    async def api_join_seat(self, req):
        m = self._get_match_or_default(req)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        try:
            body = await req.json()
        except Exception:
            body = {}
        agent = body.get("agent_name", f"MCP-{cid}")
        # Auth: if AGANTS_AUTH_URL is set, api_key is required
        user_id = None
        if AGANTS_AUTH_URL:
            api_key = body.get("api_key", "")
            user = await self._validate_api_key(api_key)
            if not user:
                return await self._api_cors(web.json_response(
                    {"error": "invalid or missing api_key — register at /register"}, status=401))
            user_id = user["id"]
            agent   = user.get("username", agent)  # prefer registered name
        if m.world.mcp_seats.get(cid) is not None:
            return await self._api_cors(web.json_response({"error": "seat occupied", "agent": m.world.mcp_seats[cid]}, status=409))
        m.world.mcp_seats[cid] = agent
        key = "red_brain" if cid == 0 else "blue_brain"
        _save_config({key: {"type": "mcp", "agent": agent}})
        self._revoke_colony_token(cid, m)
        token = str(uuid.uuid4())
        m.tokens[token] = {"colony_id": cid, "agent": agent, "user_id": user_id}
        await self._broadcast(json.dumps({"type": "seat_joined", "colony_id": cid, "agent": agent}), m=m)
        return await self._api_cors(web.json_response({
            "ok": True, "colony_id": cid, "agent": agent,
            "token": token, "match_id": m.match_id,
        }))

    async def api_release_seat(self, req):
        m = self._get_match_or_default(req)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        ok, err = self._require_token(req, cid, m)
        if not ok:
            return await self._api_cors(err)
        self._revoke_colony_token(cid, m)
        m.world.mcp_seats[cid] = None
        key = "red_brain" if cid == 0 else "blue_brain"
        _save_config({key: {"type": "bot"}})
        await self._broadcast(json.dumps({"type": "seat_released", "colony_id": cid}), m=m)
        return await self._api_cors(web.json_response({"ok": True, "colony_id": cid}))

    async def api_control(self, req):
        m = self._get_match_or_default(req)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        try:
            body = await req.json()
        except Exception:
            return await self._api_cors(web.json_response({"error": "bad json"}, status=400))
        action = body.get("action", "")
        if action == "start":
            if m.world.phase in ("lobby", "placement"):
                asyncio.create_task(self._run_placement_phase(m))
                return await self._api_cors(web.json_response({"ok": True, "phase": "starting"}))
        elif action == "pause":
            if m.world.phase == "running":
                m.world.phase = "paused"
                await self._broadcast(json.dumps({"type": "paused", "seats": {str(k): v for k, v in m.world.mcp_seats.items()}}), m=m)
                return await self._api_cors(web.json_response({"ok": True, "phase": "paused"}))
        elif action == "resume":
            if m.world.phase == "paused":
                m.world.phase = "running"
                await self._broadcast(json.dumps({"type": "resumed"}), m=m)
                return await self._api_cors(web.json_response({"ok": True, "phase": "running"}))
        elif action == "end":
            if m.world.phase in ("running", "paused"):
                w = m.world
                scores = None
                if w.winner is None:
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
            asyncio.create_task(self._reset(m))
            return await self._api_cors(web.json_response({"ok": True, "phase": "lobby"}))
        return await self._api_cors(web.json_response({"error": f"invalid action '{action}' for phase '{m.world.phase}'"}, status=400))

    def _build_colony_state(self, cid: int, m: Match = None) -> dict:
        m = m or self._m
        w = m.world
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
        m = self._get_match_or_default(req)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        return await self._api_cors(web.json_response(self._build_colony_state(cid, m)))

    async def api_notifications(self, req):
        m = self._get_match_or_default(req)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        if not m.world.colonies:
            return await self._api_cors(web.json_response({"notifications": []}))
        notifs = m.world.colonies[cid].pop_notifications()
        return await self._api_cors(web.json_response({"notifications": notifs, "count": len(notifs)}))

    async def api_intel_map(self, req):
        m = self._get_match_or_default(req)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        if not m.world.colonies:
            return await self._api_cors(web.json_response({"error": "game not started"}))
        c = m.world.colonies[cid]
        w = m.world
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
        m = self._get_match_or_default(req)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        if not m.world.colonies:
            return await self._api_cors(web.json_response({"error": "game not started"}))
        return await self._api_cors(web.json_response({"directive": m.world.colonies[cid].directive}))

    async def api_patch_directive(self, req):
        m = self._get_match_or_default(req)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        ok, err = self._require_token(req, cid, m)
        if not ok:
            return await self._api_cors(err)
        if not m.world.colonies:
            return await self._api_cors(web.json_response({"error": "game not started"}))
        try:
            body = await req.json()
        except Exception:
            return await self._api_cors(web.json_response({"error": "bad json"}, status=400))
        if "directive" in body:
            m._pending_strategies.append((cid, {"directive": body["directive"]}))
        elif "patches" in body:
            m._pending_strategies.append((cid, {"directive": body["patches"]}))
        else:
            m._pending_strategies.append((cid, {"directive": body}))
        return await self._api_cors(web.json_response({"ok": True}))

    def _apply_unit_command(self, cid: int, body: dict, m: Match = None) -> dict:
        """Apply a unit-level override command to a specific ant. Returns result dict."""
        m = m or self._m
        if not m.world.colonies:
            return {"error": "game not started"}
        c = m.world.colonies[cid]
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
        m = self._get_match_or_default(req)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        ok, err = self._require_token(req, cid, m)
        if not ok:
            return await self._api_cors(err)
        if not m.world.colonies:
            return await self._api_cors(web.json_response({"error": "game not started"}))
        try:
            body = await req.json()
        except Exception:
            return await self._api_cors(web.json_response({"error": "bad json"}, status=400))
        cmd_type = body.get("type", "")
        if cmd_type == "buy_upgrade":
            unit = body.get("unit", True)
            c = m.world.colonies[cid]
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
                m._pending_strategies.append((cid, {"buy_upgrade": unit}))
                return await self._api_cors(web.json_response({"ok": True, "status": "queued_cheapest"}))
            elif unit in _UPGRADE_COSTS_MAP:
                tier  = _UPGRADE_TIERS_MAP[unit]
                costs = _UPGRADE_COSTS_MAP[unit]
                if tier >= len(costs):
                    return await self._api_cors(web.json_response(
                        {"ok": False, "error": f"{unit} already at max tier (T{tier})"}, status=400))
                cost = costs[tier]
                m._pending_strategies.append((cid, {"buy_upgrade": unit}))
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
            c = m.world.colonies[cid]
            # Synchronous dirt validation before queuing
            _BUILD_COSTS = {"guard_post": GUARD_POST_COST, "watchtower": WATCHTOWER_COST,
                            "barracks": BARRACKS_COST, "wall": WALL_COST, "larder": LARDER_COST}
            _BUILD_LIMITS = {"guard_post": GUARD_POST_MAX, "watchtower": WATCHTOWER_MAX,
                             "barracks": BARRACKS_MAX, "wall": WALL_MAX, "larder": LARDER_MAX}
            stype = "guard_post" if isinstance(b, list) else b.get("type", "guard_post")
            dirt_cost = _BUILD_COSTS.get(stype, GUARD_POST_COST)
            limit = _BUILD_LIMITS.get(stype, GUARD_POST_MAX)
            own_count = sum(1 for st in m.world.structures if st["colony"] == cid and st.get("type", "guard_post") == stype)
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
            m._pending_strategies.append((cid, {"build": b}))
            return await self._api_cors(web.json_response({
                "ok": True, "dirt_required": dirt_cost,
                "dirt_remaining": int(c.dirt - dirt_cost)
            }))
        elif cmd_type == "convert":
            m._pending_strategies.append((cid, {"convert": body.get("convert", {})}))
        elif cmd_type == "cancel_spawn":
            unit_type = body.get("unit_type", "all")
            if unit_type not in {"worker", "soldier", "scout", "all"}:
                return await self._api_cors(web.json_response(
                    {"error": "unit_type must be 'worker', 'soldier', 'scout', or 'all'"}, status=400))
            c = m.world.colonies[cid]
            _type_map = {"worker": A_WORKER, "soldier": A_SOLDIER, "scout": A_SCOUT}
            if unit_type == "all":
                cancelled = len(c.spawn_queue)
                refund = sum(cost for _, _, cost in c.spawn_queue)
            else:
                t_int = _type_map[unit_type]
                cancelled = sum(1 for t, _, _ in c.spawn_queue if t == t_int)
                refund = sum(cost for t, _, cost in c.spawn_queue if t == t_int)
            m._pending_strategies.append((cid, {"cancel_spawn": unit_type}))
            return await self._api_cors(web.json_response({
                "ok": True, "cancelled": cancelled, "food_refunded": refund
            }))
        elif cmd_type == "unit_command":
            result = self._apply_unit_command(cid, body, m)
            return await self._api_cors(web.json_response(result, status=400 if "error" in result else 200))
        elif cmd_type == "unit_command_batch":
            cmds = body.get("commands", [])
            results = [self._apply_unit_command(cid, c, m) for c in cmds]
            errors = [r for r in results if "error" in r]
            return await self._api_cors(web.json_response({"ok": len(results) - len(errors), "errors": errors}))
        else:
            return await self._api_cors(web.json_response({"error": f"unknown command '{cmd_type}'"}, status=400))
        return await self._api_cors(web.json_response({"ok": True}))

    async def api_events(self, req):
        m = self._get_match_or_default(req)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        cid = int(req.match_info["colony_id"])
        if cid not in (0, 1):
            return await self._api_cors(web.json_response({"error": "invalid colony_id"}, status=400))
        if not m.world.colonies:
            return await self._api_cors(web.json_response({"events": []}))
        since_tick = int(req.rel_url.query.get("since_tick", 0))
        c = m.world.colonies[cid]
        events = list(c.events)
        return await self._api_cors(web.json_response({
            "events": events[:30], "tick": m.world.tick
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
            "tick": self._m.world.tick,
            "phase": self._m.world.phase,
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

    async def api_chat(self, req):
        """Broadcast a chat message to all WebSocket clients.

        Agents: POST with bearer token — name defaults to colony colour.
        Spectators: POST without auth — name comes from body 'name' field.
        In tech-demo mode everyone can chat; auth only gates the colony attribution.
        """
        m = self._get_match_or_default(req)
        try:
            body = await req.json()
        except Exception:
            return await self._api_cors(web.json_response({"error": "bad json"}, status=400))
        msg_text = (body.get("msg") or body.get("message") or "").strip()[:200]
        if not msg_text:
            return await self._api_cors(web.json_response({"error": "msg required"}, status=400))

        # Determine sender identity
        colony_id = None
        sender_name = (body.get("name") or "spectator").strip()[:32]
        css_cls = "human"
        auth_hdr = req.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:]
            for cid, tok in m.tokens.items():
                if tok == token:
                    colony_id = cid
                    seat_name = m.world.mcp_seats.get(cid)
                    sender_name = seat_name or ["RED", "BLUE"][cid]
                    css_cls = ["red", "blue"][cid]
                    break

        msg = json.dumps({
            "type": "chat",
            "colony": colony_id,
            "name": sender_name,
            "msg": msg_text,
            "cls": css_cls,
            "tick": m.world.tick,
        })
        await self._broadcast(msg, m=m)
        return await self._api_cors(web.json_response({"ok": True}))

    async def api_create_match(self, req):
        """Create a new match. Body: {config: {tps?: float}}. Returns match_id + ws_url."""
        try:
            body = await req.json()
        except Exception:
            body = {}
        cfg = body.get("config", {})
        tps = float(cfg["tps"]) if "tps" in cfg else None
        m = self._new_match(tps=tps)
        self._start_match_tasks(m)
        return await self._api_cors(web.json_response({
            "ok":       True,
            "match_id": m.match_id,
            "ws_url":   f"ws://localhost:8083/ws/{m.match_id}",
            "phase":    m.world.phase,
            "tps":      m.tps,
        }))

    async def api_get_match(self, req):
        """Return info about a specific match."""
        match_id = req.match_info["match_id"]
        m = self.matches.get(match_id)
        if m is None:
            return await self._api_cors(web.json_response({"error": "match not found"}, status=404))
        seats = {str(k): {"agent": v, "brain_type": (_brain_for(k).get("type","bot"))}
                 for k, v in m.world.mcp_seats.items()}
        return await self._api_cors(web.json_response({
            "match_id":   m.match_id,
            "ws_url":     f"ws://localhost:8083/ws/{m.match_id}",
            "phase":      m.world.phase,
            "tick":       m.world.tick,
            "winner":     m.world.winner,
            "tps":        m.tps,
            "seats":      seats,
            "created_at": m.created_at,
        }))

    async def _validate_api_key(self, key: str) -> "dict | None":
        """Call auth worker /validate. Returns {id, username} or None on failure."""
        if not AGANTS_AUTH_URL or not key:
            return None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{AGANTS_AUTH_URL.rstrip('/')}/validate",
                    json={"api_key": key},
                    headers={"X-Internal-Secret": AGANTS_AUTH_SECRET},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            print(f"⚠️  auth validate error: {e}")
        return None

    def _post_match_result(self, rec: dict, red_uid, blue_uid, winner_uid):
        """Fire-and-forget: POST completed match to auth worker."""
        if not AGANTS_AUTH_URL:
            return
        async def _send():
            try:
                async with aiohttp.ClientSession() as s:
                    await s.post(
                        f"{AGANTS_AUTH_URL.rstrip('/')}/match",
                        json={
                            "match_id":       rec["match_id"],
                            "red_user_id":    red_uid,
                            "blue_user_id":   blue_uid,
                            "winner_user_id": winner_uid,
                            "ticks":          rec["ticks"],
                            "ended_at":       int(rec["ended_at"]),
                            "result_path":    f"data/results/{rec['match_id']}.json",
                        },
                        headers={"X-Internal-Secret": AGANTS_AUTH_SECRET},
                        timeout=aiohttp.ClientTimeout(total=5),
                    )
            except Exception as e:
                print(f"⚠️  auth match post error: {e}")
        asyncio.create_task(_send())

    def _rotate_logs(self):
        """Delete oldest run_*.log files until logs/ is under LOG_MAX_MB."""
        log_dir = "logs"
        try:
            entries = [e for e in os.scandir(log_dir)
                       if e.name.startswith("run_") and e.name.endswith(".log")]
        except FileNotFoundError:
            return
        entries.sort(key=lambda e: e.stat().st_mtime)
        total = sum(e.stat().st_size for e in entries)
        limit = LOG_MAX_MB * 1024 * 1024
        while total > limit and len(entries) > 1:
            victim = entries.pop(0)
            total -= victim.stat().st_size
            os.remove(victim.path)
            print(f"🗑️  Rotated log {victim.name}")

    async def api_health(self, req):
        uptime = time.monotonic() - self._startup_time
        clients = sum(len(m.clients) for m in self.matches.values())
        mem_mb  = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB→MB on Linux
        match_info = []
        for m in self.matches.values():
            tt = m._tick_times
            if len(tt) >= 2:
                actual_tps = round((len(tt) - 1) / (tt[-1] - tt[0]), 2)
            else:
                actual_tps = None
            match_info.append({
                "match_id":   m.match_id,
                "phase":      m.world.phase,
                "tick":       m.world.tick,
                "tps_target": m.tps,
                "tps_actual": actual_tps,
            })
        return await self._api_cors(web.json_response({
            "status":          "ok",
            "version":         VERSION,
            "build":           BUILD,
            "uptime_s":        round(uptime, 1),
            "active_matches":  len(self.matches),
            "connected_clients": clients,
            "memory_mb":       round(mem_mb, 1),
            "matches":         match_info,
        }))

    async def run(self):
        app = web.Application()
        app.router.add_get("/", self.on_index)
        app.router.add_get("/game", self.on_game)
        app.router.add_get("/game/", self.on_game)
        app.router.add_get("/{page:(register|me|matches)\\.html}", self.on_static_html)
        app.router.add_get("/config.js", self.on_config_js)
        app.router.add_get("/ws", self.on_ws)
        app.router.add_get("/ws/{match_id}", self.on_ws_match)
        app.router.add_get("/health", self.api_health)
        # REST API — match management
        app.router.add_get( "/api/matches",              self.api_matches)
        app.router.add_post("/api/matches",              self.api_create_match)
        app.router.add_get( "/api/matches/{match_id}",   self.api_get_match)
        # REST API — legacy single-match routes (target default match)
        app.router.add_get("/api/tick",                          self.api_tick)
        app.router.add_get("/api/seats",                         self.api_seats)
        app.router.add_post("/api/seat/{colony_id}",             self.api_join_seat)
        app.router.add_delete("/api/seat/{colony_id}",           self.api_release_seat)
        app.router.add_post("/api/control",                      self.api_control)
        app.router.add_get("/api/state/{colony_id}",             self.api_state)
        app.router.add_get("/api/notifications/{colony_id}",     self.api_notifications)
        app.router.add_get("/api/intel_map/{colony_id}",         self.api_intel_map)
        app.router.add_get("/api/directive/{colony_id}",         self.api_directive)
        app.router.add_post("/api/directive/{colony_id}",        self.api_patch_directive)
        app.router.add_post("/api/command/{colony_id}",          self.api_command)
        app.router.add_get("/api/events/{colony_id}",            self.api_events)
        app.router.add_post("/api/feedback",                     self.api_feedback)
        app.router.add_post("/api/chat",                         self.api_chat)
        # REST API — per-match routes (same handlers, match_id resolved from path)
        app.router.add_post("/api/matches/{match_id}/seat/{colony_id}",             self.api_join_seat)
        app.router.add_delete("/api/matches/{match_id}/seat/{colony_id}",           self.api_release_seat)
        app.router.add_post("/api/matches/{match_id}/control",                      self.api_control)
        app.router.add_get("/api/matches/{match_id}/state/{colony_id}",             self.api_state)
        app.router.add_get("/api/matches/{match_id}/notifications/{colony_id}",     self.api_notifications)
        app.router.add_get("/api/matches/{match_id}/intel_map/{colony_id}",         self.api_intel_map)
        app.router.add_get("/api/matches/{match_id}/directive/{colony_id}",         self.api_directive)
        app.router.add_post("/api/matches/{match_id}/directive/{colony_id}",        self.api_patch_directive)
        app.router.add_post("/api/matches/{match_id}/command/{colony_id}",          self.api_command)
        app.router.add_get("/api/matches/{match_id}/events/{colony_id}",            self.api_events)
        # CORS preflight
        app.router.add_route("OPTIONS", "/api/{path_info:.*}", self.api_options)
        async def _on_shutdown(_app):
            for m in list(self.matches.values()):
                if m.world.phase in ("running", "paused") and m.world.winner is None:
                    self._save_match(m)
                    print(f"💾  Saved match {m.match_id} at tick {m.world.tick}")

        app.on_shutdown.append(_on_shutdown)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", 8083).start()
        red_type  = _brain_for(0)["type"]
        blue_type = _brain_for(1)["type"]
        print(f"🐜  Agants v{VERSION} ({BUILD}) — http://localhost:8083")
        print(f"   TPS={TPS} | LLM_INTERVAL={LLM_INTERVAL} | RED={red_type} | BLUE={blue_type}")
        print(f"   logs/ cap={LOG_MAX_MB} MB | health → http://localhost:8083/health")
        tunnel_log = os.path.join(os.path.dirname(__file__), "logs", "cloudflared.log")
        try:
            import subprocess
            tunnel_url = subprocess.check_output(
                ["grep", "-o", "https://[^ ]*trycloudflare.com", tunnel_log],
                stderr=subprocess.DEVNULL, text=True
            ).strip().splitlines()
            if tunnel_url:
                print(f"   tunnel → {tunnel_url[-1]}")
        except Exception:
            pass
        print(f"🔌  MCP REST API — http://localhost:8083/api/")
        self._start_match_tasks(self._m)
        await asyncio.Future()  # run forever; tasks are started above

if __name__ == "__main__":
    asyncio.run(Server().run())
