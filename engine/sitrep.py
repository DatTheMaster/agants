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


def build_sitrep(colony, world):
    return {"orders": _orders(colony, world)}
