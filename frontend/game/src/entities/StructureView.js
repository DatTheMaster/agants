// StructureView: renders all 5 structure types (guard_post, watchtower,
// barracks, wall, larder) + colony nests into the structureLayer. (spec §3.2, M2)
//
// Lifecycle states per structure:
//   - under-construction (active=0): scaffold sprite + build-progress bar
//     (build_progress / build_required)
//   - active (active=1): the active sprite + procedural overlays
//   - damaged (hp < max_hp): damaged frame if present + an HP bar
//
// Colony tint is looked up from a fixed map (never computed) to avoid an
// off-by-one: 0=RED 0xff3c3c, 1=BLUE 0x3c3cff (spec §3.2).
//
// Render order (parity with the Canvas 2-pass): walls FIRST, then the other
// structures, then nests. (spec §3.2 "Walls render first")
//
// Procedural overlays (Graphics, redrawn only when something changes):
//   - guard_post : attack-range ring (10 tiles)
//   - watchtower : vision ring (12 tiles)
//   - larder     : income pulse ring (gold)
//   - barracks   : spawn pulse ring
// These are NOT sprite frames; they are drawn with Graphics + an optional
// GlowFilter so they read as auras. They pulse via the shared elapsed clock in
// the Loop tick callback (see refreshPulse()).
import { TS } from '../scene/Camera.js';
import { SnapshotStore } from '../state/SnapshotStore.js';

// Colony id -> tint. Looked up, never computed (spec §3.2).
const COLONY_TINT = { 0: 0xff3c3c, 1: 0x3c3cff };
function tintFor(colony) { return COLONY_TINT[colony] ?? 0xffffff; }

// Range/vision radii in TILES, mirroring the Canvas renderer + legend.
const RANGE_TILES = { guard_post: 10, watchtower: 12 };
// Footprint scale (fraction of a tile the sprite covers), parity with Canvas.
const FOOTPRINT = {
  guard_post: 0.85, watchtower: 0.9, barracks: 1.2, larder: 0.95, wall: 1.0,
};

// Atlas frame name resolution for a given type + state. Falls back gracefully:
// if the requested frame is absent we use the active frame, then a placeholder.
function frameName(type, damaged) {
  if (damaged) return `${type}_damaged.png`;
  return `${type}_active.png`;
}

export class StructureView {
  // layer: structureLayer (camera-transformed). atlas: optional Spritesheet
  // (structures.json) — null falls back to procedural placeholder shapes.
  constructor(layer, atlas) {
    this.layer = layer;
    this.atlas = atlas || null;
    // key -> view record { container, sprite, hpBar, buildBar, overlay, ... }
    this.views = new Map();
    // colony id -> nest container
    this.nests = new Map();
    this._elapsed = 0;
    // Walls and other structures live in separate sub-containers so walls draw
    // first regardless of insertion order (spec §3.2).
    const PIXI = window.PIXI;
    this.wallContainer = new PIXI.Container();
    this.bodyContainer = new PIXI.Container();
    this.nestContainer = new PIXI.Container();
    this.overlayContainer = new PIXI.Container();
    // Back-to-front: nests under structures, overlays (rings) under bodies but
    // over walls so range rings show; bodies on top.
    this.layer.addChild(this.nestContainer, this.overlayContainer,
                        this.wallContainer, this.bodyContainer);
  }

  destroy() {
    for (const v of this.views.values()) v.container.destroy({ children: true });
    for (const n of this.nests.values()) n.destroy({ children: true });
    this.views.clear();
    this.nests.clear();
  }

  // ---- sprite/placeholder creation -----------------------------------------

  _texture(type, damaged) {
    if (!this.atlas?.textures) return null;
    const t = this.atlas.textures[frameName(type, damaged)];
    if (t) return t;
    // No damaged frame -> fall back to the active frame (tint conveys damage).
    return this.atlas.textures[frameName(type, false)] || null;
  }

  // Build the body sprite (atlas) or a placeholder Graphics for a structure.
  _makeBody(type, damaged) {
    const PIXI = window.PIXI;
    const tex = this._texture(type, damaged);
    const footprint = (FOOTPRINT[type] || 1) * TS;
    if (tex) {
      const s = new PIXI.Sprite(tex);
      s.anchor.set(0.5);
      s.width = footprint; s.height = footprint;
      return s;
    }
    // Placeholder: simple colored shape per type so rendering never blocks on
    // missing art (spec frugal-assets rule).
    const g = new PIXI.Graphics();
    const h = footprint / 2;
    if (type === 'wall') {
      g.rect(-h, -h, footprint, footprint).fill({ color: 0x888078 });
      g.rect(-h, -h, footprint, footprint).stroke({ color: 0x2a201a, width: 1 });
    } else if (type === 'watchtower') {
      const tw = TS * 0.5, th = TS * 0.9;
      g.rect(-tw / 2, -th / 2, tw, th).fill({ color: 0xbfbfbf });
    } else if (type === 'barracks') {
      g.rect(-TS * 0.55, -TS * 0.33, TS * 1.1, TS * 0.66).fill({ color: 0xbfbfbf });
    } else if (type === 'larder') {
      g.ellipse(0, 0, TS * 0.5, TS * 0.4).fill({ color: 0xbfbfbf });
    } else { // guard_post + default
      g.rect(-h * 0.75, -h * 0.75, footprint * 0.75, footprint * 0.75).fill({ color: 0xbfbfbf });
    }
    return g;
  }

