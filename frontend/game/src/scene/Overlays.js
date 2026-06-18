// Overlays: territory tint + per-POV fog-of-war, drawn over the world.
// Both are derived from per-tile byte grids (length mw*mh) supplied each tick.
// They are rebuilt ONLY when the grid changes (cheap hash compare), not per
// frame. (spec §3.2 territory/fog, §2.3 territoryLayer/fogLayer)
//
//   territory: 0 neutral / 1 RED / 2 BLUE   -> low-alpha colony tint
//   fog[pov]:  0 unexplored / 1 explored / 2 visible (per colony)
//              -> dark overlay: 0 dark, 1 dimmed, 2 clear
//
// Each grid is rendered into a 1px-per-tile RenderTexture, then displayed as a
// single sprite scaled up by TS so the whole overlay is one draw call. A BlurFilter
// softens the fog edges (spec §7).
import { TS } from './Camera.js';

// povMode: 0 spectator (no fog), 1 RED, 2 BLUE  -> fog array index povMode-1.
export class Overlays {
  constructor(territoryLayer, fogLayer) {
    this.territoryLayer = territoryLayer;
    this.fogLayer = fogLayer;
    this.mw = 0; this.mh = 0;
    this.povMode = 0;
    this._terrSprite = null;
    this._fogSprite = null;
    this._lastTerrKey = null;
    this._lastFogKey = null;
  }

  setMapSize(mw, mh) {
    this.mw = mw; this.mh = mh;
    this._lastTerrKey = null;
    this._lastFogKey = null;
  }

  setPov(povMode) {
    this.povMode = povMode;
    this._lastFogKey = null; // force fog rebuild for the new POV
  }

  destroy() {
    this._terrSprite?.destroy();
    this._fogSprite?.destroy();
    this._terrSprite = null;
    this._fogSprite = null;
  }

  // Cheap change key: length + a sampled checksum (every 37th cell). Avoids a
  // full-array compare while still catching real changes between ticks.
  static _key(arr, pov) {
    if (!arr) return 'none';
    let h = arr.length ^ (pov << 16);
    for (let i = 0; i < arr.length; i += 37) h = (h * 31 + arr[i]) | 0;
    return h + ':' + pov;
  }

  // Render an mw*mh byte grid into an mw*mh RGBA RenderTexture via a temporary
  // Graphics of 1px cells, returns a Sprite scaled to TS-per-tile.
  _bake(app, grid, colorFn) {
    const PIXI = window.PIXI;
    const tmp = new PIXI.Container();
    const g = new PIXI.Graphics();
    for (let i = 0; i < grid.length; i++) {
      const c = colorFn(grid[i]);          // {color, alpha} or null to skip
      if (!c) continue;
      const x = i % this.mw, y = (i / this.mw) | 0;
      g.rect(x, y, 1, 1).fill({ color: c.color, alpha: c.alpha });
    }
    tmp.addChild(g);
    const rt = PIXI.RenderTexture.create({ width: this.mw, height: this.mh });
    app.renderer.render({ container: tmp, target: rt });
    tmp.destroy({ children: true });
    const sprite = new PIXI.Sprite(rt);
    sprite.scale.set(TS); // 1 tile-texel -> TS world px
    return sprite;
  }

  updateTerritory(app, territory) {
    if (!territory || !this.mw) return;
    const key = Overlays._key(territory, 0);
    if (key === this._lastTerrKey) return;
    this._lastTerrKey = key;
    this._terrSprite?.destroy();
    this._terrSprite = this._bake(app, territory, (v) => {
      if (v === 1) return { color: 0xd23232, alpha: 40 / 255 }; // RED
      if (v === 2) return { color: 0x3250dc, alpha: 40 / 255 }; // BLUE
      return null;
    });
    this.territoryLayer.addChild(this._terrSprite);
  }

  updateFog(app, fog) {
    if (!this.mw) return;
    // Spectator (povMode 0): no fog overlay.
    const fogArr = (this.povMode > 0 && fog) ? fog[this.povMode - 1] : null;
    const key = Overlays._key(fogArr, this.povMode);
    if (key === this._lastFogKey) return;
    this._lastFogKey = key;
    this._fogSprite?.destroy();
    this._fogSprite = null;
    if (!fogArr) return;
    this._fogSprite = this._bake(app, fogArr, (v) => {
      const alpha = v === 0 ? 210 / 255 : v === 1 ? 130 / 255 : 0;
      if (alpha <= 0) return null;
      return { color: 0x000000, alpha };
    });
    // Soften fog edges (spec §7); guard if pixi-filters absent.
    const PIXI = window.PIXI;
    const BlurFilter = PIXI.BlurFilter || PIXI.filters?.BlurFilter;
    if (BlurFilter) {
      try { this._fogSprite.filters = [new BlurFilter({ strength: 2 })]; }
      catch (e) { /* filter optional */ }
    }
    this.fogLayer.addChild(this._fogSprite);
  }
}
