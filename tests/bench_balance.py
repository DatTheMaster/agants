"""Headless symmetric bot-vs-bot bench for measuring RTS balance.

Runs full matches with no HTTP server: create a World, finalize placement, then
each LLM_INTERVAL ticks call a pluggable "brain" for each colony and step the
engine until someone wins or the tick cap is hit.

The point of this harness is to make balance changes *measurable* instead of
guessed. The eval feedback claimed "soldier rush is a forced win" and "spitters
are a decorative counter" — this lets us put numbers on both: pit a soldier-rush
archetype against a spitter+bulwark defense and read the winrate. Tune a
constant, re-run, see the winrate move toward 50%.

Usage:
    python3 tests/bench_balance.py                       # default matchup sweep
    python3 tests/bench_balance.py rush spitter_defense 40   # one matchup, 40 games
    python3 tests/bench_balance.py --list                # list brains
"""
import sys, os, time, random
sys.path.insert(0, ".")

from engine.world import World
from engine.constants import A_WORKER, A_SOLDIER, A_SCOUT, A_SPITTER, A_QUEEN, A_RAIDER
from engine.colony import Ant
from bot import update_bot_strategy

LLM_INTERVAL = 15
TICK_CAP = 2500          # ~ matches resolve well under this; draw if exceeded
RED_POS, BLUE_POS = (14, 50), (136, 50)


# ── unit/army accounting ─────────────────────────────────────────────────────
_ARMY_VALUE = {A_WORKER: 5, A_SOLDIER: 20, A_SCOUT: 8, A_SPITTER: 15, A_QUEEN: 0}

def army_value(c):
    return sum(_ARMY_VALUE.get(a.type, 0) for a in c.ants)

def counts(c):
    d = {"worker": 0, "soldier": 0, "scout": 0, "spitter": 0}
    names = {A_WORKER: "worker", A_SOLDIER: "soldier", A_SCOUT: "scout", A_SPITTER: "spitter"}
    for a in c.ants:
        n = names.get(a.type)
        if n: d[n] += 1
    return d


# ── brains ───────────────────────────────────────────────────────────────────
# A brain is f(world, colony_id) -> None, called every LLM_INTERVAL ticks. It
# patches the colony directive via set_strategy (legacy flat keys) or
# update_bot_strategy. Brains read only public colony/world state, same as a real
# agent would.

def brain_bot(world, cid):
    """The shipped adaptive heuristic bot."""
    update_bot_strategy(world, cid)


def brain_rush(world, cid):
    """Pure soldier rush: minimal economy, flood soldiers, march on the enemy nest
    the moment a wave exists. The 'forced win' archetype the eval described."""
    c = world.colonies[cid]
    if not c.alive: return
    workers = sum(1 for a in c.ants if a.type == A_WORKER)
    soldiers = sum(1 for a in c.ants if a.type == A_SOLDIER)
    # Just enough workers to fuel soldier production, then go all-in on soldiers.
    if workers < 14 and world.tick < 220:
        roles = {"worker": 0.6, "scout": 0.05, "soldier": 0.35, "spitter": 0.0}
    else:
        roles = {"worker": 0.18, "scout": 0.02, "soldier": 0.80, "spitter": 0.0}
    upd = {"roles": roles, "worker_cap": 16, "defense": "aggressive"}
    mil = c.directive["military"]
    if c.enemy:
        rx = int(c.nx + (c.enemy.nx - c.nx) * 0.40)
        ry = int(c.ny + (c.enemy.ny - c.ny) * 0.40)
        if soldiers >= 3 and not mil.get("rally_point") and not mil.get("attack_target"):
            upd.update({"rally_point": [rx, ry], "rally_release_at": 10,
                        "rally_mode": "normal", "siege_priority": "queen"})
        elif mil.get("rally_point"):
            _rp = mil["rally_point"]
            _rx, _ry = (int(_rp[0]), int(_rp[1])) if not isinstance(_rp[0], (list, tuple)) else (int(_rp[0][0]), int(_rp[0][1]))
            staged = sum(1 for a in c.ants if a.type == A_SOLDIER
                         and abs(a.x - _rx) + abs(a.y - _ry) <= 6)
            if staged >= mil.get("rally_release_at", 10) or (soldiers >= 10 and world.tick > 350):
                upd.update({"rally_point": None, "rally_release_at": None,
                            "attack_target": [c.enemy.nx, c.enemy.ny],
                            "siege_priority": "queen", "auto_attack": True})
    c.set_strategy(upd)


