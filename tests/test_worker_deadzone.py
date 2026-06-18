import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_WORKER, S_IDLE


def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, enemy = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    enemy.ants = [a for a in enemy.ants if a.type == A_QUEEN]
    return w, me, enemy


def _food_at(w, x, y):
    return next((f for f in w.foods if f["x"] == x and f["y"] == y), None)


def test_worker_reaches_node_from_arrival_dead_zone():
    """A lone worker whose recruit_target is a food node, parked at Manhattan dist 4
    (inside arrival<=4 but outside pickup<=3), must push in and harvest — NOT idle
    forever in the dead zone between the arrival and pickup radii."""
    w, me, _ = _fresh()
    node = next(f for f in w.foods if f["tier"] == "approach" and f["amt"] > 50)
    fx, fy = node["x"], node["y"]
    start_amt = node["amt"]
    # place a single worker exactly 4 tiles away (dead zone), targeting the node
    a = Ant(fx - 4, fy, me.id, A_WORKER)
    a.recruit_target = (fx, fy)
    me.ants.append(a)

    idle_ticks = 0
    harvested = False
    for _ in range(40):
        w.step()
        if a not in me.ants:
            break
        if a.state == S_IDLE:
            idle_ticks += 1
        # detect a harvest: worker started carrying OR node amount dropped
        if a.carrying or _food_at(w, fx, fy)["amt"] < start_amt:
            harvested = True
            break
    assert harvested, f"worker never harvested node at ({fx},{fy}); idle_ticks={idle_ticks}"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
