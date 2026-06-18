import heapq
import math
import random
import time

from engine.constants import (
    MAP_W, MAP_H, TILE, TERRITORY_DECAY,
    T_DIRT, T_LEAF, T_WATER, T_ROCK, T_NEST,
    A_WORKER, A_SOLDIER, A_SCOUT, A_QUEEN,
    S_IDLE, S_FORAGING, S_RETURNING, S_EXPLORING,
    S_FIGHTING, S_PATROLLING, S_RECRUITED, S_BUILDING,
    WORKER_HP, SOLDIER_HP, SCOUT_HP, QUEEN_HP,
    SOLDIER_DMG, SOLDIER_CD, QUEEN_DMG, QUEEN_CD,
    SOLDIER_ENGAGE_ADVANCE, SOLDIER_ENGAGE_ATTACKMOVE, SOLDIER_ENGAGE_HOLD, SOLDIER_ENGAGE_SIEGE,
    FOOD_DELIVER, FOOD_PICK, FOOD_MAX_APPROACH, FOOD_REGROW,
    FOOD_INIT_HOME_MIN, FOOD_INIT_HOME_MAX, FOOD_MAX_HOME,
    FOOD_INIT_APPR_MIN, FOOD_INIT_APPR_MAX,
    FOOD_INIT_FRONT_MIN, FOOD_INIT_FRONT_MAX,
    FOOD_REGROW_CONTESTED, FOOD_REGROW_APPROACH,
    FOOD_NODE_WORKER_CAP,
    CORPSE_FOOD, CORPSE_DECAY,
    DIRT_PICK, DIRT_DELIVER, DIRT_CAP, DIRT_REGROW, DIRT_REGROW_FRONT,
    DIRT_FRONT_MAX, DIRT_MAX, DIRT_INIT_MIN, DIRT_INIT_MAX,
    GUARD_POST_COST, GUARD_POST_HP, GUARD_POST_DMG, GUARD_POST_CD,
    GUARD_POST_RANGE, GUARD_POST_MAX,
    WATCHTOWER_COST, WATCHTOWER_HP, WATCHTOWER_VISION, WATCHTOWER_MAX,
    BARRACKS_COST, BARRACKS_HP, BARRACKS_SPAWN_TIME, BARRACKS_MAX,
    WALL_COST, WALL_HP, WALL_MAX,
    LARDER_COST, LARDER_HP, LARDER_MAX, LARDER_INCOME,
    BUILD_WORK_REQUIRED, BUILD_WORKER_CAP, BUILD_RATE, BUILD_RANGE,
    WORKER_UPGRADE_COSTS, SCOUT_UPGRADE_COSTS, SOLDIER_UPGRADE_COSTS,
    UPGRADE_LABELS, UPGRADE_EFFECTS,
    VISION_RADIUS, SCOUT_VISION_RADIUS,
    STALEMATE_TIMEOUT,
)
from engine.colony import Ant, Colony, DirectiveEngine, _apply_upgrade_effects


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

    PASSES = [(13, 25), (44, 56), (74, 86)]
    for rx in (48, 49, 50, 100, 101, 102):
        for y in range(MAP_H):
            if not any(y0 <= y <= y1 for y0, y1 in PASSES):
                t[y][rx] = T_ROCK

    _rock_patch(t,   6,  7, 3)
    _rock_patch(t,   6, 92, 3)
    _rock_patch(t, 144,  7, 3)
    _rock_patch(t, 144, 92, 3)
    _rock_patch(t, 71, 34, 2)
    _rock_patch(t, 79, 66, 2)

    return t


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
        self.start_time = None
        self.terrain = gen_terrain()
        self.territory     = bytearray(MAP_W * MAP_H)
        self.territory_age = bytearray(MAP_W * MAP_H)
        self.foods = []
        self.dirt_nodes = []
        self.corpses = []
        self.structures = []
        self.colonies = []
        self.winner = None
        self.logger = None
        self._llm_stats_list = [None, None]
        self.phase = "lobby"
        self.mcp_seats = {0: None, 1: None}
        self._ant_pos: set = set()   # occupancy set rebuilt each tick
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

        food(10, 32, "home",     FOOD_REGROW)
        food(10, 68, "home",     FOOD_REGROW)
        food(30, 20, "approach", FOOD_REGROW_APPROACH)
        food(32, 50, "approach", FOOD_REGROW_APPROACH)
        food(30, 80, "approach", FOOD_REGROW_APPROACH)
        food(62, 19, "frontline", FOOD_REGROW_CONTESTED)
        food(75, 19, "frontline", FOOD_REGROW_CONTESTED)
        food(88, 19, "frontline", FOOD_REGROW_CONTESTED)
        food(75, 50, "frontline", FOOD_REGROW_CONTESTED)
        food(62, 81, "frontline", FOOD_REGROW_CONTESTED)
        food(75, 81, "frontline", FOOD_REGROW_CONTESTED)
        food(88, 81, "frontline", FOOD_REGROW_CONTESTED)
        food(120, 20, "approach", FOOD_REGROW_APPROACH)
        food(118, 50, "approach", FOOD_REGROW_APPROACH)
        food(120, 80, "approach", FOOD_REGROW_APPROACH)
        food(140, 32, "home",     FOOD_REGROW)
        food(140, 68, "home",     FOOD_REGROW)

        dirt(44, 19, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(44, 50, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(44, 81, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(106, 19, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(106, 50, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(106, 81, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(75, 27, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(75, 73, "frontline", DIRT_REGROW_FRONT, DIRT_FRONT_MAX)
        dirt(22, 50, "home", DIRT_REGROW, DIRT_MAX)
        dirt(128, 50, "home", DIRT_REGROW, DIRT_MAX)

    def finalize_placement(self, red_pos, blue_pos):
        """Carve nests, spawn colonies, start the logger."""
        from engine.colony import Colony  # local import avoids circular at module level
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
            for f in self.foods:
                if abs(f["x"] - nx) + abs(f["y"] - ny) <= 50:
                    key = (f["x"], f["y"])
                    c.known_food.append(key)
                    c.food_intel[key] = {
                        "amt": f["amt"], "max": int(f.get("max", FOOD_MAX_APPROACH)),
                        "tier": f.get("tier", "home"), "last_seen": 0
                    }
            # Seed known_dirt with home-tier nodes near this nest so gather_dirt=true
            # works immediately without requiring workers to stumble onto a deposit first.
            for dn in self.dirt_nodes:
                if dn["tier"] == "home" and abs(dn["x"] - nx) + abs(dn["y"] - ny) < 25:
                    c.known_dirt.append((dn["x"], dn["y"]))
            self.colonies.append(c)
        self.colonies[0].enemy = self.colonies[1]
        self.colonies[1].enemy = self.colonies[0]
        self.start_time = time.time()
        self.phase = "running"
        # RunLogger is injected by Server after this call to avoid circular import
        # self.logger = RunLogger(self)  ← done by Server

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_dict(self):
        import base64
        return {
            "tick": self.tick,
            "phase": self.phase,
            "winner": self.winner,
            "start_time": self.start_time,
            "mcp_seats": {str(k): v for k, v in self.mcp_seats.items()},
            "terrain": [[self.terrain[r][c] for c in range(MAP_W)] for r in range(MAP_H)],
            "territory": base64.b64encode(bytes(self.territory)).decode(),
            "territory_age": base64.b64encode(bytes(self.territory_age)).decode(),
            "foods": self.foods,
            "dirt_nodes": self.dirt_nodes,
            "corpses": self.corpses,
            "structures": self.structures,
            "colonies": [c.to_dict() for c in self.colonies],
            "ant_id_counter": self.colonies[0].ants[0].__class__._id if self.colonies else 0,
        }

    @classmethod
    def from_dict(cls, d):
        import base64
        from engine.colony import Colony, Ant
        w = cls.__new__(cls)
        w.tick = d["tick"]
        w.phase = d["phase"]
        w.winner = d["winner"]
        w.start_time = d.get("start_time")
        seats_raw = d.get("mcp_seats", {"0": None, "1": None})
        w.mcp_seats = {int(k): v for k, v in seats_raw.items()}
        w.terrain = d["terrain"]
        w.territory = bytearray(base64.b64decode(d["territory"]))
        w.territory_age = bytearray(base64.b64decode(d["territory_age"]))
        w.foods = d["foods"]
        w.dirt_nodes = d["dirt_nodes"]
        w.corpses = d["corpses"]
        w.structures = d["structures"]
        w._ant_pos = set()
        w.logger = None
        w._llm_stats_list = [None, None]
        w.colonies = [Colony.from_dict(cd) for cd in d["colonies"]]
        if w.colonies:
            w.colonies[0].enemy = w.colonies[1] if len(w.colonies) > 1 else None
            if len(w.colonies) > 1:
                w.colonies[1].enemy = w.colonies[0]
        if d.get("ant_id_counter"):
            Ant._id = d["ant_id_counter"]
        return w

    # ── Main Tick ──

    def step(self):
        if self.phase in ("lobby", "placement", "paused"):
            return
        if self.winner is not None:
            return

        self.tick += 1

        # Minimum income: every living colony gets +1 food/tick during running phase.
        # Prevents permanent stalls (0 workers + no larders would otherwise be 0 income).
        # Plus a small passive dirt trickle so the structures subsystem is reliably
        # reachable in the early-mid game (worker dirt-gathering alone arrives ~tick 240,
        # too late to matter now that combat is decisive). Active gathering stacks on top.
        for c in self.colonies:
            if c.alive:
                c.food += 1
                c.food_earned_tick += 1
                if c.dirt < DIRT_CAP:
                    c.dirt = min(DIRT_CAP, c.dirt + 1)
                    c.dirt_earned_tick += 1

        # Rebuild occupancy set for soft collision avoidance
        self._ant_pos = {(a.x, a.y) for c in self.colonies for a in c.ants}

        self._update_territory()

        self.corpses = [c for c in self.corpses if c["amt"] > CORPSE_DECAY]
        for c in self.corpses:
            c["amt"] -= CORPSE_DECAY

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
                if target_type is None: continue
                ant = next((a for a in c.ants if a.id == order["id"]), None)
                if ant is None or ant.type == A_QUEEN: continue
                if ant.type == target_type:
                    c.push_event(f"convert failed: ant {order['id']} is already a {order['to']}")
                    continue
                if queen and abs(ant.x - queen.x) + abs(ant.y - queen.y) > 8:
                    remaining.append(order)
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

        # Legacy guard post build path
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

        _STRUCT_LIMITS = {"watchtower": WATCHTOWER_MAX, "barracks": BARRACKS_MAX, "wall": WALL_MAX, "larder": LARDER_MAX}
        _STRUCT_COSTS  = {"watchtower": WATCHTOWER_COST, "barracks": BARRACKS_COST, "wall": WALL_COST, "larder": LARDER_COST}
        _STRUCT_HP     = {"watchtower": WATCHTOWER_HP, "barracks": BARRACKS_HP, "wall": WALL_HP, "larder": LARDER_HP}
        for c in self.colonies:
            if not c.alive or not c.structure_queue: continue
            order = c.structure_queue.pop(0)
            stype, x, y = order["type"], order["x"], order["y"]
            if stype not in _STRUCT_COSTS: continue
            cost  = _STRUCT_COSTS[stype]
            limit = _STRUCT_LIMITS[stype]
            hp    = _STRUCT_HP[stype]
            own_of_type = sum(1 for st in self.structures
                              if st["colony"] == c.id and st["type"] == stype)
            tile_clear = not any(st["x"] == x and st["y"] == y for st in self.structures)
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

        if self.tick % 30 == 0:
            for c in self.colonies:
                if not c.alive: continue
                for site in self.structures:
                    if site.get("active", True) or site["colony"] != c.id: continue
                    self._assign_builders_to_site(c, site["x"], site["y"])

        for c in self.colonies:
            if c.alive:
                DirectiveEngine.eval_triggers(c, self)
                DirectiveEngine.check_alerts(c, self)

        for c in self.colonies:
            if not c.alive: continue
            for ant in list(c.ants):
                self._update_ant(ant)

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

        dead_structs = []
        for struct in self.structures:
            stype = struct.get("type", "guard_post")
            if struct["hp"] <= 0:
                dead_structs.append(struct); continue
            if not struct.get("active", True): continue

            if stype == "guard_post":
                if struct["cd"] > 0:
                    struct["cd"] -= 1
                else:
                    best, best_d = None, GUARD_POST_RANGE + 1
                    for ec in self.colonies:
                        if ec.id == struct["colony"]: continue
                        for e in ec.ants:
                            d = abs(e.x - struct["x"]) + abs(e.y - struct["y"])
                            if d < best_d: best_d = d; best = e
                    if best:
                        struct["cd"] = GUARD_POST_CD
                        # Data hook for the (deferred) ranged-attack animation: record the
                        # shot's target + tick so the frontend can render a projectile.
                        struct["fired_at"] = [best.x, best.y]
                        struct["fire_tick"] = self.tick
                        old_hp = max(0, int(best.hp))
                        best.hp -= GUARD_POST_DMG
                        new_hp = max(0, int(best.hp))
                        owner = self.colonies[struct["colony"]]
                        owner.push_event(f"Guard Post ({struct['x']},{struct['y']}) hit {['worker','soldier','scout','queen'][best.type]}! HP {old_hp}→{new_hp}")
                        if best.hp <= 0: self._kill(best)

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

        for c in self.colonies:
            if not c.alive: continue

            for ant in list(c.ants):
                if ant.lifespan is not None:
                    ant.age += 1
                    if ant.age >= ant.lifespan:
                        c.push_event(f"aged out: {['worker','soldier','scout'][ant.type]} died at age {ant.age}")
                        self._kill(ant)

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

            for i in range(len(c.spawn_queue)):
                entry = c.spawn_queue[i]
                c.spawn_queue[i] = (entry[0], entry[1] - 1, entry[2])
            ready = [e for e in c.spawn_queue if e[1] <= 0]
            c.spawn_queue = [e for e in c.spawn_queue if e[1] > 0]
            for ant_type, _, food_cost in ready:
                queen = next((a for a in c.ants if a.type == A_QUEEN), None)
                if queen:
                    new_ant = Ant(queen.x + random.randint(-2, 2),
                                  queen.y + random.randint(-2, 2), c.id, ant_type,
                                  born_tick=self.tick)
                    if ant_type == A_SOLDIER and c.soldier_hp_bonus > 0:
                        new_ant.hp     += c.soldier_hp_bonus
                        new_ant.max_hp += c.soldier_hp_bonus
                    c.ants.append(new_ant)
                else:
                    c.food += food_cost

            if len(c.spawn_queue) < c.MAX_SPAWN_QUEUE:
                queen = next((a for a in c.ants if a.type == A_QUEEN), None)
                if queen:
                    sp = c.directive["spawn"]
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

                    # Clear stale/impossible upgrade_reserve entries so they don't
                    # permanently block spawning. An entry is stale if the unit is at
                    # max tier, or the reserved amount can never cover the next upgrade.
                    ur = c.directive["economy"].get("upgrade_reserve", {})
                    if ur:
                        _RES_TREES = {
                            "worker":  (c.worker_tier,  WORKER_UPGRADE_COSTS),
                            "scout":   (c.scout_tier,   SCOUT_UPGRADE_COSTS),
                            "soldier": (c.soldier_tier, SOLDIER_UPGRADE_COSTS),
                        }
                        _stale = []
                        for _utype, _amt in ur.items():
                            _tier, _costs = _RES_TREES.get(_utype, (3, None))
                            if _tier >= 3:
                                _stale.append(_utype); continue
                            if _amt < _costs[_tier]:
                                _stale.append(_utype)
                        for _utype in _stale:
                            del ur[_utype]
                            if _utype in sp and sp[_utype].get("pause"):
                                sp[_utype]["pause"] = False

                    reserve_floor = sp.get("reserve_food", 150) + sum(
                        c.directive["economy"].get("upgrade_reserve", {}).values())

                    # Enforce spawn.{type}.min counts: if any type is below its
                    # minimum (ants + queued), prioritize it regardless of ratios.
                    _TYPE_KEYS = [(A_WORKER, "worker"), (A_SCOUT, "scout"), (A_SOLDIER, "soldier")]
                    _PAUSED = {A_WORKER: w_paused, A_SCOUT: sc_paused, A_SOLDIER: sol_paused}
                    t = None
                    for _tc, _tk in _TYPE_KEYS:
                        if _PAUSED[_tc]: continue
                        _min = sp[_tk].get("min", 0)
                        if not _min: continue
                        _cur = sum(1 for a in c.ants if a.type == _tc)
                        _queued = sum(1 for qt, _, _ in c.spawn_queue if qt == _tc)
                        if _cur + _queued < _min:
                            t = _tc
                            break

                    if t is None:
                        r = random.random()
                        if total_sh == 0:
                            t = None
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
                        if c.food - cost >= reserve_floor:
                            c.food -= cost
                            c.spawn_queue.append((t, spawn_time, cost))

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
                    ur = c.directive["economy"].get("upgrade_reserve", {})
                    if unit_type in ur:
                        del ur[unit_type]

        for f in self.foods:
            if f["amt"] < f["max"]:
                f["amt"] = min(f["max"], f["amt"] + f.get("regrow", FOOD_REGROW))

        for dn in self.dirt_nodes:
            if dn["amt"] < dn["max"]:
                dn["amt"] = min(dn["max"], dn["amt"] + dn.get("regrow", DIRT_REGROW))

        self._update_fog()

        self._check_rally_stall()

        self._check_idle_workers()
        self._track_army_com()

        self._check_win()

    def _check_rally_stall(self):
        """Fire a notification if rally staged count hasn't grown in 30 ticks."""
        STALL_TICKS = 30
        for c in self.colonies:
            rp = c.directive["military"].get("rally_point")
            release_at = c.directive["military"].get("rally_release_at")
            if not rp or not release_at:
                c._rally_staged_prev = 0
                c._rally_stall_since = None
                continue
            _rp = rp[0] if isinstance(rp[0], (list, tuple)) else rp
            rx, ry = int(_rp[0]), int(_rp[1])
            staged = sum(1 for a in c.ants if a.type == A_SOLDIER
                         and abs(a.x - rx) + abs(a.y - ry) <= 4)
            if staged >= release_at:
                c._rally_staged_prev = staged
                c._rally_stall_since = None
                continue
            if staged > c._rally_staged_prev:
                c._rally_staged_prev = staged
                c._rally_stall_since = self.tick
            elif c._rally_stall_since is None:
                c._rally_stall_since = self.tick
            elif self.tick - c._rally_stall_since >= STALL_TICKS and (self.tick - c._rally_stall_since) % STALL_TICKS == 0:
                c.push_notification("rally_stalled", {
                    "staged": staged,
                    "release_at": release_at,
                    "rally_x": rx, "rally_y": ry,
                    "stalled_ticks": self.tick - c._rally_stall_since,
                }, tick=self.tick)
                c.push_event(
                    f"⚠ RALLY STALLED: {staged}/{release_at} staged at ({rx},{ry}) "
                    f"— count hasn't grown in {self.tick - c._rally_stall_since} ticks. "
                    f"Lower rally_release_at to {max(1, staged)} to release now, "
                    f"or check if soldiers are dying before reaching the rally point."
                )

    def _check_idle_workers(self):
        """Alert when a meaningful share of workers sit idle for a sustained stretch.
        Idle workers gather nothing — it's wasted economy the agent should notice and
        fix (priority_food / gather_dirt / expand). Mirrors _check_rally_stall."""
        IDLE_TICKS = 40
        for c in self.colonies:
            workers = [a for a in c.ants if a.type == A_WORKER]
            idle = sum(1 for a in workers if a.state == S_IDLE and not a.unit_override)
            # Only meaningful once there's an economy to mismanage.
            if len(workers) < 6 or idle < max(3, int(len(workers) * 0.3)):
                c._idle_workers_since = None
                continue
            if c._idle_workers_since is None:
                c._idle_workers_since = self.tick
            elif (self.tick - c._idle_workers_since >= IDLE_TICKS
                  and (self.tick - c._idle_workers_since) % IDLE_TICKS == 0):
                idle_ticks = self.tick - c._idle_workers_since
                c.push_notification("workers_idle", {
                    "idle": idle, "workers": len(workers), "idle_ticks": idle_ticks,
                    "label": f"{idle}/{len(workers)} workers idle for {idle_ticks}t — assign them",
                }, tick=self.tick)
                c.push_event(
                    f"⚠ {idle}/{len(workers)} WORKERS IDLE for {idle_ticks} ticks — wasted income. "
                    f"Set economy.priority_food to a viable node, economy.gather_dirt=true, or send idle "
                    f"workers to gather; expand to a frontline food node if home/approach is depleted."
                )

    def _track_army_com(self):
        """Record each colony's soldier center-of-mass this tick (for sitrep order-effect)."""
        for c in self.colonies:
            xs = [a.x for a in c.ants if a.type == A_SOLDIER]
            ys = [a.y for a in c.ants if a.type == A_SOLDIER]
            if xs:
                c._army_com_history.append((self.tick, sum(xs) / len(xs), sum(ys) / len(ys)))

    def _check_win(self):
        if self.winner is not None: return
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
                                                  "reason": "queen_death",
                                                  "feedback_prompt": "Game over! Call submit_feedback with your observations — bugs, balance issues, missing tools, or anything that made decisions harder."}, tick=self.tick)

        if self.winner is None and self.start_time:
            elapsed = time.time() - self.start_time
            if elapsed >= STALEMATE_TIMEOUT:
                _val = {A_WORKER: 5, A_SOLDIER: 20, A_SCOUT: 8}
                scores = []
                for c in self.colonies:
                    army_val = sum(_val.get(a.type, 0) for a in c.ants if a.type != A_QUEEN)
                    scores.append(c.food_collected + army_val + max(0, int(c.food)))
                if scores[0] > scores[1]:   self.winner = 0
                elif scores[1] > scores[0]: self.winner = 1
                else:                        self.winner = "draw"
                winner_name = ("RED" if self.winner == 0 else
                               "BLUE" if self.winner == 1 else "DRAW")
                sc_str = f"RED {scores[0]} vs BLUE {scores[1]}"
                for c in self.colonies:
                    c.push_event(f"STALEMATE — {winner_name} wins by score ({sc_str})")
                    outcome = ("draw" if self.winner == "draw"
                               else "victory" if self.winner == c.id else "defeat")
                    c.push_notification("game_over", {"winner": self.winner, "outcome": outcome,
                                                      "reason": "stalemate", "scores": scores,
                                                      "feedback_prompt": "Game over! Call submit_feedback with your observations — bugs, balance issues, missing tools, or anything that made decisions harder."}, tick=self.tick)
                if self.logger: self.logger.finish(self.winner)

    # ── Ant Behavior ──

    def _update_ant(self, ant):
        ant.prev_x, ant.prev_y = ant.x, ant.y
        if ant.cooldown > 0: ant.cooldown -= 1

        c = self.colonies[ant.colony]
        if c.emergency_command and ant.type != A_QUEEN:
            cmd = c.emergency_command
            if cmd["type"] == "RECALL":
                ant.tx, ant.ty = c.nx, c.ny
                self._wander(ant); return
            elif cmd["type"] == "FREEZE":
                return
            elif cmd["type"] == "RETREAT":
                if ant.type in (A_SOLDIER, A_SCOUT):
                    ant.tx, ant.ty = c.nx, c.ny
                    self._wander(ant); return
            elif cmd["type"] == "FOCUS" and ant.type == A_SOLDIER:
                tx, ty = cmd.get("target", (c.nx, c.ny))
                ant.tx, ant.ty = tx, ty
                self._wander(ant); return
            elif cmd["type"] == "ALL_IN" and ant.type == A_SOLDIER:
                if c.enemy:
                    eq = next((a for a in c.enemy.ants if a.type == A_QUEEN), None)
                    if eq:
                        ant.tx, ant.ty = eq.x, eq.y
                        self._wander(ant); return

        if ant.type == A_WORKER:    self._behavior_worker(ant)
        elif ant.type == A_SOLDIER: self._behavior_soldier(ant)
        elif ant.type == A_SCOUT:   self._behavior_scout(ant)
        elif ant.type == A_QUEEN:   self._behavior_queen(ant)

        c = self.colonies[ant.colony]
        if c.enemy and abs(ant.x - c.enemy.nx) + abs(ant.y - c.enemy.ny) <= 18:
            c.enemy_scouted_tick = self.tick
            c.enemy_scouted_counts = [sum(1 for a in c.enemy.ants if a.type == t)
                                      for t in range(4)]

    def _behavior_worker(self, ant):
        c = self.colonies[ant.colony]

        ov = ant.unit_override
        if ov:
            cmd = ov.get("cmd")
            if cmd == "gather":
                ant.recruit_target = (int(ov["x"]), int(ov["y"]))
            elif cmd == "build":
                bx, by = int(ov["x"]), int(ov["y"])
                if ant.carrying:
                    if abs(ant.x - c.nx) + abs(ant.y - c.ny) <= 5:
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
                    ant.unit_override = None
                else:
                    d = abs(ant.x - bx) + abs(ant.y - by)
                    if d > BUILD_RANGE:
                        self._move_to(ant, bx, by, 0)
                        ant.state = S_FORAGING
                    else:
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

        _flee_dist = int((c.directive.get("unit_types") or {}).get("worker", {}).get("flee_distance", 4))
        for ec in self.colonies:
            if ec.id == ant.colony: continue
            _dist = lambda e: abs(ant.x - e.x) + abs(ant.y - e.y)
            close  = sum(1 for e in ec.ants if e.type == A_SOLDIER and _dist(e) <= _flee_dist)
            nearby = sum(1 for e in ec.ants if e.type == A_SOLDIER and _dist(e) <= _flee_dist * 2)
            if close >= 1 or nearby >= 3:  # single soldier in flee_dist OR army of 3+ nearby
                if ant.carrying:
                    ant.carrying = False
                ant.recruit_target = None  # don't return to the danger zone
                ant.state = S_RETURNING
                # Flee at full speed toward the nest — a passing soldier gets
                # soldier_fast double-moves, so a single-step worker would be run
                # down. Match the carry-return double-move so workers can escape.
                self._move_to(ant, c.nx, c.ny, 0)
                if c.worker_fast:
                    self._move_to(ant, c.nx, c.ny, 0)
                return

        if ant.recruit_target:
            fx, fy = ant.recruit_target
            if ant.carrying:
                if abs(ant.x-c.nx) + abs(ant.y-c.ny) <= 5:
                    ant.carrying = False
                    if ant.carrying_type == "dirt":
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
                    if ov and ov.get("cmd") == "gather":
                        ant.recruit_target = (int(ov["x"]), int(ov["y"]))
                    else:
                        ant.recruit_target = None
                    ant.state = S_IDLE
                    self._dep(ant.x, ant.y, 0, 1.0)
                else:
                    self._move_to(ant, c.nx, c.ny, 0)
                    if c.worker_fast:
                        self._move_to(ant, c.nx, c.ny, 0)
                    self._dep(ant.x, ant.y, 0, 0.9)
            else:
                dist_to_target = abs(ant.x - fx) + abs(ant.y - fy)
                # When commanded to gather at a dirt target, don't divert to food
                # while inside the arrival zone — dirt check should fire first.
                targeting_dirt = (ov and ov.get("cmd") == "gather"
                                  and dist_to_target <= 4)
                f = self._food_nearby(ant.x, ant.y, 3)
                if f and f["amt"] > 10 and not targeting_dirt:
                    f["amt"] -= FOOD_PICK
                    if f["amt"] <= 0: self._deplete_food(f)
                    ant.carrying = True
                    ant.carrying_type = "food"
                    ant.state = S_RETURNING
                    self._dep(ant.x, ant.y, 0, 1.0)
                elif dist_to_target <= 4:
                    dn = self._dirt_nearby(ant.x, ant.y, 4)  # radius matches arrival threshold
                    if dn and c.dirt < DIRT_CAP:
                        dn["amt"] -= DIRT_PICK
                        ant.carrying = True
                        ant.carrying_type = "dirt"
                        ant.state = S_RETURNING
                        if (dn["x"], dn["y"]) not in c.known_dirt:
                            c.known_dirt.append((dn["x"], dn["y"]))
                            if len(c.known_dirt) > 12: c.known_dirt.pop(0)
                    else:
                        ant.recruit_target = None
                        ant.state = S_IDLE
                elif abs(ant.x - fx) + abs(ant.y - fy) > 50:
                    ant.recruit_target = None
                    ant.state = S_IDLE
                else:
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

        if ant.carrying:
            if abs(ant.x-c.nx) + abs(ant.y-c.ny) <= 5:
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
                    self._move_to(ant, c.nx, c.ny, 0)
            return

        for corp in self.corpses:
            if abs(ant.x - corp["x"]) + abs(ant.y - corp["y"]) <= 2 and corp["amt"] >= 1:
                corp["amt"] -= FOOD_PICK
                if corp["amt"] <= 0: self.corpses.remove(corp)
                ant.carrying = True; ant.carrying_type = "food"; ant.state = S_RETURNING
                return
        f = self._food_nearby(ant.x, ant.y, 2)
        if f and f["amt"] > 10:
            f["amt"] -= FOOD_PICK
            if f["amt"] <= 0: self._deplete_food(f)
            ant.carrying = True; ant.carrying_type = "food"; ant.state = S_RETURNING
            return

        dn = self._dirt_nearby(ant.x, ant.y, 2)
        if dn and c.dirt < DIRT_CAP:
            dn["amt"] -= DIRT_PICK
            ant.carrying = True; ant.carrying_type = "dirt"; ant.state = S_RETURNING
            if (dn["x"], dn["y"]) not in c.known_dirt:
                c.known_dirt.append((dn["x"], dn["y"]))
                if len(c.known_dirt) > 12: c.known_dirt.pop(0)
            return

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

        gather_dirt = c.directive["economy"].get("gather_dirt", False)
        if gather_dirt and c.dirt < DIRT_CAP and c.known_dirt and random.random() < 0.4:
            target_dn = min(c.known_dirt, key=lambda p: abs(ant.x-p[0])+abs(ant.y-p[1]))
            ant.state = S_FORAGING
            self._move_to(ant, target_dn[0], target_dn[1], 0)
            return

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
                c.directive["economy"]["priority_food"] = None
            if pf_t and pf_t in c.known_food and _node_amt(pf_t) <= 10:
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
                close = [p for p in pool if abs(ant.x - p[0]) + abs(ant.y - p[1]) <= 35]
                use_pool = close if close else pool
                unsaturated = [p for p in use_pool
                               if _workers_near(p) < FOOD_NODE_WORKER_CAP.get(
                                   c.food_intel.get(p, {}).get("tier", "approach"), 6)]
                target = random.choice(unsaturated if unsaturated else (close if close else pool))
            ant.recruit_target = target
            ant.state = S_FORAGING
            self._move_to(ant, target[0], target[1], 0)
            return

        for site in self.structures:
            if site.get("active", True): continue
            if site["colony"] != ant.colony: continue
            d = abs(ant.x - site["x"]) + abs(ant.y - site["y"])
            if d <= 25:
                self._move_to(ant, site["x"], site["y"], 0)
                ant.state = S_FORAGING
                return

        ant.state = S_EXPLORING
        if ant.tx is None or abs(ant.x - ant.tx) + abs(ant.y - ant.ty) <= 3:
            a = random.uniform(0, 2 * math.pi)
            d = random.randint(20, 40)
            ant.tx = max(0, min(MAP_W - 1, ant.x + int(math.cos(a) * d)))
            ant.ty = max(0, min(MAP_H - 1, ant.y + int(math.sin(a) * d)))
        self._move_to(ant, ant.tx, ant.ty, 0)
        f2 = self._food_nearby(ant.x, ant.y, 2)
        if f2 and f2["amt"] > 5:
            f2["amt"] -= FOOD_PICK
            if f2["amt"] <= 0: self._deplete_food(f2)
            ant.carrying = True; ant.carrying_type = "food"; ant.tx = ant.ty = None
            key2 = (f2["x"], f2["y"])
            if key2 not in c.known_food:
                c.known_food.append(key2)
                c.push_event(f"worker explored — found {f2['kind']} at ({f2['x']},{f2['y']})")
                if len(c.known_food) > 25: c.known_food.pop(0)
            ant.state = S_RETURNING

    def _behavior_scout(self, ant):
        c = self.colonies[ant.colony]

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
            elif cmd == "hold":
                tx, ty = int(ov["x"]), int(ov["y"])
                ant.state = S_EXPLORING
                if abs(ant.x - tx) + abs(ant.y - ty) > 2:
                    self._move_to(ant, tx, ty, 3)
                self._dep(ant.x, ant.y, 3, 0.6)
                return

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

        if ant.carrying:
            if abs(ant.x-c.nx) + abs(ant.y-c.ny) <= 5:
                ant.carrying = False
                earned = FOOD_DELIVER + c.carry_bonus
                c.food += earned
                c.food_collected += earned
                c.food_earned_tick += earned
                self._dep(ant.x, ant.y, 0, 1.0)
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
                            if recruited >= c.scout_recruit: break
                    ant.tx = None
            else:
                self._move_to(ant, c.nx, c.ny, 0)
                self._move_to(ant, c.nx, c.ny, 0)
                self._dep(ant.x, ant.y, 0, 1.0)
            return

        f = self._food_nearby(ant.x, ant.y, c.scout_detect)
        if f and f["amt"] > 10:
            f["amt"] -= 30
            if f["amt"] <= 0: self._deplete_food(f)
            ant.carrying = True
            ant.tx, ant.ty = f["x"], f["y"]
            if (f["x"], f["y"]) not in c.known_food:
                c.known_food.append((f["x"], f["y"]))
                c.push_event(f"scout found {f['kind']} at ({f['x']},{f['y']})")
                if len(c.known_food) > 20: c.known_food.pop(0)
            return

        dn = self._dirt_nearby(ant.x, ant.y, c.scout_detect)
        if dn and (dn["x"], dn["y"]) not in c.known_dirt:
            c.known_dirt.append((dn["x"], dn["y"]))
            c.push_event(f"scout found dirt deposit at ({dn['x']},{dn['y']})")
            if len(c.known_dirt) > 12: c.known_dirt.pop(0)

        enemy = self._nearest_enemy(ant, 12)
        if enemy:
            self._dep(ant.x, ant.y, 1, 1.0)

        if ant.tx is None or random.random() < 0.02:
            known = c.known_food
            if known and random.random() < 0.12:
                target = random.choice(known)
                ant.tx, ant.ty = target
            else:
                _exp = ant.resolved_config(c.directive).get("expansion", [1, 1])
                ex, ey = _exp[0], _exp[1]
                a = math.atan2(ey, ex) + random.gauss(0, 0.45)
                d = random.randint(35, min(90, 45 + len(c.ants)))
                ant.tx = max(0, min(MAP_W-1, c.nx + int(math.cos(a)*d)))
                ant.ty = max(0, min(MAP_H-1, c.ny + int(math.sin(a)*d)))
            ant.state = S_EXPLORING

        self._move_to(ant, ant.tx, ant.ty, 3)
        if c.scout_fast:
            self._move_to(ant, ant.tx, ant.ty, 3)
        if ant.tx is not None and abs(ant.x-ant.tx) <= 1 and abs(ant.y-ant.ty) <= 1:
            ant.tx = None
        self._dep(ant.x, ant.y, 3, 0.6)

    def _behavior_soldier(self, ant):
        c = self.colonies[ant.colony]

        ov = ant.unit_override
        if ov:
            cmd = ov.get("cmd")
            if cmd == "move_to":
                # Pure reposition: go to the point, do NOT divert to chase enemies (use it to
                # retreat/regroup). The adjacent-enemy strike below still fires, so the soldier
                # defends itself if something is right next to it — it just won't hunt.
                tx, ty = int(ov["x"]), int(ov["y"])
                self._move_to(ant, tx, ty, 1)
                ant.state = S_PATROLLING
                self._dep(ant.x, ant.y, 1, 0.5)
            elif cmd in ("attack_move", "attack_xy"):
                # Attack-move: advance to the point but divert to engage and KILL any enemy
                # within range, then resume. Soldiers defend themselves and fight through.
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
            # A plain move_to is a PURE reposition: it does not attack at all (use it to
            # retreat/regroup without being drawn into a fight). Every other override
            # (attack_move/attack_xy/hold/patrol) strikes the adjacent enemy.
            adj = None if cmd == "move_to" else self._adjacent_enemy(ant)
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

        in_siege = (c.enemy is not None
                    and abs(ant.x - c.enemy.nx) + abs(ant.y - c.enemy.ny) <= 12)

        # Retreat takes priority over all engagement: a soldier ordered to retreat
        # must disengage and fall back to the nest even if an enemy is in range,
        # otherwise auto_attack/attack_target keeps it locked in combat forever.
        # (Not while in_siege — a committed siege is not abandoned mid-strike.)
        if c.directive["military"]["retreat"] and not in_siege:
            dist_to_nest = abs(ant.x - c.nx) + abs(ant.y - c.ny)
            ant.state = S_PATROLLING
            if dist_to_nest > 8:
                self._move_to(ant, c.nx, c.ny, 2)
            else:
                # Defensive perimeter — 8 positions at radius 6 around nest
                _PERIM = [(6,0),(4,4),(0,6),(-4,4),(-6,0),(-4,-4),(0,-6),(4,-4)]
                ox, oy = _PERIM[ant.id % 8]
                px = max(0, min(MAP_W-1, c.nx + ox))
                py = max(0, min(MAP_H-1, c.ny + oy))
                self._move_to(ant, px, py, 2)
            self._dep(ant.x, ant.y, 2, 0.3)
            return

        queen_focus = c.directive["military"]["siege_priority"] == "queen"

        _mil = c.directive["military"]
        _rally = _mil["rally_point"]
        # A rally only HOLDS the army when it has a release count (rally_release_at) or
        # there's no attack order. A leftover rally_point with NO release must not pin
        # soldiers while attack_target / auto_attack is set — otherwise the army masses
        # at the rally forever and the attack order is silently ignored. Staged rallies
        # (rally_release_at set) still gate as intended.
        if _rally and not _mil.get("rally_release_at") and (
                _mil.get("attack_target") or (_mil.get("auto_attack") and c.enemy)):
            _rally = None
        _at_rally = False
        _en_route_to_rally = False
        if _rally and not in_siege:
            _rp = _rally[0] if isinstance(_rally[0], (list, tuple)) else _rally
            _dist_to_rally = abs(ant.x - int(_rp[0])) + abs(ant.y - int(_rp[1]))
            _at_rally = _dist_to_rally <= 4
            _en_route_to_rally = not _at_rally

        # Engagement range is INTENT-AWARE so an advancing army stays massed instead of
        # each soldier sprinting off to chase its own nearest enemy (which smears the
        # army into a 40-tile line of 1v1 duels). While moving toward an objective
        # (rally / attack_target / auto_attack push) the window is tight — fight only
        # what's right in front and keep marching. Once at the enemy nest it widens to
        # lock onto the queen.
        _advancing = bool(_mil.get("attack_target")) or (_mil.get("auto_attack") and c.enemy)
        if in_siege:
            detect_range = SOLDIER_ENGAGE_SIEGE
        elif _en_route_to_rally:
            detect_range = SOLDIER_ENGAGE_ADVANCE
        elif _advancing:
            detect_range = SOLDIER_ENGAGE_ATTACKMOVE
        elif _at_rally:
            detect_range = 5
        else:
            detect_range = SOLDIER_ENGAGE_HOLD

        # Where an advancing soldier is headed. When advancing, soldiers FIGHT THROUGH
        # toward this goal instead of freezing at the enemy line — so a numerical edge
        # converts into a breakthrough rather than an endless 1v1 front-line stalemate.
        advance_goal = None
        if not in_siege:
            _at = _mil.get("attack_target")
            if _at:
                advance_goal = (int(_at[0]), int(_at[1]))
            elif _mil.get("auto_attack") and c.enemy:
                advance_goal = (c.enemy.nx, c.enemy.ny)

        # Guard posts shoot the army from range and shred it on the approach, so they
        # are the PRIORITY target — destroy any that can reach us before engaging
        # anything else, even mid-advance. Detect them out to their own threat range.
        if not in_siege:
            gp = self._nearest_enemy_struct(ant, max(detect_range, GUARD_POST_RANGE + 2),
                                            only_type="guard_post")
            if gp is not None:
                ant.state = S_FIGHTING
                if abs(ant.x - gp["x"]) <= 1 and abs(ant.y - gp["y"]) <= 1:
                    if ant.cooldown <= 0:
                        self._strike_struct(ant, gp, c)
                else:
                    self._move_to(ant, gp["x"], gp["y"], 1)
                self._dep(ant.x, ant.y, 1, 0.8)
                return

        enemy = self._nearest_enemy(ant, detect_range, siege=in_siege, queen_focus=queen_focus)
        if enemy:
            ant.state = S_FIGHTING
            # Strike whatever enemy is ADJACENT (Chebyshev — orthogonal OR diagonal, any
            # type) — NOT just the move-target. A soldier sieging the queen but ringed by
            # enemy workers kills the workers blocking it (opening a path) instead of vainly
            # pathing around the ring; and an advancing soldier never marches past an
            # adjacent foe taking free hits.
            adj = self._adjacent_enemy(ant)
            if adj is not None and ant.cooldown <= 0:
                dmg = SOLDIER_DMG + c.dmg_bonus
                old_hp = int(adj.hp)
                adj.hp -= dmg; ant.cooldown = c.soldier_fast_cd
                new_hp = max(0, int(adj.hp))
                if adj.type == A_QUEEN:
                    c.queen_dmg_dealt_tick += dmg
                    ec = self.colonies[adj.colony]
                    ec.push_event(f"★ QUEEN UNDER ATTACK! HP: {old_hp}→{new_hp} (dealt {dmg})")
                    ec.push_notification("queen_under_attack", {"hp": int(new_hp), "old_hp": int(old_hp), "dmg": dmg}, tick=self.tick)
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
            # Movement (attack-move): if ANY enemy is adjacent, STAND and fight it — defend,
            # never march past, and grind down a blocking worker ring. Otherwise CLOSE on the
            # target (the queen while sieging, else the nearest foe within the engage window)
            # to actually engage it. Rear ranks with no foe in the engage window fall to the
            # else-branch below and flow toward the objective, bringing numbers to the front.
            if adj is None:
                self._move_to(ant, enemy.x, enemy.y, 1)
        else:
            rally = _rally   # effective rally: None when an attack order overrides a no-release rally
            if rally and not in_siege:
                if rally and isinstance(rally[0], (list, tuple)):
                    rx, ry = int(rally[0][0]), int(rally[0][1])
                else:
                    rx, ry = int(rally[0]), int(rally[1])
                if abs(ant.x - rx) + abs(ant.y - ry) > 4:
                    ant.state = S_PATROLLING
                    self._move_to(ant, rx, ry, 2)
                    self._dep(ant.x, ant.y, 2, 0.3)
                else:
                    ant.state = S_PATROLLING
                    self._dep(ant.x, ant.y, 2, 0.3)
                    near_struct = None
                    near_struct_d = 15
                    for struct in self.structures:
                        if struct["colony"] == ant.colony: continue
                        sd = abs(ant.x - struct["x"]) + abs(ant.y - struct["y"])
                        if sd < near_struct_d: near_struct_d = sd; near_struct = struct
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
                    release_n = c.directive["military"]["rally_release_at"]
                    if release_n:
                        staged = sum(1 for a in c.ants if a.type == A_SOLDIER
                                     and abs(a.x - rx) + abs(a.y - ry) <= 4)
                        if staged >= release_n:
                            rally_mode = c.directive["military"].get("rally_mode", "normal")
                            current_rally = c.directive["military"]["rally_point"]
                            if isinstance(current_rally[0], (list, tuple)) and len(current_rally) > 1:
                                c.directive["military"]["rally_point"] = current_rally[1:]
                                c.push_event(f"★ WAYPOINT ({rx},{ry}) reached — next: {current_rally[1]}")
                            elif rally_mode == "auto_forward" and c.enemy:
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
                                # Hand the freshly-massed army a forward objective so it
                                # FLOWS toward the enemy as one body (reusing the advancing
                                # flow-through model) instead of scattering — half charging
                                # and half drifting back to the nest. Only seed this when no
                                # explicit objective is set, so a player's attack_target /
                                # auto_attack push is never clobbered.
                                if (not c.directive["military"].get("attack_target")
                                        and not c.directive["military"].get("auto_attack")
                                        and c.enemy is not None):
                                    c.directive["military"]["attack_target"] = [c.enemy.nx, c.enemy.ny]
                                    c.push_event(
                                        f"★ RALLY RELEASED — {staged} soldiers advancing on "
                                        f"enemy nest ({c.enemy.nx},{c.enemy.ny})!")
                                else:
                                    c.push_event(f"★ RALLY RELEASED — {staged} soldiers advancing!")
                                c.push_notification("rally_released", {"staged": staged, "from": [rx, ry]}, tick=self.tick)
                return

            attack_tgt = c.directive["military"]["attack_target"]
            if not attack_tgt and c.directive["military"].get("auto_attack") and c.enemy:
                soldiers_in_siege_now = sum(1 for a in c.ants
                                            if a.type == A_SOLDIER
                                            and abs(a.x - c.enemy.nx) + abs(a.y - c.enemy.ny) <= 15)
                if soldiers_in_siege_now > 0:
                    eq = next((a for a in c.enemy.ants if a.type == A_QUEEN), None)
                    attack_tgt = [eq.x, eq.y] if eq else [c.enemy.nx, c.enemy.ny]
                else:
                    attack_tgt = [c.enemy.nx, c.enemy.ny]
            if attack_tgt and not in_siege:
                # Smash any enemy structure right in our path rather than marching past
                # it (a barracks pumping soldiers, a wall blocking the lane, etc.).
                es = self._nearest_enemy_struct(ant, SOLDIER_ENGAGE_ADVANCE)
                if es is not None:
                    ant.state = S_FIGHTING
                    if abs(ant.x - es["x"]) <= 1 and abs(ant.y - es["y"]) <= 1:
                        if ant.cooldown <= 0:
                            self._strike_struct(ant, es, c)
                    else:
                        self._move_to(ant, es["x"], es["y"], 1)
                    self._dep(ant.x, ant.y, 1, 0.5)
                    return
                ax, ay = int(attack_tgt[0]), int(attack_tgt[1])
                ant.state = S_PATROLLING
                self._move_to(ant, ax, ay, 2)
                self._dep(ant.x, ant.y, 2, 0.3)
                return

            if self._follow(ant, 1):
                ant.state = S_FIGHTING
            else:
                nearest_struct = None
                nearest_struct_d = 25
                for struct in self.structures:
                    if struct["colony"] == ant.colony: continue
                    d = abs(ant.x - struct["x"]) + abs(ant.y - struct["y"])
                    if d < nearest_struct_d: nearest_struct_d = d; nearest_struct = struct
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
                        eq = next((a for a in c.enemy.ants if a.type == A_QUEEN), None)
                        enx = eq.x if eq else c.enemy.nx
                        eny = eq.y if eq else c.enemy.ny
                        spread = {"column": 0.06, "spread": 0.40}.get(formation, 0.15)
                        a = math.atan2(eny - c.ny, enx - c.nx) + random.gauss(0, spread)
                        d = random.randint(60, 95)
                    elif defense == "defensive":
                        _exp = ant.resolved_config(c.directive).get("expansion", [1, 1])
                        ex, ey = _exp[0], _exp[1]
                        a = math.atan2(ey, ex) + random.gauss(0, 1.0)
                        d = random.randint(15, 35)
                    else:
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
        for st in self.structures:
            if st.get("type") == "wall" and st["x"] == x and st["y"] == y:
                return False
        return True

    def _assign_builders_to_site(self, colony, sx, sy):
        """Assign up to BUILD_WORKER_CAP idle workers to the incomplete build site at (sx,sy)."""
        already = sum(1 for a in colony.ants if a.type == A_WORKER and a.unit_override
                      and a.unit_override.get("cmd") == "build"
                      and a.unit_override.get("x") == sx and a.unit_override.get("y") == sy)
        slots = BUILD_WORKER_CAP - already
        if slots <= 0: return
        candidates = sorted(
            [a for a in colony.ants if a.type == A_WORKER and not a.unit_override and not a.carrying],
            key=lambda a: abs(a.x - sx) + abs(a.y - sy)
        )
        for ant in candidates[:slots]:
            ant.unit_override = {"cmd": "build", "x": sx, "y": sy}

    def _try_move(self, ant, nx, ny, allow_occupied=False) -> bool:
        if not self._passable(nx, ny):
            return False
        if not allow_occupied and (nx, ny) in self._ant_pos:
            return False
        self._ant_pos.discard((ant.x, ant.y))
        ant.x, ant.y = nx, ny
        self._ant_pos.add((nx, ny))
        return True

    def _astar_step(self, sx, sy, tx, ty, max_nodes=200):
        """Return the first step (nx, ny) of an A* path from (sx,sy) to (tx,ty)
        that avoids walls/water/rock. Diagonal moves are blocked from cutting wall
        corners. Returns None if no path is found or the node budget is exceeded
        (caller falls back to greedy). Only walls/terrain matter — occupied tiles
        are ignored here and handled by _try_move after the step is chosen."""
        if (sx, sy) == (tx, ty):
            return None
        # gather wall tiles once (cheap: WALL_MAX = 12 segments)
        walls = {(st["x"], st["y"]) for st in self.structures
                 if st.get("type") == "wall"}

        def blocked(x, y):
            if not (0 <= x < MAP_W and 0 <= y < MAP_H):
                return True
            if self.terrain[y][x] in (T_WATER, T_ROCK):
                return True
            return (x, y) in walls

        # target must be reachable terrain; if the target itself is blocked,
        # A* can't reach it cleanly — let greedy handle it.
        if blocked(tx, ty):
            return None

        # Weighted A*: on mostly-open terrain a plain admissible heuristic makes A*
        # explore a huge equal-cost diamond and blow the node budget before reaching a
        # distant target (returning None → the army falls back to wall-blind greedy and
        # gets stuck). A greedy bias (W>1) makes A* expand a narrow corridor toward the
        # goal, routing around walls only where it meets them — long paths are found in a
        # few hundred nodes. Paths are slightly suboptimal, which is invisible in-game.
        W = 1.6
        def h(x, y):
            ddx, ddy = abs(x - tx), abs(y - ty)
            # true octile distance: straight step = 10, diagonal step = 14. Making a
            # diagonal cost MORE than a straight step stops A* from drifting diagonally
            # off the straight line (with equal costs it wanders, then snags on walls).
            return 10 * (ddx + ddy) - 6 * min(ddx, ddy)

        open_heap = [(W * h(sx, sy), 0, sx, sy)]
        # came_from maps a node to the node it was reached from
        came_from = {(sx, sy): None}
        g_score = {(sx, sy): 0}
        nodes = 0
        found = False
        while open_heap and nodes < max_nodes:
            _, g, x, y = heapq.heappop(open_heap)
            nodes += 1
            if (x, y) == (tx, ty):
                found = True
                break
            if g > g_score.get((x, y), float("inf")):
                continue
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if blocked(nx, ny):
                        continue
                    # no diagonal corner-cutting through walls/terrain
                    if dx != 0 and dy != 0:
                        if blocked(x + dx, y) or blocked(x, y + dy):
                            continue
                    ng = g + (14 if (dx != 0 and dy != 0) else 10)
                    if ng < g_score.get((nx, ny), float("inf")):
                        g_score[(nx, ny)] = ng
                        came_from[(nx, ny)] = (x, y)
                        heapq.heappush(open_heap, (ng + W * h(nx, ny), ng, nx, ny))
        if not found:
            return None
        # walk back from target to the node whose parent is the start
        cur = (tx, ty)
        while came_from.get(cur) is not None and came_from[cur] != (sx, sy):
            cur = came_from[cur]
        if came_from.get(cur) != (sx, sy):
            return None
        return cur

    def _move_to(self, ant, tx, ty, layer):
        # A* first step: routes around wall lines (re-planned every tick so
        # destroyed walls are picked up immediately). Falls back to greedy if
        # no path is found or the node budget is exceeded.
        step = self._astar_step(ant.x, ant.y, tx, ty, max_nodes=2000)
        if step is not None:
            nx, ny = step
            if self._try_move(ant, nx, ny):
                return
            # A* step blocked by an ant this tick — try the orthogonal
            # components of that step before falling through to greedy.
            sdx, sdy = nx - ant.x, ny - ant.y
            if sdx and sdy:
                for cx, cy in ((ant.x + sdx, ant.y), (ant.x, ant.y + sdy)):
                    if self._try_move(ant, cx, cy):
                        return
        # ── greedy fallback (original behaviour) ──
        dx = (1 if tx > ant.x else -1) if tx != ant.x else 0
        dy = (1 if ty > ant.y else -1) if ty != ant.y else 0
        cands = [(ant.x+dx, ant.y+dy)]
        if dx and dy: cands += [(ant.x+dx, ant.y), (ant.x, ant.y+dy)]
        random.shuffle(cands)
        # prefer unoccupied
        for nx, ny in cands:
            if self._try_move(ant, nx, ny): return
        tried = set(cands)
        fallback = sorted(
            [(ant.x+ddx, ant.y+ddy) for ddx in (-1, 0, 1) for ddy in (-1, 0, 1)
             if (ddx, ddy) != (0, 0) and (ant.x+ddx, ant.y+ddy) not in tried],
            key=lambda p: abs(p[0]-tx) + abs(p[1]-ty)
        )
        for nx, ny in fallback:
            if self._try_move(ant, nx, ny): return
        # last resort: allow occupied to prevent deadlock
        for nx, ny in cands + fallback:
            if self._try_move(ant, nx, ny, allow_occupied=True): return

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
            if self._try_move(ant, ant.x+dx, ant.y+dy): return
        # allow occupied if all neighbours taken
        for dx, dy in dirs:
            if self._try_move(ant, ant.x+dx, ant.y+dy, allow_occupied=True): return

    def _follow(self, ant, layer, radius=2, threshold=None):
        return False  # pheromone trails removed; territory system replaces them

    def _dep(self, x, y, layer, val):
        pass  # pheromone deposition removed

    def _get_p(self, x, y, layer):
        return 0

    def _deplete_food(self, f):
        self.foods.remove(f)
        pos = (f["x"], f["y"])
        for c in self.colonies:
            if pos in c.known_food:
                c.push_notification("food_depleted", {"x": f["x"], "y": f["y"], "kind": f["kind"]}, tick=self.tick)
            # Proactively clear priority_food if it pointed at this now-dead node,
            # so workers redistribute instead of idling on a depleted target.
            pf = c.directive["economy"].get("priority_food")
            pf_t = tuple(pf) if isinstance(pf, (list, tuple)) and len(pf) == 2 else None
            if pf_t == pos:
                c.directive["economy"]["priority_food"] = []
                c.push_event(f"★ priority_food node at ({pos[0]},{pos[1]}) depleted — "
                             "clearing priority_food; workers will redistribute")
                c.push_notification("priority_food_cleared",
                                    {"x": pos[0], "y": pos[1], "reason": "depleted"}, tick=self.tick)
            # Clear recruit_target for any worker already heading to this node —
            # without this they walk all the way there before realising it's gone.
            redirected = 0
            for ant in c.ants:
                if ant.type == A_WORKER and ant.recruit_target == pos:
                    ant.recruit_target = None
                    redirected += 1
            if redirected:
                c.push_event(f"  {redirected} worker(s) redirected from depleted node ({pos[0]},{pos[1]})")

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
        c = self.colonies[ant.colony]
        if ant.cooldown > 0: return
        best, best_eff = None, 12
        for ec in self.colonies:
            if ec.id == ant.colony: continue
            for e in ec.ants:
                d = abs(ant.x-e.x) + abs(ant.y-e.y)
                priority_bonus = {A_SOLDIER: -4, A_SCOUT: -2}.get(e.type, 0)
                eff = d + priority_bonus
                if eff < best_eff: best_eff = eff; best = e
        if best:
            ant.state = S_FIGHTING
            ant.cooldown = QUEEN_CD
            old_hp = max(0, int(best.hp))
            best.hp -= QUEEN_DMG
            new_hp = max(0, int(best.hp))
            self._dep(ant.x, ant.y, 1, 1.0)
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
                    effective_d = d - 8
                elif siege:
                    effective_d = d
                else:
                    effective_d = d + (12 if o.type == A_QUEEN else 0)
                if effective_d < best_d: best_d = effective_d; best = o
        return best

    def _adjacent_enemy(self, ant):
        """Nearest enemy within Chebyshev distance 1 (orthogonally OR diagonally adjacent),
        any type — what a melee soldier can actually strike this tick. Prefers the enemy
        queen when she is adjacent, so a soldier that breaks the ring finishes the siege."""
        best, best_d = None, 99
        for c in self.colonies:
            if c.id == ant.colony: continue
            for o in c.ants:
                if abs(ant.x - o.x) <= 1 and abs(ant.y - o.y) <= 1:
                    if o.type == A_QUEEN:
                        return o
                    d = abs(ant.x - o.x) + abs(ant.y - o.y)
                    if d < best_d: best_d = d; best = o
        return best

    def _nearest_enemy_struct(self, ant, radius, only_type=None):
        """Nearest enemy structure within radius (Manhattan). Guard posts are weighted
        as closer so they are picked preferentially — they shoot the passing army and
        must be destroyed first. Pass only_type to restrict (e.g. 'guard_post')."""
        best, best_d = None, radius + 1
        for st in self.structures:
            if st["colony"] == ant.colony: continue
            if st.get("hp", 0) <= 0: continue
            stype = st.get("type", "guard_post")
            if only_type and stype != only_type: continue
            d = abs(ant.x - st["x"]) + abs(ant.y - st["y"])
            effective_d = d - 6 if stype == "guard_post" else d
            if effective_d < best_d: best_d = effective_d; best = st
        return best

    def _strike_struct(self, ant, st, c):
        """Deal one soldier hit to an enemy structure; remove it and emit events if killed."""
        dmg = SOLDIER_DMG + c.dmg_bonus
        st["hp"] -= dmg
        ant.cooldown = c.soldier_fast_cd
        if st["hp"] <= 0:
            stype = st.get("type", "guard_post").replace("_", " ").title()
            ec = self.colonies[st["colony"]]
            ec.push_event(f"★ {stype} at ({st['x']},{st['y']}) DESTROYED!")
            c.push_event(f"Destroyed enemy {stype} at ({st['x']},{st['y']})!")
            if st in self.structures:
                self.structures.remove(st)

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
        ter = self.territory
        age = self.territory_age
        for c in self.colonies:
            cid = c.id + 1
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
        for i in range(MAP_W * MAP_H):
            if ter[i] != 0:
                a = age[i] + 1
                if a >= TERRITORY_DECAY:
                    ter[i] = 0; age[i] = 0
                else:
                    age[i] = a

    # ── Visual fog-of-war ──

    def _update_fog(self):
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

            for st in self.structures:
                if st["colony"] == c.id: continue
                idx = st["y"] * MAP_W + st["x"]
                if idx in vis:
                    c.seen_structs[(st["x"], st["y"])] = {
                        "type": st.get("type", "guard_post"),
                        "hp_approx": st["hp"],
                        "last_seen": self.tick
                    }

            if c.enemy and (self.tick % 5 == 0):
                spotted = [(ea.x, ea.y, ea.type) for ea in c.enemy.ants
                           if ea.y * MAP_W + ea.x in vis]
                if spotted:
                    cx = sum(x for x, y, t in spotted) // len(spotted)
                    cy = sum(y for x, y, t in spotted) // len(spotted)
                    workers  = sum(1 for x, y, t in spotted if t == A_WORKER)
                    soldiers = sum(1 for x, y, t in spotted if t == A_SOLDIER)
                    scouts   = sum(1 for x, y, t in spotted if t == A_SCOUT)
                    c.enemy_sightings.append((cx, cy, soldiers, len(spotted), self.tick, workers, scouts))
                    if len(c.enemy_sightings) > 15: c.enemy_sightings.pop(0)
                    if soldiers >= 3:
                        c.push_notification("enemy_contact", {"cx": cx, "cy": cy, "soldiers": soldiers, "total": len(spotted)}, tick=self.tick)

            if c.enemy:
                eq = next((a for a in c.enemy.ants if a.type == A_QUEEN), None)
                if eq and eq.y * MAP_W + eq.x in vis:
                    c.enemy_queen_hp_last_seen = int(eq.hp)

            dead_nodes = [k for k in c.food_intel if not any(
                f["x"] == k[0] and f["y"] == k[1] for f in self.foods)]
            for k in dead_nodes:
                c.food_intel[k] = {**c.food_intel[k], "amt": 0}

    # ── Serialization ──

    def serialize_tick(self):
        ants = []
        for c in self.colonies:
            for a in c.ants:
                ants.append([a.id, a.x, a.y, a.prev_x, a.prev_y, a.colony,
                             a.type, a.state, int(a.carrying), a.hp, a.max_hp])
        foods = [[f["x"], f["y"], int(f["amt"]), f["kind"], f.get("tier","home")]
                 for f in self.foods]
        corpses = [[int(c["x"]), int(c["y"]), int(c["amt"])] for c in self.corpses]
        cols = []
        for c in self.colonies:
            counts = [0, 0, 0, 0]
            for a in c.ants: counts[a.type] += 1
            sq_summary = {"w": 0, "so": 0, "sc": 0, "reserved": 0, "next_t": None}
            for ant_type, ticks_rem, cost in c.spawn_queue:
                sq_summary[["w", "so", "sc"][ant_type]] += 1
                sq_summary["reserved"] += cost
            if c.spawn_queue:
                sq_summary["next_t"] = min(tr for _, tr, _ in c.spawn_queue)
            aging_soon = [0, 0, 0]
            for a in c.ants:
                if a.type < 3 and a.lifespan and a.age >= int(a.lifespan * 0.80):
                    aging_soon[a.type] += 1
            from engine.constants import (WORKER_UPGRADE_COSTS, SCOUT_UPGRADE_COSTS,
                                          SOLDIER_UPGRADE_COSTS)
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
                         sq_summary, aging_soon, upg_eta, int(c.dirt)])
        territory = list(self.territory)
        dirt = [[dn["x"], dn["y"], int(dn["amt"]), dn["tier"]] for dn in self.dirt_nodes]
        structs = [[st["x"], st["y"], st["colony"], st["hp"], st["max_hp"], st.get("type","guard_post"),
                    1 if st.get("active", True) else 0,
                    st.get("build_progress", 0), st.get("build_required", 0)]
                   for st in self.structures]
        fog = []
        for c in self.colonies:
            arr = bytearray(c.fog_explored)
            for idx in c.fog_visible:
                arr[idx] = 2
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
