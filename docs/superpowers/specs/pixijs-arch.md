# Spec: PixiJS v8 Render-Client Architecture for Agants

Status: RESEARCH/DESIGN (no implementation). Scope: re-skin the EXISTING content
(4 ant types, 5 structures, terrain, food/dirt, FX) using PixiJS v8 + WebGL, driven
by the unchanged server-authoritative WebSocket state stream. Python sim stays as-is.

---

## 0. Inputs this client must consume (verified against the live sim)

`World.serialize_tick()` (engine/world.py:1952) emits a per-tick dict at ~1 TPS:

- `ants[]` = `[id, x, y, prev_x, prev_y, colony, type, state, carrying, hp, max_hp]`
  - `type`: `0 worker, 1 soldier, 2 scout, 3 queen` (BASE_SZ/TIER_SCALE in current renderer)
  - `state` (engine/constants.py:21): `0 IDLE, 1 FORAGING, 2 RETURNING, 3 EXPLORING,
    4 FIGHTING, 5 PATROLLING, 6 RECRUITED, 7 BUILDING`
  - `carrying`: 0/1 — worker is hauling food/dirt
  - `prev_x/prev_y`: server-side previous-tick position (the interpolation source already exists)
- `food[]` = `[x, y, amt, kind, tier]`; `corpses[]` = `[x,y,amt]`; `dirt[]` = `[x,y,amt,tier]`
- `structures[]` = `[x, y, colony, hp, max_hp, type, active, build_progress, build_required]`
  - `type`: `guard_post | watchtower | barracks | wall | larder`
- `colonies[]` = big array incl. `id, nx, ny, food, [counts], directive, known_food, events,
  food_collected, ants_lost, alive, [worker_tier, scout_tier, soldier_tier], income, ...`
  - **tier is per-colony-per-class, NOT per-ant** — the renderer derives an ant's tier from
    its colony's class tier (worker→idx0, scout→idx1, soldier→idx2; queen always tier 3).
- `territory[]`, `fog[]` (per colony, byte array: 0 unseen, 1 explored, 2 visible),
  `tick, phase, elapsed_s, winner`.
- Map dims arrive on a separate `map` message: `MW=150, MH=100, TS=32` (tile px).
  World pixel extent = **4800 × 3200**.

Tick cadence is measured client-side (`tickInterval`, currently clamped 50–2000ms) because
the server can run 1–10 TPS. The renderer must interpolate over the *measured* interval, not
a hardcoded one.

Key implication: the stream is **stateless full snapshots keyed by entity id**. The client
diffs id sets each tick (spawn / despawn → death FX) exactly as the current Canvas renderer does.

---

## 1. Runtime / loading model (CF Worker static assets, no build server)

The frontend is a CF Worker serving `frontend/` as static assets (frontend-worker/wrangler.toml,
`assets = { directory = "../frontend", binding = "ASSETS" }`). There is **no bundler/build step**
in the deploy path (`npx wrangler deploy`). Two viable loading strategies:

**RECOMMENDED — vendored UMD global build, served from our own origin.**
- Drop `pixi.min.js` (v8 UMD) into `frontend/game/vendor/pixi.min.js` and load via
  `<script src="./vendor/pixi.min.js"></script>`. Exposes the global `PIXI`.
- Rationale: PixiJS v8's **ESM-from-CDN path is known-broken** (won't render / undefined
  `Application.ticker`); the official getting-started guide itself recommends the `<script>`
  tag for no-build setups. Vendoring (not hot-linking a CDN) keeps it on `agants.datthemaster.com`,
  avoids a third-party runtime dependency / CSP/WAF surprises, and is cache-controlled by our Worker.
- Filters/particles ship as separate packages — vendor `pixi-filters` (UMD, exposes
  `PIXI.filters`) and, if used, the particle emitter UMD build the same way.
- Author the renderer as **one or more plain `.js` modules loaded with native `<script type="module">`**
  that read the `window.PIXI` global. ES modules work natively in the browser with no bundler;
  only the Pixi *library* itself needs the UMD global workaround. This gives clean module
  boundaries (section 8) without a toolchain.

**Alternative (only if a build step is later added):** `npm i pixi.js`, bundle with esbuild/vite,
emit a single hashed JS into `frontend/game/`. More tree-shaking, but adds a build to the deploy
pipeline that does not exist today. Defer.

Asset bytes (sprite atlases PNG+JSON, generated from pixellab.ai, packed with TexturePacker)
live under `frontend/game/assets/` and are fetched by the Worker's `ASSETS` binding like any
other static file. No special MIME handling needed beyond what CF static assets already do.

