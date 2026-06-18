// Bootstrap for the Pixi render client. Loads ONLY under ?pixi=1 (see index.html).
// Wires Stage + Camera + TileGrid + Overlays + SnapshotStore + Connection + Loop.
// M1: terrain baking, camera (pan/zoom/clamp/zoom-to-cursor), territory + fog
// overlays, POV toggle. M2+ add structures/ants/FX in the Loop callback.
import { SnapshotStore } from './state/SnapshotStore.js';
import { Connection, resolveWsUrl } from './net/Connection.js';
import { Stage } from './scene/Stage.js';
import { Camera } from './scene/Camera.js';
import { TileGrid } from './scene/TileGrid.js';
import { Overlays } from './scene/Overlays.js';
import { Loop } from './render/Loop.js';

export async function main() {
  const PIXI = window.PIXI;
  const app = new PIXI.Application();
  await app.init({ resizeTo: window, antialias: false, roundPixels: true,
                   background: 0x0b0d10 });
  // Crisp 32px pixel art: nearest-neighbor scaling globally.
  if (PIXI.TextureSource?.defaultOptions) PIXI.TextureSource.defaultOptions.scaleMode = 'nearest';

  const stage = new Stage();
  stage.attach(app);
  const stageEl = document.getElementById('stage');
  stage.mount(app, stageEl);

  const camera = new Camera(stage.world, stageEl, app);
  camera.attachInput();

  const tileGrid = new TileGrid(stage.terrainLayer);
  const overlays = new Overlays(stage.territoryLayer, stage.fogLayer);

  // Optional terrain atlas — falls back to flat colored tiles if missing (spec).
  let terrainAtlas = null;
  try {
    terrainAtlas = await PIXI.Assets.load('./assets/terrain.json');
  } catch (e) {
    console.warn('[pixi] terrain atlas not loaded, using flat-color tiles:', e?.message || e);
  }

  const store = new SnapshotStore();
  let mapBuilt = false;

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
    tileGrid.build(app, mw, mh, terrain, terrainAtlas);
    overlays.setMapSize(mw, mh);
    camera.setWorldSize(mw, mh);
    mapBuilt = true;
    window.__lastMap = m;
    console.log(`[pixi] map baked: ${mw}x${mh} (${terrain.length} tiles)`);
  }

  const url = resolveWsUrl(window.location, window.AGANTS_BACKEND);
  const conn = new Connection(url, {
    onReset: () => { store.reset(); },
    onMap:   (m) => { buildMap(m); },
    onTick:  (snap) => {
      store.applyTick(snap, performance.now());
      store._lastSnap = snap;
      // Overlays update only when the grid changes (cheap key compare).
      if (mapBuilt) {
        overlays.updateTerritory(app, snap.territory);
        overlays.updateFog(app, snap.fog);
      }
      // Reuse the existing DOM HUD.
      try { window.state = snap; window.updateSidebar?.(snap); } catch (e) { /* HUD optional */ }
    },
  });
  conn.connect();

  // Single ticker: M1 has no per-frame interpolation work yet (terrain/overlays
  // are static between ticks); kept so M2+ can lerp sprite positions here.
  new Loop().start(app, store, (_t) => { /* M2+ binds sprites here */ });

  window.__agants = { app, stage, store, conn, camera, tileGrid, overlays };
  console.log('[pixi] client mounted; ws=', url);
}
main();
