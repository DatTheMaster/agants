#!/usr/bin/env python3
"""Rush-vs-Counter balance harness.

RED = 80%-soldier rush (attack_target=BLUE nest, auto_attack).
BLUE = defender: spitter-heavy build (target_ratio~0.4) + soldiers, pre-placed
  2-3 bulwarks across the nest approach + 1-2 guard posts.

Acceptance: BLUE queen alive at tick 600+ AND RED soldier losses high.

Run: PYTHONPATH=. python3 tools/repro/rush_vs_counter.py
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.world import World
from engine.colony import Ant, DirectiveEngine
from engine.constants import (
    RED_SPAWN, BLUE_SPAWN,
    A_SOLDIER, A_WORKER, A_SPITTER, A_QUEEN,
    BULWARK_HP, GUARD_POST_HP,
    MAP_W, MAP_H, DIRT_CAP,
)

# Tuning log (before→after + effect):
# v1 baseline: SPITTER_HP=70, SPITTER_DMG=16, SPITTER_RANGE=5, SPITTER_CD=7,
#              BULWARK_HP=250, BULWARK_CONTACT_DMG=4, GUARD_POST_DMG=22
# Result: see harness output per seed — adjust constants.py if needed.


def _pre_placed_bulwark(w, colony_id, x, y):
    """Place a fully-built bulwark (no build time needed)."""
    st = {
        "x": x, "y": y, "colony": colony_id,
        "type": "bulwark",
        "hp": BULWARK_HP, "max_hp": BULWARK_HP,
        "cd": 0, "active": True,
        "build_progress": 999, "build_required": 40,
    }
    w.structures.append(st)
    return st


def _pre_placed_guard_post(w, colony_id, x, y):
    """Place a fully-built guard post."""
    st = {
        "x": x, "y": y, "colony": colony_id,
        "type": "guard_post",
        "hp": GUARD_POST_HP, "max_hp": GUARD_POST_HP,
        "cd": 0, "active": True,
        "build_progress": 999, "build_required": 100,
    }
    w.structures.append(st)
    return st


def run(seed, ticks=1500, verbose=False):
    random.seed(seed)
    w = World()
    w.finalize_placement(RED_SPAWN, BLUE_SPAWN)
    red  = w.colonies[0]
    blue = w.colonies[1]

    # ── Generous resources so production isn't the bottleneck ──
    red.food  = 5000.0
    blue.food = 5000.0
    red.dirt  = DIRT_CAP
    blue.dirt = DIRT_CAP

    # ── RED: 80% soldier rush ──
    DirectiveEngine.patch(red, {
        "spawn.soldier.target_ratio": 0.80,
        "spawn.worker.target_ratio":  0.15,
        "spawn.scout.target_ratio":   0.05,
        "spawn.spitter.target_ratio": 0.0,
        "auto_attack": True,
        "attack_target": list(BLUE_SPAWN),
    })

    # ── BLUE: defender — spitter-heavy + some soldiers ──
    DirectiveEngine.patch(blue, {
        "spawn.spitter.target_ratio": 0.40,
        "spawn.soldier.target_ratio": 0.30,
        "spawn.worker.target_ratio":  0.20,
        "spawn.scout.target_ratio":   0.10,
    })

    # BLUE pre-placed defences: bulwarks across the nest approach corridor
    # (around x=115–125, y=50 — the main approach lane from midfield)
    bx, by = BLUE_SPAWN
    _pre_placed_bulwark(w, blue.id, bx - 18, by)       # ~118,50
    _pre_placed_bulwark(w, blue.id, bx - 18, by - 4)   # ~118,46
    _pre_placed_bulwark(w, blue.id, bx - 15, by + 4)   # ~121,54
    # Guard posts behind the bulwark line — range 10 → covers the choke
    _pre_placed_guard_post(w, blue.id, bx - 10, by - 2)  # ~126,48
    _pre_placed_guard_post(w, blue.id, bx - 10, by + 2)  # ~126,52

    # ── Initial small army so neither side spawns from scratch ──
    for _ in range(8):
        red.ants.append(Ant(RED_SPAWN[0] + random.randint(-2, 2),
                            RED_SPAWN[1] + random.randint(-2, 2),
                            red.id, A_SOLDIER))
    # BLUE: seed a couple of spitters near the defensive line
    for _ in range(4):
        blue.ants.append(Ant(bx - 14 + random.randint(-2, 2),
                             by + random.randint(-3, 3),
                             blue.id, A_SPITTER))

    blue_queen = next(a for a in blue.ants if a.type == A_QUEEN)
    red_queen  = next(a for a in red.ants  if a.type == A_QUEEN)

    peak_red_soldiers = 0
    red_losses = 0
    red_start_soldiers = sum(1 for a in red.ants if a.type == A_SOLDIER)

    blue_queen_hp_log = []   # (tick, hp) every 100 ticks

    winner = None
    decision_tick = None

    for t in range(1, ticks + 1):
        w.step()

        red_soldiers_now  = sum(1 for a in red.ants  if a.type == A_SOLDIER)
        blue_soldiers_now = sum(1 for a in blue.ants if a.type == A_SOLDIER)
        spitters_now      = sum(1 for a in blue.ants if a.type == A_SPITTER)
        peak_red_soldiers = max(peak_red_soldiers, red_soldiers_now)

        if t % 100 == 0:
            bq_hp = blue_queen.hp if blue_queen in blue.ants else 0
            blue_queen_hp_log.append((t, bq_hp))
            if verbose:
                rq_hp = red_queen.hp if red_queen in red.ants else 0
                print(f"  t={t:4d}  RED_sol={red_soldiers_now:3d}  BLUE_sol={blue_soldiers_now:2d}  "
                      f"BLUE_spit={spitters_now:2d}  BLUE_queen={bq_hp:4.0f}  RED_queen={rq_hp:4.0f}")

        if blue_queen not in blue.ants and winner is None:
            winner = "RED"; decision_tick = t
        if red_queen not in red.ants and winner is None:
            winner = "BLUE"; decision_tick = t
        if winner:
            break

    # Compute losses roughly: initial + spawned - alive
    red_losses = red.ants_lost
    bq_final_hp = blue_queen.hp if blue_queen in blue.ants else 0

    return {
        "seed": seed,
        "winner": winner or "NONE (timeout)",
        "decision_tick": decision_tick or ticks,
        "blue_queen_hp_final": bq_final_hp,
        "blue_queen_hp_log": blue_queen_hp_log,
        "peak_red_soldiers": peak_red_soldiers,
        "red_losses": red_losses,
        "blue_spitters_at_end": sum(1 for a in blue.ants if a.type == A_SPITTER),
        "blue_structures_alive": len([s for s in w.structures
                                      if s["colony"] == blue.id and s.get("active")]),
    }


if __name__ == "__main__":
    from engine.constants import (
        SPITTER_HP, SPITTER_DMG, SPITTER_RANGE, SPITTER_CD,
        BULWARK_HP, BULWARK_CONTACT_DMG, GUARD_POST_DMG, GUARD_POST_RANGE,
    )
    print("=" * 60)
    print("RUSH-VS-COUNTER HARNESS")
    print(f"  Spitter: HP={SPITTER_HP} DMG={SPITTER_DMG} RANGE={SPITTER_RANGE} CD={SPITTER_CD}")
    print(f"  Bulwark: HP={BULWARK_HP} CONTACT_DMG={BULWARK_CONTACT_DMG}")
    print(f"  GuardPost: DMG={GUARD_POST_DMG} RANGE={GUARD_POST_RANGE}")
    print("=" * 60)

    seeds = [42, 7, 13, 99, 256]
    results = []
    for s in seeds:
        r = run(s, ticks=1500, verbose=True)
        results.append(r)
        print(f"\nSeed {s:3d}: winner={r['winner']:18s}  tick={r['decision_tick']:4d}  "
              f"BLUE_queen_hp={r['blue_queen_hp_final']:5.0f}  "
              f"peak_RED_sol={r['peak_red_soldiers']:3d}  RED_losses={r['red_losses']:3d}")
        print(f"         BLUE queen HP timeline: {r['blue_queen_hp_log']}")

    print()
    print("=" * 60)
    print("ACCEPTANCE CHECK")
    # Acceptance: BLUE queen alive at tick 600+ (blue_queen_hp_log[5] is tick 600)
    passes = 0
    for r in results:
        # Find hp at or after tick 600
        hp_at_600 = next((hp for t, hp in r["blue_queen_hp_log"] if t >= 600), 0)
        alive_at_600 = hp_at_600 > 0
        high_red_losses = r["red_losses"] >= 10  # at least 10 red soldiers killed
        ok = alive_at_600 and high_red_losses
        status = "PASS" if ok else "FAIL"
        if ok: passes += 1
        print(f"  Seed {r['seed']:3d}: {status}  (BLUE_queen@600={hp_at_600:.0f}  "
              f"RED_losses={r['red_losses']}  winner={r['winner']})")
    overall = "PASS" if passes >= 3 else "FAIL"
    print(f"\nOVERALL: {overall} ({passes}/{len(seeds)} seeds pass)")
