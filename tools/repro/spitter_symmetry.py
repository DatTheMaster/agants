#!/usr/bin/env python3
"""Spitter-Symmetry balance harness.

Both colonies: identical mixed build — soldier + spitter + bulwark.
Neither side starts with inherent advantage. Real economy (workers gather food).

Acceptance:
  - Resolves (one side wins OR one side establishes a SUSTAINED clear military
    lead >2.5x the other for 3 consecutive checks at 200-tick intervals) within
    2000 ticks — no permanent stalemate.
  - ALTERNATIVELY: one queen takes >30% HP damage (hp_margin > 0.30 * QUEEN_HP).
  - If none fire by the tick budget → STALEMATE. Reported honestly as stalemate,
    NOT relabeled as resolved.
  - Neither side has a structural runaway from tick 1 (queen HP stays within 50%
    of each other for the first 800 ticks — no instant-wipe from imbalanced spawn).

NOTE: Symmetric draws are NOT automatically a bug. This harness reports truth:
  how many seeds resolve vs stalemate, and the attrition character of each.
  Do NOT force a pass — stalemate seeds are reported honestly.

Tuning log (before→after + effect):
  v1: used 5000 food (unlimited) + pre-placed defences → stalemate every seed.
  v2: realistic food (1200), no initial dirt, no pre-placed defences.
      Let natural variance + food economy break the symmetry.
  v3: removed total_losses >= 100 fallback from resolved (masked stalemates).
      Requires winner OR sustained lead (3 consecutive checks) OR >30% HP margin.
      Stalemate seeds reported as STALEMATE, not resolved.

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
    runaway_early = False
    hp_log = []   # (tick, red_qhp, blue_qhp) every 200 ticks
    mil_log = []  # (tick, r_mil, b_mil) every 200 ticks

    # Sustained clear lead tracking: require the lead to hold for 3 consecutive
    # 200-tick checks before declaring it "clear". A single-tick spike is not a lead.
    consecutive_lead_checks = 0
    LEAD_CHECKS_REQUIRED = 3
    clear_lead_tick = None

    for t in range(1, ticks + 1):
        w.step()

        rq_hp = red_queen.hp  if red_queen  in red.ants  else 0
        bq_hp = blue_queen.hp if blue_queen in blue.ants else 0

        r_mil = sum(1 for a in red.ants  if a.type in (A_SOLDIER, A_SPITTER))
        b_mil = sum(1 for a in blue.ants if a.type in (A_SOLDIER, A_SPITTER))

        if t % 200 == 0:
            hp_log.append((t, rq_hp, bq_hp))
            mil_log.append((t, r_mil, b_mil))
            if verbose:
                rs = sum(1 for a in red.ants  if a.type == A_SOLDIER)
                bs = sum(1 for a in blue.ants if a.type == A_SOLDIER)
                rsp = sum(1 for a in red.ants  if a.type == A_SPITTER)
                bsp = sum(1 for a in blue.ants if a.type == A_SPITTER)
                print(f"  t={t:4d}  RED queen={rq_hp:5.0f} sol={rs} spit={rsp}  "
                      f"BLUE queen={bq_hp:5.0f} sol={bs} spit={bsp}  "
                      f"RED_mil={r_mil} BLUE_mil={b_mil}")

            # Sustained clear-lead check: one side has >2.5x military at this 200-tick
            # sample AND it's been 3 consecutive checks. A brief blip is NOT a lead.
            if clear_lead_tick is None and t > 400:
                if r_mil > 0 and b_mil > 0:
                    if r_mil >= b_mil * 2.5 or b_mil >= r_mil * 2.5:
                        consecutive_lead_checks += 1
                    else:
                        consecutive_lead_checks = 0
                    if consecutive_lead_checks >= LEAD_CHECKS_REQUIRED:
                        clear_lead_tick = t
                else:
                    consecutive_lead_checks = 0

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

    rq_final = red_queen.hp  if red_queen  in red.ants  else 0
    bq_final = blue_queen.hp if blue_queen in blue.ants else 0
    r_mil_final = sum(1 for a in red.ants  if a.type in (A_SOLDIER, A_SPITTER))
    b_mil_final = sum(1 for a in blue.ants if a.type in (A_SOLDIER, A_SPITTER))
    hp_margin = abs(rq_final - bq_final)
    mil_ratio = max(r_mil_final, b_mil_final) / max(1, min(r_mil_final, b_mil_final))

    # REAL resolution criteria — no total_losses fallback (that masked stalemates)
    # A stalemate is a stalemate. Report it as such.
    resolved = (winner is not None                      # queen death
                or clear_lead_tick is not None          # sustained 3-check military lead
                or hp_margin > QUEEN_HP * 0.30)         # one queen took >30% damage

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
        "stalemate": not resolved,
        "hp_log": hp_log,
        "mil_log": mil_log,
        "red_losses": red.ants_lost,
        "blue_losses": blue.ants_lost,
    }


if __name__ == "__main__":
    from engine.constants import (
        SPITTER_HP, SPITTER_DMG, SPITTER_RANGE, SPITTER_CD,
        BULWARK_HP, BULWARK_CONTACT_DMG, GUARD_POST_DMG,
    )
    print("=" * 60)
    print("SPITTER-SYMMETRY HARNESS (hardened)")
    print(f"  Spitter: HP={SPITTER_HP} DMG={SPITTER_DMG} RANGE={SPITTER_RANGE} CD={SPITTER_CD}")
    print(f"  Bulwark: HP={BULWARK_HP} CONTACT_DMG={BULWARK_CONTACT_DMG}")
    print(f"  GuardPost: DMG={GUARD_POST_DMG}")
    print("  Economy: realistic (1200 starting food, workers gather)")
    print("  Resolution: winner OR sustained 3-check lead OR >30% HP margin")
    print("  Stalemates reported HONESTLY — no total_losses fallback")
    print("=" * 60)

    seeds = [42, 7, 13, 99, 256]
    results = []
    for s in seeds:
        print(f"\n--- Seed {s} ---")
        r = run(s, ticks=2000, verbose=True)
        results.append(r)
        outcome = "STALEMATE" if r["stalemate"] else ("WINNER: " + r["winner"])
        print(f"  {outcome}  tick={r['decision_tick']}  "
              f"RED_qhp={r['red_queen_hp_final']:.0f}  BLUE_qhp={r['blue_queen_hp_final']:.0f}  "
              f"margin={r['hp_margin']:.0f}  mil_ratio={r['mil_ratio']:.2f}  "
              f"runaway={r['runaway_early']}  resolved={r['resolved']}")
        print(f"  RED_losses={r['red_losses']}  BLUE_losses={r['blue_losses']}  "
              f"clear_lead_at={r['clear_lead_tick']}")
        # Print military ratio over time
        mil_str = "  mil_over_time: " + " | ".join(
            f"t{t}: R{rm} B{bm}" for t, rm, bm in r["mil_log"]
        )
        print(mil_str)

    print()
    print("=" * 60)
    print("ACCEPTANCE CHECK")
    print("  Stalemates are reported honestly — NOT forced to pass.")
    resolved_count = sum(1 for r in results if r["resolved"])
    stalemate_count = sum(1 for r in results if r["stalemate"])
    print(f"  Resolved: {resolved_count}/{len(seeds)}  Stalemates: {stalemate_count}/{len(seeds)}")
    print()
    passes = 0
    for r in results:
        resolved = r["resolved"]
        no_runaway = not r["runaway_early"]
        ok = resolved and no_runaway
        if ok: passes += 1
        status = "PASS" if ok else ("STALEMATE" if r["stalemate"] else "FAIL (runaway)")
        print(f"  Seed {r['seed']:3d}: {status}  resolved={resolved}  no_runaway={no_runaway}  "
              f"winner={r['winner']}  mil_ratio={r['mil_ratio']:.2f}  "
              f"hp_margin={r['hp_margin']:.0f}")
        print(f"           RED_qhp={r['red_queen_hp_final']:.0f}  BLUE_qhp={r['blue_queen_hp_final']:.0f}  "
              f"clear_lead_at={r['clear_lead_tick']}")
    overall = "PASS" if passes >= 3 else "FAIL"
    print(f"\nOVERALL: {overall} ({passes}/{len(seeds)} seeds pass)")
    print(f"TRUTH: {resolved_count} resolved, {stalemate_count} stalemate out of {len(seeds)} seeds")
