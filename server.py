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
    Each ant costs UPKEEP food/tick. Starving colonies lose ants.
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

VERSION = "1.2"

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
- Workers gather food. Scouts explore and recruit workers to food sources. Soldiers fight.
- Food is the only resource. Negative income = ants starve and die.
- Soldier upkeep costs 3.3x a worker's. Big armies are expensive.
- Dead ants leave harvestable corpses — big battles give the winner a food surge.

UPKEEP COSTS (per tick):
  worker=0.03  soldier=0.10  scout=0.04  queen=0.05
  → 10 soldiers cost 1.0 food/tick; 10 workers cost 0.3 food/tick; 10 scouts cost 0.4 food/tick.
  Role ratio changes only affect FUTURE production — existing ants' upkeep is immediate.
  If you shift to 50% soldiers, income drops now (existing soldiers), not after new ones spawn.
  Budget for your CURRENT army composition, not the target one.

AVAILABLE COMMANDS (respond with a JSON object containing any subset):
  "roles": {{"worker": W, "scout": S, "soldier": M}}   — production ratios, must sum to 1.0
  "defense": "aggressive" | "balanced" | "defensive"
      aggressive = soldiers patrol 45-90 tiles toward enemy nest (push/attack)
      balanced   = soldiers patrol center territory (50/50)
      defensive  = soldiers stay 15-35 tiles from own nest (hold the line)
  "worker_cap": <int>   — stop making workers above this count; overflow → scouts/soldiers
  "rally_point": [x, y] — soldiers gather HERE and HOLD until you clear it
                          set to a staging tile 15-20 tiles short of enemy nest
                          clear (null) to unleash soldiers from the rally into assault
                          soldiers in rally will NOT advance until rally is cleared
  "expansion": [dx, dy] — scout/soldier expansion direction unit vector, e.g. [-1, 0] = west
  "priority_food": [x, y] — ALL idle workers march to this source (must be in known_food list)
  "buy_upgrade": "worker" | "scout" | "soldier" | true
      — queue a specific upgrade tree (or true = buy cheapest available)
      WORKER TREE (economic):
        W1   500 food → workers carry +8/trip (12→20)
        W2  2200 food → carry +12 more (→32/trip)
        W3  6000 food → carry +18 more (→50/trip) + loaded workers move faster
      SCOUT TREE (intel/logistics):
        S1   450 food → detection range 5→9 tiles, recruit cap 8→14 workers
        S2  2000 food → scouts explore AND return at double speed
        S3  5500 food → recruit cap 14→24, queen produces 25% faster
      SOLDIER TREE (combat):
        Sol1  600 food → +10 damage per hit (22→32)
        Sol2 2800 food → +80 HP (200→280), attack cooldown 4→3
        Sol3 7500 food → splash: 40% damage to all enemies adjacent to primary target
      Upgrade state shown as upgrades.worker/scout/soldier tiers in your state.
  "rally_release_at": <int> | null
      — auto-clear rally_point when this many soldiers are staged at the rally coordinate
        (within 4 tiles). Lets you pre-set the launch condition without manual clearing.
        Pair with rally_point: set point + release_at, then forget — soldiers advance automatically.
  "siege_priority": "queen" | null
      — when "queen": soldiers in siege range strongly prefer the enemy queen over nearby
        defenders (−8 effective distance bonus). Use when queen HP is low and defenders
        are tanking your DPS. null = normal targeting (kill defenders first, queen last).
  "build": [x, y]
      — construct a Guard Post at coordinate [x, y]. Guard Posts are stationary defensive
        towers. Cost: 500 food. Max 3 per colony. Stats: 300 HP, 18 dmg/shot, range 10
        tiles, 3-tick cooldown. Posts fire automatically at the nearest enemy in range.
        Enemy soldiers will target and destroy your posts (they are not immortal).
        Good placements: nest entrance chokepoint, key food node you control, forward staging.
        Coordinate must be passable terrain (not water or rock). Shown on minimap as 'T'.
  "formation": "column" | "wedge" | "spread"   (default: "wedge")
      — controls how tightly soldiers cluster when advancing in aggressive mode.
        column = single-file spike (0.06 rad spread) — punches through chokepoints and
                 guard post kill zones; high focus but no flanking
        wedge  = default (0.15 rad spread) — balanced advance, current behavior
        spread = wide fan (0.40 rad spread) — encircles defenders and overwhelms from
                 multiple angles; use when enemy queen is exposed and you have numerical advantage
  "attack_target": [x, y] | null
      — all soldiers advance continuously toward this coordinate, engaging any enemies
        encountered en route. Unlike rally_point they do NOT hold when they arrive —
        they keep fighting. Use to aim a direct assault at a specific tile (e.g., the
        exact enemy queen position, or a guard post cluster). Set to null to clear.
  "retreat": true | false
      — soldiers immediately fall back toward own nest. Use when outnumbered and trying
        to regroup, or when income is critical and you need to reduce combat attrition.
        Soldiers still fight enemies they run directly into. Set false to resume normal pathing.
  "freeze_economy": true | false
      — EMERGENCY ALL-IN: atomically sets roles={{worker:0, scout:0.05, soldier:0.95}}
        and worker_cap=0, stopping all new worker production immediately. Use when you
        have enough food and workers to sustain the army and need every spawn slot for
        soldiers. Set false to restore worker_cap=50 (then set roles manually).

MAP: 100×75 tiles. Nest positions are chosen during the placement phase at game start.
Use own_queen_pos and enemy_queen_pos from state for actual coordinates.
SIEGE MODE: When your soldiers reach within 12 tiles of the enemy nest they automatically
hunt the queen directly. `soldiers_in_siege` in your state tells you exactly how many
soldiers are already in siege range right now.

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
- Avoid setting scout ratio to 0. Scouts are your only source of enemy intel and food
  discovery. If you must cut production, 0.1 is the minimum safe value.
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

MAP: The minimap (shown each turn) uses 20×15 cells (each = 5×5 tiles).
  B=your nest  R=enemy nest  T=your Guard Post  t=enemy Guard Post
  f=known food  S=your soldiers (2+ in cell)
  E=enemy last-scouted position  ~=water  #=rock

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
    return _LLM_SYSTEM_TEMPLATE.format(MY=my_color, EN=enemy_color)

LLM_DEBRIEF_SYSTEM = """\
You are reviewing your performance as the strategic commander of an ant colony RTS.
Respond in free text — NOT JSON. Be specific: cite tick numbers, exact decisions, and
numbers from the stats. Brutal self-assessment is more useful than flattery.
"""

