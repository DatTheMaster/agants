# Graphics Test Report — PixiJS Renderer (adversarial)

Branch: `graphics-m0-foundation` · Date: 2026-06-18 · Mode: **TEST ONLY**

**Nothing was pushed. Nothing was deployed. Production was never touched.** All work was 100%
local: a local replay WS server + bundled headless Chromium (swiftshader software-GL) against
`http://localhost:<unique-port>/game/?pixi=1&replay=true`. No renderer code, no `index.html`,
and no Python sim was modified. New artifacts are limited to `tools/replay/` (capture/scan/driver
scripts + a richer replay) and `tools/replay/shots/` screenshots.

---

## 1. Overall verdict + grade

**Verdict: PARTIAL PASS. The overnight "everything passed" report is too rosy.** On a *clean*
run the renderer mounts in WebGL and draws a coherent world — terrain, lanes, nests, nodes,
territory tint, per-POV fog, all live structures, up to ~149 ants, FX glow, minimap, and the DOM
HUD — with no crashes. That part is real and was re-confirmed against a rich 425-tick combat
replay (139 deaths / 393 hit events / up to 181 simultaneous ants).

But once the combat / death / damage paths that the overnight 60-tick peaceful replay never
touched were actually exercised, the report's robustness claims broke down:

- **Two undocumented runtime errors** appeared under combat load: an unrecoverable WebGL
  context-loss freeze (no `contextrestored` handler exists) and an intermittent uncaught Pixi
  `TypeError: ...reading 'split'`. The morning report claimed `fx.json` 404 was the *only* error.
- **The animation atlas is missing 10 of the contracted clips.** The most action-heavy states —
  carry, dig, attack, every death, scout_walk, queen_attack — are rendered as a **frozen single
  idle frame**. The "clip state machine (walk/carry/etc.)" is real only for `walk`.
- **The damaged-structure texture swap was never actually visually exercised** by the data,
  despite the capture summary marking it "covered." The two types that ship a `_damaged` frame
  (wall, guard_post) are never simultaneously built-and-damaged in the replay.
- **A real functional camera bug**: the entire world can be panned off-screen into a black void
  with no snap-back, because `clampOffset()` has no center-lock for world-smaller-than-viewport.
- **The 60fps-at-~2000-ants target remains UNVERIFIED on real GPU.** Peak measured was ~181 ants
  on software-GL only; FPS dipped to ~20 under peak combat + procedural FX.

**Letter grade: C+.** Solid M0/M1 foundation and a genuinely non-destructive Canvas fallback,
but "M2 structures DONE / M3 ants DONE / M4 FX DONE — all acceptance criteria passed" overstates
reality: combat/carry/dig/death visuals are placeholder-quality, the damaged-frame and
guard-post-beam paths are unverified, and two real runtime errors surfaced the moment combat ran.

---

## 2. Per-area verdicts

| Area | Verdict | Key evidence (screenshots) |
|---|---|---|
| M0 — scaffold / mount / Canvas fallback | **works** | `test-Verify-fx-hud-perf-7-canvas-noflag.png`, `test-Verify-fx-hud-perf-8-canvas-replay.png` |
| M1 — terrain / camera / overlays | **partial** | `test-Verify-scene-1-default.png` … `-6-overpan.png` (scene-6 = the void bug) |
| M2 — structures (5 types / construction / damaged) | **partial** | `test-Verify-structures-1-all5-overview.png` … `-10-damaged-active-watchtower.png` |
| M3 — ants (anim / interp / tint / tiers / HP / carry) | **partial** | `test-Verify-ants-1-default.png` … `-7-facing-east.png`; `rich-zoom-nest.png` |
| M4 — FX / nodes / HUD / perf | **partial** | `test-Verify-fx-hud-perf-1-combat.png` … `-6-combat-zoom2.png` |
| Damaged-structure `_damaged` texture swap | **unverifiable** | never built+damaged in replay (data gap); `-9-damaged-wall.png`, `-10-...watchtower.png` |
| guard_post **active** sprite/footprint/tint | **unverifiable** | every guard_post stuck under-construction; `-7/-8-guardpost*` |
| Guard-post **beam** FX | **unverifiable** | `beam=0` across whole replay (no active guard_post ever fired) |
| Combat/carry/dig/death **animation** clips | **broken (placeholder)** | frozen idle frame; `test-Verify-ants-4-combat.png`, `rich-zoom-midfield.png` |
| WebGL context-loss recovery | **broken** | no handler in src; frozen-canvas frames were deleted so as not to mislead |
| Real-GPU 60fps @ ~2000 ants | **unverifiable** | software-GL only; peak 181 ants @ ~20-30 fps |

