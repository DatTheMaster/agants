# Design Spec: Agants Graphics Overhaul (PixiJS Re-skin)

Status: DESIGN / IMPLEMENTATION-READY. No code in this effort beyond this file.
Companion doc: `docs/superpowers/specs/pixijs-arch.md` (lower-level PixiJS v8 architecture
reference). This spec is the higher-level, hand-to-planning document: goal/scope, module
boundaries, full data-flow field mapping, art pipeline, enumerated asset manifest, phased
milestones, testing, and risks. Where the two overlap, this doc is the source of truth for
*what* and the companion is the source of truth for *PixiJS API specifics*.

---

## 1. Goal & Scope

### Goal
Replace the current procedural Canvas-2D renderer (`frontend/game/index.html`, ~2400 lines,
all geometry primitives, no image assets) with a **PixiJS v8 (WebGL) pixel-art render client**
that makes the **existing** game content look great: 4 ant types, 5 structures, terrain,
food/dirt nodes, and the existing FX. The new client consumes the **unchanged** server
WebSocket stream (`World.serialize_tick()` in `engine/world.py:1952`).

### In scope (RE-SKIN FIRST)
- Pixel-art sprites (32px tile basis) for: workers, soldiers, scouts, queen (tiers 0–3);
  guard_post, watchtower, barracks, wall, larder (active / under-construction / damaged);
  terrain tiles (dirt, leaf, water, rock, nest); food kinds (seeds, beetle, leaf, honeydew);
  dirt deposits (2 tiers); corpses; FX (hit ring, death dissolve, build dust, guard-post beam).
- A camera with pan / zoom / clamp and screen↔world conversion.
- Movement interpolation at 60fps between server ticks (parity with current behavior; the
  server already supplies `prev_x/prev_y`).
- Fog-of-war and territory overlays, per-POV.
- An asset-generation pipeline (pixellab.ai) and a committed sprite atlas + manifest.
- The renderer slots into the SAME page served by the CF Worker; the existing DOM HUD panel
  (`#panel`, upgrade rows, clock, FPS, chat/events) is **kept as DOM** and reused.

### Explicitly OUT of scope (do NOT do in this effort)
- **No new unit or structure TYPES.** Only the 4 ants + 5 structures that exist today.
  (Architecture must make adding a new type a one-`*View`+one-atlas-entry change — but adding
  one is a later phase.)
- **No Python / sim changes.** Server stays authoritative; `serialize_tick` schema is a fixed
  contract. No changes to `engine/`, `server.py`, `mcp_server.py`, `controller/`.
- **No gameplay/balance changes**, no new HUD features beyond visual parity + in-world UI.
- **No production deploy** as part of this effort. Deploy is the CF Worker
  (`frontend-worker/wrangler.toml`) and is gated on explicit user approval. A real player is
  live; do not touch the live server, do not run `deploy.sh`, do not create matches.

### Success criteria
- Visual parity-or-better with the current renderer for every entity/terrain/FX type.
- 60fps at peak unit counts (two colonies, ~2000 ants) on desktop Chrome.
- Runs locally against a recorded/replayed tick stream with no production dependency.
- Adding a hypothetical new ant type would touch only `assets/manifest.js`, the atlas, and
  one new entry in the type→view/clip tables — no renderer rewrite.

---

## 2. Architecture

### 2.1 How it slots into the CF-Worker page
- The frontend is a CF Worker serving `frontend/` as static assets
  (`frontend-worker/wrangler.toml`: `assets = { directory = "../frontend", binding = "ASSETS" }`;
  `frontend-worker/src/index.js` redirects `/` → `/landing`, `/game` → `/game/`, else
  `env.ASSETS.fetch`). **There is no bundler in the deploy path** (`npx wrangler deploy`).
- The game page remains `frontend/game/index.html`. We KEEP its HTML shell, the DOM HUD
  (`#panel` and children), the WS connect/config modal, and the page CSS/layout. We REPLACE the
  `<canvas>` rendering block and the ~2000 lines of Canvas drawing code with a PixiJS canvas
  mounted in the same `#stage` slot, driven by ES modules under `frontend/game/src/`.
- **PixiJS loading:** vendor the **UMD** build (`frontend/game/vendor/pixi.min.js`, plus
  `pixi-filters` UMD) and load via `<script src="./vendor/pixi.min.js">`, exposing global `PIXI`.
  Rationale (see companion §1): PixiJS v8's ESM-from-CDN path is broken for no-build setups, and
  vendoring keeps everything on `agants.datthemaster.com` (no CDN/CSP/WAF surprises, cache-
  controlled by our Worker). Renderer logic is authored as native `<script type="module">` ES
  modules reading `window.PIXI` — clean module boundaries with zero toolchain.
- Atlas PNG+JSON live under `frontend/game/assets/` and are served by the `ASSETS` binding like
  any other static file (no special MIME handling needed).
- **Asset deployment model (vendored at build-time, NOT generated at deploy-time):** the
  `frontend/game/assets/` and `frontend/game/vendor/` directories **do not exist yet** and are
  created by this effort. The flow is:
  1. Run `tools/assetgen/gen.py` (pixellab SDK, reads `seeds.json`) → loose transparent PNGs.
  2. Run `tools/assetgen/pack.sh` (free-tex-packer) → `frontend/game/assets/ants.json+png`,
     `structures.*`, `terrain.*`, `nodes.*`, `fx.*`.
  3. **Commit** the packed atlases + the vendored `pixi.min.js` / `pixi-filters` UMD to git
     (they are static, content-addressed by our cache; `tools/assetgen/` itself is NOT shipped).
  4. `npx wrangler deploy --config frontend-worker/wrangler.toml` uploads `frontend/` via the
     `ASSETS` binding — no generation happens at deploy time.
  5. **Verify after deploy** (gated on user approval) with
     `curl -I https://agants.datthemaster.com/game/assets/ants.png` (expect `200` + image
     content-type) and likewise for each `.json`. Locally, the replay-harness http server (§8)
     serves the same paths for pre-deploy verification.

