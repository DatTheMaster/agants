import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_SOLDIER, A_QUEEN, A_WORKER, A_SCOUT

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, enemy = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    enemy.ants = [a for a in enemy.ants if a.type == A_QUEEN]
    return w, me, enemy

def test_com_history_tracks_soldiers():
    w, me, _ = _fresh()
    for i in range(5):
        me.ants.append(Ant(40 + i, 50, 0, A_SOLDIER, born_tick=0))
    w.step()
    assert len(me._army_com_history) >= 1, "com history not populated"
    tick, cx, cy = me._army_com_history[-1]
    assert 40 <= cx <= 45, f"com_x {cx} not the soldier mean"

from engine.sitrep import build_sitrep

def test_orders_attack_no_effect_when_frozen():
    w, me, _ = _fresh()
    for i in range(12):
        me.ants.append(Ant(48 + (i % 3) - 1, 50, 0, A_SOLDIER, born_tick=0))
    me.directive["military"].update({
        "attack_target": [136, 50], "rally_point": [48, 50],
        "rally_release_at": None, "auto_attack": True})
    for _ in range(45):                       # frozen at rally (no-release rally still set)
        # pin them in place to simulate a stuck army
        for a in me.ants:
            if a.type == A_SOLDIER: a.x, a.y = 48, 50
        w.step()
    orders = build_sitrep(me, w)["orders"]
    atk = next(o for o in orders if o["intent"] == "attack_target")
    assert atk["status"] == "no_effect", f"expected no_effect, got {atk}"

def test_orders_attack_advancing():
    w, me, _ = _fresh()
    # soldiers parked at x=120 — outside siege range of the enemy nest at x=136 (|120-136|=16 > 12)
    me.ants += [Ant(120, 50, 0, A_SOLDIER, born_tick=0) for _ in range(6)]
    me.directive["military"].update({"attack_target": [136, 50], "rally_point": None,
                                     "rally_release_at": None, "auto_attack": True})
    # deterministic history: army center-of-mass moved east (80 -> 120) toward the target
    me._army_com_history.clear()
    me._army_com_history.append((1, 80.0, 50.0))
    me._army_com_history.append((40, 120.0, 50.0))
    orders = build_sitrep(me, w)["orders"]
    atk = next(o for o in orders if o["intent"] == "attack_target")
    assert atk["status"] == "advancing", f"expected advancing, got {atk}"

def test_standing_enemy_unknown_until_scouted():
    w, me, enemy = _fresh()
    me.ants += [Ant(40, 50, 0, A_SOLDIER, born_tick=0) for _ in range(5)]
    me.enemy_scouted_tick = -9999       # never scouted
    st = build_sitrep(me, w)["standing"]
    assert st["military"]["enemy"] == "unknown", st["military"]
    assert st["military"]["verdict"] == "unknown"
    assert st["economy"]["enemy"] == "not_observable"
    assert st["economy"]["enemy_proxy"] == "unknown", st["economy"]
    assert st["military"]["you"] == 5 * 20      # 5 soldiers * 20

def test_standing_enemy_scouted_value_and_staleness():
    w, me, enemy = _fresh()
    me.ants += [Ant(40, 50, 0, A_SOLDIER, born_tick=0) for _ in range(5)]
    w.tick = 1000
    me.enemy_scouted_tick = 980
    me.enemy_scouted_counts = [4, 3, 1, 1]      # w,s,sc,q -> 4*5 + 3*20 + 1*8 = 88 (matches army_value: incl workers)
    st = build_sitrep(me, w)["standing"]
    assert st["military"]["enemy"] == 88, st["military"]
    assert st["military"]["enemy_stale_ticks"] == 20
    assert st["military"]["verdict"] == "leading"   # you 100 > enemy 88

def test_field_enemy_unseen_then_seen():
    w, me, enemy = _fresh()
    me.fog_visible = set()
    enemy.ants.append(Ant(120, 50, 1, A_SOLDIER, born_tick=0))
    f = build_sitrep(me, w)["field"]
    assert f["enemy_army"]["seen"] is False, f["enemy_army"]
    # now make that tile visible
    from engine.constants import MAP_W
    me.fog_visible = {50 * MAP_W + 120}
    w.tick = 200
    f = build_sitrep(me, w)["field"]
    assert f["enemy_army"]["seen"] is True
    assert f["enemy_army"]["pos"] == [120, 50]

def test_field_front_line_per_lane():
    w, me, enemy = _fresh()
    me.ants.append(Ant(74, 50, 0, A_SOLDIER, born_tick=0))   # center lane
    enemy.ants.append(Ant(76, 50, 1, A_SOLDIER, born_tick=0))
    from engine.constants import MAP_W
    me.fog_visible = {50 * MAP_W + 76}
    f = build_sitrep(me, w)["field"]
    assert f["front_line"]["center"] == 74, f["front_line"]
    assert f["front_line"]["north"] is None

if __name__ == "__main__":
    for fn in (test_com_history_tracks_soldiers, test_orders_attack_no_effect_when_frozen,
               test_orders_attack_advancing, test_standing_enemy_unknown_until_scouted,
               test_standing_enemy_scouted_value_and_staleness,
               test_field_enemy_unseen_then_seen, test_field_front_line_per_lane):
        fn()
    print("Task 4 PASS")