All screenshots are under `/home/deshiel/projects/agants/tools/replay/shots/`.

---

## 3. Severity-ranked defects

### BLOCKER
_None that hard-break a clean run._ (The renderer does mount and draw a full game without
crashing on clean replays — credit where due.)

### MAJOR

1. **[M1] Whole world pans off-screen into a black void; no snap-back.**
   At default zoom the baked world (~1500×1000 display px) is narrower than the 1600px viewport.
   `clampOffset()` (`Camera.js:48-52`) uses `px = max(-(mapW-80), min(screenW-80, posX))` with
   **no center/lock branch when world < viewport**. A few right-drags park the entire map at the
   far-right edge (≈80px sliver visible, huge empty void on the left). Only `dblclick` reset()
   recovers it. Reproduced via real Playwright drags AND a symmetric clamp probe
   (posPos {1520,920} / negPos {-1420,-920}). *Evidence:* `test-Verify-scene-6-overpan.png`
   (HUD + minimap only, rest black).

2. **[M3] Animation gap — 10 of the contracted §3.3 clips are missing; combat/carry/dig/death
   render as a FROZEN single idle frame.** `ants.json` contains only 6 clips
   (`queen_idle, worker_idle, worker_walk[4f], soldier_idle, soldier_walk[4f], scout_idle`);
   verified directly in the atlas JSON. MISSING vs §3.3: `worker_carry, worker_dig, worker_attack,
   worker_death, soldier_attack, soldier_death, scout_walk, scout_attack, scout_death,
   queen_attack`. `_clipTextures()` falls back to the type's single-frame `*_idle`, so during
   the replay: worker_carry (16 ants), scout_walk (10 ants — scouts NEVER animate while exploring,
   their whole job), worker_dig (4), soldier_attack — all frozen. Not a crash (49/49 bodies are
   real `AnimatedSprite`, 0 placeholders), but a genuine fidelity regression vs the report's "clip
   state machine (walk/carry/etc.)" claim. *Evidence:* `test-Verify-ants-4-combat.png`,
   `rich-zoom-midfield.png`.

3. **[M2] The `_damaged` texture-swap path is NEVER visually exercised, contradicting
   "structures_damaged: covered."** The 247 "damaged" frames in the scan are mostly
   **under-construction** structures (hp=1 while building), not battle-damaged built ones. The
   only type ever simultaneously `active=1 AND hp<max_hp` is **watchtower** (21 frames, e.g. tick
   308 hp 86/150) — and `watchtower_damaged.png` does **not exist** in the atlas, so it renders
   the active frame at alpha 0.85 + an HP bar (fallback). The two types that DO ship a damaged
   frame (`wall_damaged.png`, `guard_post_damaged.png`, both confirmed present) are never
   active+damaged in this replay. Net: `StructureView._texture()`'s damaged branch and the wall/
   guard_post damaged art are **unverified**. *Evidence:*
   `test-Verify-structures-9-damaged-wall.png`, `-10-damaged-active-watchtower.png`.