---

## 2. Stage graph / layering

`app.stage` (the root Container) holds a small fixed set of **layer containers**, back-to-front.
World layers live under ONE `worldContainer` that the camera transforms; HUD lives in a separate
screen-space `uiLayer` that the camera never touches.

```
app.stage
├─ worldContainer            // camera applies position+scale HERE only
│  ├─ terrainLayer           // tile grid (TileGrid, section 4) — mostly static
│  ├─ dirtFoodLayer          // food/dirt/corpse nodes (Sprites)
│  ├─ territoryLayer         // colony territory tint (low-alpha Graphics/Mesh)
│  ├─ structureLayer         // 5 structure types (Sprites, optional directional sets)
│  ├─ groundFxLayer          // build dust, blood/death marks under ants
│  ├─ antLayer               // AnimatedSprites for ants (the hot layer)
│  ├─ airFxLayer             // hit sparks, glints above ants
│  └─ fogLayer               // fog-of-war overlay (per-POV), drawn last over world
└─ uiLayer                   // HUD, minimap, tooltips — SCREEN space, not camera-transformed
```

- v8 detail: mark `worldContainer` (or each heavy sub-layer) with `isRenderGroup: true` so its
  transform is handled on the GPU — this is what makes pan/zoom of a large static world cheap
  in v8 (render groups are the v8 mechanism that replaces a lot of manual camera work).
- The existing HUD (the big `#panel`, legend, upgrade rows, clock, FPS) is plain DOM and can stay
  DOM-over-canvas; only put *in-world-anchored* UI (selection rings, health bars, hover tooltips,
  minimap) into `uiLayer`. Keep the canvas behind the existing DOM panel.
- `app.init({ resizeTo: window, antialias: false, roundPixels: true, backgroundAlpha: ... })`.
  **`antialias:false` + `roundPixels:true`** is correct for 32px pixel art (crisp texels).
  Set `TextureSource` scaleMode to `'nearest'` globally for the same reason.

---

## 3. Camera (pan / zoom / coord transforms)

Two options; pick based on appetite for a dependency.

**RECOMMENDED — hand-rolled camera on `worldContainer`** (≈80 lines, zero deps, full control):
- Pan: `worldContainer.position` (a screen-space offset). Drag handlers add pointer delta.
- Zoom: `worldContainer.scale` (uniform). Clamp to e.g. `[minZoom, maxZoom]` where `minZoom`
  is chosen so the whole 4800×3200 world fits the viewport (`min(screenW/4800, screenH/3200)`),
  `maxZoom` ≈ 4 for inspecting individual ants.
- **Zoom-to-cursor** (the only non-trivial bit): keep the world point under the cursor fixed.
  ```
  worldPtBefore = (cursor - worldContainer.position) / worldContainer.scale
  worldContainer.scale *= zoomFactor            // then clamp
  worldContainer.position = cursor - worldPtBefore * worldContainer.scale
  ```
- **Screen↔world** for picking/tooltips: use Pixi's built-in `container.toLocal(globalPoint)` /
  `toGlobal(localPoint)` (the canonical v8 coord-conversion API) rather than reimplementing the math.
- **Clamping/edge bounds:** after any pan/zoom, clamp `worldContainer.position` so the world edges
  can't pull past the viewport (or allow a small over-scroll margin). Do it in one `clampCamera()`
  called from both pan and zoom handlers.

**Alternative — `pixi-viewport`** (vendor the UMD build): gives drag, pinch-zoom, wheel-zoom,
clamp, clampZoom, decelerate, `toWorld()/toScreen()` out of the box. Use it if pinch/inertia on
touch is wanted soon; otherwise the hand-rolled camera is lighter and avoids a v8-compat risk on
a community plugin. Either way the camera transforms ONLY `worldContainer`; `uiLayer` stays fixed.

Bind wheel/drag listeners to the canvas DOM element (or `app.stage` with `eventMode='static'`),
and convert wheel `clientX/clientY` to canvas-local coords before the zoom math.

---

## 4. Tilemap + culling (150×100 @ 32px = 4800×3200)

The terrain is a static, low-variety grid (terrain types + a fixed center divider line in the
current renderer). Three render strategies, recommended in order:

