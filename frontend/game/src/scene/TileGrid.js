// TileGrid: bakes the static terrain grid into a few RenderTexture chunks so the
// 15k-tile map costs ~12 draw calls. Uses the terrain atlas (terrain.json/png)
// when loaded; otherwise falls back to flat colored tiles per terrain type so
// rendering never blocks on missing art. (spec §2.3, §4 baked-terrain strategy)
import { TS } from './Camera.js';

// Terrain type -> atlas frame name (terrain.json) and fallback color.
// Values verified against the replay stream: 0 dirt, 1 leaf, 2 water, 3 rock,
// 4 nest (engine T_DIRT/T_LEAF/T_WATER/T_ROCK/T_NEST). Colors mirror the Canvas
// renderer's TCOL table.
// Darker, lower-saturation, FLAT colors per terrain type. Flat (not the per-tile
// atlas texture) means adjacent same-type tiles merge into one solid block — no
// repeating grid/seam pattern — and the calmer palette keeps the ground from
// dominating so units (esp. red) read clearly. (user feedback: uniform, no gridlines)
export const TERRAIN = [
  { frame: 'dirt_0.png',  color: 0x342c24 }, // 0 dirt  (dark warm gray-brown)
  { frame: 'leaf_0.png',  color: 0x2c4322 }, // 1 leaf
  { frame: 'water_0.png', color: 0x1b2e47 }, // 2 water
  { frame: 'rock_0.png',  color: 0x44423f }, // 3 rock
  { frame: 'nest_0.png',  color: 0x271a12 }, // 4 nest
];

// Render terrain as flat color blocks (ignore the per-tile atlas texture) so there
// are no tile seams/grid. Set false to restore textured tiles.
const FLAT_TERRAIN = true;

const CHUNK_TILES = 32; // tiles per chunk edge -> 32*32px = 1024px chunks

export class TileGrid {
  // worldContainer layer to add baked chunk sprites into (terrainLayer).
  constructor(layer) {
    this.layer = layer;
    this.sprites = [];
    this.mw = 0; this.mh = 0;
  }

  destroy() {
    // Each chunk sprite is backed by a UNIQUE RenderTexture; destroy the texture +
    // its GPU source too (plain destroy() leaks them — caused the long-run OOM).
    for (const s of this.sprites) { s.destroy({ texture: true, textureSource: true }); }
    this.sprites = [];
  }

  // Build baked chunks from the init message terrain array.
  //   app: PIXI.Application (for renderer.generateTexture / RenderTexture)
  //   mw, mh: map dims in tiles
  //   terrain: flat array length mw*mh of terrain type ids
  //   atlas: optional PIXI.Spritesheet for terrain (null -> flat color fallback)
  build(app, mw, mh, terrain, atlas) {
    const PIXI = window.PIXI;
    this.destroy();
    this.mw = mw; this.mh = mh;

    const chunksX = Math.ceil(mw / CHUNK_TILES);
    const chunksY = Math.ceil(mh / CHUNK_TILES);

    for (let cy = 0; cy < chunksY; cy++) {
      for (let cx = 0; cx < chunksX; cx++) {
        const tx0 = cx * CHUNK_TILES;
        const ty0 = cy * CHUNK_TILES;
        const tilesW = Math.min(CHUNK_TILES, mw - tx0);
        const tilesH = Math.min(CHUNK_TILES, mh - ty0);
        const chunkPxW = tilesW * TS;
        const chunkPxH = tilesH * TS;

        // Temporary container holding this chunk's tile sprites/graphics.
        const tmp = new PIXI.Container();
        for (let ty = 0; ty < tilesH; ty++) {
          for (let tx = 0; tx < tilesW; tx++) {
            const t = terrain[(ty0 + ty) * mw + (tx0 + tx)] || 0;
            const def = TERRAIN[t] || TERRAIN[0];
            let node;
            const tex = FLAT_TERRAIN ? null : atlas?.textures?.[def.frame];
            if (tex) {
              node = new PIXI.Sprite(tex);
              node.width = TS; node.height = TS;
            } else {
              // Fallback: flat colored tile.
              node = new PIXI.Graphics().rect(0, 0, TS, TS).fill(def.color);
            }
            node.position.set(tx * TS, ty * TS);
            tmp.addChild(node);
          }
        }

        // Bake the chunk into a RenderTexture, then display as ONE sprite.
        const rt = PIXI.RenderTexture.create({ width: chunkPxW, height: chunkPxH });
        app.renderer.render({ container: tmp, target: rt });
        tmp.destroy({ children: true });

        const sprite = new PIXI.Sprite(rt);
        sprite.position.set(tx0 * TS, ty0 * TS);
        this.layer.addChild(sprite);
        this.sprites.push(sprite);
      }
    }
  }
}