LLM_PLACEMENT_SYSTEM = """\
You are the strategic commander of an ant colony in Swarm Wars, choosing where to place \
your queen's starting nest before the game begins.

This is a one-time decision. Your nest must be in your assigned map half.
Workers forage outward from the nest — placement shapes your early economy and how \
quickly you can contest the frontline food nodes in the center.

FOOD NODE TIERS (shown on minimap):
  F = frontline (contested)  — inexhaustible (20/tick); both sides will fight for these
  a = approach               — medium regrow (5/tick); reward forward expansion
  h = home                   — safe early economy (2.5/tick); close to starting zones
  ~ = water (impassable)   # = rock (impassable)

PLACEMENT TRADEOFFS:
  Aggressive (near center): approach + frontline nodes reachable fast; queen more exposed
  Safe (far corner): home nodes nearby, deep defensive buffer; long march to midfield

ENEMY POSITION: Unknown. They are in the opposite half. Scouts will reveal them.

Respond ONLY with a JSON object — no markdown, no extra text:
{"x": <int>, "y": <int>, "reason": "<brief strategic note (logged for analysis)>"}
"""

MAP_W, MAP_H = 100, 75
TILE = 8
TPS = int(os.environ.get("TPS", "10"))

PHERO_MAX = 1.0
ALARM_FOLLOW_THRESH = 0.20  # soldiers only chase alarm that's this fresh/strong

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
S_IDLE, S_FORAGING, S_RETURNING, S_EXPLORING, S_FIGHTING, S_PATROLLING, S_RECRUITED = range(7)

# Upkeep per ant per tick (per type: worker, soldier, scout, queen)
UPKEEP = [0.03, 0.10, 0.04, 0.05]   # soldiers cost 3x workers — military is expensive

# Food
FOOD_SOURCES   = 20
FOOD_MAX       = 5000   # normal source cap (contested sources are effectively unlimited)
FOOD_INIT_MIN  = 2500   # bumped to give more starting food (upgrade costs are real)
FOOD_INIT_MAX  = 5000
FOOD_PICK      = 15     # how much one ant haul removes
FOOD_DELIVER   = 12     # how much one haul adds to colony food
FOOD_REGROW    = 2.5    # units per tick — was 1.5; faster to support upgrade economics
FOOD_CONTESTED_MIN_DIST = 28   # source is "contested" if both nests are at least this far
FOOD_REGROW_CONTESTED   = 20.0  # contested sources regenerate fast — effectively inexhaustible
FOOD_REGROW_APPROACH    = 5.0   # approach nodes: midfield expansion rewards

# BAR-style placement phase
PLACEMENT_TIMEOUT = 60   # seconds for placement decisions (LLM gets PLACEMENT_TIMEOUT-5)

# 3-lane strategic food layout — y-ranges for each lane, x-ranges by zone
LANE_NORTH      = (8,  22)
LANE_MID        = (30, 45)
LANE_SOUTH      = (52, 67)
FRONTLINE_X     = (42, 58)   # contested center — both sides fight for these
RED_APPROACH_X  = (20, 38)   # midfield rewards forward RED expansion
BLU_APPROACH_X  = (62, 80)   # midfield rewards forward BLUE expansion
HOME_X_RED      = (5,  22)   # safe early economy for RED
HOME_X_BLUE     = (78, 95)   # safe early economy for BLUE

# Corpse harvesting
CORPSE_FOOD  = [6, 12, 6, 60]  # food value on death: worker, soldier, scout, queen
CORPSE_DECAY = 0.4              # units lost per tick (~30 ticks for soldier, ~150 for queen)

# Queen combat
QUEEN_DMG = 35         # queen hits harder than soldiers
QUEEN_CD  = 3          # faster attack rate than soldiers

# Guard Post defensive structures
GUARD_POST_COST  = 500   # food to construct
GUARD_POST_HP    = 300   # structure hit points
GUARD_POST_DMG   = 18    # damage per shot
GUARD_POST_CD    = 3     # ticks between shots
GUARD_POST_RANGE = 10    # attack range in tiles (Manhattan)
GUARD_POST_MAX   = 3     # max guard posts per colony

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
    "scout":   ["detect 5→9, recruit 8→14", "double move speed",  "recruit 14→24, +25% spawn rate"],
    "soldier": ["+10 damage (→32)",   "+80 HP + cooldown 4→3",  "splash 40% to adjacent enemies"],
}

def _apply_upgrade_effects(c, unit_type, tier):
    """Update colony bonuses when unit_type reaches a new tier (1-3)."""
    if unit_type == "worker":
        c.carry_bonus = [0, 8, 20, 38][tier]
        c.worker_fast = (tier >= 3)
    elif unit_type == "scout":
        c.scout_detect  = [5, 9, 9, 18][tier]
        c.scout_recruit = [8, 14, 14, 24][tier]
        c.scout_fast    = (tier >= 2)
        c.spawn_mult    = 0.75 if tier >= 3 else 1.0
    elif unit_type == "soldier":
        c.dmg_bonus          = 10 if tier >= 1 else 0
        c.soldier_hp_bonus   = 80 if tier >= 2 else 0
        c.soldier_fast_cd    = SOLDIER_CD - (1 if tier >= 2 else 0)
        c.soldier_splash     = (tier >= 3)

# Pheromone
PHERO_EVAP     = 0.975  # trails fade in ~27s at 5 TPS; was 0.993 (~4 min)
FOLLOW_THRESH  = 0.03   # lower threshold — weaker trails still attract
FOLLOW_RAD_W   = 5      # worker follow radius (tiles)
FOLLOW_RAD_S   = 8      # soldier follow radius (tiles)

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

def gen_terrain():
    t = [[T_DIRT] * MAP_W for _ in range(MAP_H)]
    for _ in range(14):
        cx, cy = random.randint(5, MAP_W-6), random.randint(5, MAP_H-6)
        r = random.randint(2, 5)
        for dy in range(-r, r+1):
            for dx in range(-r, r+1):
                if dx*dx + dy*dy <= r*r:
                    x, y = cx+dx, cy+dy
                    if 0 <= x < MAP_W and 0 <= y < MAP_H:
                        t[y][x] = T_LEAF
    for _ in range(3):
        cx, cy = random.randint(12, MAP_W-12), random.randint(12, MAP_H-12)
        r = random.randint(2, 4)
        for dy in range(-r, r+1):
            for dx in range(-r, r+1):
                if dx*dx + dy*dy <= r*r:
                    x, y = cx+dx, cy+dy
                    if 0 <= x < MAP_W and 0 <= y < MAP_H:
                        t[y][x] = T_WATER
    for _ in range(30):
        x, y = random.randint(0, MAP_W-1), random.randint(0, MAP_H-1)
        if t[y][x] == T_DIRT:
            t[y][x] = T_ROCK
    return t

# ═══════════════════════════════════════════════════════════════════════════════
# Ant
# ═══════════════════════════════════════════════════════════════════════════════

