import random

from engine.constants import (
    A_WORKER, A_SOLDIER, A_SCOUT, A_SPITTER,
    MAP_W, MAP_H,
    WATCHTOWER_COST, WATCHTOWER_MAX,
    GUARD_POST_COST, GUARD_POST_MAX,
    BULWARK_COST, BULWARK_MAX,
    LARDER_COST, LARDER_MAX,
    WORKER_UPGRADE_COSTS, SCOUT_UPGRADE_COSTS, SOLDIER_UPGRADE_COSTS,
)

# Bot caps for bulwarks — stay conservative; leave BULWARK_MAX headroom for humans/agents
_BOT_BULWARK_MAX = 3


def _emit_bot_reason(c, world, roles, defense, enemy_rush, rally_update,
                     workers, soldiers, food, income, rush_defense=False):
    """Build a short human-readable rationale and push it to the spectator feed on
    meaningful change. Deduped via c._last_reason_sig so steady-state ticks stay quiet."""
    sol = int(round(roles.get("soldier", 0) * 100))
    spit = int(round(roles.get("spitter", 0) * 100))
    wrk = int(round(roles.get("worker", 0) * 100))

    # Rally action is a transient event — always worth narrating when it fires.
    rally_phrase = None
    if "attack_target" in rally_update and rally_update.get("attack_target"):
        rally_phrase = f"wave released → marching on enemy queen ({soldiers} soldiers)"
    elif "rally_point" in rally_update and rally_update.get("rally_point"):
        ra = rally_update.get("rally_release_at", "?")
        rally_phrase = f"massing a wave (release at {ra})"
    elif rush_defense and rally_update:
        rally_phrase = "holding the bulwark line — won't march while out-massed"
    elif "rally_point" in rally_update and rally_update.get("rally_point") is None and rally_update:
        rally_phrase = "regrouping — too few soldiers to commit"

    if rush_defense:
        head = f"⚠ out-massed under rush — turtling: {spit}% spitter + bulwarks, {sol}% soldier"
    elif enemy_rush:
        head = f"⚠ rush detected — {defense}, leaning {sol}% soldier / {spit}% spitter+bulwark"
    elif food < 150:
        head = f"{defense} — rebuilding economy ({wrk}% worker, {sol}% soldier)"
    else:
        head = f"{defense} — {sol}% soldier / {spit}% spitter, {wrk}% worker"

    text = head if not rally_phrase else f"{head}; {rally_phrase}"
    sig = (defense, sol // 10, spit // 10, enemy_rush, rush_defense, rally_phrase or "")
    if getattr(c, "_last_reason_sig", None) == sig:
        return
    c._last_reason_sig = sig
    c.push_decision(text, tick=world.tick, source="bot",
                    data={"stance": defense, "soldier_pct": sol, "spitter_pct": spit,
                          "worker_pct": wrk, "rush": bool(enemy_rush)})


def update_bot_strategy(world, colony_id):
    """Adaptive heuristic strategy for a bot colony."""
    c = world.colonies[colony_id]
    if not c.alive: return
    workers  = sum(1 for a in c.ants if a.type == A_WORKER)
    soldiers = sum(1 for a in c.ants if a.type == A_SOLDIER)
    food     = c.food
    income   = c.income_per_s

    # Detect enemy rush: count enemy soldiers within ~30 tiles of our nest
    enemy_rush = False
    if c.enemy:
        enemy_near = sum(
            1 for a in c.enemy.ants
            if a.type == A_SOLDIER
            and abs(a.x - c.nx) + abs(a.y - c.ny) <= 30
        )
        enemy_rush = enemy_near >= 3

    # Army strength comparison — drives the rush-defense override below. A soldier is
    # worth ~20, spitter ~15, scout ~8; workers don't fight. Cheap to compute.
    def _army(col):
        v = 0
        for a in col.ants:
            if   a.type == A_SOLDIER: v += 20
            elif a.type == A_SPITTER: v += 15
            elif a.type == A_SCOUT:   v += 8
        return v
    my_army = _army(c)
    enemy_army = _army(c.enemy) if c.enemy else 0
    behind = enemy_army > my_army * 1.1   # being out-massed

    # Spitters are HOLD-AND-FIRE defenders: keep them a small supplement.
    # Soldiers must stay the clear majority of combat (~80% baseline, never < ~70%
    # even under rush). Under rush we lift the spitter share to ~25-30% of combat,
    # not past it — the offensive soldier core is what wins games.
    if (food < 150 and workers < 20) or (income < 5 and food < 100):
        # Early / struggling — mostly economy, token combat presence
        if enemy_rush:
            roles, cap, defense = {"worker": 0.55, "scout": 0.10, "soldier": 0.26, "spitter": 0.09}, 70, "defensive"
        else:
            roles, cap, defense = {"worker": 0.65, "scout": 0.15, "soldier": 0.17, "spitter": 0.03}, 70, "defensive"
    elif food > 1800 and workers >= 40:
        # Aggressive push — soldier-heavy combat budget
        if enemy_rush:
            roles, cap, defense = {"worker": 0.25, "scout": 0.08, "soldier": 0.50, "spitter": 0.17}, 55, "aggressive"
        else:
            roles, cap, defense = {"worker": 0.25, "scout": 0.10, "soldier": 0.54, "spitter": 0.11}, 55, "aggressive"
    elif food > 1000 and workers >= 30:
        # Mid-game push
        if enemy_rush:
            roles, cap, defense = {"worker": 0.35, "scout": 0.10, "soldier": 0.40, "spitter": 0.15}, 55, "aggressive"
        else:
            roles, cap, defense = {"worker": 0.35, "scout": 0.12, "soldier": 0.44, "spitter": 0.09}, 55, "aggressive"
    elif food > 500 and workers >= 20:
        # Balanced
        if enemy_rush:
            roles, cap, defense = {"worker": 0.40, "scout": 0.10, "soldier": 0.37, "spitter": 0.13}, 60, "balanced"
        else:
            roles, cap, defense = {"worker": 0.45, "scout": 0.15, "soldier": 0.33, "spitter": 0.07}, 60, "balanced"
    else:
        # Early growth
        if enemy_rush:
            roles, cap, defense = {"worker": 0.48, "scout": 0.12, "soldier": 0.30, "spitter": 0.10}, 65, "balanced"
        else:
            roles, cap, defense = {"worker": 0.55, "scout": 0.20, "soldier": 0.21, "spitter": 0.04}, 65, "balanced"

    # ── Rush-defense emergency override ──────────────────────────────────────────
    # The bench showed the bot LOSING to a committed soldier rush (~37% vs rush)
    # despite a spitter+bulwark turtle beating that same rush 100%. The gap was
    # usage: the bot fielded too few spitters under rush and kept trying to counter-
    # charge instead of holding. When we're actively rushed AND out-massed, adopt the
    # proven defensive posture — spitter-heavy behind a forward bulwark line, hold
    # until we have the army edge — then revert to the soldier-majority offense that
    # wins games (handled by the branches above once `behind` clears).
    rush_defense = enemy_rush and behind
    if rush_defense:
        roles = {"worker": 0.28, "scout": 0.05, "soldier": 0.42, "spitter": 0.25}
        defense = "defensive"

    # ── Anti-turtle: bring raiders when pushing a fortified enemy ────────────────
    # A spitter+bulwark turtle hard-counters a pure soldier push (that's the 100%
    # defensive matchup that drags games to the draw timer). Raiders melt the bulwark/
    # larder line, so when we're on the offensive against a structured enemy, carve a
    # raider share out of soldiers to crack the turtle instead of stalling on it.
    enemy_structs = 0
    if c.enemy and not rush_defense:
        enemy_structs = sum(1 for st in world.structures
                            if st["colony"] == c.enemy.id and st.get("hp", 0) > 0
                            and st.get("type") in ("bulwark", "larder", "guard_post", "wall"))
    if enemy_structs >= 2 and defense in ("aggressive", "balanced") and roles.get("soldier", 0) > 0.30:
        raid = min(0.16, 0.04 * enemy_structs)
        take = min(raid, roles["soldier"] - 0.25)
        if take > 0:
            roles = dict(roles)
            roles["soldier"] -= take
            roles["raider"] = roles.get("raider", 0.0) + take

    # Rally to mass, then release as a wave — avoids 1-by-1 trickle deaths
    rally_update = {}
    mil = c.directive["military"]
    if rush_defense:
        # Do NOT march out while being out-massed — hold the line behind bulwarks +
        # spitters and let the rush break on it. Clear any standing attack order.
        if mil.get("attack_target") or mil.get("rally_point") or mil.get("auto_attack"):
            rally_update = {"rally_point": None, "rally_release_at": None,
                            "attack_target": None, "siege_priority": "queen",
                            "auto_attack": False, "retreat": False}
    elif c.enemy and c.alive:
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

    # ── Spectator reasoning: surface WHY the bot just did what it did ────────────
    # The frontend reasoning panel + ticker read colony.feed; emit a 1-line
    # rationale on meaningful transitions (stance/composition change or a rally
    # action) rather than every interval, so the feed reads as a narrative.
    _emit_bot_reason(c, world, roles, defense, enemy_rush, rally_update,
                     workers, soldiers, food, income, rush_defense=rush_defense)

    # Build structures from dirt when stable
    dirt = c.dirt
    own_structs = [st for st in world.structures if st["colony"] == c.id]
    own_gp = sum(1 for st in own_structs if st.get("type") == "guard_post")
    own_wt = sum(1 for st in own_structs if st.get("type") == "watchtower")
    own_bw = sum(1 for st in own_structs if st.get("type") == "bulwark")
    if c.enemy:
        def _bot_find_passable(bx, by, spread=6):
            for _ in range(10):
                tx = max(2, min(MAP_W-2, bx + random.randint(-spread, spread)))
                ty = max(2, min(MAP_H-2, by + random.randint(-spread, spread)))
                if world._passable(tx, ty):
                    return tx, ty
            return None, None

        # Bulwark: build a spiked forward barrier — prioritize when under rush
        # Place ~20% of the way toward the enemy (just outside our nest approach)
        # Under a rush we're losing, commit to a deeper bulwark line (4) and build it
        # off a lower worker threshold — the barricade is what lets spitters win the
        # trade. Otherwise stay conservative (3) and leave headroom for humans/agents.
        bw_cap = (_BOT_BULWARK_MAX + 1) if rush_defense else _BOT_BULWARK_MAX
        min_workers_bw = 4 if rush_defense else 6
        if own_bw < bw_cap and dirt >= BULWARK_COST and workers >= min_workers_bw:
            # Under rush: build immediately; otherwise wait for modest stability
            should_build_bw = enemy_rush or (workers >= 12 and world.tick > 100)
            if should_build_bw:
                bwx = int(c.nx + (c.enemy.nx - c.nx) * 0.18)
                bwy = int(c.ny + (c.enemy.ny - c.ny) * 0.18)
                bwx2, bwy2 = _bot_find_passable(bwx, bwy, spread=5)
                if bwx2 is not None:
                    entry = {"type": "bulwark", "x": bwx2, "y": bwy2}
                    if entry not in c.structure_queue:
                        c.structure_queue.append(entry)

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
