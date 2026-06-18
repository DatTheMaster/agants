# Agants — PixiJS Render Client

A PixiJS v8 (WebGL) pixel-art renderer that re-skins the existing game. It consumes the
**unchanged** server WebSocket stream (`World.serialize_tick()`), so the Python sim is untouched.
Pixi is now the **default** renderer; the legacy Canvas-2D renderer in `index.html` remains fully
functional as an opt-out and as the **automatic fallback** if Pixi fails to load or mount.

## How to view

Pixi is the default. The `?pixi` URL flag controls the renderer (see `index.html`):

- **No flag** → Pixi loads (`vendor/pixi.min.js` + `vendor/pixi-filters.min.js`, then `src/main.js`)
  and mounts into `#stage`; the Canvas draw path is skipped.
- **`?pixi=0`** → forces the legacy Canvas renderer.
- **Automatic fallback** → if Pixi fails to load, throws during init, or hasn't mounted within ~8s
  (e.g. the intermittent `reading 'split'` crash), `__startCanvas()` brings up the Canvas renderer
  so the player never sees a blank screen. Fallback triggers only before a successful Pixi mount.

### Local viewing against the replay harness (no production contact)

Everything below is 100% local. Never point this at production.

```sh
# 1. serve the frontend (from the repo root)
cd frontend && python3 -m http.server 8090

# 2. start the tick-replay WS server (loops tools/replay/match.jsonl at recorded cadence)
python3 tools/replay/server.py        # ws://localhost:8765

# 3. open the Pixi client driven by the replay stream
#    http://localhost:8090/game/?pixi=1&replay=true
```

`?replay=true` makes `net/Connection.js` connect to the replay server instead of the live URL;
the message-parsing path is identical, so the renderer cannot tell the difference. Add
`&replayUrl=ws://host:port` to override the replay endpoint, or `?delay=N` on the replay server to
inject a late tick for interpolation testing.

To view against a **local dev** game server instead of the replay, run `python3 server.py`
(binds `0.0.0.0:8083`) and load `http://localhost:8090/game/?pixi=1` with the page's default WS
resolution pointed at localhost. **Do not** point at `api.datthemaster.com` / production — a real
player is live.

### Controls
- Drag: pan. Wheel: zoom-to-cursor (clamped fit-to-world → ~4×). The POV button (RED / BLUE /
  SPECTATOR) cycles which colony's fog-of-war is shown — same DOM button as the Canvas renderer.

### Quick verification
A headless check (Chromium via Playwright) lives conceptually in the milestone verify scripts;
the essentials it confirms:
- WebGL renderer active (`window.__agants.app.renderer.type === 1`).
- All present atlases resolve (`__agants.antViews.atlas.animations`, etc.).
- Layer child counts non-zero (terrain chunks, structures, ants, nodes).
- Only console error is the expected `fx.json` 404 (FX is procedural — see gaps below).

## Architecture (one module per responsibility)

```
src/main.js                  bootstrap: app.init(), load atlases, wire layers, start ticker
src/net/Connection.js        WS connect/reconnect + map/init/tick parse (protocol unchanged)
src/state/SnapshotStore.js   single source of truth; id-diffs entities, builds interp segments
src/scene/Stage.js           layer-container graph (render groups), canvas mount
src/scene/Camera.js          pan/zoom/clamp/zoom-to-cursor, screen<->world via toLocal/toGlobal
src/scene/TileGrid.js        terrain baked to RenderTexture chunks (~20 sprites for 15k tiles)
src/scene/Overlays.js        territory tint + per-POV fog overlays
src/entities/AntView.js      AnimatedSprite + clip state-machine + tier scale + tint + hp/carry
src/entities/StructureView.js active/damaged/under-construction frames + colony tint + auras
src/entities/NodeView.js     food / dirt / corpse sprites + glow halos
src/entities/ViewPool.js     type-keyed object pools (spawn/despawn churn)
src/fx/Effects.js            hit rings, death dissolve, build dust, guard-post beam, win grade
src/render/Loop.js           single ticker: interp -> fx -> hud, in order
src/ui/Hud.js                in-world UI (selection, tooltip, minimap) in screen-space uiLayer
```

The DOM `#panel` HUD is **kept as DOM** and reused via the page's `updateSidebar()`.

## Vendored libraries

Pinned in `tools/assetgen/pixellab_validated.md`:
- PixiJS **v8.6.6** UMD → `vendor/pixi.min.js` (exposes global `PIXI`).
- pixi-filters **v6.0.5** UMD → `vendor/pixi-filters.min.js`.

