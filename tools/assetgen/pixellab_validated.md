# pixellab.ai — validated facts (pre-M0 gate)

## Pinned versions (graphics overhaul)
- PixiJS: **v8.6.6** (UMD) — `frontend/game/vendor/pixi.min.js`
- pixi-filters: **v6.0.5** (UMD, targets Pixi v8) — `frontend/game/vendor/pixi-filters.min.js`


Date: 2026-06-17. Key in `.env` as `PIXELLAB_API_TOKEN` (gitignored, never committed).

## Confirmed by live API calls
- **Free tier bills in GENERATIONS, not USD.** A `POST /v1/generate-image-pixflux` returned
  `usage: {type:"generations", generations:1.0}` and the USD balance stayed `$0.00`.
  → "40 credits/month" = **40 generations/month**. 1 still image = **1 generation**.
- `GET /v1/balance` → `{type:"usd", usd:0.0}` (USD balance is for PAID usage; separate from the
  free generation quota — free calls do not draw it down).
- Auth: `Authorization: Bearer <token>`. Base: `https://api.pixellab.ai/v1/`.
- First asset generated and saved: `tools/assetgen/worker_anchor.png` (64×64, transparent,
  good silhouette; came out near-black — tune prompt/`color_image` for a mid-gray tint base).

## Per-generation USD cost (from pricing page; 1 generation ≈ these)
| Endpoint | Size | USD/gen | ~credits (est) |
|---|---|---|---|
| generate-image-pixflux (text→sprite) | 64×64 | $0.00793 | 1 |
| generate-image-bitforge (style-ref) | 64×64 | $0.00716 | 1 |
| animate-with-skeleton (4 frames) | 64×64 | $0.01433 | ~2 (confirm) |
| rotate | 64×64 | $0.01057 | ~1.3 (confirm) |
| generate-8-rotations v3 | 64×64 | $0.0337 | ~4 (confirm) |
| animate-with-text v3 (4 frames) | 128×128 | $0.0302 | ~4 (confirm) |

Still = 1 generation (confirmed). Animation/rotation credit cost is ESTIMATED from the USD ratio
and must be confirmed when we run the first real animation (record actual `usage.generations`).

## pixflux request shape (confirmed via openapi)
`POST /v1/generate-image-pixflux` — required: `description`, `image_size:{width,height}` (16–400).
Optional: `no_background`, `view` ("high top-down"), `outline`, `shading`, `detail`,
`text_guidance_scale` (1–20, def 8), `color_image` (forced palette), `seed`, `init_image`.
Response: `{usage, image:{base64}}`.

## Asset generation run (2026-06-18) — actual generations used

**TOTAL: 26 / 30 budget** (4 buffer left, stopped early per frugal mandate).
Full per-call ledger: `tools/assetgen/ledger.json`. Script: `tools/assetgen/gen.py`
(REST API direct via `requests`; the `pixellab` SDK was NOT installed, not needed).

| Group | Endpoint | Subjects | Gens |
|---|---|---|---|
| anchor (pre-existing) | generate-image-pixflux | worker_anchor / worker_idle | 1 |
| ants | generate-image-pixflux | soldier, scout, queen[128] | 3 |
| structures | generate-image-pixflux | guard_post, watchtower, barracks, wall, larder (all _active) | 5 |
| terrain | generate-image-pixflux (no_background=false) | dirt, leaf, water, rock, nest | 5 |
| nodes | generate-image-pixflux | food×4 (seeds/beetle/leaf/honeydew), dirt_node×2 | 6 |
| anim | **animate-with-text** | worker_walk (4 frames) | **1.0** |
| extra | generate-image-pixflux + animate-with-text | guard_post_damaged, wall_damaged, soldier_walk(4f) | 3 |

### CONFIRMED animation cost
- `POST /v1/animate-with-text`, 4 frames @ 64×64, returns `usage.generations = 1.0`
  (NOT ~2 as estimated). A 4-frame walk = **1 generation**. Confirmed twice
  (worker_walk, soldier_walk). The `/animate-with-skeleton` endpoint was NOT used;
  `animate-with-text` with `action:"walk"` + `reference_image` worked first try.

### Generation method notes (lessons)
- **pixflux (text→sprite) is the reliable path.** bitforge `style_image` transfer
  produced NOISE when fed the sparse/transparent 64×64 anchor (style_strength read the
  empty bg as texture). Switched all subjects to pixflux with a shared `seed=4242` +
  gray `color_image` for coherence instead. Two bitforge calls failed BEFORE spending
  budget (HTTP 500 on a size mismatch; the failed queen-at-128 bitforge call also did
  not bill) — only successful 200s were counted.
- **Queen @128 via bitforge fails**: `style_image must be size (128,128)`. Use pixflux
  for the queen (text→sprite has no style-size constraint).
- Walk-anim frames inherited the near-black tone of the original anchor (the anchor
  came out dark). Frames are distinct (leg motion visible) and usable; if a cleaner
  gray walk is wanted later, regenerate from a gray `worker_idle_0` reference (1 gen).

## Packing (DONE)
- `free-tex-packer-cli` v0.3.0 is installed but ONLY accepts `--project file.ftpp`
  (no inline flags), awkward to script. Replaced with `tools/assetgen/pack.py` — an
  in-house deterministic packer emitting the **TexturePacker JSON-hash** format Pixi v8
  `Assets.load()` parses (verified field shapes against the vendored pixi.min.js parser:
  `frames`, `animations`, `rotated`, `trimmed`, `sourceSize`, `spriteSourceSize`,
  `meta.image`, `meta.scale` all present/consumed).
- Output committed to `frontend/game/assets/`: ants (12 frames/6 anims, 512×256),
  structures (8 frames, 512×256), terrain (5 frames, 512×128), nodes (6 frames, 512×128).
  `fx.*` NOT produced — all FX are procedural per spec §6.5 (renderer falls back).
- `animations` resolve: `ants.animations.worker_walk` = 4 ordered textures,
  `soldier_walk` = 4, single-frame idles (`worker_idle/soldier_idle/scout_idle/queen_idle`)
  = 1 each. Frame keys keep the `.png` suffix (e.g. `worker_idle_0.png`); clip names per
  spec §3.3 are the animation keys.

## Verification status
- Atlas frame bounds + image sizes validated (Python/PIL): all in-bounds, sizes match meta.
- Pixi JSON-hash field compatibility confirmed by inspecting the vendored Pixi v8 parser.
- **Visual browser load NOT run**: Playwright Chrome is not installed in this env
  (`/opt/google/chrome/chrome` missing). M2/M3 should load the atlases in-browser to
  confirm `sheet.animations[...]` renders. Static format checks all pass.

## Still TODO (deferred to later milestones, not this asset pass)
- Grayscale-base + Pixi tint legibility test at 32px (RED vs BLUE) — needs in-browser Pixi.
- Free-rotation legibility test on a real generated ant + the 128×128 queen — M3.
- Optional: structure under-construction (scaffold) frames + scout/queen walk (budget: 4 left).
