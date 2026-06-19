import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SOLDIER

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def test_splash_hits_adjacent_enemies_only():
    w, me, en = _fresh()
    target = Ant(50, 50, en.id, A_SOLDIER); en.ants.append(target)
    near   = Ant(51, 50, en.id, A_SOLDIER); en.ants.append(near)     # adjacent enemy
    far    = Ant(55, 50, en.id, A_SOLDIER); en.ants.append(far)      # out of radius
    friend = Ant(49, 50, me.id, A_SOLDIER); me.ants.append(friend)   # friendly, must be safe
    near_hp0, far_hp0, friend_hp0 = near.hp, far.hp, friend.hp
    w._apply_splash(me.id, target, dmg=16, radius=1, falloff=0.5)
    assert near.hp == near_hp0 - 8, near.hp
    assert far.hp == far_hp0, "splash leaked beyond radius"
    assert friend.hp == friend_hp0, "splash hit a friendly"
    assert target.hp == target.hp, "splash must not double-hit the primary target"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