4. **[M2] guard_post ACTIVE sprite never renders.** All 31 guard_post frames are `active=0,
   hp=1/300, build_progress=0/25` — perpetually under construction. So `guard_post_active.png`,
   its 0.85-tile footprint, and its colony tint are NOT visually verifiable; only the generic
   procedural scaffold box ever shows. The "all 5 types" claim holds only because the
   under-construction scaffold counts as "present." *Evidence:*
   `test-Verify-structures-7-guardpost-overview.png`, `-8-guardpost-zoom.png`.

5. **[M3] BLUE colony legibility.** At 6x zoom BLUE ants (tint `0x3c3cff` on grayscale base)
   render as near-black navy and blend into the dark territory tint, navy grid lines, and shadows
   — the green carry pellet is often more visible than the ant. RED ants on RED territory tint are
   likewise nearly invisible mid-zoom. Tints are correctly looked up (not computed), and RED reads
   well on green leaf terrain, but the spec §9 "RED/BLUE distinct at 32px" risk is real on dark
   backgrounds; needs an outline/rim or lighter base luminance. *Evidence:*
   `test-Verify-ants-5-maxzoom-blue.png`, `test-Verify-ants-6-maxzoom-red.png`.

### MEDIUM

6. **[M0/runtime] WebGL context-loss freezes rendering with NO recovery, while internal state
   silently advances.** Confirmed: there is **no `webglcontextlost`/`contextrestored` handler
   anywhere in `frontend/game/src/`** (grep clean). In the first combat run the browser logged
   `CONTEXT_LOST_WEBGL: loseContext` and canvas pixels then stayed byte-identical (3 screenshots
   md5-identical over ~20s) while `SnapshotStore` kept advancing (ants 53→100→149, tick 72→255).
   On a real context loss the game looks frozen while reporting healthy state. Likely worse under
   swiftshader, but no defensive handling exists. (The frozen frames were deleted so they don't
   mislead later reviewers.)

7. **[M3/runtime] Intermittent uncaught `TypeError: Cannot read properties of null (reading
   'split')`** — 3 page errors in the context-loss run, undocumented in the morning report. Verified
   **no `.split` exists anywhere in `frontend/game/src/`** (grep clean), so it originates inside
   the bundled Pixi UMD — most plausibly its spritesheet/AnimatedSprite path under the churn of
   139 deaths + missing animation clips. Not reproduced in two later clean ~55s runs, so it is
   timing/context-loss-correlated, not deterministic — but it IS a real uncaught error during
   combat that the report missed.

8. **[M4] Win-state ColorMatrix grade (and FX state) reset ONLY on WS reconnect, never on
   in-stream init/game_start.** `Connection.js:25` fires `onReset` only when `this._opened` is
   already true (i.e. a *second* `onopen`). When the replay loops over the same socket it re-sends
   init+game_start without a reconnect, so `effects._winGraded` stays true and the desaturate/
   darken grade PERSISTS into the next match (visible as a dimmed early-game scene). In production
   a new match usually means a new socket so impact is low, but any new game on the same socket
   carries over the win grade. `main.js` should call `onReset` on init/game_start, not just
   reconnect. *Evidence:* `test-Verify-fx-hud-perf-3-zoomed.png` (dimmed early scene after loop).

9. **[M4/perf] FPS under peak combat dips to ~20 on software-GL** at default whole-world-fit
   zoom with 181 ants + up to 161 simultaneously-active procedural Graphics FX (each hit ring /
   dissolve / flash is its own Graphics redrawn per frame). Software-GL/swiftshader so NOT
   representative of real GPU, but it shows the procedural-FX path is the heaviest cost and the
   60fps target is unproven on real hardware.

### LOW / MINOR

10. **[M1] Terrain has no tile variation.** `terrain.json` ships exactly ONE frame per type
    (`dirt_0/leaf_0/water_0/rock_0/nest_0` — confirmed in atlas JSON) despite spec §6.3 requiring
    2-3 noise variants per tile. With nearest scaling and no extrude on the bake, the repeated 64px
    tile downscaled to 32px reads as a visible graph-paper grid across the whole map.
    *Evidence:* `test-Verify-scene-2-zoomed.png`, `rich-zoom-nest.png`.