def brain_spitter_defense(world, cid):
    """Economy + spitter/bulwark turtle: the intended rush counter. Builds bulwarks
    forward, fields a meaningful spitter share, keeps soldiers home on defense, and
    only counter-attacks once it has a clear army edge."""
    c = world.colonies[cid]
    if not c.alive: return
    workers = sum(1 for a in c.ants if a.type == A_WORKER)
    dirt = c.dirt
    enemy_army = army_value(c.enemy) if c.enemy else 0
    my_army = army_value(c)

    # Spitter-forward defensive composition; lean harder on spitters under pressure.
    if world.tick < 200:
        roles = {"worker": 0.62, "scout": 0.10, "soldier": 0.20, "spitter": 0.08}
    elif enemy_army > my_army:
        roles = {"worker": 0.30, "scout": 0.05, "soldier": 0.35, "spitter": 0.30}
    else:
        roles = {"worker": 0.40, "scout": 0.08, "soldier": 0.34, "spitter": 0.18}

    upd = {"roles": roles, "worker_cap": 40, "defense": "defensive",
           "retreat": False, "attack_target": None, "rally_point": None}

    # Forward bulwark chain between nest and enemy (toward the lane the rush uses).
    own_bw = sum(1 for st in world.structures if st["colony"] == c.id and st.get("type") == "bulwark")
    if c.enemy and own_bw < 4 and dirt >= 50 and workers >= 6:
        frac = 0.16 + 0.03 * own_bw
        bx = int(c.nx + (c.enemy.nx - c.nx) * frac)
        by = int(c.ny + (c.enemy.ny - c.ny) * frac) + (own_bw - 1) * 3
        by = max(2, min(97, by))
        if world._passable(bx, by) or True:  # bulwarks may sit on blocked tiles
            entry = {"type": "bulwark", "x": bx, "y": by}
            if entry not in c.structure_queue:
                c.structure_queue.append(entry)

    # Larders for sustain once stable.
    own_lr = sum(1 for st in world.structures if st["colony"] == c.id and st.get("type") == "larder")
    if c.enemy and own_lr < 2 and dirt >= 150 and world.tick > 200:
        lx, ly = c.nx + 5, c.ny + (own_lr * 2 - 1)
        upd_struct = {"type": "larder", "x": lx, "y": ly}
        if upd_struct not in c.structure_queue:
            c.structure_queue.append(upd_struct)

    # Counter-attack only with a decisive army edge.
    if c.enemy and my_army > enemy_army * 1.5 and my_army > 200:
        upd.update({"attack_target": [c.enemy.nx, c.enemy.ny],
                    "siege_priority": "queen", "auto_attack": True, "defense": "aggressive"})
    c.set_strategy(upd)


def brain_raider_push(world, cid):
    """Anti-turtle assault archetype: solid economy, then a raider-HEAVY committed
    push (raiders clear the bulwark/larder line, soldiers exploit the breach). Used to
    test whether RAIDERS can crack a spitter+bulwark turtle when actually committed —
    isolating the unit's potential from the shipped bot's passivity."""
    c = world.colonies[cid]
    if not c.alive: return
    workers = sum(1 for a in c.ants if a.type == A_WORKER)
    enemy_structs = 0
    if c.enemy:
        enemy_structs = sum(1 for st in world.structures if st["colony"] == c.enemy.id
                            and st.get("hp", 0) > 0)
    if workers < 22 and world.tick < 260:
        roles = {"worker": 0.60, "scout": 0.06, "soldier": 0.28, "spitter": 0.0, "raider": 0.06}
    else:
        # raider-heavy assault composition
        roles = {"worker": 0.22, "scout": 0.03, "soldier": 0.45, "spitter": 0.0, "raider": 0.30}
    upd = {"roles": roles, "worker_cap": 26, "defense": "aggressive"}
    mil = c.directive["military"]
    soldiers = sum(1 for a in c.ants if a.type == A_SOLDIER)
    raiders = sum(1 for a in c.ants if a.type == A_RAIDER)
    if c.enemy:
        rx = int(c.nx + (c.enemy.nx - c.nx) * 0.40)
        ry = int(c.ny + (c.enemy.ny - c.ny) * 0.40)
        force = soldiers + raiders
        if force >= 3 and not mil.get("rally_point") and not mil.get("attack_target"):
            upd.update({"rally_point": [rx, ry], "rally_release_at": 12,
                        "rally_mode": "normal", "siege_priority": "queen"})
        elif mil.get("rally_point"):
            _rp = mil["rally_point"]
            _rx, _ry = (int(_rp[0]), int(_rp[1])) if not isinstance(_rp[0], (list, tuple)) else (int(_rp[0][0]), int(_rp[0][1]))
            staged = sum(1 for a in c.ants if a.type in (A_SOLDIER, A_RAIDER)
                         and abs(a.x - _rx) + abs(a.y - _ry) <= 6)
            if staged >= 12 or (force >= 12 and world.tick > 360):
                upd.update({"rally_point": None, "rally_release_at": None,
                            "attack_target": [c.enemy.nx, c.enemy.ny],
                            "siege_priority": "queen", "auto_attack": True})
        # once attacking, STAY committed (don't oscillate back to re-mass)
    c.set_strategy(upd)