  // ---- overlay (range/vision/income/spawn) ---------------------------------

  _makeOverlay(type, colony) {
    const PIXI = window.PIXI;
    const g = new PIXI.Graphics();
    const tint = tintFor(colony);
    const r = (RANGE_TILES[type] || 0) * TS;
    if (type === 'guard_post' || type === 'watchtower') {
      g.circle(0, 0, r).stroke({ color: tint, width: 1.5, alpha: 0.35 });
    } else if (type === 'larder') {
      // Income pulse ring (gold) around the dome.
      g.circle(0, 0, TS * 0.6).stroke({ color: 0xffdc50, width: 1.5, alpha: 0.5 });
    } else if (type === 'barracks') {
      // Spawn pulse ring.
      g.circle(0, 0, TS * 0.7).stroke({ color: tint, width: 1.5, alpha: 0.45 });
    } else {
      return null;
    }
    // Soft glow on the overlay if pixi-filters is present.
    const GlowFilter = PIXI.filters?.GlowFilter || PIXI.GlowFilter;
    if (GlowFilter) {
      try { g.filters = [new GlowFilter({ distance: 6, outerStrength: 1.2, color: type === 'larder' ? 0xffdc50 : tint })]; }
      catch (e) { /* filter optional */ }
    }
    return g;
  }

  // ---- per-tick bind --------------------------------------------------------

  // Sync the structureLayer to the store's structures + colonies. Called once
  // per tick (non-positional, no interpolation).
  sync(store) {
    // Remove despawned structures (destroyed/captured).
    for (const key of store.removedStructKeys) {
      const v = this.views.get(key);
      if (v) { v.container.destroy({ children: true }); this.views.delete(key); }
    }
    // Add / update.
    for (const [key, s] of store.structures) {
      let v = this.views.get(key);
      const damaged = s.hp < s.maxHp;
      const underConstruction = !s.active;
      if (!v) {
        v = this._createView(key, s);
        this.views.set(key, v);
      }
      this._updateView(v, s, damaged, underConstruction);
    }
    // Nests.
    this._syncNests(store);
  }

  _createView(key, s) {
    const PIXI = window.PIXI;
    const container = new PIXI.Container();
    container.position.set(s.x * TS + TS / 2, s.y * TS + TS / 2);
    const isWall = s.type === 'wall';
    (isWall ? this.wallContainer : this.bodyContainer).addChild(container);

    // Overlay (range/vision/income/spawn) lives in the shared overlayContainer
    // so it renders under bodies; positioned to match the structure.
    let overlay = this._makeOverlay(s.type, s.colony);
    if (overlay) {
      overlay.position.set(s.x * TS + TS / 2, s.y * TS + TS / 2);
      this.overlayContainer.addChild(overlay);
    }

    return {
      key, type: s.type, colony: s.colony, x: s.x, y: s.y,
      container, overlay,
      sprite: null, hpBar: null, buildBar: null, scaffold: null,
      _lastDamaged: null, _lastConstruction: null,
    };
  }

  _updateView(v, s, damaged, underConstruction) {
    const PIXI = window.PIXI;
    v.colony = s.colony;

    // Rebuild the body sprite only when the damaged/construction state flips.
    if (v._lastDamaged !== damaged || v._lastConstruction !== underConstruction) {
      if (v.sprite) { v.sprite.destroy(); v.sprite = null; }
      if (v.scaffold) { v.scaffold.destroy(); v.scaffold = null; }

      if (underConstruction) {
        // Scaffold placeholder (dashed-look box) — no _construction atlas frame
        // exists, so this is procedural per spec fallback.
        const scaffR = TS * 0.65;
        const g = new PIXI.Graphics();
        g.rect(-scaffR / 2, -scaffR / 2, scaffR, scaffR)
         .stroke({ color: tintFor(s.colony), width: 1.5, alpha: 0.6 });
        v.scaffold = g;
        v.container.addChild(g);
      } else {
        const body = this._makeBody(s.type, damaged);
        body.tint = tintFor(s.colony);
        // Desaturate a touch when damaged so it reads as hurt even without a
        // dedicated damaged frame.
        if (damaged) body.alpha = 0.85;
        v.sprite = body;
        v.container.addChild(body);
      }
      v._lastDamaged = damaged;
      v._lastConstruction = underConstruction;
    }

    // Overlay only shown when active (no aura on a construction site).
    if (v.overlay) v.overlay.visible = !underConstruction;

    // Build-progress bar (under construction).
    if (underConstruction) {
      const pct = s.buildRequired > 0 ? Math.min(1, s.buildProgress / s.buildRequired) : 0;
      this._drawBuildBar(v, pct);
      this._clearHpBar(v);
    } else {
      this._clearBuildBar(v);
      // HP bar only when damaged.
      if (damaged) this._drawHpBar(v, s.hp / s.maxHp);
      else this._clearHpBar(v);
    }
  }

