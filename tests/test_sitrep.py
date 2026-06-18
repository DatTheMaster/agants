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

if __name__ == "__main__":
    test_com_history_tracks_soldiers()
    print("Task 1 PASS")