### 2.2 Module boundaries
Plain ESM under `frontend/game/src/`, each module a single responsibility:

| Module | Responsibility | Reused / New |
|---|---|---|
| `net/Connection.js` | WebSocket connect/reconnect, parse `map`/`init`/tick messages. **Protocol unchanged.** | Logic ported from current `index.html` WS block (lines ~928-954) |
| `state/SnapshotStore.js` | Single source of truth. Holds latest snapshot; id-diffs ants/structures/nodes each tick; builds from/to interpolation segments; records tick timing; snaps non-positional fields. | Replaces inline `antMap`/`dyingAnts`/`colTiers` state |
| `scene/Stage.js` | Builds the layer container graph (render groups), fog & territory overlays, mounts the Pixi canvas into `#stage`. | New |
| `scene/Camera.js` | Pan / zoom / clamp / zoom-to-cursor; `screen↔world` via `toLocal/toGlobal`. | Replaces `camX/camY/camScale` + `clampCamera` |
| `scene/TileGrid.js` | Bake terrain to a few `RenderTexture` chunks (or `pixi-tilemap`). | Replaces procedural `terrainBuf` |
| `entities/AntView.js` | AnimatedSprite wrapper + state-machine clip selection + hp bar + carry pellet + free-rotation facing. | Replaces cached-sprite ant draw funcs |
| `entities/StructureView.js` | Directional/variant + build-progress + damaged frames; colony tint. | Replaces 2-pass structure draw |
| `entities/NodeView.js` | Food / dirt / corpse sprites + glow halo. | Replaces node draw funcs |
| `entities/ViewPool.js` | Type-keyed object pools for spawn/despawn churn. | New |
| `fx/Effects.js` | ParticleContainer bursts, GlowFilter/Blur/ColorMatrix, fog texture update, guard-post beam. | Replaces `effects[]` + procedural FX |
| `render/Loop.js` | Single ticker callback: interp → FX → cull, in order. | Replaces `requestAnimationFrame render()` |
| `ui/Hud.js` | In-world UI only (selection rings, hover tooltips, minimap) in screen-space `uiLayer`. The DOM `#panel` HUD stays DOM. | Partly new; DOM `updateSidebar()` kept |
| `assets/manifest.js` | Pixi `Assets` bundle manifest (atlas names → paths). | New |
| `main.js` | Bootstrap: `app.init()`, load bundles, wire layers, start `Connection` + ticker. | New |

### 2.3 Stage graph (back-to-front)
```
app.stage
├─ worldContainer            (isRenderGroup:true — camera applies position+scale HERE only)
│  ├─ terrainLayer           (baked RenderTexture chunks)
│  ├─ dirtFoodLayer          (food / dirt / corpse Sprites)
│  ├─ territoryLayer         (colony tint, low-alpha; rebuilt only when territory changes)
│  ├─ structureLayer         (5 structure types; walls drawn first, others after)
│  ├─ groundFxLayer          (build dust, death marks, beam endpoints under ants)
│  ├─ antLayer               (AnimatedSprites — the hot layer; cullableChildren=true)
│  ├─ airFxLayer             (hit sparks, glints above ants; guard-post beam line)
│  └─ fogLayer               (per-POV fog overlay, drawn last over the world)
└─ uiLayer                   (HUD/minimap/tooltips — SCREEN space, never camera-transformed)
```
`app.init({ resizeTo: window, antialias:false, roundPixels:true })`; global TextureSource
scaleMode `'nearest'` for crisp 32px texels.

### 2.4 What is replaced vs kept
- **Replaced:** the `<canvas>` 2D drawing context and ALL primitive drawing — terrain noise,
  territory/fog `putImageData`, ant shape draw + cached rotation, structure shapes, node glows,
  HP bars, FX circles/lines. The 60fps `render()` loop body. `resetCamera/clampCamera` math.
- **Kept:** the page HTML/CSS, the DOM HUD panel and `updateSidebar()` content, the WS URL
  resolution + reconnect logic (re-homed into `Connection.js`, protocol identical), the config
  modal, the POV toggle wiring, the interpolation *concept* and tick-interval clamping, the
  CF-Worker deploy mechanism, the colony color constants (RED `255,60,60` / BLUE `60,60,255`),
  food/dirt color constants, TIER_SCALE `[1.0,1.25,1.5,1.8]`.

---

## 3. Data Flow & Field Mapping

### 3.1 Flow
```
Connection (WS msg) ─▶ SnapshotStore.applyTick(snapshot)
        │                   │  - id-diff each entity list (added → bind view; removed → death FX + pool)
        │                   │  - update per-colony class tiers (worker/scout/soldier; queen=3)
        │                   │  - record tickStartMs = now, measure tickInterval
        │                   │  - snap non-positional fields (hp, carrying, clip, build%, territory, fog)
        ▼                   ▼
   Loop (ticker, 60fps): t = clamp((now-tickStartMs)/tickInterval, 0, 1)
        - AntView/NodeView/StructureView position = lerp(from, to, t)   (x/y only)
        - AntView.selectClip(type, state, carrying) → swap AnimatedSprite textures if changed
        - AntView.rotation = atan2(to.y-from.y, to.x-from.x) (smoothed; idle keeps last heading)
        - Effects.update(dtMS): particle lifetimes, beam fade, fog/territory if dirty
        - cull antLayer; render worldContainer (camera) + uiLayer (fixed)
```
Critical: positional interpolation keys off wall-clock elapsed vs. **measured** tick interval
(server runs 1–10 TPS), NOT `deltaTime` accumulation. `from = (prev_x,prev_y)`, `to = (x,y)`
from the stream — each tick is a self-contained segment, so a late tick rests at `to` (clamp
t=1) with no teleport when the next arrives.

### 3.2 serialize_tick field → visual mapping
Verified against `engine/world.py:1952` and `engine/constants.py:21`.

**ants[]** = `[id, x, y, prev_x, prev_y, colony, type, state, carrying, hp, max_hp]`

