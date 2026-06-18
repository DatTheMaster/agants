#!/usr/bin/env python3
"""Pack loose PNGs in out/ into Pixi v8 spritesheet atlases (TexturePacker
JSON-hash format) under frontend/game/assets/.

One atlas per concern: ants, structures, terrain, nodes, fx.
Frames are laid out in a simple shelf/grid with padding; the JSON includes a
`meta.image`, per-frame rects, and an `animations` map grouping numbered frame
keys (e.g. worker_walk_0..3 -> animations.worker_walk = [worker_walk_0, ...]).

This is a deterministic in-house packer (free-tex-packer-cli v0.3.0 only takes
an .ftpp project file, which is awkward to script). Output is exactly the format
Pixi v8 `Assets.load()` / `Spritesheet` parses, with ordered animation arrays.
"""
import json
import re
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
DEST = ROOT.parent.parent / "frontend" / "game" / "assets"
DEST.mkdir(parents=True, exist_ok=True)

PAD = 2

# Atlas -> list of frame-key stems (filename without .png). Keys match clip names
# in the design spec §3.3 so sheet.animations[<clip>] resolves.
ATLASES = {
    "ants": [
        "worker_idle_0",
        "worker_walk_0", "worker_walk_1", "worker_walk_2", "worker_walk_3",
        "soldier_idle_0",
        "soldier_walk_0", "soldier_walk_1", "soldier_walk_2", "soldier_walk_3",
        "scout_idle_0",
        "queen_idle_0",
    ],
    "structures": [
        "guard_post_active", "guard_post_damaged",
        "watchtower_active",
        "barracks_active",
        "wall_active", "wall_damaged",
        "larder_active",
        "nest_0",
    ],
    "terrain": ["dirt_0", "leaf_0", "water_0", "rock_0", "nest_0"],
    "nodes": [
        "food_seeds", "food_beetle", "food_leaf", "food_honeydew",
        "dirt_node_0", "dirt_node_1",
    ],
    "fx": [],  # no generated FX sprites this pass; renderer FX are procedural
}

ANIM_RE = re.compile(r"^(.*)_(\d+)$")


def shelf_pack(images):
    """Greedy shelf packer. images: list of (key, PIL.Image). Returns
    (atlas_image, placements{key:(x,y,w,h)}, (W,H))."""
    if not images:
        return None, {}, (1, 1)
    # sort by height desc for tighter shelves
    items = sorted(images, key=lambda kv: -kv[1].height)
    max_w = 512
    x = PAD
    y = PAD
    shelf_h = 0
    placements = {}
    used_w = 0
    for key, im in items:
        w, h = im.width, im.height
        if x + w + PAD > max_w:
            x = PAD
            y += shelf_h + PAD
            shelf_h = 0
        placements[key] = (x, y, w, h)
        x += w + PAD
        shelf_h = max(shelf_h, h)
        used_w = max(used_w, x)
    total_h = y + shelf_h + PAD

    def next_pow2(n):
        p = 1
        while p < n:
            p *= 2
        return p

    W = next_pow2(used_w)
    H = next_pow2(total_h)
    atlas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for key, im in images:
        x, y, w, h = placements[key]
        atlas.paste(im.convert("RGBA"), (x, y))
    return atlas, placements, (W, H)


def build_atlas(name, stems):
    images = []
    missing = []
    for stem in stems:
        p = OUT / f"{stem}.png"
        if not p.exists():
            missing.append(stem)
            continue
        images.append((stem, Image.open(p)))
    if not images:
        print(f"  {name}: no source frames -> skipping (renderer falls back to placeholders)")
        return missing
    atlas, placements, (W, H) = shelf_pack(images)
    png_path = DEST / f"{name}.png"
    atlas.save(png_path)

    frames = {}
    for key, (x, y, w, h) in placements.items():
        frames[f"{key}.png"] = {
            "frame": {"x": x, "y": y, "w": w, "h": h},
            "rotated": False,
            "trimmed": False,
            "spriteSourceSize": {"x": 0, "y": 0, "w": w, "h": h},
            "sourceSize": {"w": w, "h": h},
        }

    # group animations by numeric suffix
    anims = {}
    for key in placements:
        m = ANIM_RE.match(key)
        if m:
            base, idx = m.group(1), int(m.group(2))
            anims.setdefault(base, []).append((idx, f"{key}.png"))
    animations = {b: [fk for _, fk in sorted(v)] for b, v in anims.items()}

    sheet = {
        "frames": frames,
        "animations": animations,
        "meta": {
            "app": "agants/tools/assetgen/pack.py",
            "version": "1.0",
            "image": f"{name}.png",
            "format": "RGBA8888",
            "size": {"w": W, "h": H},
            "scale": "1",
        },
    }
    (DEST / f"{name}.json").write_text(json.dumps(sheet, indent=1))
    print(f"  {name}: {len(frames)} frames, {len(animations)} anims, "
          f"{W}x{H} -> {name}.png + {name}.json"
          + (f"  (missing: {missing})" if missing else ""))
    return missing


def main():
    print(f"Packing into {DEST}")
    all_missing = {}
    for name, stems in ATLASES.items():
        miss = build_atlas(name, stems)
        if miss:
            all_missing[name] = miss
    if all_missing:
        print("\nMissing source frames (placeholders will be used at runtime):")
        for k, v in all_missing.items():
            print(f"  {k}: {v}")
    print("\nDone.")


if __name__ == "__main__":
    main()