class Ant:
    _id = 0
    def __init__(self, x, y, colony, ant_type):
        Ant._id += 1
        self.id = Ant._id
        self.x = x
        self.y = y
        self.colony = colony
        self.type = ant_type
        self.state = S_IDLE
        self.carrying = False
        self.hp = {A_WORKER: WORKER_HP, A_SOLDIER: SOLDIER_HP,
                   A_SCOUT: SCOUT_HP, A_QUEEN: QUEEN_HP}[ant_type]
        self.max_hp = self.hp
        self.prev_x = x
        self.prev_y = y
        self.tx = None
        self.ty = None
        self.cooldown = 0
        self.recruit_target = None  # (food_x, food_y) set by scout recruitment

# ═══════════════════════════════════════════════════════════════════════════════
# Colony — with LLM strategy hooks
# ═══════════════════════════════════════════════════════════════════════════════

class Colony:
    def __init__(self, cid, nx, ny):
        self.id = cid
        self.nx = nx
        self.ny = ny
        self.ants = []
        self.food = 400.0
        self.prod_timer = 0
        self.alive = True
        self.food_collected = 0   # lifetime stat
        self.ants_lost = 0        # lifetime stat

        # ── LLM-controllable strategy ──
        # Expansion biased toward center so colonies expand into each other's territory
        ex = 1 if nx < MAP_W // 2 else -1
        ey = 1 if ny < MAP_H // 2 else -1
        self.strategy = {
            "roles": {"worker": 0.55, "scout": 0.25, "soldier": 0.20},
            "expansion": (ex, ey),
            "defense": "aggressive",   # colonies push hard by default
            "priority_food": None,
            "rally_point": None,       # (x, y) staging coordinate for soldiers, or None
            "rally_release_at": None,  # int: auto-clear rally when N soldiers are staged
            "worker_cap": 50,          # bot default; LLM overrides; keeps pop balanced
            "siege_priority": None,    # "queen" | None — queen-focused targeting in siege
            "formation": "wedge",      # "column"|"wedge"|"spread" — soldier patrol spread
            "attack_target": None,     # [x, y] — soldiers advance here continuously (non-holding)
            "retreat": False,          # True — soldiers fall back toward own nest
        }
        self.build_queue = []      # [(x, y)] — pending guard post construction orders
        self.known_food = []       # list of (x, y) discovered by scouts
        self.events = deque(maxlen=MAX_EVENTS)  # recent events for LLM + dashboard
        # Fog of war: track last time a friendly ant was near enemy nest
        self.enemy_scouted_tick   = -9999  # tick when we last had eyes near enemy nest
        self.enemy_scouted_counts = [0, 0, 0, 0]  # [W, S, sc, Q] as of last scout
        self.log_queue = []        # drained each tick by RunLogger
        self.food_prev = self.food # for per-second income tracking
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

    def push_event(self, msg):
        self.events.appendleft(msg)
        self.log_queue.append(msg)

    def drain_events(self):
        ev = list(self.events)
        self.events.clear()
        return ev

    def set_strategy(self, s):
        """LLM sets colony strategy. Merges with current values."""
        if "roles" in s:
            total = sum(s["roles"].values())
            if total > 0:
                self.strategy["roles"] = {k: v/total for k, v in s["roles"].items()}
        if "expansion" in s:
            self.strategy["expansion"] = s["expansion"]
        if "defense" in s:
            self.strategy["defense"] = s["defense"]
        if "priority_food" in s:
            self.strategy["priority_food"] = s["priority_food"]
        if "rally_point" in s:
            self.strategy["rally_point"] = s["rally_point"]   # (x, y) or None to clear
        if "worker_cap" in s:
            self.strategy["worker_cap"] = s["worker_cap"]     # int or None to remove cap
        if "rally_release_at" in s:
            self.strategy["rally_release_at"] = s["rally_release_at"]  # int or None to clear
        if "siege_priority" in s:
            self.strategy["siege_priority"] = s["siege_priority"]      # "queen" or None
        if "buy_upgrade" in s:
            v = s["buy_upgrade"]
            if v == "worker":
                self.worker_upgrade_pending = True
            elif v == "scout":
                self.scout_upgrade_pending = True
            elif v == "soldier":
                self.soldier_upgrade_pending = True
            elif v is True:
                self._queue_cheapest_upgrade()
        if "build" in s:
            pos = s["build"]
            if pos is not None and isinstance(pos, (list, tuple)) and len(pos) == 2:
                x, y = int(pos[0]), int(pos[1])
                if [x, y] not in self.build_queue:
                    self.build_queue.append([x, y])
        if "formation" in s:
            v = s["formation"]
            if v in ("column", "wedge", "spread"):
                self.strategy["formation"] = v
        if "attack_target" in s:
            pos = s["attack_target"]
            if pos is None:
                self.strategy["attack_target"] = None
            elif isinstance(pos, (list, tuple)) and len(pos) == 2:
                self.strategy["attack_target"] = [int(pos[0]), int(pos[1])]
        if "retreat" in s:
            self.strategy["retreat"] = bool(s["retreat"]) if s["retreat"] is not None else False
        if "freeze_economy" in s:
            if s["freeze_economy"]:
                # Atomic all-in: halt worker production, redirect entirely to soldiers
                self.strategy["roles"] = {"worker": 0.0, "scout": 0.05, "soldier": 0.95}
                self.strategy["worker_cap"] = 0
            else:
                # Unfreeze: restore a sensible worker cap (LLM should set roles explicitly)
                self.strategy["worker_cap"] = 50

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
            return {"label": UPGRADE_LABELS[unit][tier],
                    "effect": UPGRADE_EFFECTS[unit][tier],
                    "cost": costs[tier]}
        return {
            # Own state
            "food": int(self.food),
            "income_per_s": round(self.income_smooth if self.income_history else self.income_per_s, 1),
            "workers": counts[0], "soldiers": counts[1],
            "scouts": counts[2], "queen_hp": queen.hp if queen else 0,
            "total": len(self.ants),
            "upgrades": {
                "worker":  {"tier": self.worker_tier,  "next": _upgrade_next("worker",  WORKER_UPGRADE_COSTS,  self.worker_tier)},
                "scout":   {"tier": self.scout_tier,   "next": _upgrade_next("scout",   SCOUT_UPGRADE_COSTS,   self.scout_tier)},
                "soldier": {"tier": self.soldier_tier, "next": _upgrade_next("soldier", SOLDIER_UPGRADE_COSTS, self.soldier_tier)},
            },
            "known_food": self.known_food[:10],
            "strategy": self.strategy,
            "food_collected": self.food_collected,
            "ants_lost": self.ants_lost,
            # Positional awareness
            "own_queen_pos":    own_queen_pos,
            "enemy_queen_pos":  enemy_queen_pos,
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
                "queen_hp": enemy_queen_hp,   # visible when under siege, otherwise 900
                "tiers": ([self.enemy.worker_tier, self.enemy.scout_tier, self.enemy.soldier_tier]
                          if (self.enemy and intel_status != "unknown") else None),
            },
        }

    def spawn_initial(self):
        self.ants.append(Ant(self.nx, self.ny, self.id, A_QUEEN))
        for _ in range(18):
            self.ants.append(Ant(self.nx+random.randint(-2,2), self.ny+random.randint(-2,2), self.id, A_WORKER))
        for _ in range(4):
            self.ants.append(Ant(self.nx+random.randint(-2,2), self.ny+random.randint(-2,2), self.id, A_SOLDIER))
        for _ in range(4):
            self.ants.append(Ant(self.nx+random.randint(-2,2), self.ny+random.randint(-2,2), self.id, A_SCOUT))

