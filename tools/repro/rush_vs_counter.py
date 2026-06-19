#!/usr/bin/env python3
"""Rush-vs-Counter balance harness.

RED = 80%-soldier rush (attack_target=BLUE nest, auto_attack).
BLUE = defender: spitter-heavy build (target_ratio~0.4) + soldiers, pre-placed
  2-3 bulwarks across the nest approach + 1-2 guard posts.

Acceptance (PRIMARY GOAL = rush is blunted):
  - BLUE queen alive at end AND
  - Substantial RED SOLDIER attrition (red_soldier_losses >= 50) AND
  - BLUE not wiped

Report per seed:
  - red_soldier_losses (soldiers specifically, not all ants)
  - min_chebyshev_dist_to_queen: closest any RED soldier ever got to BLUE queen
  - peak_red_soldiers_near_nest: peak count of RED soldiers within 6 tiles of BLUE nest
  - BLUE queen HP at end

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

    # BLUE pre-placed defences: bulwarks at a defensible line inside BLUE territory
    # (~x=110-120, just inside BLUE approach corridor — not hugging the nest)
    # BLUE_SPAWN = (136, 50), so x=110 is ~26 tiles out from nest (realistic hold line)
    bx, by = BLUE_SPAWN
    _pre_placed_bulwark(w, blue.id, bx - 26, by)       # ~110,50 — center lane hold
    _pre_placed_bulwark(w, blue.id, bx - 26, by - 5)   # ~110,45 — upper approach
    _pre_placed_bulwark(w, blue.id, bx - 23, by + 5)   # ~113,55 — lower approach
    # Guard posts just behind the bulwark line — range 10 → covers the choke
    _pre_placed_guard_post(w, blue.id, bx - 18, by - 2)  # ~118,48
    _pre_placed_guard_post(w, blue.id, bx - 18, by + 2)  # ~118,52

    # ── Initial small army so neither side spawns from scratch ──
    for _ in range(8):
        red.ants.append(Ant(RED_SPAWN[0] + random.randint(-2, 2),
                            RED_SPAWN[1] + random.randint(-2, 2),
                            red.id, A_SOLDIER))
    # BLUE: seed a couple of spitters near the defensive line
    for _ in range(4):
        blue.ants.append(Ant(bx - 20 + random.randint(-2, 2),
                             by + random.randint(-3, 3),
                             blue.id, A_SPITTER))

    blue_queen = next(a for a in blue.ants if a.type == A_QUEEN)
    red_queen  = next(a for a in red.ants  if a.type == A_QUEEN)

    peak_red_soldiers = 0

    # Track RED SOLDIER losses specifically (not omnibus ants_lost which includes all types)
    # We track soldier count each tick and accumulate decreases
    prev_red_soldiers = sum(1 for a in red.ants if a.type == A_SOLDIER)
    red_soldier_losses = 0

    # Track how close RED soldiers got to BLUE's queen
    min_chebyshev_dist_to_queen = 9999
    peak_red_soldiers_near_nest = 0   # peak count within 6 tiles of BLUE nest

    blue_queen_hp_log = []   # (tick, hp) every 100 ticks

    winner = None
    decision_tick = None

    for t in range(1, ticks + 1):
        w.step()

        red_soldiers_now  = sum(1 for a in red.ants  if a.type == A_SOLDIER)
        blue_soldiers_now = sum(1 for a in blue.ants if a.type == A_SOLDIER)
        spitters_now      = sum(1 for a in blue.ants if a.type == A_SPITTER)
        peak_red_soldiers = max(peak_red_soldiers, red_soldiers_now)

        # Accumulate RED SOLDIER deaths this tick (soldiers only, not workers/scouts/spitters)
        soldier_delta = prev_red_soldiers - red_soldiers_now
        if soldier_delta > 0:
            red_soldier_losses += soldier_delta
        prev_red_soldiers = red_soldiers_now

        # Track proximity metrics to BLUE queen
        bq_alive = blue_queen in blue.ants
        if bq_alive:
            bqx, bqy = blue_queen.x, blue_queen.y
            red_near_nest = 0
            for a in red.ants:
                if a.type == A_SOLDIER:
                    # Chebyshev distance to BLUE queen
                    cheb = max(abs(a.x - bqx), abs(a.y - bqy))
                    if cheb < min_chebyshev_dist_to_queen:
                        min_chebyshev_dist_to_queen = cheb
                    # Count within 6 tiles of BLUE nest (use BLUE_SPAWN as reference)
                    nest_cheb = max(abs(a.x - bx), abs(a.y - by))
                    if nest_cheb <= 6:
                        red_near_nest += 1
            peak_red_soldiers_near_nest = max(peak_red_soldiers_near_nest, red_near_nest)

        if t % 100 == 0:
            bq_hp = blue_queen.hp if blue_queen in blue.ants else 0
            blue_queen_hp_log.append((t, bq_hp))
            if verbose:
                rq_hp = red_queen.hp if red_queen in red.ants else 0
                near_str = f"near_nest={peak_red_soldiers_near_nest}"
                print(f"  t={t:4d}  RED_sol={red_soldiers_now:3d}  BLUE_sol={blue_soldiers_now:2d}  "
                      f"BLUE_spit={spitters_now:2d}  BLUE_queen={bq_hp:4.0f}  RED_queen={rq_hp:4.0f}  "
                      f"sol_losses={red_soldier_losses}  min_dist={min_chebyshev_dist_to_queen}  {near_str}")

        if blue_queen not in blue.ants and winner is None:
            winner = "RED"; decision_tick = t
        if red_queen not in red.ants and winner is None:
            winner = "BLUE"; decision_tick = t
        if winner:
            break

    bq_final_hp = blue_queen.hp if blue_queen in blue.ants else 0

    return {
        "seed": seed,
        "winner": winner or "NONE (timeout)",
        "decision_tick": decision_tick or ticks,
        "blue_queen_hp_final": bq_final_hp,
        "blue_queen_hp_log": blue_queen_hp_log,
        "peak_red_soldiers": peak_red_soldiers,
        "red_soldier_losses": red_soldier_losses,   # SOLDIERS only, not all ants
        "red_total_losses": red.ants_lost,           # all red losses (for reference)
        "min_chebyshev_dist_to_queen": min_chebyshev_dist_to_queen,
        "peak_red_soldiers_near_nest": peak_red_soldiers_near_nest,
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
    print("RUSH-VS-COUNTER HARNESS (hardened)")
    print(f"  Spitter: HP={SPITTER_HP} DMG={SPITTER_DMG} RANGE={SPITTER_RANGE} CD={SPITTER_CD}")
    print(f"  Bulwark: HP={BULWARK_HP} CONTACT_DMG={BULWARK_CONTACT_DMG}")
    print(f"  GuardPost: DMG={GUARD_POST_DMG} RANGE={GUARD_POST_RANGE}")
    print("  Metrics: RED SOLDIER losses (not omnibus), min Chebyshev dist to BLUE queen,")
    print("           peak RED soldiers within 6 tiles of BLUE nest")
    print("=" * 60)

    seeds = [42, 7, 13, 99, 256]
    results = []
    for s in seeds:
        r = run(s, ticks=1500, verbose=True)
        results.append(r)
        print(f"\nSeed {s:3d}: winner={r['winner']:18s}  tick={r['decision_tick']:4d}  "
              f"BLUE_queen_hp={r['blue_queen_hp_final']:5.0f}  "
              f"peak_RED_sol={r['peak_red_soldiers']:3d}  "
              f"RED_sol_losses={r['red_soldier_losses']:3d}  (total_losses={r['red_total_losses']})")
        print(f"         min_dist_to_BLUE_queen={r['min_chebyshev_dist_to_queen']}  "
              f"peak_near_nest={r['peak_red_soldiers_near_nest']}")
        print(f"         BLUE queen HP timeline: {r['blue_queen_hp_log']}")

    print()
    print("=" * 60)
    print("ACCEPTANCE CHECK  (PRIMARY GOAL: rush is blunted)")
    print("  Criteria: BLUE queen alive at end AND red_soldier_losses >= 50 AND BLUE not wiped")
    passes = 0
    for r in results:
        # Find hp at or after tick 600
        hp_at_600 = next((hp for t, hp in r["blue_queen_hp_log"] if t >= 600), 0)
        alive_at_600 = hp_at_600 > 0
        # PRIMARY: soldier losses (not omnibus) must be substantial
        high_red_sol_losses = r["red_soldier_losses"] >= 50
        blue_not_wiped = r["winner"] != "RED"
        ok = alive_at_600 and high_red_sol_losses and blue_not_wiped
        status = "PASS" if ok else "FAIL"
        if ok: passes += 1
        perimeter_note = ""
        if r["min_chebyshev_dist_to_queen"] <= 6:
            perimeter_note = " [RED BROKE perimeter — was stopped near queen]"
        elif r["min_chebyshev_dist_to_queen"] <= 15:
            perimeter_note = " [RED approached nest — defense held]"
        else:
            perimeter_note = " [RED never reached nest — test may be too easy]"
        print(f"  Seed {r['seed']:3d}: {status}  (BLUE_queen@600={hp_at_600:.0f}  "
              f"RED_sol_losses={r['red_soldier_losses']}  winner={r['winner']})")
        print(f"           min_dist_to_queen={r['min_chebyshev_dist_to_queen']}  "
              f"peak_near_nest={r['peak_red_soldiers_near_nest']}{perimeter_note}")
    overall = "PASS" if passes >= 3 else "FAIL"
    print(f"\nOVERALL: {overall} ({passes}/{len(seeds)} seeds pass)")