1. **Bake to a few big textures (RECOMMENDED for terrain).** The map never changes during a match.
   Compose the tile grid once into a small number of `RenderTexture` chunks (e.g. 4×3 chunks of
   ~1600×1024) using a temporary tiling pass, then display each chunk as one Sprite in
   `terrainLayer`. This collapses 15,000 tiles into ~12 draw calls and makes culling trivial
   (per-chunk visibility). Equivalent to v8's `container.cacheAsTexture()` on a tile container —
   render the children to a texture once, then draw a single texture thereafter.
2. **`pixi-tilemap` plugin** if tiles must stay individually live (animated water, destructible
   terrain later). It batches a whole tile layer into one draw call and is the standard answer for
   large Pixi tilemaps. Vendor the UMD build. Heavier than needed for a fully static map.
3. **Plain Sprites + culling** only if neither fits. 15k sprites is too many to leave uncull­ed.

**Culling** (for the dynamic layers — ants, food, structures — not the baked terrain):
- v8 ships culling in core. Properties: `cullable=true`, optional `cullArea` (a `Rectangle` in
  GLOBAL coords that does NOT inherit transforms), `cullableChildren`.
- Register `CullerPlugin` (`extensions.add(CullerPlugin)`) to auto-cull each frame, OR call
  `Culler.shared.cull(worldContainer, app.screen)` manually in the ticker.
- **Caveat from the field:** for big uniform grids/lists, Pixi's own docs note manual
  `visible=false` based on a position check is often faster than the generic culler. Given chunked
  baked terrain (already few sprites) the only layer that benefits from culling is `antLayer` at
  high ant counts; set `antLayer.cullableChildren = true` and let the CullerPlugin handle it, or
  do a cheap bounds test against the camera's visible world rect computed from `toLocal`.

Note the current sim caps total entities modestly (two colonies); even a naive approach is fine,
but baked terrain + render groups is the cheap, future-proof default.

---

## 5. Sprites, atlases, animation state machines

**Assets / loading (v8 `Assets` API):**
- Define a manifest of bundles and init once:
  `await Assets.init({ manifest })`, then `await Assets.loadBundle('core')` with a loading screen.
- Load an atlas by pointing `Assets.load('ants.json')` at the TexturePacker JSON (multipack/v8
  hash format); Pixi auto-fetches the JSON, loads the PNG, and calls `sheet.parse()`.
- Access frame sets: `sheet.textures['worker_idle_0.png']` and animation sequences via
  `sheet.animations['worker_walk']` (an ordered texture array).
- Create animations: `new AnimatedSprite(sheet.animations['worker_walk'])`,
  set `animationSpeed`, `anchor=0.5`, `play()`.
- Set global nearest-neighbor scaling for crisp pixel art.

**Atlas organization (one atlas per concern, keeps adding TYPES trivial later):**
- `ants.json` — for each of {worker, soldier, scout, queen} × animation {idle, walk, attack,
  carry, death} a short frame strip. Free-rotation facing (per spec): a single side/top sprite
  rotated by `sprite.rotation` toward movement heading (small sprites tolerate the distortion).
  Tiers 0–3 are rendered as the same base sprite with a scale multiplier (existing TIER_SCALE
  `[1.0,1.25,1.5,1.8]`) plus an optional tier accent (tint/badge), so no extra art per tier.
- `structures.json` — guard_post, watchtower, barracks, wall, larder. These are large/static →
  use **pre-rendered directional/variant frames** (per spec) rather than rotation, plus a
  build-in-progress frame set keyed off `build_progress/build_required`, and a damaged frame keyed
  off `hp/max_hp`. Color is applied by colony tint (RED/BLUE) on a grayscale base sprite.
- `terrain.json` + `fx.json` — generic style-matched pack supplements (terrain tiles, dust, spark,
  glint particles).

**Per-entity animation state machine** — map `state` + `type` + `carrying` to a clip:

```
worker:  IDLE→idle  FORAGING→walk  RETURNING→(carrying?carry:walk)  BUILDING→attack(dig)  FIGHTING→attack  default→walk
soldier: IDLE→idle  FIGHTING→attack  PATROLLING/RECRUITED→walk      default→walk
scout:   IDLE→idle  EXPLORING→walk  FIGHTING→attack                default→walk
queen:   IDLE→idle  FIGHTING→attack(rare)                          default→idle
death:   any id that vanished from the snapshot → spawn a one-shot `death` AnimatedSprite
         (loop=false) at last pos in groundFxLayer (current renderer already tracks dyingAnts).
```

