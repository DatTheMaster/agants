// Bootstrap for the Pixi render client. Loads ONLY under ?pixi=1 (see index.html).
// Wires Stage + SnapshotStore + Connection + Loop. M1+ binds sprites in the Loop
// callback and consumes the map message; M0 stands up the skeleton.
import { SnapshotStore } from './state/SnapshotStore.js';
import { Connection, resolveWsUrl } from './net/Connection.js';
import { Stage } from './scene/Stage.js';
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
  stage.mount(app, document.getElementById('stage'));

  const store = new SnapshotStore();
  const url = resolveWsUrl(window.location, window.AGANTS_BACKEND);
  const conn = new Connection(url, {
    onReset: () => store.reset(),
    onMap:   (m) => { window.__lastMap = m; },             // M1 consumes this
    onTick:  (snap) => {
      store.applyTick(snap, performance.now());
      // Reuse the existing DOM HUD: updateSidebar() reads the page-global `state`.
      try { window.state = snap; window.updateSidebar?.(snap); } catch (e) { /* HUD optional in M0 */ }
    },
  });
  conn.connect();

  new Loop().start(app, store, (_t) => { /* M1 binds sprites here */ });
  window.__agants = { app, stage, store, conn };           // debug handle
  console.log('[pixi] client mounted; ws=', url);
}
main();
