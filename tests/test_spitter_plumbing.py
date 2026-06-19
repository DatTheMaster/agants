import sys; sys.path.insert(0, ".")
from engine.world import World
from engine.colony import Ant, Colony
from engine.constants import A_QUEEN, A_SPITTER, SPITTER_HP
from engine.sitrep import build_sitrep

def _fresh():
    w = World(); w.finalize_placement((14, 50), (136, 50))
    return w, w.colonies[0]

def test_spitter_ant_constructs_with_hp():
    a = Ant(20, 50, 0, A_SPITTER)
    assert a.hp == SPITTER_HP and a.max_hp == SPITTER_HP
    assert a.lifespan is not None

def test_directive_has_spitter_spawn_section():
    _, c = _fresh()
    assert "spitter" in c.directive["spawn"]
    assert c.directive["spawn"]["spitter"]["target_ratio"] == 0.0

def test_world_step_with_spitter_no_crash():
    w, c = _fresh()
    c.ants.append(Ant(c.nx + 2, c.ny, c.id, A_SPITTER))
    for _ in range(5):
        w.step()              # must not IndexError on counts/sitrep/aging
    s = build_sitrep(c, w)    # sitrep must handle 5 types
    assert s is not None

def test_serialize_roundtrip_with_spitter():
    w, c = _fresh()
    c.ants.append(Ant(c.nx + 2, c.ny, c.id, A_SPITTER))
    d = w.to_dict()
    w2 = World.from_dict(d)
    assert any(a.type == A_SPITTER for a in w2.colonies[0].ants)

if __name__ == "__main__":
    import traceback
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]; failed=0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed+=1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
