"""Builds the agent Situation Report (sitrep) — an honest, fog-respecting, factual read
of a colony's situation and the effect of its own orders. Instrument, not coach: facts
only, never prescriptions. See docs/superpowers/specs/2026-06-18-agent-situation-report-design.md
"""
from engine.constants import A_WORKER, A_SOLDIER, A_SCOUT, A_QUEEN

UNIT_VALUE = {A_WORKER: 5, A_SOLDIER: 20, A_SCOUT: 8}
ADVANCE_EPS = 3        # tiles of net COM movement over the window below which an attack is "no_effect"


def _orders(colony, world):
    mil = colony.directive["military"]
    out = []

    # attack_target / auto_attack — is the army advancing toward the objective?
    target = mil.get("attack_target")
    if not target and mil.get("auto_attack") and colony.enemy:
        target = [colony.enemy.nx, colony.enemy.ny]
    if target:
        hist = colony._army_com_history
        soldiers_in_siege = sum(
            1 for a in colony.ants if a.type == A_SOLDIER and colony.enemy
            and abs(a.x - colony.enemy.nx) + abs(a.y - colony.enemy.ny) <= 12)
        if soldiers_in_siege > 0:
            status, detail = "engaged", f"{soldiers_in_siege} soldiers in siege range of the objective"
        elif len(hist) >= 2:
            (_, x_old, _), (_, x_now, _) = hist[0], hist[-1]
            moved = x_now - x_old
            toward = target[0] - x_now
            progressing = (moved > 0 and toward > 0) or (moved < 0 and toward < 0)
            if progressing and abs(moved) >= ADVANCE_EPS:
                status, detail = "advancing", f"army center-of-mass x {x_old:.0f}->{x_now:.0f} toward {target}"
            else:
                status = "no_effect"
                detail = (f"army center-of-mass x {x_old:.0f}->{x_now:.0f} over last {len(hist)}t "
                          f"(not advancing toward {target})")
                rp = mil.get("rally_point")
                if rp and not mil.get("rally_release_at"):
                    detail += " — a rally_point with no rally_release_at is holding the army"
        else:
            status, detail = "unknown", "no soldiers yet"
        out.append({"intent": "attack_target", "target": list(target), "status": status, "detail": detail})

    # rally — staged vs release
    rp = mil.get("rally_point")
    if rp:
        pt = rp[0] if isinstance(rp[0], (list, tuple)) else rp
        rx, ry = int(pt[0]), int(pt[1])
        staged = sum(1 for a in colony.ants if a.type == A_SOLDIER
                     and abs(a.x - rx) + abs(a.y - ry) <= 4)
        release = mil.get("rally_release_at")
        if not release:
            status, detail = "holding", f"{staged} staged at ({rx},{ry}); no rally_release_at set (holds indefinitely)"
        elif staged >= release:
            status, detail = "released", f"{staged}/{release} staged — release threshold met"
        else:
            status, detail = "filling", f"{staged}/{release} staged at ({rx},{ry})"
        out.append({"intent": "rally", "point": [rx, ry], "status": status, "detail": detail})

    # spawn — target vs actual ratios
    counts = [sum(1 for a in colony.ants if a.type == t) for t in range(4)]
    total = counts[A_WORKER] + counts[A_SOLDIER] + counts[A_SCOUT]
    if total > 0:
        spawn = colony.directive.get("spawn", {})
        parts, drift = [], False
        for name, idx in (("worker", A_WORKER), ("soldier", A_SOLDIER), ("scout", A_SCOUT)):
            tgt = spawn.get(name, {}).get("target_ratio")
            if tgt is None:
                continue
            actual = counts[idx] / total
            parts.append(f"{name} target {tgt:.2f}/actual {actual:.2f}")
            if abs(actual - tgt) > 0.2:
                drift = True
        if parts:
            out.append({"intent": "spawn", "status": "drifting" if drift else "on_target",
                        "detail": "; ".join(parts)})
    return out


def _verdict(you, enemy, margin_floor=1):
    if enemy is None:
        return "unknown", None
    margin = you - enemy
    if abs(margin) <= margin_floor:
        return "even", margin
    return ("leading" if margin > 0 else "trailing"), margin


def _standing(colony, world):
    from engine.constants import MAP_W, MAP_H
    counts = [sum(1 for a in colony.ants if a.type == t) for t in range(4)]
    you_mil = counts[A_SOLDIER] * UNIT_VALUE[A_SOLDIER] + counts[A_SCOUT] * UNIT_VALUE[A_SCOUT] \
        + counts[A_WORKER] * UNIT_VALUE[A_WORKER]

    # enemy military: scouted-only (coarse counts), with staleness
    sc = getattr(colony, "enemy_scouted_counts", [0, 0, 0, 0])
    seen_tick = getattr(colony, "enemy_scouted_tick", -9999)
    if seen_tick < 0:
        enemy_mil, stale, mverdict, mmargin = "unknown", None, "unknown", None
    else:
        enemy_mil = sc[A_SOLDIER] * UNIT_VALUE[A_SOLDIER] + sc[A_SCOUT] * UNIT_VALUE[A_SCOUT] \
            + sc[A_WORKER] * UNIT_VALUE[A_WORKER]
        stale = world.tick - seen_tick
        mverdict, mmargin = _verdict(you_mil, enemy_mil)

    # economy: enemy not directly observable; honest proxy from scouted counts
    economy = {"you": {"food": int(colony.food), "income_per_s": round(colony.income_per_s, 1)},
               "enemy": "not_observable",
               "enemy_proxy": {"scouted_workers": sc[A_WORKER], "scouted_soldiers": sc[A_SOLDIER]}}

    # territory: your owned tiles (full); enemy fog-gated -> unknown
    you_tiles = sum(1 for o in getattr(world, "territory", b"") if o == colony.id)
    you_pct = round(you_tiles / (MAP_W * MAP_H) * 100, 1)

    # queen: your hp full; threat = enemy soldiers on your turf (observable); enemy queen hp only if sieged
    queen = next((a for a in colony.ants if a.type == A_QUEEN), None)
    qhp = int(queen.hp) if queen else 0
    threat = 0
    enemy_qhp = "unknown"
    if colony.enemy:
        threat = sum(1 for a in colony.enemy.ants if a.type == A_SOLDIER
                     and abs(a.x - colony.nx) + abs(a.y - colony.ny) <= 15)
        in_siege = sum(1 for a in colony.ants if a.type == A_SOLDIER
                       and abs(a.x - colony.enemy.nx) + abs(a.y - colony.enemy.ny) <= 12)
        if in_siege > 0:
            eq = next((a for a in colony.enemy.ants if a.type == A_QUEEN), None)
            if eq:
                enemy_qhp = int(eq.hp)

    return {
        "military": {"you": you_mil, "enemy": enemy_mil, "enemy_seen_tick": seen_tick if seen_tick >= 0 else None,
                     "enemy_stale_ticks": stale, "verdict": mverdict, "margin": mmargin},
        "economy": economy,
        "territory": {"you_pct": you_pct, "enemy_pct": "unknown", "verdict": "unknown"},
        "queen": {"your_hp": qhp, "your_hp_pct": round(qhp / 900 * 100), "threat_soldiers_near_nest": threat,
                  "enemy_queen_hp": enemy_qhp},
    }


def build_sitrep(colony, world):
    return {"standing": _standing(colony, world), "orders": _orders(colony, world)}
