#!/usr/bin/env python3
"""Agants pixel-art asset generator (pixellab.ai REST API, OFFLINE batch).

100% LOCAL. Never contacts the game server/production. Only talks to
https://api.pixellab.ai. Saves loose PNGs under tools/assetgen/out/.

HARD BUDGET: at most 30 generation calls TOTAL. The script counts every call
and refuses to exceed MAX_GENERATIONS. Each still = 1 generation; an animation
records its real usage.generations cost.

Run a single phase:  python3 gen.py <phase>   where phase in
  anchor ants structures terrain nodes anim all
Resume-safe: skips a subject whose output PNG already exists (so re-runs do not
re-spend budget).
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
LEDGER = ROOT / "ledger.json"

API = "https://api.pixellab.ai/v1"
MAX_GENERATIONS = 30

# Consistent style across the whole set.
SEED = 4242
STYLE_STRENGTH = 55  # bitforge: how strongly to follow the style image
GRAY = "#9a9a9a"     # mid-gray base palette so runtime Pixi tint reads cleanly


def token():
    # Load PIXELLAB_API_TOKEN from .env (repo root) without exporting secrets.
    env = ROOT.parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line.startswith("PIXELLAB_API_TOKEN"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    t = os.environ.get("PIXELLAB_API_TOKEN")
    if not t:
        raise SystemExit("PIXELLAB_API_TOKEN not found in .env or env")
    return t


TOKEN = token()
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def load_ledger():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"generations": 0.0, "calls": []}


def save_ledger(led):
    LEDGER.write_text(json.dumps(led, indent=2))


def budget_left(led):
    return MAX_GENERATIONS - led["generations"]


def post(endpoint, payload, led, label):
    if budget_left(led) < 1:
        print(f"  SKIP {label}: budget exhausted ({led['generations']}/{MAX_GENERATIONS})")
        return None
    url = f"{API}/{endpoint}"
    t0 = time.time()
    r = requests.post(url, headers=H, json=payload, timeout=180)
    dt = time.time() - t0
    if r.status_code != 200:
        print(f"  FAIL {label}: HTTP {r.status_code} {r.text[:200]}")
        led["calls"].append({"label": label, "endpoint": endpoint, "status": r.status_code,
                             "error": r.text[:300]})
        save_ledger(led)
        return None
    data = r.json()
    used = 1.0
    try:
        used = float(data.get("usage", {}).get("generations", 1.0))
    except Exception:
        pass
    led["generations"] += used
    led["calls"].append({"label": label, "endpoint": endpoint, "generations": used,
                         "latency_s": round(dt, 1)})
    save_ledger(led)
    print(f"  OK   {label}: +{used} gen ({dt:.1f}s)  total={led['generations']}/{MAX_GENERATIONS}")
    return data


def save_b64(b64, path):
    path.write_bytes(base64.b64decode(b64))


def img_b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


def pixflux(description, led, label, size=64, color=GRAY, view="high top-down", extra=None):
    payload = {
        "description": description,
        "image_size": {"width": size, "height": size},
        "no_background": True,
        "view": view,
        "outline": "single color black outline",
        "shading": "medium shading",
        "detail": "medium detail",
        "seed": SEED,
    }
    if color:
        payload["color_image"] = {"type": "base64", "base64": _solid_color_b64(color)}
    if extra:
        payload.update(extra)
    return post("generate-image-pixflux", payload, led, label)


def bitforge(description, style_png, led, label, size=64, view="high top-down", extra=None):
    payload = {
        "description": description,
        "image_size": {"width": size, "height": size},
        "no_background": True,
        "view": view,
        "outline": "single color black outline",
        "style_image": {"type": "base64", "base64": img_b64(style_png)},
        "style_strength": STYLE_STRENGTH,
        "seed": SEED,
    }
    if extra:
        payload.update(extra)
    return post("generate-image-bitforge", payload, led, label)


_COLOR_CACHE = {}


def _solid_color_b64(hexcolor):
    if hexcolor in _COLOR_CACHE:
        return _COLOR_CACHE[hexcolor]
    from PIL import Image
    import io
    h = hexcolor.lstrip("#")
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    im = Image.new("RGB", (64, 64), rgb)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    b = base64.b64encode(buf.getvalue()).decode()
    _COLOR_CACHE[hexcolor] = b
    return b


def have(name):
    return (OUT / f"{name}.png").exists()


# ---- phases -------------------------------------------------------------

def phase_anchor(led):
    """Canonical worker style anchor — grayscale base for runtime tint."""
    name = "worker_idle_0"
    anchor = OUT / "worker_anchor.png"
    if anchor.exists() and have(name):
        print("anchor: already present, skipping")
        return
    d = ("top-down pixel art worker ant, neutral mid-gray body, six legs, "
         "simple clean silhouette, centered, game sprite")
    r = pixflux(d, led, "anchor worker", size=64)
    if r:
        save_b64(r["image"]["base64"], anchor)
        save_b64(r["image"]["base64"], OUT / f"{name}.png")


def phase_ants(led):
    """4 ant bases: worker(already=anchor), soldier, scout, queen[128].
    Uses pixflux (text->sprite) for all — bitforge style-transfer produced noise
    from the sparse transparent anchor, so we keep coherence via shared SEED +
    gray color_image instead.
    """
    jobs = [
        ("soldier_idle_0", "top-down pixel art soldier ant, neutral mid-gray body, "
         "large mandibles, armored head, bulky, six legs, clean silhouette, "
         "centered, game sprite", 64),
        ("scout_idle_0", "top-down pixel art scout ant, neutral mid-gray body, slender, "
         "long legs, small and fast, six legs, clean silhouette, centered, game sprite", 64),
        ("queen_idle_0", "top-down pixel art queen ant, neutral mid-gray body, very large "
         "elongated abdomen, regal, six legs, detailed, centered, game sprite", 128),
    ]
    for name, desc, size in jobs:
        if have(name):
            print(f"  have {name}, skip")
            continue
        r = pixflux(desc, led, f"ant {name}", size=size)
        if r:
            save_b64(r["image"]["base64"], OUT / f"{name}.png")


def phase_structures(led):
    anchor = OUT / "worker_anchor.png"
    jobs = [
        ("guard_post_active", "top-down pixel art ant guard tower outpost, gray stone and twig, "
         "small fortified post, game building sprite"),
        ("watchtower_active", "top-down pixel art tall watchtower, gray wood, lookout platform, "
         "game building sprite"),
        ("barracks_active", "top-down pixel art barracks hut, gray, wide low structure, "
         "game building sprite"),
        ("wall_active", "top-down pixel art stone wall segment, gray bricks, blocky, "
         "game building sprite"),
        ("larder_active", "top-down pixel art domed food storehouse larder, gray, rounded roof, "
         "game building sprite"),
    ]
    for name, desc in jobs:
        if have(name):
            print(f"  have {name}, skip")
            continue
        r = pixflux(desc, led, f"struct {name}", size=64)
        if r:
            save_b64(r["image"]["base64"], OUT / f"{name}.png")


def phase_terrain(led):
    anchor = OUT / "worker_anchor.png"
    jobs = [
        ("dirt_0", "top-down pixel art seamless dirt ground tile, brown soil texture, "
         "tileable, no objects"),
        ("leaf_0", "top-down pixel art seamless leaf litter ground tile, green foliage, "
         "tileable, no objects"),
        ("water_0", "top-down pixel art seamless shallow water tile, blue, gentle ripples, "
         "tileable, no objects"),
        ("rock_0", "top-down pixel art seamless rocky ground tile, gray stone, "
         "tileable, no objects"),
        ("nest_0", "top-down pixel art ant nest entrance carved in earth, dark hole, "
         "mounded soil ring, game tile"),
    ]
    for name, desc in jobs:
        if have(name):
            print(f"  have {name}, skip")
            continue
        # terrain keeps its own natural color (no gray palette) and is full-frame
        # (no transparent background, it tiles).
        r = pixflux(desc, led, f"terrain {name}", size=64, color=None,
                    extra={"no_background": False})
        if r:
            save_b64(r["image"]["base64"], OUT / f"{name}.png")


def phase_nodes(led):
    anchor = OUT / "worker_anchor.png"
    jobs = [
        ("food_seeds", "top-down pixel art pile of golden seeds, food resource, game sprite"),
        ("food_beetle", "top-down pixel art dead brown beetle, food resource, game sprite"),
        ("food_leaf", "top-down pixel art green leaf fragment, food resource, game sprite"),
        ("food_honeydew", "top-down pixel art glistening honeydew droplet, pale green sweet "
         "resource, game sprite"),
        ("dirt_node_0", "top-down pixel art small mound of loose diggable dirt, brown, "
         "game resource sprite"),
        ("dirt_node_1", "top-down pixel art large rich mound of diggable dirt, dark brown, "
         "game resource sprite"),
    ]
    for name, desc in jobs:
        if have(name):
            print(f"  have {name}, skip")
            continue
        # nodes keep natural color, transparent background (they sit on terrain).
        r = pixflux(desc, led, f"node {name}", size=64, color=None)
        if r:
            save_b64(r["image"]["base64"], OUT / f"{name}.png")


def phase_anim(led):
    """ONE worker walk animation — record real usage.generations cost.
    Uses animate-with-skeleton if available; falls back to animate-with-text.
    """
    anchor = OUT / "worker_anchor.png"
    if not anchor.exists():
        print("anim: no anchor; skip")
        return
    if (OUT / "worker_walk_0.png").exists():
        print("anim: worker_walk already present, skip")
        return
    # Try the text-driven animation endpoint (4 directional walk frames).
    payload = {
        "description": "worker ant walking",
        "action": "walk",
        "image_size": {"width": 64, "height": 64},
        "no_background": True,
        "view": "high top-down",
        "reference_image": {"type": "base64", "base64": img_b64(anchor)},
        "n_frames": 4,
        "seed": SEED,
    }
    r = post("animate-with-text", payload, led, "anim worker_walk (animate-with-text)")
    if not r:
        print("anim: animate-with-text failed; leaving placeholder (walk falls back to idle)")
        return
    # Response may carry a list of frame images.
    frames = r.get("images") or r.get("frames")
    if isinstance(frames, list):
        for i, fr in enumerate(frames):
            b64 = fr.get("base64") if isinstance(fr, dict) else fr
            save_b64(b64, OUT / f"worker_walk_{i}.png")
        print(f"anim: saved {len(frames)} walk frames")
    elif "image" in r:
        save_b64(r["image"]["base64"], OUT / "worker_walk_0.png")
        print("anim: single-image response saved as worker_walk_0")


def phase_extra(led):
    """Budget-permitting polish (priority 6): structure damaged frames +
    soldier walk anim. Each guarded by have()/budget so re-runs are safe.
    """
    # damaged structure stills (most-seen first)
    dmg = [
        ("guard_post_damaged", "top-down pixel art damaged ant guard tower, gray stone, "
         "cracked and crumbling, smoke, game building sprite"),
        ("wall_damaged", "top-down pixel art broken stone wall segment, gray bricks, "
         "cracked with rubble, game building sprite"),
    ]
    for name, desc in dmg:
        if have(name):
            print(f"  have {name}, skip")
            continue
        r = pixflux(desc, led, f"struct {name}", size=64)
        if r:
            save_b64(r["image"]["base64"], OUT / f"{name}.png")
    # soldier walk anim
    if not have("soldier_walk_0") and (OUT / "soldier_idle_0.png").exists():
        payload = {
            "description": "soldier ant walking",
            "action": "walk",
            "image_size": {"width": 64, "height": 64},
            "no_background": True,
            "view": "high top-down",
            "reference_image": {"type": "base64", "base64": img_b64(OUT / "soldier_idle_0.png")},
            "n_frames": 4,
            "seed": SEED,
        }
        r = post("animate-with-text", payload, led, "anim soldier_walk")
        if r:
            frames = r.get("images") or r.get("frames")
            if isinstance(frames, list):
                for i, fr in enumerate(frames):
                    b64 = fr.get("base64") if isinstance(fr, dict) else fr
                    save_b64(b64, OUT / f"soldier_walk_{i}.png")
                print(f"anim: saved {len(frames)} soldier walk frames")


PHASES = {
    "anchor": phase_anchor,
    "ants": phase_ants,
    "structures": phase_structures,
    "terrain": phase_terrain,
    "nodes": phase_nodes,
    "anim": phase_anim,
    "extra": phase_extra,
}
ORDER = ["anchor", "ants", "structures", "terrain", "nodes", "anim"]


def main():
    led = load_ledger()
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"Budget: {led['generations']}/{MAX_GENERATIONS} used. Phase: {arg}")
    if arg == "all":
        for p in ORDER:
            print(f"== {p} ==")
            PHASES[p](led)
            if budget_left(led) < 1:
                print("Budget exhausted; stopping.")
                break
    elif arg in PHASES:
        PHASES[arg](led)
    else:
        raise SystemExit(f"unknown phase {arg}; choose from {ORDER + ['all']}")
    print(f"\nDONE. Total generations: {led['generations']}/{MAX_GENERATIONS}")


if __name__ == "__main__":
    main()