Implement as a tiny `AntView` wrapper holding the sprite + `currentClip`; on each tick, if the
desired clip changed, swap `sprite.textures = sheet.animations[clip]` and `play()` (don't recreate
the AnimatedSprite). `carrying` adds a food-pellet child sprite toggled on/off. HP bar is a child
`Graphics`/Sprite shown only when `hp < max_hp`.

**Object pooling:** keep an id→AntView `Map` (mirrors current `antMap`). On despawn, return the
view to a free pool keyed by type instead of destroying it — avoids GC churn at high spawn rates.

---

## 6. Tick sync + interpolation via the Pixi ticker

The sim is server-authoritative at 1–10 TPS; the renderer runs at 60fps and must glide sprites
between snapshots (parity with the existing Canvas interpolation, which already uses `prev_x/prev_y`).

**Model: snapshot buffering with render-time interpolation.**
- `onTick(snapshot)`: for each ant id, store `{from:{x,y}, to:{x,y}}`. The cleanest source of
  truth is the server's own `prev_x,prev_y → x,y` pair (already in the stream), so each tick is
  self-contained: `from = (prev_x,prev_y)`, `to = (x,y)`. Record `tickStartMs = now` and the
  measured `tickInterval`.
- `app.ticker.add(() => { ... })`: each frame compute `t = clamp((now - tickStartMs)/tickInterval, 0,1)`
  and set `view.sprite.position = lerp(from, to, t)`. Use `ticker.deltaTime`/`deltaMS` only for
  FX timers; positional interp keys off wall-clock elapsed vs. measured tick interval, NOT
  deltaTime accumulation (avoids drift).