| Field | Index | Visual mapping |
|---|---|---|
| `id` | 0 | Key into `SnapshotStore` view map (`id→AntView`); diff drives spawn/death |
| `x, y` | 1,2 | `to` of interp segment → `sprite.position` (lerp with `from`) |
| `prev_x, prev_y` | 3,4 | `from` of interp segment |
| `colony` | 5 | Sprite tint: RED `0xff3c3c` / BLUE `0x3c3cff` (grayscale base art). **Verified:** `engine/world.py:1956` passes `a.colony` as an integer colony id; colonies are constructed in order at `engine/world.py:220-221` (`enumerate([red_pos, blue_pos])`), so the value is zero-indexed in range `[0,1]` — `0=RED, 1=BLUE` (matches winner mapping at `engine/world.py:725`). Colony tint map (also used by StructureView): `{0: 0xff3c3c, 1: 0x3c3cff}`. AntView/StructureView MUST look the tint up from this map, never compute it, to avoid off-by-one. |
| `type` | 6 | `0 worker / 1 soldier / 2 scout / 3 queen` → atlas subject + base size + tier index source |
| `state` | 7 | `0 IDLE / 1 FORAGING / 2 RETURNING / 3 EXPLORING / 4 FIGHTING / 5 PATROLLING / 6 RECRUITED / 7 BUILDING` → animation clip (§3.3). **Verified against `engine/constants.py:21`**: `S_IDLE=0, S_FORAGING=1, S_RETURNING=2, S_EXPLORING=3, S_FIGHTING=4, S_PATROLLING=5, S_RECRUITED=6, S_BUILDING=7`. The state→clip mapping in §3.3 is a **client-side rendering contract only**; the Python sim has no state→animation table, so this table is the single source of truth for clip selection and lives entirely in the renderer. |
| `carrying` | 8 | `1` → show food-pellet child sprite + prefer `carry` clip when RETURNING |
| `hp, max_hp` | 9,10 | HP bar child shown only when `hp < max_hp`; `hp` drop between ticks → spawn hit FX |

Tier is derived, NOT in the ant tuple: from `colonies[]` per-class tiers
(`worker→worker_tier`, `scout→scout_tier`, `soldier→soldier_tier`, queen→3). Tier → scale via
`TIER_SCALE[tier]` + optional tier accent (tint/badge). No extra art per tier.

**food[]** = `[x, y, amt, kind, tier]`

| Field | Visual |
|---|---|
| `x,y` | NodeView position (static; no interp needed) |
| `amt` | Scale / glow intensity of the node (depletes as foraged) |
| `kind` | `seeds / beetle / leaf / honeydew` → atlas frame + base color (FCOL) |
| `tier` | `home / approach / frontline` → ring/glow color accent |

**corpses[]** = `[x, y, amt]` → corpse sprite at `x,y`; see §6.4 for exact rendering.
NodeView renders the single neutral-gray corpse sprite (no animation; static). Size scales by
`sqrt(amt / CORPSE_AMT_MAX)` (area-proportional, so a 4× pile is 2× wide, not 4×), and alpha
fades proportionally from `1.0` down toward `0.25` as `amt` depletes across ticks, until the id
leaves `corpses[]` and the view is pooled. `CORPSE_AMT_MAX` is a client display constant (set to
the largest observed corpse `amt`, default `200`); it only scales appearance, not gameplay.

**dirt[]** = `[x, y, amt, tier]` → hexagon-core dirt sprite; `tier` (home/frontline) → glow accent.

**structures[]** = `[x, y, colony, hp, max_hp, type, active, build_progress, build_required]`

| Field | Visual |
|---|---|
| `x,y` | StructureView position (tile-snapped) |
| `colony` | Tint RED/BLUE |
| `hp, max_hp` | `hp/max_hp` selects `active` vs `damaged` frame; HP bar when damaged |
| `type` | `guard_post / watchtower / barracks / wall / larder` → atlas subject + footprint |
| `active` | `0` → under-construction frame set + progress bar; `1` → active frame |
| `build_progress / build_required` | progress-bar fill ratio + construction frame index |

Walls render first (grid pass), other structures second (parity with current 2-pass order).

**Guard-post beam (procedural, fully client-side):** The server *stores* `fired_at` / `fire_tick`
on the struct dict (`engine/world.py:463-464`) but does **NOT** serialize them — the structures
tuple is only `[x, y, colony, hp, max_hp, type, active, build_progress, build_required]`
(`engine/world.py:1999-2002`), so the target XY is **not available to the renderer**. Since no
sim change is allowed, the beam is **inferred from observable state changes**, not from
`fired_at`:
1. In `SnapshotStore.applyTick`, when an ant's `hp` drops between ticks (the same hp-drop signal
   already used to spawn hit FX), check whether any active `guard_post` of the **enemy** colony is
   within guard-post range (`GUARD_POST_RANGE`, a client constant mirroring the sim value) of that
   ant. If so, emit a beam event `{from:{x,y} of that guard_post, to:{x,y} of the hit ant,
   startTick:currentTick}` into `Effects`.
2. In `Effects.update(dtMS)`, each beam computes `alpha = 1 - (now - startMs)/tickIntervalMs`
   (one-tick lifetime), and is drawn as a `Graphics` line in `airFxLayer`, redrawn every frame.
   Remove the beam when `alpha <= 0`.
3. This is best-effort visual only (a single guard_post is credited when several are in range);
   that is acceptable for an FX flourish and avoids any sim change. If exact targeting is later
   wanted, the only correct fix is to add `fired_at` to the serialized struct tuple — explicitly
   out of scope here. (See §6.5 / §7 Phase M4.)

**colonies[]** (big array per `engine/world.py:1991-1996`): `id, nest_x, nest_y, food, counts,
directive, known_food, events, food_collected, ants_lost, alive, tiers[worker,scout,soldier],
income, spawn_queue_summary, aging_soon, upgrade_eta, dirt`.
- `nest_x, nest_y` → nest sprite (carved 5×5 nest tile region; `T_NEST` terrain).
- `tiers[...]` → per-class tier source for ant scale/accent (§3.2).
- `alive` → on `0`, optional desaturate ColorMatrix on that colony's units / game-over grade.
- Everything else (food, counts, directive, events, income, spawn queue, aging, upgrade_eta,
  dirt, known_food) → **DOM HUD** via the kept `updateSidebar()`; NOT Pixi-rendered.