def brain_balanced_aggro(world, cid):
    """Healthy-economy aggressor: keeps a ~40-worker economy AND builds a soldier+raider
    army, pushing in committed waves (raiders dive the structures, soldiers fight). The
    proper test of whether *good* aggression can beat a turtle — unlike the all-in
    rush/raider_push brains that collapse economically after a failed push."""
    c = world.colonies[cid]
    if not c.alive: return
    workers = sum(1 for a in c.ants if a.type == A_WORKER)
    soldiers = sum(1 for a in c.ants if a.type == A_SOLDIER)
    raiders = sum(1 for a in c.ants if a.type == A_RAIDER)
    enemy_structs = 0
    if c.enemy:
        enemy_structs = sum(1 for st in world.structures if st["colony"] == c.enemy.id and st.get("hp", 0) > 0)
    if workers < 24 and world.tick < 300:
        roles = {"worker": 0.55, "scout": 0.08, "soldier": 0.30, "spitter": 0.0, "raider": 0.07}
    else:
        # sustain economy (worker share replaces aging workers) + heavy mixed military
        raid = 0.24 if enemy_structs >= 1 else 0.12
        roles = {"worker": 0.32, "scout": 0.04, "soldier": round(0.64 - raid, 2), "spitter": 0.0, "raider": raid}
    upd = {"roles": roles, "worker_cap": 40, "defense": "aggressive"}
    mil = c.directive["military"]
    if c.enemy:
        rx = int(c.nx + (c.enemy.nx - c.nx) * 0.42)
        ry = int(c.ny + (c.enemy.ny - c.ny) * 0.42)
        force = soldiers + raiders
        if force >= 4 and not mil.get("rally_point") and not mil.get("attack_target"):
            upd.update({"rally_point": [rx, ry], "rally_release_at": 14,
                        "rally_mode": "normal", "siege_priority": "queen"})
        elif mil.get("rally_point"):
            _rp = mil["rally_point"]
            _rx, _ry = (int(_rp[0]), int(_rp[1])) if not isinstance(_rp[0], (list, tuple)) else (int(_rp[0][0]), int(_rp[0][1]))
            staged = sum(1 for a in c.ants if a.type in (A_SOLDIER, A_RAIDER)
                         and abs(a.x - _rx) + abs(a.y - _ry) <= 6)
            if staged >= 14 or (force >= 16 and world.tick > 400):
                upd.update({"rally_point": None, "rally_release_at": None,
                            "attack_target": [c.enemy.nx, c.enemy.ny],
                            "siege_priority": "queen", "auto_attack": True})
        elif mil.get("attack_target") and force < 4:
            upd.update({"attack_target": None, "auto_attack": False})  # army wiped — re-mass
    c.set_strategy(upd)


def brain_naive_defense(world, cid):
    """Control: economy + SOLDIERS only — no spitters, no bulwarks. A 'turtle' that
    relies purely on soldier mass to hold. If rush can't beat this, the rush brain is
    broken; if rush beats this but loses to spitter_defense, the spitter/bulwark kit
    is what's carrying the counter."""
    c = world.colonies[cid]
    if not c.alive: return
    enemy_army = army_value(c.enemy) if c.enemy else 0
    my_army = army_value(c)
    if world.tick < 200:
        roles = {"worker": 0.62, "scout": 0.10, "soldier": 0.28, "spitter": 0.0}
    elif enemy_army > my_army:
        roles = {"worker": 0.30, "scout": 0.05, "soldier": 0.65, "spitter": 0.0}
    else:
        roles = {"worker": 0.42, "scout": 0.08, "soldier": 0.50, "spitter": 0.0}
    upd = {"roles": roles, "worker_cap": 40, "defense": "defensive",
           "retreat": False, "attack_target": None, "rally_point": None}
    if c.enemy and my_army > enemy_army * 1.5 and my_army > 200:
        upd.update({"attack_target": [c.enemy.nx, c.enemy.ny],
                    "siege_priority": "queen", "auto_attack": True, "defense": "aggressive"})
    c.set_strategy(upd)


