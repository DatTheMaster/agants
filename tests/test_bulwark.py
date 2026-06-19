import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SOLDIER, BULWARK_HP, BULWARK_CONTACT_DMG

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def _bulwark(w, cid, x, y):
    st = {"x": x, "y": y, "colony": cid, "type": "bulwark", "hp": BULWARK_HP,
          "max_hp": BULWARK_HP, "cd": 0, "active": True, "build_progress": 999,
          "build_required": 40}
    w.structures.append(st); return st

def test_bulwark_blocks_movement():
    w, me, en = _fresh()
    _bulwark(w, me.id, 50, 50)
    assert w._passable(50, 50) is False

def test_bulwark_damages_adjacent_enemy():
    w, me, en = _fresh()
    _bulwark(w, me.id, 50, 50)
    foe = Ant(51, 50, en.id, A_SOLDIER); en.ants.append(foe)
    hp0 = foe.hp
    w.step()
    assert foe.hp <= hp0 - BULWARK_CONTACT_DMG, "bulwark did not bleed an adjacent enemy"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