**territory** = bytearray 150×100 (`0 neutral / 1 RED / 2 BLUE`) → `territoryLayer` low-alpha
tint; rebuild only when changed (compare to last), not per frame.

**fog[]** = per-POV byte grid (`0 unexplored / 1 explored / 2 visible`) → `fogLayer` overlay:
`2` clear, `1` dimmed, `0` dark; texture updated on tick when fog changes; BlurFilter softens edges.

**tick / phase / elapsed_s / winner** → DOM HUD clock + win-state ColorMatrix grade.

**map message** (`MW=150, MH=100, TS=32`) → world extent 4800×3200; drives TileGrid bake +
camera clamp bounds.

### 3.3 Animation state machine (type × state × carrying → clip)

State values are **verified against `engine/constants.py:21`**: `S_IDLE=0, S_FORAGING=1,
S_RETURNING=2, S_EXPLORING=3, S_FIGHTING=4, S_PATROLLING=5, S_RECRUITED=6, S_BUILDING=7`. The
Python sim defines no state→animation mapping; **clip selection is client-side only and this
table is the renderer contract** (the single source of truth, superseding the companion
`pixijs-arch.md §5` sketch where they differ).

**BUILDING uses a distinct `dig` clip — not `attack`.** (This resolves the conflict with
`pixijs-arch.md §5`, which wrote `BUILDING→attack(dig)`.) The clip is named `worker_dig` and
is its own 4-frame entry in `ants.json`; it is NOT an alias of `worker_attack`. Only the worker
ever enters BUILDING, so only the worker has a `dig` clip.

Canonical clip names (must match atlas `sheet.animations[...]` keys exactly):

```
type    | state                                          | clip
--------|------------------------------------------------|----------------
worker  | IDLE                                           | worker_idle
worker  | FORAGING                                       | worker_walk
worker  | RETURNING (carrying)                           | worker_carry
worker  | RETURNING (not carrying)                       | worker_walk
worker  | BUILDING                                       | worker_dig   (distinct 4-frame clip)
worker  | FIGHTING                                       | worker_attack
worker  | PATROLLING / RECRUITED / EXPLORING            | worker_walk
soldier | IDLE                                           | soldier_idle
soldier | FIGHTING                                       | soldier_attack
soldier | PATROLLING / RECRUITED / FORAGING / RETURNING  | soldier_walk
soldier | default                                        | soldier_walk
scout   | IDLE                                           | scout_idle
scout   | EXPLORING                                      | scout_walk
scout   | FIGHTING                                       | scout_attack
scout   | default                                        | scout_walk
queen   | IDLE                                           | queen_idle
queen   | FIGHTING (rare)                                | queen_attack
queen   | default                                        | queen_idle
death   | id vanished from snapshot                      | <type>_death (one-shot AnimatedSprite, loop=false, at last pos in groundFxLayer)
```

Implemented as a small data table `{type:{state:clipName}}` with a per-type `default`, so adding
a type = adding one row. The atlas (§6.1) MUST contain exactly these clip names so frames match
the table; `worker_dig` is counted as a separate clip in §6.1's frame budget.

---

## 4. Camera & Input

Hand-rolled camera on `worldContainer` (≈80 lines, zero deps). The companion `pixijs-arch.md §3`
is **normative** for the exact API usage; the core zoom-to-cursor math is inlined below so this
spec is self-contained.
- **Pan:** drag updates `worldContainer.position` (screen offset). Bind pointer listeners to the
  canvas DOM element.
- **Zoom:** wheel multiplies `worldContainer.scale` (uniform), clamped to
  `[minZoom, maxZoom]` where `minZoom = min(screenW/4800, screenH/3200)` (whole world fits) and
  `maxZoom ≈ 4` (inspect a single ant). Zoom-to-cursor keeps the world point under the cursor
  fixed. Convert the wheel `clientX/clientY` to canvas-local coords first
  (`cursor = {x: clientX - canvasRect.left, y: clientY - canvasRect.top}`), then:
  ```js
  // 1. world point currently under the cursor (before zoom)
  const wpx = (cursor.x - worldContainer.position.x) / worldContainer.scale.x;
  const wpy = (cursor.y - worldContainer.position.y) / worldContainer.scale.y;
  // 2. apply + clamp the new uniform scale
  let s = worldContainer.scale.x * zoomFactor;       // zoomFactor e.g. 1.1 up / 0.9 down
  s = Math.max(minZoom, Math.min(maxZoom, s));
  worldContainer.scale.set(s);
  // 3. reposition so (wpx,wpy) lands back under the cursor
  worldContainer.position.set(cursor.x - wpx * s, cursor.y - wpy * s);
  clampCamera();                                     // then re-clamp edges
  ```
- **Clamp:** one `clampCamera()` after pan and zoom keeps world edges from pulling past the
  viewport (small over-scroll margin allowed). Mirrors current `clampCamera` semantics.
- **Screen↔world:** Pixi `container.toLocal(global)` / `toGlobal(local)` for picking, hover
  tooltips, selection, minimap clicks. No reimplemented matrix math.
- **Initial view:** fit-to-world (`resetCamera` equivalent), centered.
- `uiLayer` is never transformed by the camera (stays screen-space).
- **Alternative:** `pixi-viewport` (UMD) if touch pinch/inertia is needed soon; otherwise the
  hand-rolled camera is lighter and avoids a community-plugin v8-compat risk. Keep the POV toggle
  (RED/BLUE/spectator) wiring from the current page; POV selects which colony's `fog[]` is shown.

---

## 5. Art Pipeline

### 5.1 Source & access mode
- **Generator:** pixellab.ai. Public REST API (`https://api.pixellab.ai/v1/`, Bearer token,
  OpenAPI at `/v1/openapi.json`), official Python SDK (`pip install pixellab`, v1.0.5), and an
  official remote MCP server (`https://api.pixellab.ai/mcp`).