BRAINS = {
    "bot": brain_bot,
    "rush": brain_rush,
    "spitter_defense": brain_spitter_defense,
    "naive_defense": brain_naive_defense,
    "raider_push": brain_raider_push,
    "balanced_aggro": brain_balanced_aggro,
}


# ── match runner ─────────────────────────────────────────────────────────────
def run_match(red_brain, blue_brain, seed=0, tick_cap=TICK_CAP):
    random.seed(seed)
    # Ant ids persist across matches via a class counter; reset for clean state.
    Ant._id = 0
    w = World()
    w.finalize_placement(RED_POS, BLUE_POS)
    brains = [BRAINS[red_brain], BRAINS[blue_brain]]
    bot_last = [-LLM_INTERVAL, -LLM_INTERVAL]
    while w.winner is None and w.tick < tick_cap:
        for cid in (0, 1):
            if w.tick - bot_last[cid] >= LLM_INTERVAL:
                try:
                    brains[cid](w, cid)
                except Exception as e:
                    print(f"  brain error c{cid} @t{w.tick}: {e}")
                bot_last[cid] = w.tick
        w.step()
    red, blue = w.colonies[0], w.colonies[1]
    return {
        "winner": w.winner,            # 0=red, 1=blue, "draw", or None(=cap)
        "ticks": w.tick,
        "red_army": army_value(red), "blue_army": army_value(blue),
        "red_counts": counts(red), "blue_counts": counts(blue),
        "red_alive": red.alive, "blue_alive": blue.alive,
    }


def run_matchup(red_brain, blue_brain, n=20, base_seed=1000):
    """Run n games of red_brain vs blue_brain AND n with sides swapped, to cancel
    any first-mover / side asymmetry. Returns aggregate stats keyed by brain name."""
    wins = {red_brain: 0, blue_brain: 0, "draw": 0}
    ticks_total, games = 0, 0
    t0 = time.time()
    for i in range(n):
        # normal sides
        r = run_match(red_brain, blue_brain, seed=base_seed + i)
        _tally(r, red_brain, blue_brain, wins); ticks_total += r["ticks"]; games += 1
        # swapped sides
        r = run_match(blue_brain, red_brain, seed=base_seed + 5000 + i)
        _tally(r, blue_brain, red_brain, wins); ticks_total += r["ticks"]; games += 1
    dt = time.time() - t0
    return {"wins": wins, "games": games, "avg_ticks": ticks_total / games, "secs": dt}


def _tally(r, red_name, blue_name, wins):
    if r["winner"] == 0:   wins[red_name] += 1
    elif r["winner"] == 1: wins[blue_name] += 1
    else:                  wins["draw"] += 1


def _print_matchup(a, b, res):
    w = res["wins"]
    total = res["games"]
    a_pct = 100 * w[a] / total
    b_pct = 100 * w[b] / total
    d_pct = 100 * w["draw"] / total
    print(f"\n=== {a}  vs  {b}   ({total} games, {res['secs']:.1f}s, avg {res['avg_ticks']:.0f}t) ===")
    print(f"  {a:18s} {w[a]:3d}  ({a_pct:5.1f}%)")
    print(f"  {b:18s} {w[b]:3d}  ({b_pct:5.1f}%)")
    print(f"  {'draw/cap':18s} {w['draw']:3d}  ({d_pct:5.1f}%)", flush=True)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--list" in sys.argv:
        print("brains:", ", ".join(BRAINS)); sys.exit(0)
    if len(args) >= 2:
        a, b = args[0], args[1]
        n = int(args[2]) if len(args) > 2 else 20
        res = run_matchup(a, b, n=n)
        _print_matchup(a, b, res)
    else:
        # Default sweep: the questions the eval raised.
        n = 12
        for a, b in [("bot", "bot"), ("rush", "naive_defense"),
                     ("rush", "spitter_defense"), ("rush", "bot")]:
            res = run_matchup(a, b, n=n)
            _print_matchup(a, b, res)
