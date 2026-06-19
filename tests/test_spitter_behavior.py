import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SOLDIER, A_SPITTER

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def test_spitter_damages_clump_at_range():
    w, me, en = _fresh()
    sp = Ant(50, 50, me.id, A_SPITTER); me.ants.append(sp)
    ball = [Ant(53 + (i % 2), 50 + (i // 2), en.id, A_SOLDIER) for i in range(6)]  # clump within range 5
    for b in ball: en.ants.append(b)
    hp0 = sum(b.hp for b in ball)
    for _ in range(10):
        w.step()
    hp1 = sum(b.hp for b in [b for b in ball if b in en.ants])
    assert hp1 < hp0, "spitter did no damage to a clump in range"

def test_soldier_beats_spitter_1v1():
    w, me, en = _fresh()
    sp = Ant(50, 50, me.id, A_SPITTER); me.ants.append(sp)
    sol = Ant(52, 50, en.id, A_SOLDIER); en.ants.append(sol)
    for _ in range(120):
        w.step()
        if sp not in me.ants or sol not in en.ants: break
    assert sp not in me.ants and sol in en.ants, "spitter should lose 1v1 to a soldier"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