- **Recommended access mode:** **Python SDK as an OFFLINE batch script** (not runtime). Put the
  API key in `.env` mirroring the existing `CLOUDFLARE_API_TOKEN` pattern. Generate once, commit
  the packed atlas, ship as static Worker assets. The MCP server is the right tool for interactive
  "regenerate this one sprite" loops in Claude Code; the deterministic pipeline uses SDK + saved
  seeds.

- **PRE-M0 PIXELLAB VALIDATION (gate before any asset generation — must complete first):**
  1. Obtain a pixellab API key; store it in `.env` (`PIXELLAB_API_TOKEN`), mirroring the existing
     `CLOUDFLARE_API_TOKEN` pattern. Never commit it.
  2. Confirm the SDK installs at the pinned version: `pip install pixellab==1.0.5` (if 1.0.5 is
     not available, record the latest available version and pin that instead).
  3. Call `GET https://api.pixellab.ai/v1/balance` (Bearer token) to confirm available
     credits, the free-trial credit allowance, and any documented rate limits. Record the raw
     response.
  4. Run exactly ONE real generation on the free tier (one `/generate-image-pixflux` call for the
     canonical worker, 64×64, `no_background=true`) and record the **actual** credit cost
     deducted (balance before vs after) and wall-clock latency.
  5. Write all findings — key works, SDK version, balance JSON, per-call cost observed, rate
     limits, free-trial applicability — to `tools/assetgen/pixellab_validated.md`. Asset
     generation (M5) is BLOCKED until this file exists and shows sufficient credits.
  This replaces the deferred "confirm cost once a key exists" language: do it before, not during.
- **Endpoints used:** `/generate-image-pixflux` (text→sprite, the style anchor),
  `/generate-image-bitforge` (style-reference sprite for the rest of the set),
  `/animate-with-skeleton` (4-frame walk/attack/dig sets) + `/estimate-skeleton`,
  `/rotate` (only if free-rotation fails QA → directional sets), `no_background=true` for
  transparent PNG.

### 5.2 Pixel-art spec
- **Tile basis 32px.** Generate ants/structures at **64×64** (pixellab quality sweet spot;
  weak ≤16px) with `no_background=true`, then nearest-neighbor downscale in Pixi
  (`scaleMode:'nearest'`). Queen footprint 2×2 tiles (generate 128×128). Wall = 1×1.
  Watchtower/barracks/larder/guard_post = 1–1.5 tiles.
- **Queen sprite (largest, validate early):** generate at 128×128 (2×2 tiles) using the same
  **free-rotation** approach as the other ants (single sprite rotated by heading), since the queen
  is part of `antLayer`. Two risks to validate in **M3**: (a) pixellab quality at 128×128 — confirm
  the larger canvas does not degrade detail vs the 64×64 ants; (b) free-rotation legibility — test
  the queen sprite rendered at 30° intervals (0/30/.../330) and judge distortion. If free-rotation
  reads badly at this size, generate an 8-direction pre-rendered set instead (same `AntView` API,
  more frames). The queen rarely moves, so distortion exposure is low, but it is the most-detailed
  sprite so verify explicitly. Downscaling 128→64 display px (at default zoom) must not lose the
  silhouette; check at both min and max zoom. Add to M5 success criteria: "Queen visual parity
  verified side-by-side with the Canvas queen at default and max zoom."
- **Style consistency:** generate ONE canonical worker (pixflux) as the style anchor, then use it
  as `style_image` (+ fixed `color_image` palette, per-subject `seed`, tuned `style_strength`) in
  bitforge for ALL other ants/structures so the set reads as one coherent style.
- **Color/colony:** the Phase-1 plan is **grayscale base sprites + runtime Pixi `tint`** for
  RED/BLUE (cheaper, half the ant art). "Grayscale base" means: generate the sprite with a neutral
  *desaturated* palette (luminance only, no hue) so that a single multiplicative Pixi `tint`
  (`0xff3c3c` / `0x3c3cff`) yields the colony color without muddying. **PRE-M0 VALIDATION (gate
  before M1 commits to this path):** as part of the single validation generation, produce one
  worker at 64×64 grayscale, nearest-downscale to 32px, and apply both Pixi tints side-by-side;
  judge legibility and RED-vs-BLUE distinctness at 32px. The spec must commit to ONE path before
  M1: if tint legibility passes → grayscale-base + tint (locked). If it fails → escalate to a
  **blocker** and switch to per-colony pre-rendered color variants (doubles ant atlas size and
  generation cost; update §6.6 totals accordingly). Record the decision and the side-by-side image
  in `tools/assetgen/pixellab_validated.md`.
- **Ant facing — FREE ROTATION (locked decision).** Generate a single top/side ant sprite and
  rotate by movement heading (`sprite.rotation`). Small sprites tolerate minor distortion. Reserve
  pre-rendered 8-direction sets (same `AntView` API, more frames) only if 360° rotation reads
  badly in playtest. Structures/large units use pre-rendered directional/variant frames, not
  rotation.

### 5.3 Atlas / packing / manifest
- **pixellab does NOT emit packed sheets** — it returns one base64 PNG per call. Packing is a
  mandatory external step.
