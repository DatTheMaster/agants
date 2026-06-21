import sys; sys.path.insert(0, ".")
import engine.constants as K

def test_spitter_type_id():
    assert K.A_QUEEN == 3, "queen index must not move"
    assert K.A_SPITTER == 4
    assert K.A_RAIDER == 5
    assert K.NUM_ANT_TYPES == 6
    assert K.ANT_TYPE_NAMES == ["worker", "soldier", "scout", "queen", "spitter", "raider"]

def test_spitter_and_bulwark_stats_exist():
    for name in ("SPITTER_HP", "SPITTER_DMG", "SPITTER_RANGE", "SPITTER_CD",
                 "SPITTER_SPAWN_COST", "SPITTER_SPAWN_TIME", "SPLASH_RADIUS",
                 "SPLASH_FALLOFF", "BULWARK_COST", "BULWARK_HP", "BULWARK_MAX",
                 "BULWARK_CONTACT_DMG", "GUARD_POST_SPLASH_RADIUS"):
        assert hasattr(K, name), f"missing constant {name}"
    assert K.GUARD_POST_HP >= 400 and K.GUARD_POST_DMG >= 22

if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try: fn(); print(f"PASS {fn.__name__}")
        except Exception as e: failed += 1; print(f"FAIL {fn.__name__}: {e}"); traceback.print_exc()
    sys.exit(1 if failed else 0)