# ═══════════════════════════════════════════════════════════════════════════════
# LLM helpers
# ═══════════════════════════════════════════════════════════════════════════════

_MAP_COLS, _MAP_ROWS = 20, 15   # ASCII minimap grid size
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
    rally    = colony.strategy.get("rally_point")
    at_rally = defending = patrolling = forward = 0
    for ant in colony.ants:
        if ant.type != A_SOLDIER: continue
        d = abs(ant.x - colony.nx) + abs(ant.y - colony.ny)
        dist_to_enemy = abs(ant.x - colony.enemy.nx) + abs(ant.y - colony.enemy.ny)
        if rally and abs(ant.x - rally[0]) + abs(ant.y - rally[1]) <= 4:
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

    siege_line = (f"SIEGE: {siege} of your soldiers are within 12 tiles of enemy nest"
                  f" — queen is UNDER ATTACK" if siege > 0
                  else "SIEGE: 0 soldiers in enemy nest range")

    # Build fog-of-war enemy intel line
    ei = s["enemy"]
    intel = ei.get("intel", "unknown")
    qhp_note = " (900=unobserved)" if ei["queen_hp"] >= 900 else ""
    if intel == "unknown":
        enemy_line = (f"ENEMY ({enemy_color}): intel UNKNOWN — no recent scouts near enemy nest. "
                      f"pos={ene_qpos}  queen_hp={int(ei['queen_hp'])}{qhp_note}")
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
                      f"({freshness}){tier_str}  queen_hp={int(ei['queen_hp'])}{qhp_note} pos={ene_qpos}")

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

    income_line = f"FOOD: {s['food']}  INCOME: {s['income_per_s']:+.0f}/s net (5s avg, after upkeep)  UPGRADES: {upg_summary}"
    if s['income_per_s'] < -5 and s['food'] > 0:
        starve_s = int(s['food'] / abs(s['income_per_s']))
        income_line += f"  *** STARVE IN ~{starve_s}s ***"

    lines = [
        f"=== TICK {tick} ({tick//10//60:02d}:{tick//10%60:02d}) ===",
        income_line,
        f"ARMY: {s['workers']}W {s['soldiers']}S {s['scouts']}sc  total={s['total']}",
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
        f"ACTIVE FOOD SOURCES: {s.get('active_food_sources', '?')} known",
    ]
    nx, ny = colony.nx, colony.ny
    food_with_dist = [
        f"({fx},{fy}) d={abs(fx-nx)+abs(fy-ny)}"
        for fx, fy in s['known_food'][:8]
    ]
    lines += [
        f"KNOWN FOOD SOURCES: {', '.join(food_with_dist) if food_with_dist else 'none yet'}",
    ]
    if world:
        own_posts = [st for st in world.structures if st["colony"] == colony.id]
        enemy_posts = [st for st in world.structures if st["colony"] != colony.id]
        post_limit = f"{len(own_posts)}/{GUARD_POST_MAX}"
        if own_posts:
            own_str = ", ".join(f"({st['x']},{st['y']}) HP={st['hp']}/{st['max_hp']}" for st in own_posts)
        else:
            own_str = "none"
        if enemy_posts:
            enemy_str = ", ".join(f"({st['x']},{st['y']}) HP={st['hp']}/{st['max_hp']}" for st in enemy_posts)
        else:
            enemy_str = "none"
        lines.append(f"GUARD POSTS: yours={own_str} [{post_limit}]  enemy={enemy_str}"
                     f"  (build cost 500♦, max {GUARD_POST_MAX}, shown as T/t on minimap)")
    if upg_available:
        lines.append(f"AVAILABLE UPGRADES (buy_upgrade: \"worker\"|\"scout\"|\"soldier\"|true):")
        for ua in upg_available:
            lines.append(f"  {ua}")
    lines += ["", "CURRENT STRATEGY:"]
    strat = s["strategy"]
    lines.append(f"  roles={strat['roles']}  defense={strat['defense']}")
    rally_pt   = strat.get('rally_point')
    release_at = strat.get('rally_release_at')
    if rally_pt:
        staged = sum(1 for a in colony.ants
                     if a.type == A_SOLDIER
                     and abs(a.x - rally_pt[0]) + abs(a.y - rally_pt[1]) <= 4)
        if release_at:
            rally_str = f"{tuple(rally_pt)} [{staged} staged → release at {release_at}]"
        else:
            rally_str = f"{tuple(rally_pt)} [{staged} staged]"
    else:
        rally_str = str(rally_pt)
    lines.append(f"  worker_cap={strat.get('worker_cap')}  "
                 f"rally_point={rally_str}  "
                 f"rally_release_at={release_at}")
    lines.append(f"  expansion={strat.get('expansion')}  "
                 f"priority_food={strat.get('priority_food')}  "
                 f"siege_priority={strat.get('siege_priority')}")
    # New levers — only show when non-default to keep prompt compact
    extra = []
    if strat.get("formation", "wedge") != "wedge":
        extra.append(f"formation={strat['formation']}")
    if strat.get("attack_target"):
        extra.append(f"attack_target={tuple(strat['attack_target'])}")
    if strat.get("retreat"):
        extra.append("retreat=True  ← soldiers falling back to nest")
    if extra:
        lines.append("  " + "  ".join(extra))

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
            # Normalize: convert lists to tuples for map coordinates
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
            f.write(f"upkeep W={UPKEEP[0]} SO={UPKEEP[1]} SC={UPKEEP[2]} Q={UPKEEP[3]}/tick | "
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
            c.income_per_s = c.food - c.food_prev
            c.food_prev = c.food
            c.income_history.append(c.income_per_s)
            c.income_smooth = sum(c.income_history) / len(c.income_history)
            pop = len(c.ants)
            if pop > c.peak_pop:
                c.peak_pop = pop
                c.peak_pop_tick = w.tick

        # Macro: income crossed negative
        for i, c in enumerate(w.colonies):
            if c.income_per_s < -15 and not c.income_neg_warned:
                c.income_neg_warned = True
                lines.append(f"  ★ {self.NAMES[i]} INCOME NEGATIVE "
                              f"({c.income_per_s:+.0f}/s) — starvation risk")
            elif c.income_per_s > 10 and c.income_neg_warned:
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
        self.pheros = [[[0.0]*MAP_W for _ in range(MAP_H)] for _ in range(4)]
        self.foods = []
        self.corpses = []    # [{"x", "y", "amt"}] — harvestable by workers
        self.structures = [] # [{"x","y","colony","hp","max_hp","cd"}] — guard posts
        self.colonies = []
        self.winner = None    # None | 0 | 1 | "draw"
        self.logger = None
        self._llm_stats_list = [None, None]  # per-colony stats; set by Server after each LLM call
        self.phase = "placement"  # "placement" | "running"
        self._place_strategic_food()

    def _place_strategic_food(self):
        """Generate 3-lane strategic food layout (BAR-inspired) before placement."""
        kinds = ["seeds", "beetle", "leaf", "honeydew"]
        placed = []

        def try_node(x_range, y_range, tier, regrow, max_amt, attempts=80):
            for _ in range(attempts):
                x = random.randint(x_range[0], x_range[1])
                y = random.randint(y_range[0], y_range[1])
                if (self.terrain[y][x] not in (T_WATER, T_ROCK)
                        and all(abs(x-n["x"])+abs(y-n["y"]) > 8 for n in placed)):
                    placed.append({
                        "x": x, "y": y,
                        "amt": float(random.randint(FOOD_INIT_MIN, FOOD_INIT_MAX)),
                        "max": float(max_amt),
                        "regrow": regrow,
                        "kind": random.choice(kinds),
                        "contested": (tier == "frontline"),
                        "tier": tier,
                    })
                    return True
            return False

        # 1 frontline node per lane + 1 bonus center node (4 total)
        for yr in [LANE_NORTH, LANE_MID, LANE_SOUTH]:
            try_node(FRONTLINE_X, yr, "frontline", FOOD_REGROW_CONTESTED, 999999)
        try_node((44, 56), (28, 47), "frontline", FOOD_REGROW_CONTESTED, 999999)

        # 1 RED approach + 1 BLUE approach per lane (6 total)
        for yr in [LANE_NORTH, LANE_MID, LANE_SOUTH]:
            try_node(RED_APPROACH_X, yr, "approach", FOOD_REGROW_APPROACH, FOOD_MAX)
            try_node(BLU_APPROACH_X, yr, "approach", FOOD_REGROW_APPROACH, FOOD_MAX)

        # 1 RED home + 1 BLUE home per lane (6 total)
        for yr in [LANE_NORTH, LANE_MID, LANE_SOUTH]:
            try_node(HOME_X_RED,  yr, "home", FOOD_REGROW, FOOD_MAX)
            try_node(HOME_X_BLUE, yr, "home", FOOD_REGROW, FOOD_MAX)

        self.foods = placed

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
            self.colonies.append(c)
        self.colonies[0].enemy = self.colonies[1]
        self.colonies[1].enemy = self.colonies[0]
        self.start_time = time.time()
        self.phase = "running"
        self.logger = RunLogger(self)

    def _valid_placement(self, x, y, half):
        """True if (x,y) is a valid starting position for the given half."""
        # Hard minimum: 12 tiles from center ensures colonies can't start adjacent
        if half == "red"  and x >= MAP_W // 2 - 12: return False
        if half == "blue" and x <  MAP_W // 2 + 12: return False
        if not (6 <= x < MAP_W-6 and 6 <= y < MAP_H-6): return False
        if self.terrain[y][x] in (T_WATER, T_ROCK): return False
        passable = sum(
            1 for ddx in range(-2, 3) for ddy in range(-2, 3)
            if (ddx, ddy) != (0, 0) and 0 <= x+ddx < MAP_W and 0 <= y+ddy < MAP_H
            and self.terrain[y+ddy][x+ddx] not in (T_WATER, T_ROCK)
        )
        if passable < 15: return False
        return any(abs(x-f["x"])+abs(y-f["y"]) <= 50 for f in self.foods)

    def _score_placement(self, x, y):
        """Higher score = better strategic position."""
        score = 0.0
        for f in self.foods:
            d = abs(x-f["x"]) + abs(y-f["y"])
            # Frontline nodes are contested prizes to fight FOR, not start next to.
            # Approach nodes (midfield) are the key early expansion targets.
            mult = {"frontline": 0.8, "approach": 2.0, "home": 1.4}.get(f.get("tier","home"), 1.0)
            if d <= 15: score += 25 * mult
            elif d <= 28: score += 10 * mult
            elif d <= 45: score += 3  * mult
        for ddx in range(-4, 5):
            for ddy in range(-4, 5):
                nx, ny = x+ddx, y+ddy
                if 0 <= nx < MAP_W and 0 <= ny < MAP_H:
                    if self.terrain[ny][nx] in (T_WATER, T_ROCK):
                        score -= 5
        score -= abs(y - MAP_H//2) * 0.15
        return score

    def _best_placement(self, half):
        """Scan the assigned half and return the highest-scoring valid position."""
        xs = range(6, MAP_W//2) if half == "red" else range(MAP_W//2, MAP_W-6)
        best_pos, best_score = None, -9999.0
        for x in xs:
            for y in range(6, MAP_H-6):
                if self._valid_placement(x, y, half):
                    s = self._score_placement(x, y)
                    if s > best_score:
                        best_score = s
                        best_pos = (x, y)
        if best_pos is None:
            return (15, MAP_H//2) if half == "red" else (MAP_W-16, MAP_H//2)
        return best_pos

    # ── Main Tick ──

    def step(self):
        if self.phase == "placement":
            return   # frozen until finalize_placement() is called
        if self.winner is not None:
            return   # frozen after game over

        self.tick += 1

        # Evaporate pheromones
        for layer in self.pheros:
            for y in range(MAP_H):
                row = layer[y]
                for x in range(MAP_W):
                    row[x] *= PHERO_EVAP
                    if row[x] < 0.001: row[x] = 0.0

        # Decay corpses
        self.corpses = [c for c in self.corpses if c["amt"] > CORPSE_DECAY]
        for c in self.corpses:
            c["amt"] -= CORPSE_DECAY

        # Process guard post build orders
        for c in self.colonies:
            if not c.alive or not c.build_queue: continue
            x, y = c.build_queue.pop(0)
            own_count = sum(1 for st in self.structures if st["colony"] == c.id)
            if (c.food >= GUARD_POST_COST
                    and own_count < GUARD_POST_MAX
                    and self._passable(x, y)
                    and not any(st["x"] == x and st["y"] == y for st in self.structures)):
                c.food -= GUARD_POST_COST
                self.structures.append({"x": x, "y": y, "colony": c.id,
                                        "hp": GUARD_POST_HP, "max_hp": GUARD_POST_HP, "cd": 0})
                c.push_event(f"★ Guard Post built at ({x},{y})! ({own_count+1}/{GUARD_POST_MAX})")

        # Update ants (queens included — _behavior_queen handles queen defense)
        for c in self.colonies:
            if not c.alive: continue
            for ant in list(c.ants):
                self._update_ant(ant)

        # Guard post attacks — fire at nearest enemy in range each tick
        for struct in self.structures:
            if struct["cd"] > 0:
                struct["cd"] -= 1
                continue
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

        # Colony production & upkeep
        for c in self.colonies:
            if not c.alive: continue

            c.food -= sum(UPKEEP[a.type] for a in c.ants)

            # Starvation: lose ONE ant per tick when food deeply negative
            if c.food < -50:
                non_queens = [a for a in c.ants if a.type != A_QUEEN]
                if non_queens:
                    victim = random.choice(non_queens)
                    c.push_event(f"starved: lost a {['worker','soldier','scout'][victim.type]}")
                    self._kill(victim)
                else:
                    # Only queen left — she starves the moment food goes negative
                    # (a queen cannot forage or recover alone)
                    if c.food < -55:
                        c.push_event("queen starved — colony extinct")
                        c.ants.clear()
                        c.alive = False

            # Production — rate scales with food surplus, capped by spawn_mult (tier 3)
            c.prod_timer += 1
            base_interval = max(3, 22 - int(c.food / 50))
            interval = max(2, int(base_interval * c.spawn_mult))
            if c.prod_timer >= interval and c.food >= 3:
                c.prod_timer = 0
                c.food -= 3
                queen = next((a for a in c.ants if a.type == A_QUEEN), None)
                if queen:
                    r = random.random()
                    roles = c.strategy["roles"]
                    wshare  = roles.get("worker", 0.55)
                    sshare  = roles.get("scout",  0.25)
                    solshare = roles.get("soldier", 0.20)
                    worker_cap = c.strategy.get("worker_cap")
                    n_workers = sum(1 for a in c.ants if a.type == A_WORKER)
                    worker_capped = worker_cap is not None and n_workers >= worker_cap
                    if worker_capped:
                        # Redistribute worker share to scout/soldier in their relative ratio
                        non_w = sshare + solshare
                        t = A_SCOUT if (r < sshare / max(non_w, 0.01)) else A_SOLDIER
                    elif r < wshare:
                        t = A_WORKER
                    elif r < wshare + sshare:
                        t = A_SCOUT
                    else:
                        t = A_SOLDIER
                    new_ant = Ant(queen.x+random.randint(-2,2),
                                  queen.y+random.randint(-2,2), c.id, t)
                    if t == A_SOLDIER and c.soldier_hp_bonus > 0:
                        new_ant.hp     += c.soldier_hp_bonus
                        new_ant.max_hp += c.soldier_hp_bonus
                    c.ants.append(new_ant)

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
                bot_ready = (c.food >= cost * buf
                             and (c.income_per_s >= 0 or c.food >= cost * buf * 1.4))
                if (pending or bot_ready) and c.food >= cost:
                    c.food -= cost
                    setattr(c, f"{unit_type}_upgrade_pending", False)
                    new_tier = cur_tier + 1
                    setattr(c, f"{unit_type}_tier", new_tier)
                    _apply_upgrade_effects(c, unit_type, new_tier)
                    label  = UPGRADE_LABELS[unit_type][cur_tier]
                    effect = UPGRADE_EFFECTS[unit_type][cur_tier]
                    c.push_event(f"★ {unit_type.upper()} T{new_tier} {label}: {effect}")

        # Food regrowth — per-source rate (contested mid-map sources regrow much faster)
        for f in self.foods:
            if f["amt"] < f["max"]:
                f["amt"] = min(f["max"], f["amt"] + f.get("regrow", FOOD_REGROW))

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

    # ── Ant Behavior ──

    def _update_ant(self, ant):
        ant.prev_x, ant.prev_y = ant.x, ant.y
        if ant.cooldown > 0: ant.cooldown -= 1
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
                    c.food += FOOD_DELIVER + c.carry_bonus
                    c.food_collected += FOOD_DELIVER + c.carry_bonus
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
                    ant.state = S_RETURNING
                    self._dep(ant.x, ant.y, 0, 1.0)
                else:
                    self._move_to(ant, fx, fy, 0)
                    self._dep(ant.x, ant.y, 0, 0.6)
            return

        # NOT RECRUITED: carrying → return home
        if ant.carrying:
            if abs(ant.x-c.nx) <= 2 and abs(ant.y-c.ny) <= 2:
                ant.carrying = False
                c.food += FOOD_DELIVER + c.carry_bonus
                c.food_collected += FOOD_DELIVER + c.carry_bonus
                ant.state = S_IDLE
                self._dep(ant.x, ant.y, 0, 1.0)
            else:
                self._move_to(ant, c.nx, c.ny, 0)
                if c.worker_fast:
                    self._move_to(ant, c.nx, c.ny, 0)   # Worker T3: fast return while loaded
                self._dep(ant.x, ant.y, 0, 0.9)
            return

        # Check if food or corpse is directly reachable
        for corp in self.corpses:
            if abs(ant.x - corp["x"]) + abs(ant.y - corp["y"]) <= 2 and corp["amt"] >= 1:
                corp["amt"] -= FOOD_PICK
                if corp["amt"] <= 0: self.corpses.remove(corp)
                ant.carrying = True
                ant.state = S_RETURNING
                self._dep(ant.x, ant.y, 0, 1.0)
                return
        f = self._food_nearby(ant.x, ant.y, 2)
        if f and f["amt"] > 10:
            f["amt"] -= FOOD_PICK
            if f["amt"] <= 0: self.foods.remove(f)
            ant.carrying = True
            ant.state = S_RETURNING
            self._dep(ant.x, ant.y, 0, 1.0)
            return

        # Navigate outbound: prefer safe-side corpses, then known food locations
        # Only target corpses closer to own nest than enemy nest (avoid walking into combat)
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
        if c.known_food:
            pf = c.strategy.get("priority_food")
            if pf and pf in c.known_food:
                target = pf
            else:
                target = random.choice(c.known_food)
            ant.state = S_FORAGING
            self._move_to(ant, target[0], target[1], 0)
            return

        # No known food yet — wander outward from nest
        ant.state = S_IDLE
        self._wander(ant)

    def _behavior_scout(self, ant):
        c = self.colonies[ant.colony]

        # Carrying food: rush home (scouts are fast — single extra step), recruit workers on arrival
        if ant.carrying:
            if abs(ant.x-c.nx) <= 2 and abs(ant.y-c.ny) <= 2:
                ant.carrying = False
                c.food += FOOD_DELIVER + c.carry_bonus
                c.food_collected += FOOD_DELIVER + c.carry_bonus
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
            self._dep(ant.x, ant.y, 0, 1.0)
            return

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
                ex, ey = c.strategy["expansion"]
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
        self._dep(ant.x, ant.y, 3, 0.3)

    def _behavior_soldier(self, ant):
        c = self.colonies[ant.colony]
        # Siege mode: near enemy nest, hunt queen directly instead of mopping up workers
        in_siege = (c.enemy is not None
                    and abs(ant.x - c.enemy.nx) + abs(ant.y - c.enemy.ny) <= 12)
        queen_focus = c.strategy.get("siege_priority") == "queen"
        enemy = self._nearest_enemy(ant, 15, siege=in_siege, queen_focus=queen_focus)
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
                        ec = self.colonies[enemy.colony]
                        ec.push_event(f"★ QUEEN UNDER ATTACK! HP: {old_hp}→{new_hp} (dealt {dmg})")
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
            if c.strategy.get("retreat") and not in_siege:
                ant.state = S_PATROLLING
                self._move_to(ant, c.nx, c.ny, 2)
                self._dep(ant.x, ant.y, 2, 0.3)
                return

            # Rally point takes priority over alarm pheromone — soldiers with staging orders
            # march through active combat zones to the staging point, fighting only direct threats.
            rally = c.strategy.get("rally_point")
            if rally and not in_siege:
                rx, ry = rally
                if abs(ant.x - rx) + abs(ant.y - ry) > 4:
                    ant.state = S_PATROLLING
                    self._move_to(ant, rx, ry, 2)
                    self._dep(ant.x, ant.y, 2, 0.3)
                else:
                    # At rally — hold position, deposit territory marker
                    ant.state = S_PATROLLING
                    self._dep(ant.x, ant.y, 2, 0.3)
                    # Auto-release when rally_release_at threshold is met
                    release_n = c.strategy.get("rally_release_at")
                    if release_n:
                        staged = sum(1 for a in c.ants
                                     if a.type == A_SOLDIER
                                     and abs(a.x - rx) + abs(a.y - ry) <= 4)
                        if staged >= release_n:
                            c.strategy["rally_point"] = None
                            c.strategy["rally_release_at"] = None
                            c.push_event(f"★ RALLY AUTO-RELEASED — {staged} soldiers staged, advancing!")
                return

            # ATTACK_TARGET: continuous advance toward a specific coordinate — never hold.
            # Soldiers keep pushing toward the target, engaging any enemies encountered en route.
            attack_tgt = c.strategy.get("attack_target")
            if attack_tgt and not in_siege:
                ax, ay = attack_tgt
                ant.state = S_PATROLLING
                self._move_to(ant, ax, ay, 2)
                self._dep(ant.x, ant.y, 2, 0.3)
                return

            # No rally — follow fresh alarm pheromone toward active combat.
            # Do NOT re-deposit alarm here: that keeps stale pools alive and traps soldiers.
            if self._follow(ant, 1, radius=FOLLOW_RAD_S, threshold=ALARM_FOLLOW_THRESH):
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
                        if nearest_struct["hp"] <= 0:
                            ec = self.colonies[nearest_struct["colony"]]
                            ec.push_event(f"★ Guard Post at ({nearest_struct['x']},{nearest_struct['y']}) DESTROYED!")
                            c.push_event(f"Destroyed enemy Guard Post at ({nearest_struct['x']},{nearest_struct['y']})!")
                            self.structures.remove(nearest_struct)
                    return
                if ant.tx is None or random.random() < 0.03:
                    defense = c.strategy["defense"]
                    formation = c.strategy.get("formation", "wedge")
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
                        ex, ey = c.strategy["expansion"]
                        a = math.atan2(ey, ex) + random.gauss(0, 1.0)
                        d = random.randint(15, 35)
                    else:
                        # Balanced: patrol toward center with moderate spread
                        ex, ey = c.strategy["expansion"]
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
        return self.terrain[y][x] not in (T_WATER, T_ROCK)

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
        if threshold is None: threshold = FOLLOW_THRESH
        best, best_v = (ant.x, ant.y), -1
        for dx in range(-radius, radius+1):
            for dy in range(-radius, radius+1):
                if dx == 0 and dy == 0: continue
                nx, ny = ant.x+dx, ant.y+dy
                if not self._passable(nx, ny): continue
                v = self._get_p(nx, ny, layer)
                dist = max(1, abs(dx)+abs(dy))
                v = v / dist
                if nx == ant.prev_x and ny == ant.prev_y: v *= 0.3
                if v > best_v: best_v = v; best = (nx, ny)
        if best_v > threshold:
            ant.x, ant.y = best; return True
        return False

    # ── Queries ──

    def _dep(self, x, y, layer, val):
        if 0 <= x < MAP_W and 0 <= y < MAP_H:
            self.pheros[layer][y][x] = min(PHERO_MAX, self.pheros[layer][y][x] + val)

    def _get_p(self, x, y, layer):
        if 0 <= x < MAP_W and 0 <= y < MAP_H: return self.pheros[layer][y][x]
        return 0

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
            cols.append([c.id, c.nx, c.ny, int(c.food), counts,
                         c.strategy, c.known_food[:10],
                         list(c.events), c.food_collected, c.ants_lost, int(c.alive),
                         [c.worker_tier, c.scout_tier, c.soldier_tier],
                         round(c.income_per_s, 1)])
        names = ["f", "a", "t", "s"]
        ph = {}
        for i, n in enumerate(names):
            flat = []
            layer = self.pheros[i]
            for y in range(MAP_H):
                for x in range(MAP_W):
                    flat.append(int(layer[y][x]*255))
            ph[n] = flat
        structs = [[st["x"], st["y"], st["colony"], st["hp"], st["max_hp"]]
                   for st in self.structures]
        return {
            "tick": self.tick,
            "phase": self.phase,
            "elapsed_s": round(time.time() - self.start_time, 1) if self.start_time else 0,
            "ants": ants, "food": foods,
            "corpses": corpses,
            "structures": structs,
            "colonies": cols, "phero": ph,
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
        # Send new terrain immediately so client can render map during placement
        await self._broadcast(self._make_init_msg())
        await self._run_placement_phase()
        print("🔄  new game started")

    async def _run_placement_phase(self):
        """Orchestrate nest placement for both colonies, then start the game."""
        world = self.world
        print(f"\n{'═'*70}")
        print(f"🗺️  PLACEMENT PHASE — evaluating the battlefield...")
        print(f"{'═'*70}")

        food_data = [[f["x"], f["y"], int(f["amt"]), f["kind"], f.get("tier","home")]
                     for f in world.foods]
        self._placement_food    = food_data
        self._placement_updates = []
        self._placement_start_t = time.monotonic()
        await self._broadcast(json.dumps({
            "type": "placement_phase",
            "food": food_data,
            "timeout": PLACEMENT_TIMEOUT,
        }))

        async def _place_colony(colony_id):
            half   = "red"  if colony_id == 0 else "blue"
            name   = "RED"  if colony_id == 0 else "BLUE"
            brain  = _brain_for(colony_id)
            errors = []   # collect for run log

            pos    = None
            reason = ""

            if brain["type"] == "llm" and brain.get("api_key"):
                try:
                    import openai as _openai
                    client   = _openai.AsyncOpenAI(base_url=brain["base_url"],
                                                    api_key=brain["api_key"])
                    prompt_t = build_placement_prompt(world, half)
                    t0       = time.monotonic()
                    resp = await asyncio.wait_for(
                        client.chat.completions.create(
                            model=brain["model"],
                            messages=[
                                {"role": "system", "content": LLM_PLACEMENT_SYSTEM},
                                {"role": "user",   "content": prompt_t},
                            ],
                            temperature=0.7,
                        ),
                        timeout=PLACEMENT_TIMEOUT - 5,
                    )
                    elapsed = time.monotonic() - t0
                    raw = resp.choices[0].message.content or ""
                    think_m = re.search(r"<think>(.*?)</think>", raw, re.DOTALL)
                    reasoning = think_m.group(1).strip() if think_m else ""
                    if think_m: raw = raw[think_m.end():]
                    raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
                    # Extract outermost JSON object — find/rfind handles } inside string values
                    js_start, js_end = raw.find("{"), raw.rfind("}")
                    if js_start != -1 and js_end > js_start:
                        d = json.loads(raw[js_start:js_end+1])
                        px, py = int(d.get("x", 0)), int(d.get("y", 0))
                        reason = d.get("reason", "")
                        if world._valid_placement(px, py, half):
                            pos = (px, py)
                            sc  = world._score_placement(px, py)
                            print(f"  {name} (LLM) → ({px:3d},{py:3d})  score={sc:.0f}"
                                  f"  in {elapsed:.1f}s — {reason}")
                            if reasoning:
                                print(f"  [THINK] {reasoning[:400]}")
                        else:
                            msg = f"{name} LLM chose invalid pos ({px},{py}) [half={half}] — bot fallback. reason='{reason}'"
                            print(f"  {msg}")
                            errors.append(msg)
                    else:
                        msg = f"{name} LLM response had no JSON — bot fallback. raw='{raw[:200]}'"
                        print(f"  {msg}")
                        errors.append(msg)
                except asyncio.TimeoutError:
                    msg = f"{name} LLM placement timed out after {PLACEMENT_TIMEOUT-5}s — bot fallback"
                    print(f"  {msg}")
                    errors.append(msg)
                except Exception as e:
                    msg = f"{name} LLM placement error [{type(e).__name__}]: {e} — bot fallback"
                    print(f"  {msg}")
                    errors.append(msg)

            if pos is None:
                pos    = world._best_placement(half)
                sc     = world._score_placement(*pos)
                reason = f"bot heuristic (score={sc:.0f})" if brain["type"] == "bot" else \
                         f"bot fallback (score={sc:.0f})"
                lbl    = "bot" if brain["type"] == "bot" else "LLM-fallback"
                print(f"  {name} ({lbl}) → ({pos[0]:3d},{pos[1]:3d})  score={sc:.0f}")

            score = world._score_placement(*pos)
            ltype = "LLM" if brain["type"] == "llm" else "bot"
            upd = {"type": "placement_update", "colony": colony_id,
                   "pos": list(pos), "label": f"{name} ({ltype})",
                   "score": round(score, 1)}
            self._placement_updates.append(upd)
            await self._broadcast(json.dumps(upd))
            return pos, reason, errors

        (red_pos, red_reason, red_errs), (blue_pos, blue_reason, blue_errs) = await asyncio.gather(
            _place_colony(0), _place_colony(1)
        )
        all_placement_errors = red_errs + blue_errs

        # Both sides placed — tell clients so they can collapse the countdown immediately
        await self._broadcast(json.dumps({"type": "placement_ready"}))
        await asyncio.sleep(1.0)

        world.finalize_placement(red_pos, blue_pos)
        world.logger.log_placement(red_pos, red_reason, blue_pos, blue_reason,
                                   errors=all_placement_errors or None)
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

        if income < -30 or (food < 150 and workers < 20):
            # Emergency: hemorrhaging — dump everything into workers
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

        # Rally-and-release: when 18+ soldiers ready, stage at midfield then assault
        rally_update = {}
        if soldiers >= 18 and food > 800 and c.enemy and c.alive:
            current_rally = c.strategy.get("rally_point")
            if current_rally is None:
                # Stage at a point 20 tiles from enemy nest
                mx = (c.nx + c.enemy.nx) // 2
                my = (c.ny + c.enemy.ny) // 2
                rally_update = {"rally_point": [mx, my], "rally_release_at": 15,
                                "siege_priority": "queen"}
        elif soldiers < 8 and c.strategy.get("rally_point"):
            # Lost too many — clear rally and regroup
            rally_update = {"rally_point": None, "rally_release_at": None, "siege_priority": None}

        c.set_strategy({**{"roles": roles, "worker_cap": cap, "defense": defense}, **rally_update})

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
            if income >= 0 and food >= cost * buf:
                setattr(c, f"{unit_type}_upgrade_pending", True)

    async def tick_loop(self):
        bot_last_tick = 0
        loop = asyncio.get_event_loop()
        while True:
            t0 = time.monotonic()
            # Drain queued strategy updates before stepping (main-thread, safe)
            while self._pending_strategies:
                cid, strat = self._pending_strategies.popleft()
                if self.world.colonies[cid].alive:
                    self.world.colonies[cid].set_strategy(strat)
            # Bot decisions (tick-based, still safe here)
            if self.world.phase == "running" and self.world.winner is None:
                if self.world.tick - bot_last_tick >= LLM_INTERVAL:
                    bot_last_tick = self.world.tick
                    for cid in (0, 1):
                        if _brain_for(cid)["type"] == "bot":
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
            # Replay placement state for clients that connect mid-phase
            if self.world.phase == "placement" and self._placement_food:
                elapsed   = time.monotonic() - (self._placement_start_t or time.monotonic())
                remaining = max(1, PLACEMENT_TIMEOUT - elapsed)
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
                    except Exception:
                        pass
        finally:
            self.clients.discard(ws)
        return ws

    async def run(self):
        app = web.Application()
        app.router.add_get("/", self.on_index)
        app.router.add_get("/ws", self.on_ws)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", 8083).start()
        print("🐝  Swarm Wars — http://localhost:8083")
        asyncio.create_task(self.llm_loop_for(0))
        asyncio.create_task(self.llm_loop_for(1))
        await self._run_placement_phase()   # blocks until both sides placed
        await self.tick_loop()

if __name__ == "__main__":
    asyncio.run(Server().run())
