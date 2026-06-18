import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_SOLDIER, A_QUEEN

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
    sol = [Ant(40, 50, 0, A_SOLDIER, born_tick=0) for _ in range(6)]
    me.ants += sol
    me.directive["military"].update({"attack_target": [136, 50], "rally_point": None,
                                      "rally_release_at": None, "auto_attack": True})
    for t in range(45):
        for a in sol: a.x = min(135, a.x + 1)   # march east toward target
        w.step()
    orders = build_sitrep(me, w)["orders"]
    atk = next(o for o in orders if o["intent"] == "attack_target")
    assert atk["status"] == "advancing", f"expected advancing, got {atk}"

if __name__ == "__main__":
    test_com_history_tracks_soldiers()
    test_orders_attack_no_effect_when_frozen()
    test_orders_attack_advancing()
    print("Task 2 PASS")
