import random
from collections import deque

from engine.constants import (
    MAP_W, MAP_H,
    A_WORKER, A_SOLDIER, A_SCOUT, A_QUEEN, A_SPITTER,
    NUM_ANT_TYPES, ANT_TYPE_NAMES,
    S_IDLE, S_FORAGING, S_RETURNING, S_EXPLORING,
    S_FIGHTING, S_PATROLLING, S_RECRUITED, S_BUILDING,
    WORKER_HP, SOLDIER_HP, SCOUT_HP, QUEEN_HP, SPITTER_HP,
    SPITTER_LIFESPAN, SPITTER_SPAWN_COST, SPITTER_SPAWN_TIME,
    SOLDIER_CD, MAX_EVENTS, DIRT_CAP, DIRT_DELIVER, FOOD_DELIVER,
    WORKER_UPGRADE_COSTS, SCOUT_UPGRADE_COSTS, SOLDIER_UPGRADE_COSTS,
    UPGRADE_LABELS, UPGRADE_EFFECTS,
)


def _apply_upgrade_effects(c, unit_type, tier):
    """Update colony bonuses when unit_type reaches a new tier (1-3)."""
    if unit_type == "worker":
        c.carry_bonus = [0, 8, 20, 38][tier]
        c.worker_fast = (tier >= 3)
    elif unit_type == "scout":
        c.scout_detect  = [5, 9, 9, 14][tier]
        c.scout_recruit = [8, 12, 12, 16][tier]
        c.scout_fast    = (tier >= 2)
    elif unit_type == "soldier":
        c.dmg_bonus          = 10 if tier >= 1 else 0
        c.soldier_hp_bonus   = 80 if tier >= 2 else 0
        c.soldier_fast_cd    = SOLDIER_CD - (1 if tier >= 2 else 0)
        c.soldier_splash     = (tier >= 3)


# ═══════════════════════════════════════════════════════════════════════════════
# Ant
# ═══════════════════════════════════════════════════════════════════════════════

