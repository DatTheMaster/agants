// TileGrid: draws the static terrain as ONE continuous vector Graphics — a single
// base fill plus the sparse non-dirt tiles in the same mesh. Vector geometry scales
// uniformly with the camera, so there are NO tile/chunk seams at any zoom level
// (raster-baked tiles bled the dark background through 1px gaps that changed shape
// with every zoom ratio — the "grid that's a different mess at each zoom"). One
// display object => one draw call, and nothing to leak. (user feedback: no gridlines)
import { TS } from './Camera.js';

// Flat color per terrain type — the ORIGINAL warm brown ground (user liked it; only
// the old gridlines/seams ruined it, and those are gone now that terrain is vector).
// Red-unit legibility comes from the faint territory tint (Overlays alpha) + the
// brightened red ant tint, not from desaturating the ground.
export const TERRAIN = [
  { color: 0x5f4634 }, // 0 dirt  (warm brown)
  { color: 0x48762c }, // 1 leaf  (green)
  { color: 0x1e416e }, // 2 water (blue)
  { color: 0x646464 }, // 3 rock  (gray, walls)
  { color: 0x412d20 }, // 4 nest  (dark brown)
];

export class TileGrid {
  constructor(layer) {
    this.layer = layer;
    this.gfx = null;
    this.mw = 0; this.mh = 0;
  }

  destroy() {
    if (this.gfx) { this.gfx.destroy(); this.gfx = null; }
  }

  // Build the terrain Graphics.
  //   mw, mh: map dims in tiles; terrain: flat array length mw*mh of type ids.
  //   app/atlas: unused (kept for call-site compatibility) — terrain is now vector.
  build(app, mw, mh, terrain, atlas) {
    const PIXI = window.PIXI;
    this.destroy();
    this.mw = mw; this.mh = mh;

    const g = new PIXI.Graphics();
    // Base: one continuous fill over the whole map (zero seams at any zoom).
    g.rect(0, 0, mw * TS, mh * TS).fill(TERRAIN[0].color);

    // Overlay each non-dirt type as a batch of rects, one fill() per color. All rects
    // live in this single Graphics mesh, so adjacent tiles share geometry (no gaps)
    // and sit opaque over the base (no background bleed).
    for (let t = 1; t < TERRAIN.length; t++) {
      let any = false;
      for (let i = 0; i < terrain.length; i++) {
        if ((terrain[i] || 0) === t) {
          g.rect((i % mw) * TS, ((i / mw) | 0) * TS, TS, TS);
          any = true;
        }
      }
      if (any) g.fill(TERRAIN[t].color);
    }

    this.layer.addChild(g);
    this.gfx = g;
  }
}