11. **[M1] Map is baked TWICE on load.** The stream has two `init` messages; `Connection` routes
    both to `onMap → buildMap`, which rebuilds all 20 terrain RenderTexture chunks each time
    (`[pixi] map baked` logged twice). Old chunks are destroyed first (no leak), but `buildMap`
    has no "already built" guard — wasted GPU work, and on replay loops it re-bakes ~once per loop.

12. **[M4] food_pellet sprite missing** — carry pellet falls back to a procedural green Graphics
    circle. It DOES show on carrying ants (16-35 during foraging), so the feature works, but it's
    a placeholder, not the authored art §6.1 implies.

13. **[M2] Structure legibility / type-agnostic scaffold.** wall, barracks, larder render as dark
    muddy near-indistinct blobs at zoom (grayscale base × multiply tint darkens further; RED
    especially). Only the procedural rings/HP bars disambiguate them. And the under-construction
    scaffold is identical for all 5 types (no `_construction` frames exist), so you can't tell what
    is being built. Below the spec §5.2/§9 "tier+colony stay distinct" bar.

14. **[M3] Queen is a single frozen frame** — only `queen_idle` (1 frame), no `queen_attack`/
    `queen_death`; the §6.1 "idle pulse" is absent. Acceptable per design (queen rarely moves) but
    noted.

15. **[M4] Mass-combat ant facing reads oddly** — stationary fighting ants keep `_lastAngle`
    (defaults 0 = sprite native "up"), so nearly all ants in the combat cluster point straight
    north rather than at their opponent. Free-rotation works for *moving* ants. Documented
    "idle keeps last heading" limitation, but uniform/unnatural in dense combat.

### COSMETIC

16. **[M4] Corpses render only as a faint dark-brown placeholder disc** (`NodeView._buildCorpse`,
    color `0x5a3010`; no corpse atlas frame). With 53 simultaneous corpses peak they are nearly
    invisible against dirt even at 5x. Expected fallback, but the corpse path is only weakly
    verifiable.

17. **[M1] Fog does not fully occlude bright nodes** — fog black at alpha 0.82, so high-contrast
    green food bleeds through ~18% in unexplored regions. Z-order is actually CORRECT (fog topmost,
    index 7, alpha 1); this is purely the chosen dim alpha — a realism nit, not a z-order bug.

18. **[all] `fx.json` 404 on load** — confirmed `fx.json` does not exist; procedural-FX fallback
    works (expected per §6.5). This is the *only* console **error** on a clean run.

---

## 4. STILL UNVERIFIED (replay/browser could not exercise these)

- **Real-GPU performance / the 60fps-at-~2000-ants success criterion.** Only software-GL
  (swiftshader) was available. Peak in the renderer probe was ~149 ants @ ~30fps; capture peaked
  at 181 ants. The spec's ~2000-ant target was never approached and never measured on real GPU.
- **`_damaged` built-structure texture swap** (wall_damaged / guard_post_damaged) — the two types
  that ship the art are never simultaneously built+damaged in the data; watchtower is the only
  built+damaged type and it has no damaged frame.
- **guard_post ACTIVE sprite / footprint / tint** — every guard_post stayed under-construction.
- **Guard-post beam FX** — `beam=0` across the entire replay; no active enemy guard_post ever
  fired on an in-range ant, so the signature beam path is visually unexercised. (Not a renderer
  bug — a data gap; richer data with an active firing guard_post is needed.)
- **Authored animation for carry/dig/attack/death/scout_walk/queen_attack** — clips don't exist;
  only the idle-frame fallback was observed.
- **WebGL context-restore behavior** — there is no restore handler to test; the only observed
  context-loss left the canvas permanently frozen.
- **RED guard_post** — never materialized in the stream (workers killed/diverted; sim behavior,
  not a renderer issue); guard_post render path only covered via BLUE's (under-construction) posts.

---

## 5. Console errors + FPS (software-GL caveat)

