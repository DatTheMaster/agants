#!/usr/bin/env python3
"""Repro: workers idle after a food node depletes (passdown s51 cluster A.2,
Hermes #5: '5-11 idle even with priority_food; finish a node and stand there').

Run: python3 tools/repro/worker_idle.py
"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.world import World
from engine.colony import Ant
from engine.constants import RED_SPAWN, BLUE_SPAWN, A_WORKER, S_IDLE

# state name lookup
from engine import constants as K


def make_world():
    random.seed(7)
    w = World()
    w.finalize_placement(RED_SPAWN, BLUE_SPAWN)
    return w


def state_name(s):
    for n in dir(K):
        if n.startswith("S_") and getattr(K, n) == s:
            return n
    return str(s)


def run(ticks=600):
    w = make_world()
    red = w.colonies[0]
    # give RED a healthy worker force
    for _ in range(24):
        red.ants.append(Ant(RED_SPAWN[0] + random.randint(-3, 3),
                            RED_SPAWN[1] + random.randint(-3, 3), red.id, A_WORKER))
    # pick a nearby approach food node as priority and watch it deplete
    pf = (30, 80)  # approach node from _build_map
    red.directive["economy"]["priority_food"] = list(pf)

    S_IDLE_VAL = K.S_IDLE
    streak = {}  # ant.id -> consecutive idle ticks
    print(f"=== worker idle repro: priority_food={pf} ===")
    for t in range(1, ticks + 1):
        w.step()
        workers = [a for a in red.ants if a.type == A_WORKER]
        ids = set()
        for a in workers:
            ids.add(a.id)
            if a.state == S_IDLE_VAL:
                streak[a.id] = streak.get(a.id, 0) + 1
            else:
                streak[a.id] = 0
        for k in list(streak):
            if k not in ids:
                del streak[k]
        if t % 40 == 0:
            idle = sum(1 for a in workers if a.state == S_IDLE_VAL)
            stuck = sum(1 for v in streak.values() if v >= 5)   # idle 5+ ticks straight
            stuck10 = sum(1 for v in streak.values() if v >= 10)
            maxstreak = max(streak.values()) if streak else 0
            pf_node = next((f for f in w.foods if (f["x"], f["y"]) == pf), None)
            pf_amt = int(pf_node["amt"]) if pf_node else "GONE"
            print(f"  t={t:3d}  workers={len(workers):2d}  idle_now={idle:2d}  "
                  f"stuck>=5t={stuck:2d}  stuck>=10t={stuck10:2d}  max_idle_streak={maxstreak:3d}  pf={pf_amt}")


if __name__ == "__main__":
    run()
