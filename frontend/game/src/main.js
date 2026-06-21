// Bootstrap for the Pixi render client. Loads ONLY under ?pixi=1 (see index.html).
// Wires Stage + Camera + TileGrid + Overlays + SnapshotStore + Connection + Loop.
// M1: terrain baking, camera (pan/zoom/clamp/zoom-to-cursor), territory + fog
// overlays, POV toggle. M2+ add structures/ants/FX in the Loop callback.
import { SnapshotStore } from './state/SnapshotStore.js';
import { Stage } from './scene/Stage.js';
import { Camera } from './scene/Camera.js';
import { TileGrid } from './scene/TileGrid.js';
import { Overlays } from './scene/Overlays.js';
import { StructureView } from './entities/StructureView.js';
import { AntViews } from './entities/AntView.js';
import { NodeViews } from './entities/NodeView.js';
import { ViewPool } from './entities/ViewPool.js';
import { Effects } from './fx/Effects.js';
import { Hud } from './ui/Hud.js';
import { IntentOverlay } from './entities/IntentOverlay.js';
import { Loop } from './render/Loop.js';

export async function main() {
  const PIXI = window.PIXI;
  const app = new PIXI.Application();
  await app.init({ resizeTo: window, antialias: false, roundPixels: true,
                   background: 0x0b0d10 });
  // Crisp 32px pixel art: nearest-neighbor scaling globally.
  if (PIXI.TextureSource?.defaultOptions) PIXI.TextureSource.defaultOptions.scaleMode = 'nearest';

  // WebGL context-loss resilience: by default a lost GL context is permanent — the canvas
  // freezes silently while SnapshotStore keeps advancing (observed under software-GL during
  // heavy combat). preventDefault on loss lets the browser restore the context; on restore
  // Pixi v8 re-uploads GPU resources, and we nudge one render so the frozen frame clears.
  const _canvas = app.canvas;
  if (_canvas && _canvas.addEventListener) {
    _canvas.addEventListener('webglcontextlost', (e) => {
      e.preventDefault();
      console.warn('[pixi] WebGL context lost — awaiting restore');
    }, false);
    _canvas.addEventListener('webglcontextrestored', () => {
      console.warn('[pixi] WebGL context restored');
      try { app.renderer.render(app.stage); } catch (_) { /* renderer rebuilds on next tick */ }
    }, false);
  }

  const stage = new Stage();
  stage.attach(app);
  const stageEl = document.getElementById('stage');
  stage.mount(app, stageEl);

  // The generated ant base art is dark, so multiplicative colony tint came out muddy
  // (esp. red on the warm ground). Lift ant brightness at the layer level so RED/BLUE
  // read as bright, saturated colors. One filter for the whole layer (cheap on GPU).
  try {
    const CMF = PIXI.ColorMatrixFilter || PIXI.filters?.ColorMatrixFilter;
    if (CMF) { const f = new CMF(); f.brightness(1.7, false); stage.antLayer.filters = [f]; }
  } catch (e) { /* filter optional */ }

  const camera = new Camera(stage.world, stageEl, app);
  camera.attachInput();

  // Live intent overlay: rally/attack markers drawn on the map from the directive.
  const intentOverlay = new IntentOverlay(stage.world);

  // In-world UI (selection ring, hover tooltip, minimap). DOM #panel HUD is kept.
  // store is constructed below; Hud only reads it per frame so the order is fine.

  const tileGrid = new TileGrid(stage.terrainLayer);
  const overlays = new Overlays(stage.territoryLayer, stage.fogLayer);

  // Optional terrain atlas — falls back to flat colored tiles if missing (spec).
  let terrainAtlas = null;
  try {
    terrainAtlas = await PIXI.Assets.load('./assets/terrain.json');
  } catch (e) {
    console.warn('[pixi] terrain atlas not loaded, using flat-color tiles:', e?.message || e);
  }

  // Optional structures atlas — falls back to placeholder shapes if missing (spec).
  let structAtlas = null;
  try {
    structAtlas = await PIXI.Assets.load('./assets/structures.json');
  } catch (e) {
    console.warn('[pixi] structures atlas not loaded, using placeholder shapes:', e?.message || e);
  }
  const structureView = new StructureView(stage.structureLayer, structAtlas);

  // Optional ants atlas — falls back to placeholder ant shapes if missing (spec).
  let antsAtlas = null;
  try {
    antsAtlas = await PIXI.Assets.load('./assets/ants.json');
  } catch (e) {
    console.warn('[pixi] ants atlas not loaded, using placeholder ants:', e?.message || e);
  }
  // Optional nodes atlas — falls back to colored shapes if missing (spec).
  let nodesAtlas = null;
  try {
    nodesAtlas = await PIXI.Assets.load('./assets/nodes.json');
  } catch (e) {
    console.warn('[pixi] nodes atlas not loaded, using placeholder nodes:', e?.message || e);
  }

  // Optional fx atlas — falls back to procedural FX if missing (spec §6.5).
  let fxAtlas = null;
  try {
    fxAtlas = await PIXI.Assets.load('./assets/fx.json');
  } catch (e) {
    console.warn('[pixi] fx atlas not loaded, using procedural FX:', e?.message || e);
  }

  const store = new SnapshotStore();

  // FX layer: hit rings, death dissolve, build dust, guard-post beam, build-
  // complete flash, queen-attack warning, win-state grade. (spec M4)
  const effects = new Effects(
    { ground: stage.groundFxLayer, air: stage.airFxLayer }, stage.world, store, fxAtlas);

  const antPool = new ViewPool();
  // AntViews fires effects.deathFx on despawn (death dissolve).
  const antViews = new AntViews(stage.antLayer, antsAtlas, antPool,
    { deathFx: (x, y, typeIdx, colony) => effects.deathFx(x, y, typeIdx, colony) });

  // Resource nodes: food / dirt / corpses with glow halos. (spec M4)
  const nodePool = new ViewPool();
  const nodeViews = new NodeViews(stage.dirtFoodLayer, nodesAtlas, nodePool);

  // In-world UI overlay (screen-space uiLayer). DOM #panel HUD stays.
  const hud = new Hud(stage.uiLayer, camera, store, app, stageEl);

  let mapBuilt = false;
  let _mapKey = null;

  // POV toggle: 0 spectator, 1 RED, 2 BLUE. Reuse the existing DOM #pov-btn by
  // overriding the page-global cyclePov() so the Canvas-era button drives Pixi.
  let povMode = 0;
  const povLabels = ['👁 SPECTATOR', '🔴 RED POV', '🔵 BLUE POV'];
  function applyPov() {
    overlays.setPov(povMode);
    const btn = document.getElementById('pov-btn');
    if (btn) {
      btn.textContent = povLabels[povMode];
      btn.style.color = povMode === 1 ? '#f88' : povMode === 2 ? '#88f' : '#aaa';
      btn.style.borderColor = povMode === 1 ? '#833' : povMode === 2 ? '#338' : '#444';
    }
    // Repaint fog immediately from the last snapshot.
    if (store._lastSnap) overlays.updateFog(app, store._lastSnap.fog);
  }
  window.cyclePov = () => { povMode = (povMode + 1) % 3; applyPov(); };

  function buildMap(m) {
    const mw = m?.map?.w, mh = m?.map?.h, terrain = m?.terrain;
    if (!mw || !mh || !terrain) return;
    // Terrain is STATIC, but the replay (and reconnects) re-send the map message every
    // loop. Build only when it actually changes — re-baking each loop leaked chunk
    // RenderTextures and was a primary cause of the long-run OOM.
    let cs = mw ^ (mh << 8) ^ terrain.length;
    for (let i = 0; i < terrain.length; i += 137) cs = (cs * 31 + terrain[i]) | 0;
    const key = mw + 'x' + mh + ':' + cs;
    if (key === _mapKey) return;
    _mapKey = key;
    tileGrid.build(app, mw, mh, terrain, terrainAtlas);
    overlays.setMapSize(mw, mh);
    camera.setWorldSize(mw, mh);
    hud.setWorldSize(mw * 32, mh * 32);
    mapBuilt = true;
    window.__lastMap = m;
    console.log(`[pixi] map baked: ${mw}x${mh} (${terrain.length} tiles)`);
  }

  // Pixi is RENDER-ONLY. It does NOT open its own WebSocket — the Canvas client's
  // connect() is the single socket and owns the full game lifecycle + all DOM
  // (lobby/START, placement, winner screen, info panel, legend, chat, controls).
  // index.html forwards the parsed map/tick/reset to this renderer object; we never
  // touch those DOM concerns here (the Canvas render() Pixi-branch drives the panel).
  const renderer = {
    onMap: (m) => { buildMap(m); },
    onReset: () => {
      store.reset(); antViews.reset(); antPool.clear();
      nodeViews.reset(); nodePool.clear(); effects.reset(); hud.reset();
      intentOverlay.reset();
    },
    onTick: (snap) => {
      store.applyTick(snap, performance.now());
      store._lastSnap = snap;
      // Overlays update only when the grid changes (cheap key compare).
      if (mapBuilt) {
        overlays.updateTerritory(app, snap.territory);
        overlays.updateFog(app, snap.fog);
      }
      structureView.sync(store);   // structures + nests, once per tick
      nodeViews.sync(store);       // food / dirt / corpses, id-diffed
      effects.onTick(store);       // hit rings, beams, flashes, win grade
    },
  };

  // Single ticker: structures pulse their procedural auras; ants interpolate
  // their positions over the measured tick interval (`t` from store.interp).
  new Loop().start(app, store, (t) => {
    const dt = app.ticker.deltaMS;
    structureView.refreshPulse(dt);
    nodeViews.refreshPulse(dt);
    antViews.sync(store, t);
    effects.update(dt);
    intentOverlay.update(store, dt);
    hud.update();
  });

  window.__agants = { app, stage, store, camera, tileGrid, overlays,
    structureView, antViews, antPool, nodeViews, nodePool, effects, hud };
  // Register the renderer and replay the init/map the Canvas client already received
  // while Pixi was loading (so a reconnect into a running game still bakes the map).
  window.__pixiRenderer = renderer;
  window.__pixiMounted = true;   // tells index.html Pixi is live (suppress fallback)
  if (window.__lastInit) renderer.onMap(window.__lastInit);
  console.log('[pixi] renderer ready (fed by Canvas WS)');
}
// On any init failure (incl. the intermittent Pixi crash), degrade to the Canvas
// renderer rather than leaving a blank screen for the live player.
main().catch((e) => {
  console.error('[pixi] init failed — falling back to Canvas', e);
  try { window.__startCanvas?.('pixi init exception'); } catch (_) { /* canvas optional */ }
});