  _drawBuildBar(v, pct) {
    const PIXI = window.PIXI;
    if (!v.buildBar) { v.buildBar = new PIXI.Graphics(); v.container.addChild(v.buildBar); }
    const w = TS * 0.7, y = TS * 0.45;
    v.buildBar.clear();
    v.buildBar.rect(-w / 2, y, w, 3).fill({ color: 0x000000, alpha: 0.6 });
    v.buildBar.rect(-w / 2, y, w * pct, 3).fill({ color: 0x50dc78 });
  }
  _clearBuildBar(v) { if (v.buildBar) { v.buildBar.destroy(); v.buildBar = null; } }

  _drawHpBar(v, pct) {
    const PIXI = window.PIXI;
    if (!v.hpBar) { v.hpBar = new PIXI.Graphics(); v.container.addChild(v.hpBar); }
    const w = (FOOTPRINT[v.type] || 1) * TS * 0.8, y = TS * 0.45;
    const r = Math.round(255 * (1 - pct)), g = Math.round(200 * pct);
    v.hpBar.clear();
    v.hpBar.rect(-w / 2, y, w, 3).fill({ color: 0x000000, alpha: 0.55 });
    v.hpBar.rect(-w / 2, y, w * pct, 3).fill({ color: (r << 16) | (g << 8) });
  }
  _clearHpBar(v) { if (v.hpBar) { v.hpBar.destroy(); v.hpBar = null; } }

  // ---- nests ----------------------------------------------------------------

  _syncNests(store) {
    const PIXI = window.PIXI;
    for (const [id, c] of store.colonies) {
      let n = this.nests.get(id);
      if (!n) {
        n = this._makeNest(c);
        this.nests.set(id, n);
        this.nestContainer.addChild(n.container);
      }
      n.alive = c.alive;
      n.container.position.set(c.nestX * TS + TS / 2, c.nestY * TS + TS / 2);
      n.glow.alpha = c.alive ? 1 : 0.25;
      n.dead.visible = !c.alive;
    }
  }

  _makeNest(c) {
    const PIXI = window.PIXI;
    const container = new PIXI.Container();
    const tint = tintFor(c.id);
    // Carved nest footprint (diamond, ~5x5 tiles, low alpha) — parity with Canvas.
    const carve = new PIXI.Graphics();
    for (let dy = -2; dy <= 2; dy++)
      for (let dx = -2; dx <= 2; dx++)
        if (Math.abs(dx) + Math.abs(dy) <= 3)
          carve.rect(dx * TS - TS / 2, dy * TS - TS / 2, TS, TS).fill({ color: tint, alpha: 0.12 });
    container.addChild(carve);

    // Nest sprite (atlas nest_0) or a glowing core placeholder.
    let core;
    const tex = this.atlas?.textures?.['nest_0.png'];
    if (tex) {
      core = new PIXI.Sprite(tex);
      core.anchor.set(0.5);
      core.width = TS * 1.4; core.height = TS * 1.4;
      core.tint = tint;
    } else {
      core = new PIXI.Graphics().circle(0, 0, TS * 0.4).fill({ color: tint, alpha: 0.8 });
    }
    container.addChild(core);

    // Soft glow halo (alive only).
    const glow = new PIXI.Graphics().circle(0, 0, TS * 2).fill({ color: tint, alpha: 0.12 });
    const GlowFilter = PIXI.filters?.GlowFilter || PIXI.GlowFilter;
    if (GlowFilter) {
      try { glow.filters = [new GlowFilter({ distance: 12, outerStrength: 1.5, color: tint })]; }
      catch (e) { /* optional */ }
    }
    container.addChildAt(glow, 0);

    // Dead marker (X), hidden while alive.
    const dead = new PIXI.Graphics();
    dead.moveTo(-8, -8).lineTo(8, 8).moveTo(8, -8).lineTo(-8, 8)
        .stroke({ color: tint, width: 2, alpha: 0.5 });
    dead.visible = false;
    container.addChild(dead);

    return { container, glow, core, dead, alive: c.alive };
  }

  // ---- pulse animation (called from the Loop ticker) ------------------------

  // dtMs: ms elapsed this frame. Pulses overlay rings + nest glows so they read
  // as living auras (parity with the Canvas pulse). Cheap: only alpha tweaks.
  refreshPulse(dtMs) {
    this._elapsed += dtMs || 0;
    const p = 0.55 + 0.45 * Math.sin(this._elapsed / 600);
    for (const v of this.views.values()) {
      if (v.overlay && v.overlay.visible) v.overlay.alpha = p;
    }
    for (const n of this.nests.values()) {
      if (n.alive) n.glow.alpha = 0.6 + 0.4 * p;
    }
  }
}