- **Packer — DECIDED: `free-tex-packer-core` (npm CLI), zero cost, no toolchain license.** Chosen
  now so the pipeline is deterministic; Aseprite (~$20) is only a manual fallback if a packing
  edge case appears. free-tex-packer's `JsonHash` exporter emits the **TexturePacker JSON-hash**
  format, which Pixi v8's `Spritesheet`/`Assets` loader parses directly (same format Pixi documents
  for TexturePacker). Use `exporter: "Pixi"` if available in the installed version for an exact
  match; otherwise `JsonHash` is compatible. **Frame naming:** strip the directory and `.png`
  extension is kept in the frame key so that `sheet.animations[<clip>]` groups by the numeric
  suffix (e.g. `worker_walk_0.png … worker_walk_3.png` → `sheet.animations['worker_walk']`); set
  `removeFileExtension:false` and `prependFolderName:false` so keys match the clip names in §3.3.
  `tools/assetgen/pack.sh` codifies the exact command per atlas, e.g.:
  ```sh
  # tools/assetgen/pack.sh  (one invocation per atlas concern)
  npx free-tex-packer-cli \
    --project tools/assetgen/atlas-ants.ftpp \
    --format pixijs \
    --name ants \
    --output frontend/game/assets \
    --width 2048 --height 2048 \
    --padding 2 --extrude 1 \
    --allowRotation false --trimMode trim --removeFileExtension false
  # emits frontend/game/assets/ants.json + ants.png ; repeat for structures/terrain/nodes/fx
  ```
  Exact flag names follow the installed `free-tex-packer-cli` version (pin it in
  `tools/assetgen/pixellab_validated.md` alongside the SDK version). DoD for packing: load each
  emitted JSON with Pixi `Assets.load()` in a throwaway test page and confirm
  `sheet.animations['worker_walk']` returns an ordered 4-texture array before relying on it.
- **One atlas per concern** (keeps adding TYPES trivial): `ants.json/png`, `structures.json/png`,
  `terrain.json/png`, `nodes.json/png`, `fx.json/png`. Target ≤2048×2048 each; ~2–4 MB total.
  Ship PNG only for Phase 1; WebP deferred (see §6.6 for rationale + the deferred WebP recipe).
- **Where assets live:**
  ```
  frontend/game/assets/        committed atlases (ants.json/png, structures.json/png, terrain.json/png, nodes.json/png, fx.json/png)
  frontend/game/vendor/        pixi.min.js (UMD) + pixi-filters UMD
  tools/assetgen/              OFFLINE generation: gen.py (pixellab SDK), pack.sh (Aseprite/free-tex-packer), seeds.json manifest of {subject,prompt,seed,style_strength,palette}
  ```
  `tools/assetgen/` is build-time only, never shipped/served. `seeds.json` makes every asset
  reproducible.
- **Loading:** Pixi `Assets.init({ manifest })` (from `src/assets/manifest.js`) → `loadBundle`
  with a loading screen → access via `sheet.textures[...]` and `sheet.animations[clip]`.
- **Generation → ship flow:**
  `gen.py (SDK, seeds.json) → loose transparent PNGs per {subject,state,frame} → pack.sh → atlas.png+atlas.json → commit to frontend/game/assets/ → served by CF Worker ASSETS binding`.

---

## 6. Asset Manifest (enumerated)

Sizes are generation size (downscaled to 32px tile basis in Pixi). Colony color via runtime tint
(grayscale base) unless noted. "Sprites" = unique base art; "Frames" = animation frames.

### 6.1 Ants (`ants.json`) — base art is colony-agnostic grayscale; tint at runtime
| Subject | Type idx | Gen size | States/clips | Sprites | Frames |
|---|---|---|---|---|---|
| Worker | 0 | 64×64 | idle, walk, carry, dig (distinct, BUILDING — not an attack alias), attack, death | 1 base + tier accents | 6 clips × 3-4 frames ≈ 22 |
| Soldier | 1 | 64×64 | idle, walk, attack, death | 1 base + tier accents | ~4 clips × 4-6 frames ≈ 20 |
| Scout | 2 | 64×64 | idle, walk, attack, death | 1 base + tier accents | ~4 clips × 3-4 frames ≈ 14 |
| Queen | 3 | 128×128 | idle(pulse), attack(rare) | 1 base | ~2 clips × 2-4 frames ≈ 6 |

Tiers 0–3 = same base sprite × `TIER_SCALE[1.0,1.25,1.5,1.8]` + optional tier accent overlay
(tint/badge/glow). NO per-tier base art. Food pellet child sprite (1) for carry state.
**Ant subtotal:** ~4 base subjects, ~60 animation frames, +1 pellet.

### 6.2 Structures (`structures.json`) — grayscale base, colony tint
| Subject | Footprint | Active | Under-construction | Damaged | Sprites |
|---|---|---|---|---|---|
| guard_post | 1×1 | 1 | 1 (scaffold) | 1 | 3 |
| watchtower | ~0.55×0.9 | 1 | 1 | 1 | 3 |
| barracks | ~1.1×0.65 | 1 | 1 | 1 | 3 |
| wall | 1×1 | 1 (+grid edge variants) | n/a (instant) | 1 | 2 + edges |
| larder | 1×1 dome | 1 | 1 | 1 | 3 |
| nest | 5×5 carve | 1 (per colony marker) | n/a | n/a | 1 |

Vision/range rings (guard_post, watchtower), income pulse (larder), spawn pulse (barracks) are
**procedural overlays** (Graphics/GlowFilter), not sprite frames. Build progress bar = Graphics.
**Structure subtotal:** ~18 sprites + wall edge variants, ~6-12 procedural-overlay states.

### 6.3 Terrain (`terrain.json`)
| Tile | Const | Variants | Sprites |
|---|---|---|---|
| dirt | T_DIRT=0 | 2-3 noise variants | 2-3 |
| leaf | T_LEAF=1 | 2-3 | 2-3 |
| water | T_WATER=2 | 1-2 (+ optional ripple frames) | 1-2 |
| rock | T_ROCK=3 | 2-3 | 2-3 |
| nest | T_NEST=4 | 1 | 1 |

Optional Wang/edge-connector tiles for natural blending (defer to polish). Source = pixellab +
style-matched terrain pack supplement. Baked to RenderTexture chunks once.
**Terrain subtotal:** ~10-12 tile sprites.

### 6.4 Resource nodes (`nodes.json`)
| Subject | Kinds/tiers | Sprites |
|---|---|---|
| food | seeds, beetle, leaf, honeydew | 4 |
| dirt deposit | home tier, frontline tier | 2 |
| corpse | 1 static, neutral gray | 1 |

**Corpse rendering (detail):** `NodeView` renders the single neutral-gray corpse sprite. It is a
**static sprite, no animation.** "Scale by amt" means **size**, not alpha: `scale = base ×
sqrt(amt / CORPSE_AMT_MAX)` (area-proportional). `alpha` fades proportionally with `amt` from
`1.0` toward `0.25` as the pile depletes, then the view is pooled when the id leaves `corpses[]`.
Color stays neutral gray (no colony tint — corpses are unowned). See §3.2.

