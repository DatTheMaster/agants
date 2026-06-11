import random

from engine.constants import (
    A_WORKER, A_SOLDIER, A_SCOUT,
    MAP_W, MAP_H,
    WATCHTOWER_COST, WATCHTOWER_MAX,
    GUARD_POST_COST, GUARD_POST_MAX,
    LARDER_COST, LARDER_MAX,
    WORKER_UPGRADE_COSTS, SCOUT_UPGRADE_COSTS, SOLDIER_UPGRADE_COSTS,
)


def update_bot_strategy(world, colony_id):
    """Adaptive heuristic strategy for a bot colony."""
    c = world.colonies[colony_id]
    if not c.alive: return
    workers  = sum(1 for a in c.ants if a.type == A_WORKER)
    soldiers = sum(1 for a in c.ants if a.type == A_SOLDIER)
    food     = c.food
    income   = c.income_per_s

    if (food < 150 and workers < 20) or (income < 5 and food < 100):
        roles, cap, defense = {"worker": 0.65, "scout": 0.15, "soldier": 0.20}, 70, "defensive"
    elif food > 1800 and workers >= 40:
        roles, cap, defense = {"worker": 0.25, "scout": 0.10, "soldier": 0.65}, 55, "aggressive"
    elif food > 1000 and workers >= 30:
        roles, cap, defense = {"worker": 0.35, "scout": 0.12, "soldier": 0.53}, 55, "aggressive"
    elif food > 500 and workers >= 20:
        roles, cap, defense = {"worker": 0.45, "scout": 0.15, "soldier": 0.40}, 60, "balanced"
    else:
        roles, cap, defense = {"worker": 0.55, "scout": 0.20, "soldier": 0.25}, 65, "balanced"

    # Rally to mass, then release as a wave — avoids 1-by-1 trickle deaths
    rally_update = {}
    mil = c.directive["military"]
    if c.enemy and c.alive:
        rx = int(c.nx + (c.enemy.nx - c.nx) * 0.40)
        ry = int(c.ny + (c.enemy.ny - c.ny) * 0.40)
        if soldiers < 3:
            if mil.get("attack_target") or mil.get("rally_point"):
                rally_update = {"rally_point": None, "rally_release_at": None,
                                "attack_target": None, "siege_priority": None, "auto_attack": False}
        elif soldiers >= 3 and not mil.get("rally_point") and not mil.get("attack_target"):
            release_at = max(8, min(soldiers + 4, 18))
            rally_update = {"rally_point": [rx, ry], "rally_release_at": release_at,
                            "rally_mode": "normal", "siege_priority": "queen"}
        elif mil.get("rally_point"):
            _rp = mil["rally_point"]
            _rx, _ry = ((int(_rp[0][0]), int(_rp[0][1]))
                        if isinstance(_rp[0], (list, tuple))
                        else (int(_rp[0]), int(_rp[1])))
            staged = sum(1 for a in c.ants if a.type == A_SOLDIER
                         and abs(a.x - _rx) + abs(a.y - _ry) <= 6)
            release_at = mil.get("rally_release_at", 8)
            if staged >= release_at or (soldiers >= release_at and world.tick > 400):
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

        if own_wt < WATCHTOWER_MAX and dirt >= WATCHTOWER_COST and workers >= 8:
            bx = int(c.nx + (c.enemy.nx - c.nx) * 0.25)
            by = int(c.ny + (c.enemy.ny - c.ny) * 0.25)
            wx, wy = _bot_find_passable(bx, by)
            if wx is not None:
                entry = {"type": "watchtower", "x": wx, "y": wy}
                if entry not in c.structure_queue:
                    c.structure_queue.append(entry)
        elif own_gp < GUARD_POST_MAX and dirt >= GUARD_POST_COST and workers >= 10:
            bx = int(c.nx + (c.enemy.nx - c.nx) * 0.38)
            by = int(c.ny + (c.enemy.ny - c.ny) * 0.38)
            gx, gy = _bot_find_passable(bx, by, spread=4)
            if gx is not None and [gx, gy] not in c.build_queue:
                c.build_queue.append([gx, gy])

        # Larder for late-game food sustain — build near nest before approach nodes deplete
        own_lr = sum(1 for st in world.structures if st["colony"] == c.id and st.get("type") == "larder")
        if own_lr < LARDER_MAX and dirt >= LARDER_COST and world.tick > 200:
            lx, ly = _bot_find_passable(c.nx + 5, c.ny, spread=4)
            if lx is not None:
                entry = {"type": "larder", "x": lx, "y": ly}
                if entry not in c.structure_queue:
                    c.structure_queue.append(entry)

    # Queue upgrades when a comfortable buffer exists
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
