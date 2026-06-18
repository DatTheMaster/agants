"""Fill the MISSING ant animation clips flagged by the honest test (defect #2).
Standalone (does NOT import gen.py, to avoid re-running its phases). Reuses the proven
animate-with-text pattern. Each call = 1 generation; hard-capped so total stays < 40/mo.
Re-run safe: skips a clip whose frames already exist."""
import base64, json, time, os, io
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"; OUT.mkdir(exist_ok=True)
LEDGER = ROOT / "ledger.json"
ENV = ROOT.parent.parent / ".env"
API = "https://api.pixellab.ai/v1"
HARD_CAP = 39   # real free tier is 40/month; never exceed

def token():
    for line in ENV.read_text().splitlines():
        if line.strip().startswith("PIXELLAB_API_TOKEN"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no PIXELLAB_API_TOKEN in .env")

H = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
led = json.loads(LEDGER.read_text()) if LEDGER.exists() else {"generations": 0.0, "calls": []}

def save_ledger(): LEDGER.write_text(json.dumps(led, indent=2))
def img_b64(p): return base64.b64encode(Path(p).read_bytes()).decode()
def _strip(b64): return b64.split("base64,", 1)[1] if "base64," in b64 else b64

def post(endpoint, payload, label):
    if led["generations"] >= HARD_CAP:
        print(f"  SKIP {label}: hard cap {HARD_CAP} reached ({led['generations']})"); return None
    t0 = time.time()
    r = requests.post(f"{API}/{endpoint}", headers=H, json=payload, timeout=180)
    dt = time.time() - t0
    if r.status_code != 200:
        print(f"  FAIL {label}: HTTP {r.status_code} {r.text[:160]}")
        led["calls"].append({"label": label, "status": r.status_code, "error": r.text[:200]})
        save_ledger(); return None
    data = r.json()
    used = float(data.get("usage", {}).get("generations", 1.0))
    led["generations"] += used
    led["calls"].append({"label": label, "endpoint": endpoint, "generations": used, "latency_s": round(dt, 1)})
    save_ledger()
    print(f"  OK   {label}: +{used} gen  total={led['generations']}/40")
    return data

def have(prefix):
    return (OUT / f"{prefix}_0.png").exists()

def animate(ref, action, prefix):
    if have(prefix): print(f"  HAVE {prefix} (skip)"); return
    subject = prefix.split("_", 1)[0]  # worker / soldier / scout
    data = post("animate-with-text", {
        "description": f"a top-down pixel-art {subject} ant",   # API requires BOTH description + action
        "action": action,
        "image_size": {"width": 64, "height": 64},
        "reference_image": {"type": "base64", "base64": img_b64(OUT / ref)},
        "n_frames": 4,
    }, f"anim {prefix}")
    if not data: return
    imgs = data.get("images") or data.get("frames") or []
    for i, im in enumerate(imgs):
        b64 = im["base64"] if isinstance(im, dict) else im
        (OUT / f"{prefix}_{i}.png").write_bytes(base64.b64decode(_strip(b64)))
    print(f"       saved {len(imgs)} frames -> {prefix}_0..{len(imgs)-1}.png")

def pixflux_still(desc, prefix, size=32):
    if have(prefix): print(f"  HAVE {prefix} (skip)"); return
    data = post("generate-image-pixflux", {
        "description": desc, "image_size": {"width": size, "height": size},
        "no_background": True, "view": "high top-down", "detail": "medium detail", "seed": 4242,
    }, f"still {prefix}")
    if not data: return
    im = data.get("image"); b64 = im["base64"] if isinstance(im, dict) else im
    (OUT / f"{prefix}_0.png").write_bytes(base64.b64decode(_strip(b64)))
    print(f"       saved {prefix}_0.png")

# 9 missing clips (queen_attack intentionally skipped: queen rarely attacks + 64px anim
# vs 128px base mismatch — idle fallback is correct) + the carry pellet.
CLIPS = [
    ("worker_idle_0.png", "walking while carrying a green leaf in its mandibles", "worker_carry"),
    ("worker_idle_0.png", "digging into the ground with its front legs", "worker_dig"),
    ("worker_idle_0.png", "lunging forward biting with mandibles, attacking", "worker_attack"),
    ("worker_idle_0.png", "collapsing and dying, legs curling up", "worker_death"),
    ("soldier_idle_0.png", "lunging forward attacking with large mandibles", "soldier_attack"),
    ("soldier_idle_0.png", "collapsing and dying, legs curling up", "soldier_death"),
    ("scout_idle_0.png", "scurrying quickly forward", "scout_walk"),
    ("scout_idle_0.png", "lunging forward attacking", "scout_attack"),
    ("scout_idle_0.png", "collapsing and dying", "scout_death"),
]
print(f"start: {led['generations']}/40 generations used")
for ref, action, prefix in CLIPS:
    animate(ref, action, prefix)
pixflux_still("a tiny green leaf fragment held by an ant, small game item icon", "food_pellet", size=32)
print(f"done: {led['generations']}/40 generations used")