Glow halos / tier rings are procedural (Graphics + blur), not sprites.
**Node subtotal:** ~7 sprites.

### 6.5 FX (`fx.json`)
| FX | Mechanism | Asset |
|---|---|---|
| Hit ring | expanding circle, fade 300ms | procedural OR 1 ring sprite (1-2 frames) |
| Death dissolve | scale-down + alpha fade 300ms one-shot | 1 dissolve strip (3-4 frames) |
| Build dust | ParticleContainer puffs | 1 dust particle sprite |
| Spark / glint (combat) | ParticleContainer | 1 spark sprite |
| Guard-post beam | line from guard_post → hit ant (target inferred client-side; see §3.2), fade over a tick | procedural (Graphics) |
| Build-complete flash | scale/glow pulse | procedural (GlowFilter) |
| Queen-under-attack | nest glow spike / screen-edge indicator | procedural |

**FX subtotal:** ~4-5 small sprites + 10-20 particle frames; rest procedural.

### 6.6 Totals (Phase-1 MVP)
~40 unique base sprites + ~80-110 animation/particle frames across 5 atlases. ~2-4 MB total.
Well under one 2048² atlas per concern.

**Compression — DECIDED: ship PNG only for Phase 1; no WebP build step.** Rationale: at ~2-4 MB
total, served once from CF cache to a desktop spectator client, the payload is acceptable as-is,
and Pixi v8's spritesheet loader pairs a JSON with a fixed image path — content-negotiating
PNG↔WebP per request would require either two manifests or an Accept-based rewrite the no-build
CF static-assets path does not provide. CF's static-assets `ASSETS` binding already serves with
long-lived caching and gzip/Brotli over the wire for the JSON; the PNG bytes are already
compressed. **If** payload later matters (e.g. mobile), the WebP path is: in M5 run
`cwebp -q 90 ants.png -o ants.webp` for each atlas, commit both, and either (a) point the atlas
JSON's `meta.image` at the `.webp` (Pixi loads it directly — simplest, WebP-only) or (b) add a
manifest variant. This is explicitly **deferred**, not part of Phase-1 DoD. Update §9 open
question #6 accordingly: resolved to PNG-only for now.

---

## 7. Phasing / Milestones

Each milestone is independently shippable and visually verifiable against the current renderer.
**No production deploy without explicit user approval** (deploy = CF Worker; a real player is live).
Each milestone is verified LOCALLY first (§8).

- **M0 — Scaffold & loading.** Vendor Pixi UMD + pixi-filters. Create `frontend/game/src/`
  module skeleton (§2.2), `Connection.js` (ported WS, protocol unchanged), `SnapshotStore.js`,
  Pixi `app.init()` mounting into `#stage` behind the kept DOM HUD. Render a blank world rect +
  the existing DOM panel updating live. *Verify:* WS connects, FPS counter runs, no Canvas code
  path active. **DoD also requires the M0.5 replay harness working** (every later milestone is
  verified against it, never against production).
- **M0.5 — Tick-replay harness (offline verification backbone).** Build under `tools/replay/`:
  (a) **Capture:** run a local dev match (`python3 server.py`, a locally-created dev match — NEVER
  production) and record ~1 minute of the WS stream to `tools/replay/match.jsonl` — one JSON
  message per line, in arrival order, including the initial `map`/`init` messages and every tick
  dict, each line prefixed-free (raw message JSON). A tiny capture client (`tools/replay/capture.py`,
  a `websockets` client that appends each received frame) produces it.
  (b) **Replay server:** `tools/replay/server.py` — a `websockets` server on `ws://localhost:8765`
  that, on each client connect, reads `match.jsonl` and re-emits the lines **at the recorded
  cadence** (sleep by the delta between the captured `tick`/wall-clock stamps; fall back to a fixed
  1s if no stamp), looping when it reaches EOF. Supports a `?delay=N` (or env var) to inject an
  artificial late tick for interpolation testing.
  (c) **Wiring:** `net/Connection.js` honors a URL param `?replay=true` (optionally
  `&replayUrl=ws://localhost:8765`) to point the WebSocket at the replay server instead of the
  live URL — the message-parsing path is identical, so the renderer cannot tell the difference.
  *Verify:* loading `frontend/game/?replay=true` (served via `python3 -m http.server` from
  `frontend/`) drives `SnapshotStore` from the JSONL with realistic data and zero production
  contact.
- **M1 — Terrain + camera.** TileGrid baked to RenderTexture chunks from the `map` message;
  territory + fog overlays; hand-rolled camera (pan/zoom/clamp/zoom-to-cursor, POV toggle wired to
  `fog[]`). *Verify:* terrain matches map, camera smooth, fog/territory correct per POV.
- **M2 — Structures.** `StructureView` for all 5 types + nests; active/under-construction
  (progress bar)/damaged frames; colony tint; walls-first 2-pass order; procedural range/vision/
  income/spawn overlays. *Verify:* every structure type + lifecycle state renders, parity with
  current.
- **M3 — Ants + animation + interpolation.** `AntView` for 4 types × tiers (scale + accent),
  state-machine clip selection, free-rotation facing, carry pellet, HP bars, `ViewPool`,
  snapshot interpolation in `Loop.js`. *Verify:* ants glide at 60fps, correct clip per state,
  tier scaling, carry/HP visible, no teleport on late ticks; FPS ≥60 at peak counts.
- **M4 — Nodes + FX + HUD polish.** `NodeView` (food/dirt/corpse + glow), Effects (hit ring,
  death dissolve, build dust, guard-post beam, build-complete flash, queen-attack warning,
  win-state ColorMatrix), in-world `uiLayer` (selection ring, hover tooltip, minimap). *Verify:*
  all FX fire on the right events, full visual parity-or-better, DOM HUD intact.