**Console errors/warnings (consistent across clean runs):**
- `error: Failed to load resource: 404 — /game/assets/fx.json` (the one clean-run error)
- `warning: [pixi] fx atlas not loaded, using procedural FX … SyntaxError: Unexpected token '<'`
  (the 404 returns the SPA `index.html`; handled gracefully)
- `warning: GL Driver Message … GPU stall due to ReadPixels` (benign; software-GL/swiftshader
  only, from the RenderTexture bakes)
- `log: [pixi] map baked: 150x100 (15000 tiles)` — emitted **twice** (double bake; see defect 11)

**Combat-run-ONLY errors the morning report missed (defects 6 & 7):**
- `CONTEXT_LOST_WEBGL: loseContext` → permanent frozen canvas, no recovery
- `TypeError: Cannot read properties of null (reading 'split')` × 3 page errors (inside bundled
  Pixi UMD; not deterministic)

**Canvas no-flag path:** ZERO console errors, ZERO page errors — `fx.json` 404 is Pixi-only.
Non-destructive contract HOLDS (legacy Canvas renders a full game against the replay backend with
no regression).

**FPS (software-GL / swiftshader — NOT representative of real GPU):**
- Default whole-world-fit zoom: min 20 / max 61 / avg ~43 over 148 samples; dips to ~20 under
  peak combat + 161 active FX.
- Zoomed-3x: min 30 / max 60 / avg ~56.
- Renderer probe: max ~149 ants rendered @ ~30fps under combat load; 60fps at default zoom idle.

---

## 6. Prioritized fix list

1. **Camera clamp (MAJOR, defect 1):** add a center-lock branch to `clampOffset()` for
   world-dimension < viewport-dimension so the world can never be parked off-screen. Easy, high
   user-visible impact.
2. **Ship the missing animation clips (MAJOR, defect 2):** generate/pack `worker_carry,
   worker_dig, worker_attack, *_death, scout_walk, scout_attack, soldier_attack, queen_attack`
   (and `food_pellet`) — the spec budgeted ~60 frames and only ~12 shipped. Until then the
   most-watched states (combat/carry) are frozen.
3. **WebGL context-loss handler (MEDIUM, defect 6):** add `webglcontextlost` (preventDefault) +
   `contextrestored` (re-bake terrain, re-bind views) so a context loss doesn't silently freeze a
   "healthy" game.
4. **Reset FX/win-grade on init/game_start, not just reconnect (MEDIUM, defect 8):** call
   `onReset` from `onMap`/game_start in `main.js`; also add an "already built" guard to `buildMap`
   to kill the double/loop re-bake (defect 11).
5. **Investigate the Pixi `split` TypeError (MEDIUM, defect 7):** likely the AnimatedSprite/
   spritesheet path tripping on the missing clips under death churn — fixing defect 2 may resolve
   it; add a guard regardless.
6. **Legibility (MAJOR/MINOR, defects 5 & 13):** add a colony rim/outline or raise base luminance
   so BLUE ants and dark structures stay distinct on dark terrain at 32px.
7. **Capture richer data to actually exercise the unverified paths (defects 3, 4, beam):** a
   replay where a built wall/guard_post takes damage while active, a guard_post completes and
   fires, so the `_damaged` swap, guard_post_active, and beam FX can be confirmed.
8. **Re-measure FPS on real GPU (defect 9):** the ~2000-ant / 60fps success criterion is still
   unproven; the procedural-FX path is the prime suspect for the heaviest cost.
9. **Terrain variants + extrude (MINOR, defect 10):** add the 2-3 noise variants per tile and
   extrude the bake to kill the graph-paper grid.

---

_Artifacts: `tools/replay/match.jsonl` (rich 429-row replay), `match-thin.jsonl` (old peaceful
backup), `tools/replay/capture_rich.py`, `scan.py`, `shoot*.py`, `trace_err.py`, and
`tools/replay/shots/*.png`. No git push, no deploy, no wrangler, no SSH, no production contact._
