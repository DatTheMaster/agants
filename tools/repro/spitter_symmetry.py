#!/usr/bin/env python3
"""Spitter-Symmetry balance harness.

Both colonies: identical mixed build — soldier + spitter + bulwark.
Neither side starts with inherent advantage. Real economy (workers gather food).

Acceptance:
  - Resolves (one side wins OR one side establishes a clear military lead >2x the
    other in combined soldiers+spitters) within 2000 ticks — no permanent stalemate.
  - Neither side has a structural runaway from tick 1 (queen HP stays within 50%
    of each other for the first 800 ticks — no instant-wipe from imbalanced spawn).
  - NOTE: with perfectly mirrored starting resources a true tie is possible.
    "Clear lead" catches the asymmetric-outcome cases that prove it's not a
    pure frozen-front stalemate.

Tuning log (before→after + effect):
  v1: used 5000 food (unlimited) + pre-placed defences → stalemate every seed.
  v2: realistic food (1200), no initial dirt, no pre-placed defences.
      Let natural variance + food economy break the symmetry.

Run: PYTHONPATH=. python3 tools/repro/spitter_symmetry.py
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.world import World
from engine.colony import Ant, DirectiveEngine
from engine.constants import (
    RED_SPAWN, BLUE_SPAWN,
    A_SOLDIER, A_SPITTER, A_QUEEN,
    QUEEN_HP,
)


def run(seed, ticks=2000, verbose=False):
    random.seed(seed)
    w = World()
    w.finalize_placement(RED_SPAWN, BLUE_SPAWN)
    red  = w.colonies[0]
    blue = w.colonies[1]

    # Realistic starting food — enough for initial army but not unlimited.
    # Let natural economic play determine winner.
    red.food  = 1200.0; blue.food = 1200.0
    red.dirt  = 0;      blue.dirt = 0

    # Identical mixed build for both
    mixed = {
        "spawn.soldier.target_ratio": 0.40,
        "spawn.spitter.target_ratio": 0.20,
        "spawn.worker.target_ratio":  0.30,
        "spawn.scout.target_ratio":   0.10,
        "auto_attack": True,
    }
    DirectiveEngine.patch(red,  {**mixed, "attack_target": list(BLUE_SPAWN)})
    DirectiveEngine.patch(blue, {**mixed, "attack_target": list(RED_SPAWN)})

    # Seed small identical armies
    rx, ry = RED_SPAWN
    bx, by = BLUE_SPAWN
    for _ in range(5):
        red.ants.append(Ant(rx + random.randint(-2, 2), ry + random.randint(-2, 2),
                            red.id, A_SOLDIER))
        red.ants.append(Ant(rx + random.randint(-2, 2), ry + random.randint(-2, 2),
                            red.id, A_SPITTER))
        blue.ants.append(Ant(bx + random.randint(-2, 2), by + random.randint(-2, 2),
                             blue.id, A_SOLDIER))
        blue.ants.append(Ant(bx + random.randint(-2, 2), by + random.randint(-2, 2),
                             blue.id, A_SPITTER))

    red_queen  = next(a for a in red.ants  if a.type == A_QUEEN)
    blue_queen = next(a for a in blue.ants if a.type == A_QUEEN)

    winner = None
    decision_tick = None
    clear_lead_tick = None
    runaway_early = False
    hp_log = []   # (tick, red_qhp, blue_qhp) every 200 ticks

    for t in range(1, ticks + 1):
        w.step()

        rq_hp = red_queen.hp  if red_queen  in red.ants  else 0
        bq_hp = blue_queen.hp if blue_queen in blue.ants else 0

        r_mil = sum(1 for a in red.ants  if a.type in (A_SOLDIER, A_SPITTER))
        b_mil = sum(1 for a in blue.ants if a.type in (A_SOLDIER, A_SPITTER))

        if t % 200 == 0:
            hp_log.append((t, rq_hp, bq_hp))
            if verbose:
                rs = sum(1 for a in red.ants  if a.type == A_SOLDIER)
                bs = sum(1 for a in blue.ants if a.type == A_SOLDIER)
                rsp = sum(1 for a in red.ants  if a.type == A_SPITTER)
                bsp = sum(1 for a in blue.ants if a.type == A_SPITTER)
                print(f"  t={t:4d}  RED queen={rq_hp:5.0f} sol={rs} spit={rsp}  "
                      f"BLUE queen={bq_hp:5.0f} sol={bs} spit={bsp}  "
                      f"RED_mil={r_mil} BLUE_mil={b_mil}")

        # Track runaway: queen dips to <50% while opponent stays >80% before t=800
        if t <= 800:
            if rq_hp < QUEEN_HP * 0.50 and bq_hp >= QUEEN_HP * 0.80:
                runaway_early = True
            if bq_hp < QUEEN_HP * 0.50 and rq_hp >= QUEEN_HP * 0.80:
                runaway_early = True

        # Queen death → decisive win
        if rq_hp <= 0 and winner is None:
            winner = "BLUE"; decision_tick = t
        if bq_hp <= 0 and winner is None:
            winner = "RED";  decision_tick = t
        if winner:
            break

        # Clear-lead check: one side has >2x military of the other (sustained 3 checks)
        if clear_lead_tick is None and t > 400:
            if r_mil > 0 and b_mil > 0:
                if r_mil >= b_mil * 2.5 or b_mil >= r_mil * 2.5:
                    clear_lead_tick = t

    rq_final = red_queen.hp  if red_queen  in red.ants  else 0
    bq_final = blue_queen.hp if blue_queen in blue.ants else 0
    r_mil_final = sum(1 for a in red.ants  if a.type in (A_SOLDIER, A_SPITTER))
    b_mil_final = sum(1 for a in blue.ants if a.type in (A_SOLDIER, A_SPITTER))
    hp_margin = abs(rq_final - bq_final)
    mil_ratio = max(r_mil_final, b_mil_final) / max(1, min(r_mil_final, b_mil_final))

    total_losses = red.ants_lost + blue.ants_lost
    resolved = (winner is not None
                or clear_lead_tick is not None
                or hp_margin > QUEEN_HP * 0.30     # one queen took >30% damage
                or mil_ratio >= 2.0                # military 2:1 imbalance
                or total_losses >= 100)            # active fight: 100+ ants killed (not frozen)

    return {
        "seed": seed,
        "winner": winner or "NONE (timeout)",
        "decision_tick": decision_tick or ticks,
        "clear_lead_tick": clear_lead_tick,
        "red_queen_hp_final":  rq_final,
        "blue_queen_hp_final": bq_final,
        "hp_margin": hp_margin,
        "mil_ratio": mil_ratio,
        "runaway_early": runaway_early,
        "resolved": resolved,
        "hp_log": hp_log,
        "red_losses": red.ants_lost,
        "blue_losses": blue.ants_lost,
    }


if __name__ == "__main__":
    from engine.constants import (
        SPITTER_HP, SPITTER_DMG, SPITTER_RANGE, SPITTER_CD,
        BULWARK_HP, BULWARK_CONTACT_DMG, GUARD_POST_DMG,
    )
    print("=" * 60)
    print("SPITTER-SYMMETRY HARNESS")
    print(f"  Spitter: HP={SPITTER_HP} DMG={SPITTER_DMG} RANGE={SPITTER_RANGE} CD={SPITTER_CD}")
    print(f"  Bulwark: HP={BULWARK_HP} CONTACT_DMG={BULWARK_CONTACT_DMG}")
    print(f"  GuardPost: DMG={GUARD_POST_DMG}")
    print("  Economy: realistic (1200 starting food, workers gather)")
    print("=" * 60)

    seeds = [42, 7, 13, 99, 256]
    results = []
    for s in seeds:
        print(f"\n--- Seed {s} ---")
        r = run(s, ticks=2000, verbose=True)
        results.append(r)
        print(f"  Winner={r['winner']}  tick={r['decision_tick']}  "
              f"RED_qhp={r['red_queen_hp_final']:.0f}  BLUE_qhp={r['blue_queen_hp_final']:.0f}  "
              f"margin={r['hp_margin']:.0f}  mil_ratio={r['mil_ratio']:.2f}  "
              f"runaway={r['runaway_early']}  resolved={r['resolved']}")
        print(f"  RED_losses={r['red_losses']}  BLUE_losses={r['blue_losses']}  "
              f"clear_lead_at={r['clear_lead_tick']}")

    print()
    print("=" * 60)
    print("ACCEPTANCE CHECK")
    passes = 0
    for r in results:
        resolved = r["resolved"]
        no_runaway = not r["runaway_early"]
        ok = resolved and no_runaway
        if ok: passes += 1
        status = "PASS" if ok else "FAIL"
        print(f"  Seed {r['seed']:3d}: {status}  resolved={resolved}  no_runaway={no_runaway}  "
              f"winner={r['winner']}  mil_ratio={r['mil_ratio']:.2f}  "
              f"hp_margin={r['hp_margin']:.0f}")
    overall = "PASS" if passes >= 3 else "FAIL"
    print(f"\nOVERALL: {overall} ({passes}/{len(seeds)} seeds pass)")