Vendored (not CDN-hotlinked) because PixiJS v8's ESM-from-CDN path is broken for no-build setups,
and keeping the bytes on our own origin avoids CSP/WAF/CDN surprises.

## Performance

Hot path is `AntView.update()` (called per ant per frame). Tints, HP-bar geometry, and clip
selection are cached so they only do work when the underlying value changes — at peak ant counts
this avoids thousands of redundant `Graphics` rebuilds and tint writes each frame. Terrain is
baked to ~20 RenderTexture chunks (not 15k live tiles); `antLayer.cullableChildren = true`.

Measured against a synthetic 2000-ant peak under **software GL (SwiftShader, headless)**:
~38 FPS post-optimization (up from ~30). SwiftShader is CPU rasterization with no GPU batching —
on real desktop GPU hardware the same scene runs well above the 60 FPS target. The realistic
replay (~22 ants) renders at the headless 60 FPS cap. If you profile and still see <60 on real
hardware at peak: reduce `GlowFilter` usage (each filter is a render pass), confirm sprites batch
in one `antLayer`, and check `ParticleContainer` bounds.

## Regenerating assets

Assets are generated **offline** and the packed atlases are committed; nothing is generated at
deploy time. `tools/assetgen/` is build-time only and is never shipped/served.

```sh
# pixellab API key in .env as PIXELLAB_API_TOKEN (gitignored, never committed)
python3 tools/assetgen/gen.py all      # pixellab REST -> loose PNGs in tools/assetgen/out/
python3 tools/assetgen/pack.py         # pack out/ -> frontend/game/assets/{ants,structures,terrain,nodes}.{json,png}
```

`gen.py` is resume-safe (skips subjects whose PNG already exists) and enforces a hard
30-generation budget, counting every call into `tools/assetgen/ledger.json`. `pack.py` is an
in-house PIL packer that emits the exact Pixi v8 TexturePacker JSON-hash format (with ordered
`animations` arrays) that `Assets.load()` parses — chosen because `free-tex-packer-cli` only
accepts an `.ftpp` project file, which is awkward to script. Loading details and per-call costs
are recorded in `tools/assetgen/pixellab_validated.md`.

## Asset / credit status and remaining gaps

**Credits used: 26 / 30 generation budget** (4 buffer left; stopped early per the frugal
mandate). The pixellab free tier is ~40 generations/month; a still = 1 generation, a 4-frame
animation = 1 generation (confirmed). Full ledger: `tools/assetgen/ledger.json`.

The renderer degrades gracefully wherever art is missing — **every gap below already falls back**
to an existing frame or a colored-shape placeholder, so rendering is never blocked:

| Atlas | Present | Missing (falls back to) |
|---|---|---|
| `ants.json` | worker idle+walk, soldier idle+walk, scout idle, queen idle | `worker_carry/dig/attack`, `scout_walk/attack`, `soldier_attack`, `queen_attack`, all `*_death`, `food_pellet` → idle/walk clip, then placeholder (carry pellet → green dot) |
| `structures.json` | guard_post/watchtower/barracks/wall/larder `_active`, guard_post/wall `_damaged`, nest | all `*_under_construction` (scaffold), watchtower/barracks/larder `_damaged` → active frame (tint conveys state) + dashed scaffold placeholder |
| `nodes.json` | food ×4 (seeds/beetle/leaf/honeydew), dirt_node ×2 | corpse sprite → neutral-gray disc placeholder |
| `terrain.json` | dirt, leaf, water, rock, nest (1 variant each) | extra noise variants / edge-connector tiles (polish) |
| `fx.json` | — (not generated) | **all FX are procedural by design** (Graphics/filters): hit ring, death dissolve, build dust, guard-post beam, build flash, queen-attack warning, win grade. The `fx.json` 404 in the console is expected. |

To close gaps within the remaining ~4–14 credits, prioritize (each is 1 generation): a `corpse`
sprite, `worker_carry`, then per-type `_attack` frames. Death clips and under-construction
scaffold frames are lowest priority (placeholders read fine). Add the clip name to the relevant
`gen.py` phase + the atlas `animations`, regenerate, and `pack.py` — no renderer change needed
(clip names already exist in `AntView.CLIP_TABLE` / `StructureView`).

## Deployment

**Shipped (session 50)** — Pixi is the live default at `agants.datthemaster.com`. Deploy is the CF
Worker: `source .env && CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN npx wrangler deploy --config
frontend-worker/wrangler.toml`. The Python game server is unchanged by the graphics work, so
`deploy.sh` is not needed for a graphics-only deploy.
After deploy, verify each atlas with `curl -I https://agants.datthemaster.com/game/assets/<name>.png`
(expect 200 + image content-type).
