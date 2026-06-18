#!/usr/bin/env python3
"""Repro harness for the rally-staging bug (passdown s51, cluster A.1).

Hermes set rally_point=[87,50] + rally_release_at=15 and 0 soldiers ever staged.
This drives the engine headless to reproduce and isolate the cause.

Scenario UNCONTESTED: rally at midfield, no enemy soldiers near it.
Scenario CONTESTED:   rally at midfield with BLUE soldiers parked on/around it.

Run: python3 tools/repro/rally_staging.py
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.world import World
from engine.colony import Ant
from engine.constants import (RED_SPAWN, BLUE_SPAWN, A_SOLDIER, A_WORKER, A_SCOUT, A_QUEEN)

RALLY = [87, 50]
RELEASE = 15
N_RED_SOLDIERS = 20


def make_world():
    random.seed(42)
    w = World()
    w.finalize_placement(RED_SPAWN, BLUE_SPAWN)
    return w


def add_soldiers(colony, n, around):
    ax, ay = around
    for _ in range(n):
        colony.ants.append(Ant(ax + random.randint(-3, 3), ay + random.randint(-3, 3),
                               colony.id, A_SOLDIER))


def staged_count(colony, rx, ry):
    return sum(1 for a in colony.ants if a.type == A_SOLDIER
               and abs(a.x - rx) + abs(a.y - ry) <= 4)


def soldier_xs(colony):
    return [a.x for a in colony.ants if a.type == A_SOLDIER]


def run(label, contested, ticks=200):
    w = make_world()
    red = w.colonies[0]
    blue = w.colonies[1]
    add_soldiers(red, N_RED_SOLDIERS, RED_SPAWN)
    red.directive["military"]["rally_point"] = list(RALLY)
    red.directive["military"]["rally_release_at"] = RELEASE
    if contested:
        # BLUE soldiers parked on the rally / midfield approach
        add_soldiers(blue, 10, (87, 50))
        add_soldiers(blue, 6, (70, 50))

    print(f"\n=== {label} (rally={RALLY} release={RELEASE} red_soldiers={N_RED_SOLDIERS}) ===")
    rx, ry = RALLY
    for t in range(1, ticks + 1):
        w.step()
        if t % 20 == 0 or t == 1:
            xs = soldier_xs(red)
            alive = len(xs)
            avgx = sum(xs) / alive if alive else 0
            maxx = max(xs) if xs else 0
            st = staged_count(red, rx, ry)
            # how many got at least to x=70 (past midfield approach)
            past70 = sum(1 for x in xs if x >= 70)
            print(f"  t={t:3d}  red_soldiers_alive={alive:2d}  staged={st:2d}  "
                  f"avg_x={avgx:5.1f}  max_x={maxx:3d}  reached_x>=70={past70:2d}  "
                  f"rally={red.directive['military']['rally_point']}")
        if red.directive["military"]["rally_point"] is None:
            print(f"  *** rally RELEASED/cleared at tick {t} ***")
            break


def run_trickle(label, ticks=400, spawn_every=12, blue_army=18):
    """Real-game condition: RED reinforces ONE soldier at a time while BLUE holds
    an army in the contested midfield around the rally. Tests Hermes' actual report:
    'soldiers trickle to the rally one-by-one and get picked off en route' -> 0 staged."""
    w = make_world()
    red, blue = w.colonies[0], w.colonies[1]
    red.directive["military"]["rally_point"] = list(RALLY)
    red.directive["military"]["rally_release_at"] = RELEASE
    add_soldiers(blue, blue_army, (87, 50))   # BLUE army parked on the rally
    print(f"\n=== {label} (trickle 1 soldier/{spawn_every}t, BLUE army={blue_army} on rally) ===")
    rx, ry = RALLY
    total_spawned = 0
    for t in range(1, ticks + 1):
        if t % spawn_every == 0:
            add_soldiers(red, 1, RED_SPAWN)
            total_spawned += 1
        w.step()
        if t % 25 == 0:
            xs = soldier_xs(red)
            alive = len(xs)
            st = staged_count(red, rx, ry)
            maxx = max(xs) if xs else 0
            print(f"  t={t:3d}  spawned_total={total_spawned:2d}  red_alive={alive:2d}  "
                  f"staged={st:2d}  max_x={maxx:3d}  rally={red.directive['military']['rally_point']}")
        if red.directive["military"]["rally_point"] is None:
            print(f"  *** rally RELEASED at tick {t} ***")
            break


if __name__ == "__main__":
    run("UNCONTESTED", contested=False)
    run("CONTESTED", contested=True)
    run_trickle("TRICKLE+CONTESTED")
