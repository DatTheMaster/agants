# Game constants — no env reads, no functions, no classes.
# All configuration that changes gameplay lives here.

VERSION = "0.1.0"

# ── Map ────────────────────────────────────────────────────────────────────────
MAP_W, MAP_H = 150, 100
MAP_NAME   = "The Crossing"
RED_SPAWN  = (14, 50)
BLUE_SPAWN = (136, 50)
TILE = 8

TERRITORY_DECAY = 60   # ticks without ant presence before tile reverts to neutral

# ── Terrain tile types ─────────────────────────────────────────────────────────
T_DIRT, T_LEAF, T_WATER, T_ROCK, T_NEST = range(5)

# ── Ant types / states ─────────────────────────────────────────────────────────
A_WORKER, A_SOLDIER, A_SCOUT, A_QUEEN = range(4)

S_IDLE, S_FORAGING, S_RETURNING, S_EXPLORING, S_FIGHTING, S_PATROLLING, S_RECRUITED, S_BUILDING = range(8)

# ── Vision ─────────────────────────────────────────────────────────────────────
VISION_RADIUS       = {0: 4, 1: 5, 2: 8, 3: 3}   # worker, soldier, scout, queen (base)
SCOUT_VISION_RADIUS = [8, 12, 16, 22]              # scout vision per tier

# ── Food ───────────────────────────────────────────────────────────────────────
FOOD_SOURCES   = 17   # 4H + 6A + 7F
FOOD_MAX_HOME      = 400
FOOD_MAX_APPROACH  = 800
FOOD_MAX           = FOOD_MAX_APPROACH   # legacy alias
FOOD_INIT_HOME_MIN = 200;  FOOD_INIT_HOME_MAX  = 350
FOOD_INIT_APPR_MIN = 300;  FOOD_INIT_APPR_MAX  = 600
FOOD_INIT_FRONT_MIN= 2000; FOOD_INIT_FRONT_MAX = 4000
FOOD_INIT_MIN  = FOOD_INIT_APPR_MIN   # legacy alias
FOOD_INIT_MAX  = FOOD_INIT_APPR_MAX
FOOD_PICK      = 20
FOOD_DELIVER   = 30
FOOD_NODE_WORKER_CAP = {"home": 4, "approach": 6, "frontline": 12}
FOOD_REGROW    = 0.1
FOOD_CONTESTED_MIN_DIST  = 28
FOOD_REGROW_CONTESTED    = 20.0
FOOD_REGROW_APPROACH     = 0.5

STALEMATE_TIMEOUT = 7200   # 2 hours — effectively never fires

# ── Corpses ────────────────────────────────────────────────────────────────────
CORPSE_FOOD  = [12, 25, 17, 0]   # worker / soldier / scout / queen
CORPSE_DECAY = 0.4

# ── Queen combat ───────────────────────────────────────────────────────────────
QUEEN_DMG = 35
QUEEN_CD  = 3
QUEEN_HP  = 900

# ── Soldier / worker / scout base stats ────────────────────────────────────────
SOLDIER_DMG = 22
SOLDIER_CD  = 4
SOLDIER_HP  = 200
WORKER_HP   = 55
SCOUT_HP    = 45

# ── Guard Post ─────────────────────────────────────────────────────────────────
GUARD_POST_COST  = 150
GUARD_POST_HP    = 300
GUARD_POST_DMG   = 18
GUARD_POST_CD    = 3
GUARD_POST_RANGE = 10
GUARD_POST_MAX   = 3

# ── Watchtower ─────────────────────────────────────────────────────────────────
WATCHTOWER_COST   = 80
WATCHTOWER_HP     = 150
WATCHTOWER_VISION = 12
WATCHTOWER_MAX    = 3

# ── Barracks ───────────────────────────────────────────────────────────────────
BARRACKS_COST       = 200
BARRACKS_HP         = 200
BARRACKS_SPAWN_TIME = 20
BARRACKS_MAX        = 2

# ── Wall ───────────────────────────────────────────────────────────────────────
WALL_COST = 25
WALL_HP   = 500
WALL_MAX  = 12

# ── Larder ─────────────────────────────────────────────────────────────────────
LARDER_COST   = 150
LARDER_HP     = 150
LARDER_MAX    = 2
LARDER_INCOME = 6   # food per tick (passive)

# ── Dirt ───────────────────────────────────────────────────────────────────────
DIRT_PICK         = 10
DIRT_DELIVER      = 8
DIRT_REGROW       = 0.8
DIRT_REGROW_FRONT = 2.0
DIRT_MAX          = 200
DIRT_FRONT_MAX    = 999999
DIRT_INIT_MIN     = 80
DIRT_INIT_MAX     = 160
DIRT_CAP          = 600

# ── Construction ───────────────────────────────────────────────────────────────
BUILD_WORK_REQUIRED = {"guard_post": 100, "watchtower": 60, "barracks": 150, "larder": 120, "wall": 25}
BUILD_WORKER_CAP    = 4
BUILD_RATE          = [1, 2, 3, 4]   # work/tick per worker, indexed by worker tier
BUILD_RANGE         = 2              # Manhattan distance counted as "at site"

# ── Upgrades ───────────────────────────────────────────────────────────────────
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
    "scout":   ["vision 8→12 tiles",  "vision 12→16 + double speed", "vision 16→22 tiles"],
    "soldier": ["+10 damage (→32)",   "+80 HP + cooldown 4→3",        "splash 40% to adjacent enemies"],
}

# ── Misc ───────────────────────────────────────────────────────────────────────
MAX_EVENTS       = 12
MEMORY_MAX_CHARS = 800
