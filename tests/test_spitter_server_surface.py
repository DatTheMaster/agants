import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant
from engine.constants import A_QUEEN, A_SPITTER
from engine.sitrep import build_sitrep

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    me, en = w.colonies[0], w.colonies[1]
    me.ants = [a for a in me.ants if a.type == A_QUEEN]
    en.ants = [a for a in en.ants if a.type == A_QUEEN]
    return w, me, en

def test_enemy_army_composition_includes_spitters_when_visible():
    w, me, en = _fresh()
    for _ in range(3): en.ants.append(Ant(78, 50, en.id, A_SPITTER))
    for _ in range(3): me.ants.append(Ant(76, 50, me.id, __import__("engine.constants", fromlist=["A_SOLDIER"]).A_SOLDIER))
    for _ in range(3): w.step()
    ea = build_sitrep(me, w)["field"]["enemy_army"]
    if ea["seen"]:
        assert "spitters" in ea["composition"], ea["composition"]

def test_build_structure_constants_have_bulwark():
    import server
    # _BUILD_COSTS map in api_command must include bulwark (smoke: attribute exists in source)
    import inspect
    src = inspect.getsource(server)
    assert '"bulwark"' in src, "server has no bulwark handling"

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