- Facing: `sprite.rotation = atan2(to.y-from.y, to.x-from.x)` (smoothed); cache last heading so
  idle ants keep their last facing (current renderer's `lastAngle`).
- Late/dropped tick resilience: clamp `t` at 1 so sprites rest at `to` if the next tick is late;
  when it arrives, the new `from` is the server `prev`, so there's no teleport. Optionally hold a
  1-tick buffer (render slightly in the past) for extra smoothness if jitter shows up — start
  without it since `prev_x/prev_y` already gives clean segments.
- Non-positional fields (hp bar, carry pellet, animation clip, structure build %) snap at tick
  boundaries; only x/y interpolate.

Keep a single ticker callback that drives: camera inertia (if any), ant interpolation, FX
lifetimes, and culling — one ordered update per frame.

---

## 7. Particle / filter FX

The current renderer has hit flashes, death puffs, build dust, and territory/fog tints. Map these:

- **Hit / death bursts:** v8 `ParticleContainer` (reworked, 100k+ particles, stores `Particle`
  objects in `particleChildren`, requires a `boundsArea` since it skips bounds calc). One
  `ParticleContainer` in `airFxLayer` for sparks, one in `groundFxLayer` for dust; spawn short-lived
  `Particle`s on hp-drop / despawn events detected in `onTick`. For low volumes, a pooled set of
  AnimatedSprite one-shots from `fx.json` is simpler and good enough.
- **Glow on combat / queen / selection:** `pixi-filters` `GlowFilter({ outerStrength, color })`
  on the relevant sprite (`sprite.filters = [glow]`). Use sparingly — filters are per-object render
  passes.
- **Fog of war:** render the per-POV fog byte grid as a single dark overlay (a `Mesh`/`Sprite`
  whose texture is updated from the fog bytes, or a low-res `Graphics` of unseen tiles) in
  `fogLayer`; `visible=2` cells fully clear, `1` dimmed, `0` dark. A `BlurFilter` on the fog layer
  softens edges cheaply.
- **Territory tint:** low-alpha colony-colored `Graphics`/Mesh in `territoryLayer`; rebuild only on
  tick when `territory` changes, not per frame.
- **Color grading / win state:** a `ColorMatrixFilter` on `worldContainer` (e.g. desaturate on
  game-over, brief `night`/`contrast` preset on big events). One filter on the world root is cheap.
- Filters are assigned as `sprite.filters = [...]` and applied in array order (v8 semantics).

---

## 8. Recommended client module layout (module boundaries)

Plain ESM modules under `frontend/game/`, reading `window.PIXI` (UMD global). No bundler.

```
frontend/game/
  index.html              // loads vendor/pixi UMD, then <script type="module" src=./src/main.js>
  vendor/pixi.min.js      // vendored v8 UMD (+ pixi-filters, pixi-tilemap UMD as needed)
  assets/                 // ants.json/png, structures.json/png, terrain.json/png, fx.json/png
  src/
    main.js               // bootstrap: app.init(), load bundles, wire layers, start ticker
    net/Connection.js     // WebSocket: connect/reconnect, parse map+tick msgs (unchanged protocol)
    state/SnapshotStore.js// holds latest snapshot, id-diffing, from/to interp segments, tick timing
    scene/Stage.js        // builds layer containers, render groups, fog/territory overlays
    scene/Camera.js       // pan/zoom/clamp/zoom-to-cursor, screen<->world (toLocal/toGlobal)
    scene/TileGrid.js     // bake terrain to RenderTexture chunks (or pixi-tilemap)
    entities/AntView.js   // AnimatedSprite wrapper + state-machine clip selection + hp/carry
    entities/StructureView.js // directional/build/damage frames, colony tint
    entities/NodeView.js  // food/dirt/corpse sprites
    entities/ViewPool.js  // type-keyed object pools
    fx/Effects.js         // ParticleContainer bursts, glow/blur/colormatrix filters, fog update
    render/Loop.js        // single ticker callback: interp → fx → cull (order matters)
    ui/Hud.js             // in-world UI in uiLayer (selection, tooltips, minimap); DOM panel stays
    assets/manifest.js    // Assets bundle manifest
```

Data flow: `Connection` → `SnapshotStore` (tick boundary, snaps non-positional fields, builds
interp segments) → `Loop` (per-frame: lerp positions, drive AnimatedSprite clips via `AntView`,
update FX/fog, cull) → Pixi renders `worldContainer` (camera-transformed) + `uiLayer` (fixed).
`SnapshotStore` is the single owner of game state; views are pure projections of it — this is what
makes adding new unit/structure TYPES later a matter of one new `*View` + atlas entry.

---

## 9. Open decisions / risks

- **pixellab.ai facing:** spec mandates free-rotation for ants (rotate one sprite). Validate that
  the generated top-down ant reads acceptably when rotated 360°; if not, fall back to an 8-direction
  pre-rendered set (same `AntView` API, just more frames). Structures already use pre-rendered sets.
- **v8 ESM-from-CDN is broken** → MUST vendor the UMD build; do not hot-link an ESM CDN.
- **Culling on uniform grids** may be slower than manual `visible` checks (Pixi's own caveat) —
  measure; baked terrain sidesteps it for the biggest layer.
- **Render-group count:** mark only the world root (and maybe antLayer) as render groups; too many
  render groups can regress. Profile with the on-screen FPS counter that already exists.
- No bundler means no tree-shaking → full Pixi payload (~hundreds of KB). Acceptable for a desktop
  spectator client served from CF cache; revisit only if mobile load time matters.

---

## Sources

- App/init, scene graph, render groups, cacheAsTexture: https://pixijs.com/8.x/guides/components/application , https://pixijs.com/8.x/guides/concepts/scene-graph , https://pixijs.com/blog/better-docs-v8
- v8 migration (Application.init async, ParticleContainer, culling): https://pixijs.com/8.x/guides/migrations/v8
- Camera / render groups for pan-zoom large world: https://pixijs.com/blog/pixi-v8-launches , https://pixijs.com/8.x/guides/components/scene-objects
- pixi-viewport (camera plugin): https://www.npmjs.com/package/pixi-viewport , https://viewport.pixijs.io/jsdoc/Viewport.html
- Assets + Spritesheet + AnimatedSprite: https://pixijs.com/8.x/guides/components/assets , https://pixijs.download/dev/docs/assets.Spritesheet.html , https://github.com/pixijs/pixijs-skills/blob/main/skills/pixijs-assets/references/spritesheet.md , https://www.codeandweb.com/texturepacker/tutorials/how-to-create-sprite-sheets-and-animations-with-pixijs
- Culling API: https://www.richardfu.net/optimizing-rendering-with-pixijs-v8-a-deep-dive-into-the-new-culling-api/ , https://pixijs.com/8.x/guides/concepts/performance-tips
- ParticleContainer v8: https://pixijs.com/blog/particlecontainer-v8 , https://pixijs.download/dev/docs/scene.ParticleContainer.html
- Ticker / render loop: https://pixijs.com/8.x/guides/components/ticker , https://pixijs.com/8.x/guides/concepts/render-loop
- Filters (Glow/Blur/ColorMatrix): https://pixijs.com/8.x/guides/components/filters , https://github.com/pixijs/filters
- CDN/no-build loading + v8 ESM-CDN breakage: https://pixijs.io/guides/basics/getting-started.html , https://github.com/pixijs/pixijs/issues/10446
