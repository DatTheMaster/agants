import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import (A_QUEEN, A_WORKER, BUILD_WORKER_CAP,
                              BUILD_WORK_REQUIRED, BUILD_RATE)


def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, enemy = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    enemy.ants = [a for a in enemy.ants if a.type == A_QUEEN]
    return w, me, enemy


def _lay_site(w, colony, sx, sy, stype="barracks"):
    site = {"x": sx, "y": sy, "colony": colony.id, "type": stype,
            "hp": 1, "max_hp": 200, "cd": 0, "active": False,
            "build_progress": 0, "build_required": BUILD_WORK_REQUIRED[stype],
            "spawn_timer": 0}
    w.structures.append(site)
    return site


def _add_builders(colony, n, sx, sy):
    for i in range(n):
        a = Ant(sx, sy, colony.id, A_WORKER)
        a.unit_override = {"cmd": "build", "x": sx, "y": sy}
        colony.ants.append(a)


def test_build_progresses_at_cap():
    """Sanity: exactly BUILD_WORKER_CAP workers build the structure."""
    w, me, _ = _fresh()
    sx, sy = 40, 50
    site = _lay_site(w, me, sx, sy)
    _add_builders(me, BUILD_WORKER_CAP, sx, sy)
    for _ in range(60):
        w.step()
        if site.get("active"):
            break
    assert site.get("active"), "structure never built with workers at the cap"


def test_build_progresses_when_crowded():
    """REGRESSION: more than BUILD_WORKER_CAP workers must NOT freeze the build.
    Bug: `if builders <= BUILD_WORKER_CAP` -> over-cap crowding adds zero progress."""
    w, me, _ = _fresh()
    sx, sy = 40, 50
    site = _lay_site(w, me, sx, sy)
    _add_builders(me, BUILD_WORKER_CAP * 3, sx, sy)   # 12 workers, way over cap
    for _ in range(60):
        w.step()
        if site.get("active"):
            break
    assert site.get("build_progress", 0) > 0, \
        f"crowded build froze at 0 progress (builders={BUILD_WORKER_CAP*3})"
    assert site.get("active"), "crowded structure never built"


def test_crowding_does_not_exceed_cap_rate():
    """Contribution is capped: crowding must not build FASTER than the cap allows."""
    w, me, _ = _fresh()
    sx, sy = 40, 50
    site = _lay_site(w, me, sx, sy)
    _add_builders(me, BUILD_WORKER_CAP * 3, sx, sy)
    w.step()
    tier0_rate = BUILD_RATE[0]
    max_per_tick = BUILD_WORKER_CAP * BUILD_RATE[me.worker_tier]
    assert site["build_progress"] <= max_per_tick, \
        f"crowd built {site['build_progress']}/tick, cap is {max_per_tick}"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
