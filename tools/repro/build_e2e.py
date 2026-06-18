#!/usr/bin/env python3
"""A: end-to-end confirmation that a CROWDED build completes through the REAL pipeline
(structure_queue -> _assign_builders_to_site -> per-tick build loop) inside a full
World.step() game loop — not just the hand-laid deterministic repro.

Run: python3 tools/repro/build_e2e.py
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.world import World
from engine.colony import Ant
from engine.constants import RED_SPAWN, BLUE_SPAWN, A_WORKER, BARRACKS_COST, DIRT_CAP


def run():
    random.seed(11)
    w = World(); w.finalize_placement(RED_SPAWN, BLUE_SPAWN)
    red = w.colonies[0]
    red.dirt = DIRT_CAP                       # afford the barracks
    sx, sy = 40, 50

    # Crowd the future site with workers so >BUILD_WORKER_CAP pile on.
    for _ in range(14):
        red.ants.append(Ant(sx + random.randint(-1, 1), sy + random.randint(-1, 1), red.id, A_WORKER))

    # Queue the structure through the REAL order pipeline (server normally does this).
    red.structure_queue.append({"type": "barracks", "x": sx, "y": sy})

    built_tick = None
    for t in range(1, 200):
        w.step()
        site = next((s for s in w.structures if s["x"] == sx and s["y"] == sy), None)
        if site and site.get("active"):
            built_tick = t
            break
    site = next((s for s in w.structures if s["x"] == sx and s["y"] == sy), None)
    near = sum(1 for a in red.ants if a.type == A_WORKER and abs(a.x - sx) + abs(a.y - sy) <= 2)
    print(f"barracks foundation laid + crowded ({near} workers near site at peak region)")
    if built_tick:
        print(f"✅ PASS: crowded barracks BUILT via real pipeline at tick {built_tick} "
              f"(progress {site['build_progress']:.0f}/{site['build_required']})")
        return 0
    else:
        print(f"❌ FAIL: barracks NOT built in 200 ticks — "
              f"progress={site.get('build_progress') if site else 'no site'}")
        return 1


if __name__ == "__main__":
    sys.exit(run())