- **M5 — Asset finalization & cutover prep.** Prereq: `tools/assetgen/pixellab_validated.md`
  exists (pre-M0 gate passed) and the grayscale-tint vs per-colony-variant decision (§5.2) is
  locked. Run `tools/assetgen/gen.py` + `pack.sh` to produce final PNG atlases, **commit** them to
  `frontend/game/assets/` (and the vendored Pixi UMD to `frontend/game/vendor/`), swap any
  placeholder art, perf-profile at peak, feature-flag the Pixi renderer (URL param) alongside the
  Canvas fallback for A/B. *Verify:* atlases load via Pixi `Assets.load()` (`sheet.animations`
  resolve), queen visual parity side-by-side with Canvas at default + max zoom, side-by-side vs
  Canvas for every type, 60fps at peak, atlas payload acceptable. After (and only after) user
  approval to deploy, run `npx wrangler deploy` and `curl -I` each atlas URL (§2.1) to confirm the
  CF Worker serves them. **Deploy only on user approval.**

---

## 8. Testing & Verification

- **Local viewing without production:** the **tick-replay harness** under `tools/replay/` (built in
  **M0.5**, see §7 for its full spec: `capture.py` → `match.jsonl`, `server.py` replaying at
  recorded cadence on `ws://localhost:8765`, `Connection.js ?replay=true` wiring). Serve
  `frontend/game/` locally (`python3 -m http.server` from `frontend/`) and load
  `?replay=true`. This lets every milestone be verified with realistic data and NO production
  dependency / no live-server contact. The JSONL is captured from a local dev match (`python3
  server.py`), NEVER production.
- **Visual checks (per milestone):** screenshot side-by-side vs the current Canvas renderer for
  each entity/terrain/FX type; confirm correct clip per ant state, tier scaling, colony tint,
  structure lifecycle frames, fog/territory per POV, FX on correct events.
- **Interpolation correctness:** verify ants glide smoothly between ticks, rest (not teleport) on
  an artificially delayed tick, and resume cleanly (replay harness can inject delay).
- **Performance target — 60fps at peak unit counts** (two colonies, ~2000 ants) on desktop
  Chrome. Use the existing on-screen FPS counter; profile with Chrome DevTools / the
  `chrome-devtools-mcp:web-perf` skill against the LOCAL replay. Watch draw-call count
  (target: terrain ~12 chunks, structures ~20, ants batched in one layer), filter passes
  (use GlowFilter sparingly), and ParticleContainer bounds.
- **Regression gate:** Pixi renderer behind a feature flag (URL param) so the Canvas renderer
  remains a fallback during A/B; cut over only after parity + perf confirmed.
- **No automated pixel-diff required**, but capture reference screenshots per milestone for review.

---

## 9. Risks & Open Questions

### Risks
- **pixellab free-rotation quality (ants):** locked decision is free rotation; if 360° rotation
  distorts unacceptably, fall back to 8-direction pre-rendered sets (same `AntView` API, more
  frames, more credits). Mitigation: validate early in M3 with a real generated ant.
- **PixiJS v8 ESM-from-CDN is broken** → MUST vendor the UMD build; do not hot-link an ESM CDN or
  the renderer silently fails to draw.
- **No bundler / no tree-shaking** → full Pixi payload (hundreds of KB) ships. Acceptable for a
  CF-cached desktop spectator client; revisit only if mobile load time matters.
- **Culling on uniform grids** can be slower than manual `visible` checks (Pixi's own caveat);
  baked terrain sidesteps it for the biggest layer — only `antLayer` needs culling. Profile.
- **Tier/colony legibility at 32px:** T3 detail + RED/BLUE must stay distinct; rely on glow/outline
  color, not just body tint.
- **Atlas/style consistency** across subjects is good but not guaranteed identical; plan a
  manifest (`seeds.json`) + regeneration loop, not one-shot generation.
- **Community UMD plugins** (`pixi-viewport`, `pixi-tilemap`) carry v8-compat risk; default to
  hand-rolled camera + baked terrain to minimize that surface.
- **Reconnect state reset:** on WS `init`/reconnect, `SnapshotStore` must clear view maps + pools
  to avoid ghost sprites (current renderer resets `antMap`/`dyingAnts`).
- **Field-index off-by-one:** ant/structure tuples are positional; use named-index constants or
  destructuring in `SnapshotStore` to avoid silent mis-mapping.

### Open questions
1. **RESOLVED to a gate (pre-M0).** Exact per-endpoint credit cost + rate limits are no longer
   deferred: the pre-M0 validation (§5.1) calls `GET /v1/balance`, runs one measured generation,
   and records real cost/limits in `tools/assetgen/pixellab_validated.md` before any asset work.
   The $0.002–$0.185/gen and 1-vs-40-credit figures remain unconfirmed marketing numbers until
   that file records the observed values.
2. Does free-rotation read acceptably for the generated ant (incl. the 128×128 queen), or are
   directional sets needed? — validated in M3 (ants) and M3/M5 (queen, §5.2).
3. **DECISION DEADLINE: before M1.** Is runtime grayscale-base + `tint` sufficient for RED/BLUE
   legibility, or are per-colony color variants required (doubles ant art)? Decided by the pre-M0
   side-by-side tint test (§5.2); record the chosen path in `pixellab_validated.md`.
4. **RESOLVED: free-tex-packer-core** (§5.3). Aseprite (~$20) only as a manual fallback for a
   packing edge case.
5. Which Pixi v8 UMD version to pin for both core and `pixi-filters` (avoid v1/v2 API drift)? —
   pin a single v8.x and record it in `pixellab_validated.md` alongside the SDK/packer versions.
6. **RESOLVED: PNG only for Phase 1** (§6.6); WebP deferred with a recorded recipe if payload
   later matters.

---

## 10. Non-negotiables (restate)
- Python sim untouched; `serialize_tick` schema is a fixed contract.
- No new unit/structure TYPES in this effort.
- No production deploy / no live-server contact without explicit user approval.
- Architecture keeps adding a new type to: one atlas entry + one row in the type→view/clip tables
  + one `*View` subclass — no renderer rewrite.