class Ant:
    _id = 0
    LIFESPAN = {0: 500, 1: 300, 2: 200, 3: None, 4: SPITTER_LIFESPAN}   # worker, soldier, scout, queen, spitter

    def __init__(self, x, y, colony, ant_type, born_tick=0):
        Ant._id += 1
        self.id = Ant._id
        self.x = x
        self.y = y
        self.colony = colony
        self.type = ant_type
        self.state = S_IDLE
        self.carrying = False
        self.carrying_type = "food"   # "food" | "dirt"
        self.hp = {A_WORKER: WORKER_HP, A_SOLDIER: SOLDIER_HP,
                   A_SCOUT: SCOUT_HP, A_QUEEN: QUEEN_HP,
                   A_SPITTER: SPITTER_HP}[ant_type]
        self.max_hp = self.hp
        self.prev_x = x
        self.prev_y = y
        self.tx = None
        self.ty = None
        self.cooldown = 0
        self.recruit_target = None
        self.born_tick = born_tick
        self.lifespan = self.LIFESPAN.get(ant_type)
        self.age = 0
        self.behavior_config = {}
        self.birth_config    = {}
        self.unit_override   = None

    def to_dict(self):
        return {
            "id": self.id, "x": self.x, "y": self.y,
            "prev_x": self.prev_x, "prev_y": self.prev_y,
            "colony": self.colony, "type": self.type, "state": self.state,
            "carrying": self.carrying, "carrying_type": self.carrying_type,
            "hp": self.hp, "max_hp": self.max_hp,
            "cooldown": self.cooldown, "tx": self.tx, "ty": self.ty,
            "recruit_target": self.recruit_target,
            "born_tick": self.born_tick, "age": self.age,
            "behavior_config": self.behavior_config,
            "birth_config": self.birth_config,
            "unit_override": self.unit_override,
        }

    @classmethod
    def from_dict(cls, d):
        a = cls.__new__(cls)
        a.id           = d["id"]
        a.x            = d["x"];  a.y      = d["y"]
        a.prev_x       = d["prev_x"]; a.prev_y = d["prev_y"]
        a.colony       = d["colony"]; a.type   = d["type"]
        a.state        = d["state"]
        a.carrying     = d["carrying"]; a.carrying_type = d["carrying_type"]
        a.hp           = d["hp"];  a.max_hp = d["max_hp"]
        a.cooldown     = d["cooldown"]; a.tx = d["tx"]; a.ty = d["ty"]
        a.recruit_target  = d["recruit_target"]
        a.born_tick    = d["born_tick"]; a.age = d["age"]
        a.lifespan     = cls.LIFESPAN.get(a.type)
        a.behavior_config = d.get("behavior_config", {})
        a.birth_config    = d.get("birth_config", {})
        a.unit_override   = d.get("unit_override")
        return a

    def resolved_config(self, colony_directive):
        """Merge: individual override > birth_config > directive.unit_types[type] > hardcoded defaults."""
        type_name = ANT_TYPE_NAMES[self.type]
        defaults = {
            "worker":  {"flee_distance": 4},
            "soldier": {"expansion": [1, 1]},
            "scout":   {"expansion": [1, 1], "revisit_pct": 0.12},
            "queen":   {},
            "spitter": {"expansion": [1, 1]},
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
                "spitter": {"target_ratio": 0.0,  "min_ratio": 0.0, "min": 0,  "max": 20, "pause": False, "birth_config": {}},
                "reserve_food": 50,
                "burst_at": 800,
            },
            "economy": {
                "upgrade_priority": ["scout", "worker", "soldier"],
                "auto_upgrade": True,
                "priority_food": None,
                # Default ON so the structures subsystem is reachable without an agent
                # discovering an obscure flag. Only task-seeking (idle) workers divert to
                # dirt, and only at a low rate, so active food foraging is unaffected.
                "gather_dirt": True,
                "upgrade_reserve": {},
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

    # Leaf keys that live inside a directive SECTION but are commonly patched flat by
    # agents (e.g. {"auto_attack": true} instead of {"military": {"auto_attack": true}}
    # or "military.auto_attack"). Without routing, a flat key lands at the directive
    # top level where NOTHING reads it — the patch silently no-ops. That single failure
    # mode looks like "rally never staged / auto_attack did nothing / my changes didn't
    # take effect". Auto-route known leaves to their real section (forgiving-API policy).
    _FLAT_KEY_SECTION = {
        "stance": "military", "formation": "military", "rally_point": "military",
        "rally_release_at": "military", "rally_mode": "military", "auto_attack": "military",
        "attack_target": "military", "retreat": "military", "siege_priority": "military",
        "priority_food": "economy", "gather_dirt": "economy",
    }

    @staticmethod
    def patch(colony, partial):
        """Deep-merge partial into colony.directive. Supports dot-notation keys, and
        auto-routes known flat leaf keys (e.g. "auto_attack") into their section.
        Empty dict {} replaces rather than merges (allows clearing upgrade_reserve etc.)."""
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
            elif key in DirectiveEngine._FLAT_KEY_SECTION and not (
                    isinstance(value, dict) and key in d and isinstance(d[key], dict)):
                # Flat leaf key (none of these are valid top-level directive keys) →
                # route into its real section so the engine actually reads it.
                section = DirectiveEngine._FLAT_KEY_SECTION[key]
                d.setdefault(section, {})[key] = value
            elif isinstance(value, dict) and value and key in d and isinstance(d[key], dict):
                DirectiveEngine._merge(d[key], value)
            else:
                d[key] = value

    @staticmethod
    def _merge(base, override):
        for k, v in override.items():
            if isinstance(v, dict) and v and k in base and isinstance(base[k], dict):
                DirectiveEngine._merge(base[k], v)
            else:
                base[k] = v

    @staticmethod
    def _build_ns(colony, world):
        """Build the shared evaluation namespace used by triggers and alerts."""
        counts = [0] * NUM_ANT_TYPES
        for a in colony.ants:
            counts[a.type] += 1
        queen = next((a for a in colony.ants if a.type == A_QUEEN), None)
        queen_hp = queen.hp if queen else 0
        soldiers_in_siege = 0
        soldiers_near_enemy_nest = 0
        enemy_soldiers_near_nest = 0
        enemy_queen_hp = float(QUEEN_HP)
        if colony.enemy:
            for a in colony.ants:
                if a.type == A_SOLDIER:
                    d_enemy = abs(a.x - colony.enemy.nx) + abs(a.y - colony.enemy.ny)
                    if d_enemy <= 12: soldiers_in_siege += 1
                    if d_enemy <= 20: soldiers_near_enemy_nest += 1
            for a in colony.enemy.ants:
                if a.type == A_SOLDIER and abs(a.x - colony.nx) + abs(a.y - colony.ny) <= 15:
                    enemy_soldiers_near_nest += 1
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
        return {
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
            "enemy_intel_age": enemy_intel_age,
        }

    @staticmethod
    def eval_triggers(colony, world):
        """Evaluate triggers; auto-patch directive when conditions are met."""
        triggers = colony.directive.get("triggers", [])
        if not triggers:
            return
        ns = DirectiveEngine._build_ns(colony, world)
        if not hasattr(colony, "trigger_cooldowns"):
            colony.trigger_cooldowns = {}
        for trigger in sorted(triggers, key=lambda t: t.get("priority", 0), reverse=True):
            condition = trigger.get("if", "")
            if not condition:
                continue
            label = trigger.get("label", "?")
            cooldown = trigger.get("cooldown", 0)
            if cooldown > 0:
                last = colony.trigger_cooldowns.get(label, -(cooldown + 1))
                if world.tick - last < cooldown:
                    continue
            try:
                expr = condition.replace(" AND ", " and ").replace(" OR ", " or ")
                fired = bool(eval(expr, {"__builtins__": {}}, ns))  # noqa: S307 — restricted namespace
                # Pick the active block: `then` when condition holds, else the
                # optional `else` block (lets a trigger undo its own patches when
                # the condition no longer holds — e.g. clear military.retreat).
                block = trigger.get("then", {}) if fired else trigger.get("else", {})
                if block and (fired or "else" in trigger):
                    buy_upg = block.get("buy_upgrade")
                    patch_keys = {k: v for k, v in block.items() if k != "buy_upgrade"}
                    if patch_keys:
                        DirectiveEngine.patch(colony, patch_keys)
                    if buy_upg and buy_upg in ("worker", "scout", "soldier"):
                        setattr(colony, f"{buy_upg}_upgrade_pending", True)
                    if cooldown > 0:
                        colony.trigger_cooldowns[label] = world.tick
                    recent = [e for e in colony.trigger_log
                              if e["label"] == label and world.tick - e["tick"] < 5]
                    if not recent:
                        colony.trigger_log.append({
                            "tick":    world.tick,
                            "label":   label,
                            "patches": block,
                        })
            except Exception:
                pass

    @staticmethod
    def check_alerts(colony, world):
        """Evaluate alerts; push notifications when conditions fire.

        sampling=True  → edge-triggered: notify only on False→True transition
        sampling=False → level-triggered: notify every 30 ticks while condition holds
        """
        alerts = colony.directive.get("alerts", [])
        if not alerts:
            return
        ns = DirectiveEngine._build_ns(colony, world)
        if not hasattr(colony, "alert_states"):
            colony.alert_states = {}
        for alert in alerts:
            condition = alert.get("if", "")
            label = alert.get("label", "?")
            sampling = alert.get("sampling", False)
            if not condition:
                continue
            try:
                expr = condition.replace(" AND ", " and ").replace(" OR ", " or ")
                fired = bool(eval(expr, {"__builtins__": {}}, ns))  # noqa: S307
            except Exception:
                continue
            prev = colony.alert_states.get(label, False)
            if sampling:
                if fired and not prev:
                    colony.push_notification("alert", {"label": label}, tick=world.tick)
            else:
                if fired:
                    last_tick = colony.alert_states.get(f"{label}_tick", -(31))
                    if world.tick - last_tick >= 30:
                        colony.push_notification("alert", {"label": label}, tick=world.tick)
                        colony.alert_states[f"{label}_tick"] = world.tick
            colony.alert_states[label] = fired


# ═══════════════════════════════════════════════════════════════════════════════
# Colony — directive-driven strategy
# ═══════════════════════════════════════════════════════════════════════════════

class Colony:
    def __init__(self, cid, nx, ny):
        self.id = cid
        self.nx = nx
        self.ny = ny
        self.ants = []
        self.food = 800.0
        self.prod_timer = 0
        self.alive = True
        self.food_collected = 0
        self.ants_lost = 0

        ex = 1 if nx < MAP_W // 2 else -1
        ey = 0 if ny == MAP_H // 2 else (1 if ny < MAP_H // 2 else -1)
        self.directive = DirectiveEngine.default_directive(ex, ey)
        self.directive["spawn"]["worker"]["max"] = 60
        self.build_queue = []
        self.structure_queue = []
        self.convert_queue = []
        self.known_food = []
        self.known_dirt = []
        self.food_intel = {}
        self.seen_structs = {}
        self.enemy_sightings = []
        self.dirt = 0
        self.dirt_earned_tick = 0.0
        self.dirt_per_s = 0.0
        self.events = deque(maxlen=MAX_EVENTS)
        self.notifications = deque(maxlen=50)
        self.enemy_scouted_tick   = -9999
        self.enemy_scouted_counts = [0] * NUM_ANT_TYPES
        self.fog_explored = bytearray(MAP_W * MAP_H)
        self.fog_visible  = set()
        self.trigger_log      = deque(maxlen=30)
        self.trigger_cooldowns = {}
        self.enemy_queen_hp_last_seen = None
        self.queen_dmg_dealt_tick = 0.0
        self.queen_dps_actual = 0.0
        self.log_queue = []
        self.food_prev = self.food
        self.food_earned_tick = 0.0
        self.income_per_s = 0.0
        self.income_smooth = 0.0
        self.income_history = deque(maxlen=5)
        self.income_neg_warned = False
        self.peak_pop = 0
        self.peak_pop_tick = 0

        self.worker_tier  = 0
        self.scout_tier   = 0
        self.soldier_tier = 0
        self.worker_upgrade_pending  = False
        self.scout_upgrade_pending   = False
        self.soldier_upgrade_pending = False
        self.carry_bonus  = 0
        self.worker_fast  = False
        self.scout_detect  = 5
        self.scout_recruit = 8
        self.scout_fast    = False
        self.spawn_mult    = 1.0
        self.dmg_bonus         = 0
        self.soldier_hp_bonus  = 0
        self.soldier_fast_cd   = SOLDIER_CD
        self.soldier_splash    = False

        self.enemy = None

        self.spawn_queue = []
        self.SPAWN_COST = {A_WORKER: 25, A_SOLDIER: 50, A_SCOUT: 35, A_SPITTER: SPITTER_SPAWN_COST}
        self.SPAWN_TIME = {A_WORKER: 20, A_SOLDIER: 35, A_SCOUT: 25, A_SPITTER: SPITTER_SPAWN_TIME}
        self.MAX_SPAWN_QUEUE = 10

        self.emergency_command = None
        self.emergency_ticks_left = 0

        self._rally_staged_prev = 0
        self._rally_stall_since = None
        self._idle_workers_since = None
        self._army_com_history = deque(maxlen=40)   # (tick, com_x, com_y) of own soldiers; for order-effect

    # ── Serialization ──────────────────────────────────────────────────────────

    def to_dict(self):
        import base64
        return {
            "id": self.id, "nx": self.nx, "ny": self.ny,
            "food": self.food, "dirt": self.dirt, "alive": self.alive,
            "food_collected": self.food_collected, "ants_lost": self.ants_lost,
            "prod_timer": self.prod_timer,
            "directive": self.directive,
            "build_queue": self.build_queue,
            "structure_queue": self.structure_queue,
            "convert_queue": self.convert_queue,
            "known_food": self.known_food,
            "known_dirt": self.known_dirt,
            "food_intel": self.food_intel,
            "seen_structs": self.seen_structs,
            "enemy_sightings": self.enemy_sightings,
            "enemy_scouted_tick": self.enemy_scouted_tick,
            "enemy_scouted_counts": self.enemy_scouted_counts,
            "fog_explored": base64.b64encode(bytes(self.fog_explored)).decode(),
            "trigger_cooldowns": {str(k): v for k, v in self.trigger_cooldowns.items()},
            "enemy_queen_hp_last_seen": self.enemy_queen_hp_last_seen,
            "income_per_s": self.income_per_s, "income_smooth": self.income_smooth,
            "peak_pop": self.peak_pop, "peak_pop_tick": self.peak_pop_tick,
            "worker_tier": self.worker_tier, "scout_tier": self.scout_tier,
            "soldier_tier": self.soldier_tier,
            "worker_upgrade_pending": self.worker_upgrade_pending,
            "scout_upgrade_pending": self.scout_upgrade_pending,
            "soldier_upgrade_pending": self.soldier_upgrade_pending,
            "carry_bonus": self.carry_bonus, "worker_fast": self.worker_fast,
            "scout_detect": self.scout_detect, "scout_recruit": self.scout_recruit,
            "scout_fast": self.scout_fast, "spawn_mult": self.spawn_mult,
            "dmg_bonus": self.dmg_bonus, "soldier_hp_bonus": self.soldier_hp_bonus,
            "soldier_fast_cd": self.soldier_fast_cd, "soldier_splash": self.soldier_splash,
            "spawn_queue": self.spawn_queue,
            "emergency_command": self.emergency_command,
            "emergency_ticks_left": self.emergency_ticks_left,
            "ants": [a.to_dict() for a in self.ants],
        }

    @classmethod
    def from_dict(cls, d):
        import base64
        c = cls.__new__(cls)
        c.id = d["id"]; c.nx = d["nx"]; c.ny = d["ny"]
        c.food = d["food"]; c.dirt = d["dirt"]; c.alive = d["alive"]
        c.food_collected = d["food_collected"]; c.ants_lost = d["ants_lost"]
        c.prod_timer = d.get("prod_timer", 0)
        c.directive = d["directive"]
        c.build_queue = d.get("build_queue", [])
        c.structure_queue = d.get("structure_queue", [])
        c.convert_queue = d.get("convert_queue", [])
        c.known_food = d.get("known_food", [])
        c.known_dirt = d.get("known_dirt", [])
        c.food_intel = d.get("food_intel", {})
        c.seen_structs = d.get("seen_structs", {})
        c.enemy_sightings = d.get("enemy_sightings", [])
        c.enemy_scouted_tick = d.get("enemy_scouted_tick", -9999)
        c.enemy_scouted_counts = d.get("enemy_scouted_counts", [0] * NUM_ANT_TYPES)
        c.fog_explored = bytearray(base64.b64decode(d["fog_explored"]))
        c.fog_visible = set()
        c.trigger_cooldowns = {k: v for k, v in d.get("trigger_cooldowns", {}).items()}
        c.alert_states = {}
        c.enemy_queen_hp_last_seen = d.get("enemy_queen_hp_last_seen")
        c.income_per_s = d.get("income_per_s", 0.0)
        c.income_smooth = d.get("income_smooth", 0.0)
        c.income_history = deque(maxlen=5)
        c.income_neg_warned = False
        c.peak_pop = d.get("peak_pop", 0); c.peak_pop_tick = d.get("peak_pop_tick", 0)
        c.worker_tier  = d.get("worker_tier", 0)
        c.scout_tier   = d.get("scout_tier", 0)
        c.soldier_tier = d.get("soldier_tier", 0)
        c.worker_upgrade_pending  = d.get("worker_upgrade_pending", False)
        c.scout_upgrade_pending   = d.get("scout_upgrade_pending", False)
        c.soldier_upgrade_pending = d.get("soldier_upgrade_pending", False)
        c.carry_bonus     = d.get("carry_bonus", 0)
        c.worker_fast     = d.get("worker_fast", False)
        c.scout_detect    = d.get("scout_detect", 5)
        c.scout_recruit   = d.get("scout_recruit", 8)
        c.scout_fast      = d.get("scout_fast", False)
        c.spawn_mult      = d.get("spawn_mult", 1.0)
        c.dmg_bonus       = d.get("dmg_bonus", 0)
        c.soldier_hp_bonus = d.get("soldier_hp_bonus", 0)
        c.soldier_fast_cd  = d.get("soldier_fast_cd", SOLDIER_CD)
        c.soldier_splash   = d.get("soldier_splash", False)
        c.spawn_queue = d.get("spawn_queue", [])
        c.SPAWN_COST = {A_WORKER: 25, A_SOLDIER: 50, A_SCOUT: 35, A_SPITTER: SPITTER_SPAWN_COST}
        c.SPAWN_TIME = {A_WORKER: 20, A_SOLDIER: 35, A_SCOUT: 25, A_SPITTER: SPITTER_SPAWN_TIME}
        c.MAX_SPAWN_QUEUE = 10
        c.emergency_command = d.get("emergency_command")
        c.emergency_ticks_left = d.get("emergency_ticks_left", 0)
        c.events = deque(maxlen=MAX_EVENTS)
        c.notifications = deque(maxlen=50)
        c.trigger_log = deque(maxlen=30)
        c.log_queue = []
        c.food_prev = c.food
        c.food_earned_tick = 0.0
        c.dirt_earned_tick = 0.0
        c.dirt_per_s = 0.0
        c.queen_dmg_dealt_tick = 0.0
        c.queen_dps_actual = 0.0
        c.enemy = None
        c.ants = [Ant.from_dict(a) for a in d.get("ants", [])]
        return c

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
                entry = {"type": str(pos["type"]), "x": int(pos["x"]), "y": int(pos["y"])}
                if entry not in self.structure_queue:
                    self.structure_queue.append(entry)
            elif isinstance(pos, (list, tuple)) and len(pos) == 2:
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
        if s.get("redistribute_workers"):
            for ant in self.ants:
                if ant.type == A_WORKER:
                    ant.recruit_target = None

    def _apply_legacy_strategy(self, s):
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
        counts = [0] * NUM_ANT_TYPES
        for a in self.ants: counts[a.type] += 1
        queen = next((a for a in self.ants if a.type == A_QUEEN), None)

        soldiers_in_siege = 0
        if self.enemy:
            for a in self.ants:
                if a.type == A_SOLDIER:
                    if abs(a.x - self.enemy.nx) + abs(a.y - self.enemy.ny) <= 12:
                        soldiers_in_siege += 1

        own_queen_pos    = (queen.x, queen.y) if queen else None
        enemy_queen      = next((a for a in self.enemy.ants if a.type == A_QUEEN), None) if self.enemy else None
        enemy_queen_pos  = (enemy_queen.x, enemy_queen.y) if enemy_queen else None

        enemy_counts = [0] * NUM_ANT_TYPES
        enemy_queen_hp = 0
        if self.enemy:
            for a in self.enemy.ants: enemy_counts[a.type] += 1
            eq = next((a for a in self.enemy.ants if a.type == A_QUEEN), None)
            enemy_queen_hp = eq.hp if eq else 0

        own_soldiers  = counts[1]

        ticks_since_recon = (world_tick - self.enemy_scouted_tick) if hasattr(self, 'enemy_scouted_tick') else 9999
        sc = self.enemy_scouted_counts if hasattr(self, 'enemy_scouted_counts') else [0] * NUM_ANT_TYPES
        if ticks_since_recon < 150:
            intel_status   = "fresh"
            intel_soldiers = sc[1]
        elif ticks_since_recon < 500:
            intel_status   = "stale"
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
            "own_queen_pos":    own_queen_pos,
            "enemy_queen_pos":  enemy_queen_pos if soldiers_in_siege > 0 else None,
            "soldiers_in_siege": soldiers_in_siege,
            "active_food_sources": len(self.known_food),
            "army_ratio": round(own_soldiers / max(1, intel_soldiers), 2) if intel_soldiers else None,
            "pressure": (
                "overwhelmed" if intel_soldiers > own_soldiers * 2.0 else
                "losing"      if intel_soldiers > own_soldiers * 1.25 else
                "even"        if intel_soldiers > own_soldiers * 0.77 else
                "dominant"    if intel_soldiers > own_soldiers * 0.5 else
                "crushing"
            ) if intel_status != "unknown" else "unknown",
            "enemy": {
                "intel": intel_status,
                "ticks_since_recon": ticks_since_recon,
                "workers":  sc[0] if intel_status != "unknown" else None,
                "soldiers": sc[1] if intel_status != "unknown" else None,
                "scouts":   sc[2] if intel_status != "unknown" else None,
                "queen_hp": enemy_queen_hp if soldiers_in_siege > 0 else None,
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
