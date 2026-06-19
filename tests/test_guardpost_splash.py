import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SOLDIER, GUARD_POST_HP

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def test_guardpost_splashes_clustered_enemies():
    w, me, en = _fresh()
    w.structures.append({"x": 50, "y": 50, "colony": me.id, "type": "guard_post",
                         "hp": GUARD_POST_HP, "max_hp": GUARD_POST_HP, "cd": 0, "active": True,
                         "build_progress": 999, "build_required": 100})
    primary = Ant(52, 50, en.id, A_SOLDIER); en.ants.append(primary)
    neighbor = Ant(52, 51, en.id, A_SOLDIER); en.ants.append(neighbor)  # adjacent to primary
    nhp0 = neighbor.hp
    for _ in range(6):
        w.step()
        if neighbor.hp < nhp0: break
    assert neighbor.hp < nhp0, "guard post did not splash a neighbor of its target"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
