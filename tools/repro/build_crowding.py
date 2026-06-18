#!/usr/bin/env python3
"""Repro: build_structure stalls when >BUILD_WORKER_CAP workers crowd the site
(passdown s51 cluster A.3, Hermes #6: '16 in building state, nothing built').

Hypothesis: the contribution check is `if builders <= BUILD_WORKER_CAP: progress += rate`.
With MORE than the cap of workers within BUILD_RANGE, the condition is False and
build_progress NEVER increments -> crowding freezes the build entirely.

Run: python3 tools/repro/build_crowding.py
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.world import World
from engine.colony import Ant
from engine.constants import RED_SPAWN, BLUE_SPAWN, A_WORKER, BUILD_WORKER_CAP, BUILD_WORK_REQUIRED


def make_world():
    random.seed(3)
    w = World()
    w.finalize_placement(RED_SPAWN, BLUE_SPAWN)
    return w


def run(label, n_workers, ticks=120):
    w = make_world()
    red = w.colonies[0]
    sx, sy = 40, 50  # build site away from any food rush
    # lay an inactive barracks foundation
    w.structures.append({"x": sx, "y": sy, "colony": red.id, "type": "barracks",
                         "hp": 1, "max_hp": 200, "cd": 0, "active": False,
                         "build_progress": 0, "build_required": BUILD_WORK_REQUIRED.get("barracks", 60),
                         "spawn_timer": 0})
    # park n_workers right on the site with an explicit build override (so they stay)
    for _ in range(n_workers):
        a = Ant(sx + random.randint(-1, 1), sy + random.randint(-1, 1), red.id, A_WORKER)
        a.unit_override = {"cmd": "build", "x": sx, "y": sy}
        red.ants.append(a)

    site = w.structures[0]
    print(f"\n=== {label}: {n_workers} workers on site (BUILD_WORKER_CAP={BUILD_WORKER_CAP}, "
          f"required={site['build_required']}) ===")
    for t in range(1, ticks + 1):
        w.step()
        if t % 20 == 0 or site.get("active"):
            near = sum(1 for a in red.ants if a.type == A_WORKER
                       and abs(a.x - sx) + abs(a.y - sy) <= 2)
            print(f"  t={t:3d}  builders_in_range={near:2d}  "
                  f"build_progress={site.get('build_progress',0):5.1f}/{site['build_required']}  "
                  f"active={site.get('active')}")
        if site.get("active"):
            print(f"  *** built at tick {t} ***")
            break


if __name__ == "__main__":
    run("FEW (at cap)", n_workers=4)
    run("CROWDED (over cap)", n_workers=12)
