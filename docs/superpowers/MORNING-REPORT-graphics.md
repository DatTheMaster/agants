# Morning Report — Agants Graphics Overhaul (overnight build)

_Generated 2026-06-17. All work LOCAL ONLY: nothing was pushed, nothing was deployed, the live game server was never touched. Branch: `graphics-m0-foundation`._

## TL;DR (6 lines)
1. All 7 milestones (M0 foundation, Assets, M1–M5) completed and verified against the local replay harness — every acceptance criterion passed.
2. The new PixiJS renderer is fully opt-in behind `?pixi=1`; the legacy Canvas renderer is unchanged and still works with no flag (non-destructive contract held).
3. 26 of ~30 pixellab generations used (4 left as buffer per the frugal mandate); the only missing atlas is `fx.json` — all FX run procedurally as designed, so nothing is broken.
4. Verified via the bundled Playwright Chromium (the MCP's `chrome` channel isn't installed) pointed strictly at localhost; unit tests green (25/25 under `tests/js/`, plus AntView 7/7 and Effects suites).
5. Known rough edges: missing `fx.json` + several un-packed sprite clips (graceful fallbacks), FPS only measured under software GL (swiftshader), and a thin replay (59 ticks) that never exercises combat/damaged-structure paths.
6. Screenshots are in `tools/replay/shots/`; to view locally run the two servers and open `/game/?pixi=1&replay=true`.

## Per-milestone status

| Milestone | Status | Notes |
|---|---|---|
| M0-foundation | DONE | 6 commits (one per task). Pinned PixiJS 8.6.6 + pixi-filters 6.0.5 (vendored UMD). `?pixi=1` mounts cleanly; Canvas path unchanged. Verified: `node --test tests/js/` green, replay mounts with no errors, `ants.size` grows 0→22. |
| Assets | DONE | 25 generations this run (+1 pre-existing anchor = 26 total). 4 ant bases, 5 structures, 5 terrain tiles, 6 nodes, worker+soldier walk anims, damaged frames. In-house PIL packer (`tools/assetgen/pack.py`) emits Pixi v8 JSON-hash atlases: ants/structures/terrain/nodes. |
| M1-terrain-camera | DONE | Terrain baked (150×100, 20 chunk RenderTextures), territory + per-POV fog overlays, pan/zoom with clamping, POV toggle wired to existing DOM `#pov-btn`. Camera math pure + unit-tested (8 tests). |
| M2-structures | DONE | All 5 structure types tinted by colony, build-progress scaffold + bar, procedural range/vision rings. Visual replay only built watchtowers — guard_post/barracks/wall/larder + damaged-frame paths covered by code+unit tests, not visuals. |
| M3-ants | DONE | 22 ants interpolated (sub-tick glide), free-rotation facing, tier scaling, colony tint, clip state machine (walk/carry/etc.), carry pellets, ViewPool recycle. ~60 FPS at default zoom. |
| M4-fx-hud | DONE | Procedural FX (hit ring, death dissolve, build dust/flash, inferred guard-post beam, queen warning, win-state ColorMatrix), NodeView with glow halos, minimap + viewport rect, click-select ring, hover tooltip. Fixed a real bug: v8 ParticleContainer needs `addParticle`, not `addChild`. |
| M5-finalize | DONE | README-pixi.md documents asset gaps + regen pipeline. Pixi kept opt-in (not made default). Final scene renders coherently: terrain, lane walls, food/dirt nodes, ants, structures, fog/territory tint, FX glow, full HUD. |

## Pixellab usage & missing art
- **Used: 26 / ~30 generations** (1 pre-existing worker anchor + 25 this run). 4 left as buffer. Ledger: `tools/assetgen/ledger.json`. Confirmed cost finding: a 4-frame walk animation = 1.0 generation (not ~2 as previously estimated).
- **Atlases produced & committed:** `ants.json`, `structures.json`, `terrain.json`, `nodes.json` (+ matching PNGs) in `frontend/game/assets/`.
- **Still missing (all gracefully handled — never block rendering):**
  - `fx.json` — never generated; all FX are procedural by design (spec §6.5), so this is expected, not a gap. FX are placeholder-quality rather than authored sprites.
  - Ant clips: carry / dig / attack / death / scout_walk / queen_attack + `food_pellet` — fall back to the type's idle/walk clip, then to a colored-shape placeholder.
  - Structure frames: under-construction scaffold + some `_damaged` frames — fall back to active sprite + procedural scaffold/dashed overlay.
  - Node: corpse sprite — falls back to a gray-disc placeholder.

## How to view it locally
From the repo root (`tools/replay/server.py` opens `match.jsonl` via a relative path, so cwd MUST be the repo root):

```bash
cd /home/deshiel/projects/agants
python3 tools/replay/server.py &                 # replay WS on ws://localhost:8765
( cd frontend && python3 -m http.server 8090 ) & # static server on :8090
# then open in a browser:
#   http://localhost:8090/game/?pixi=1&replay=true   (new Pixi renderer)
#   http://localhost:8090/game/                       (legacy Canvas, unchanged)
```

Ensure `tools/replay/match.jsonl` exists first (63 lines: init + lobby + game_start + 59 tick snapshots). It is gitignored as a local artifact.

## Screenshots
All in `tools/replay/shots/` (gitignored): `m0-foundation.png`, `m1-verify-initial.png`, `m1-verify-red-pov.png`, `m1-verify-zoomed.png`, `m1-verify-camera.png`, `m2-structures.png`, `m3-ants.png`, `m3-ants-zoom.png`, `m3-ants-workers.png`, `m4-verify.png`, `m5-finalize.png`, `m5-canvas-noflag.png`.

## Known bugs / rough edges
- **`fx.json` 404** on load — handled gracefully (procedural FX fallback); the only console error seen, by design.
- **FPS unverified on real hardware** — only software GL (swiftshader) was available, which produces benign "GPU stall due to ReadPixels" warnings and depressed FPS (~47 under load, clean ~60 at default zoom). Real-GPU headroom is unknown.
- **Thin replay coverage** — the captured match is ~60 ticks of early game: no combat, no kills, no corpses, no damaged structures, only watchtowers built. So combat FX, the damaged-frame path, and 4 of 5 structure types are verified by code + unit tests only, not visually.
- **Harness launch-cwd footgun** — `tools/replay/server.py` must be launched from the repo root or it crashes per-connection with FileNotFoundError and leaves the client stuck at tick 0.
- **Playwright MCP unusable as-is** — pinned to the system `chrome` channel which isn't installed; all verification used the bundled Chromium via a standalone script instead.

## Recommended next steps
1. Capture a richer/longer replay (or synthesize a JSONL) that includes combat, kills, corpses, a damaged completed structure, and all 5 structure types — to visually verify the combat FX, damaged-frame, and remaining structure rendering paths.
2. Generate `fx.json` (and/or the missing ant clips: carry/dig/attack/death, scout_walk, queen_attack) with the remaining 4 pixellab generations to upgrade FX/animation fidelity from procedural/fallback to authored sprites.
3. Re-measure FPS on real GPU hardware to confirm the 60fps target with headroom.
4. Fix the Playwright MCP config (point at the bundled Chromium / install the `chrome` channel) so future verification can use the MCP directly.
5. Consider documenting the replay-server repo-root launch requirement in the harness or making the path resolution absolute.
6. When ready (and explicitly approved), merge `graphics-m0-foundation` and plan the deploy — currently untouched per the hard rules.
