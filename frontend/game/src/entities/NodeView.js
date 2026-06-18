// NodeView: renders resource nodes — food sources, dirt deposits, and corpses —
// into the dirtFoodLayer, with procedural glow halos + tier accent rings.
// (spec §3.2 food/dirt/corpses, §6.4)
//
// Nodes are STATIC (no interpolation): the server reports tile-snapped x,y and an
// `amt` that depletes across ticks. NodeViews id-diffs each list by an "x,y" key
// and pools sprites across spawn/despawn churn.
//
//   food[]    = [x, y, amt, kind, tier]    kind: seeds|beetle|leaf|honeydew
//                                           tier: home|approach|frontline
//   dirt[]    = [x, y, amt, tier]          tier: home|frontline
//   corpses[] = [x, y, amt]                neutral gray, no colony tint
//
// Atlas: frontend/game/assets/nodes.json (food_<kind>.png, dirt_node_0/1.png).
// Falls back to colored Graphics shapes when a frame is missing (frugal-assets
// rule — never block rendering on art). Glow halos + tier rings are always
// procedural Graphics (spec §6.4 "Glow halos / tier rings are procedural").
import { TS } from '../scene/Camera.js';

// Food core colors (parity with the Canvas FCOL, index.html:923).
const FCOL = {
  seeds:    0xd2b446,
  beetle:   0x965f28,
  leaf:     0x64aa28,
  honeydew: 0x78eb78,
};
// Dirt deposit colors (parity with the Canvas DCOL, index.html:919).
const DCOL = { home: 0xb47800, frontline: 0xc88c3c };

// Corpse display constant — largest observed pile for area-proportional scaling
// (spec §3.2). The Canvas renderer faded over amt/12; we keep both readings tame.
const CORPSE_AMT_MAX = 200;
const FOOD_AMT_MAX = 200;
const DIRT_AMT_MAX = 150;

const CORPSE_COLOR = 0x5a3010;

// --- one node's view ---------------------------------------------------------

class NodeSprite {
  constructor() {
    const PIXI = window.PIXI;
    this.container = new PIXI.Container();
    this.halo = null;      // Graphics glow halo (procedural)
    this.core = null;      // Sprite (atlas) | Graphics (placeholder)
    this.ring = null;      // Graphics tier-accent / amount ring
    this._kind = null;     // for change detection on rebuild
    this._isCorpse = false;
    this._phase = Math.random() * Math.PI * 2; // per-node pulse offset
  }

  reset() {
    if (this.core) { this.core.destroy(); this.core = null; }
    if (this.halo) { this.halo.destroy(); this.halo = null; }
    if (this.ring) { this.ring.destroy(); this.ring = null; }
    this._kind = null;
    this.container.visible = true;
    this.container.alpha = 1;
  }

  destroy() {
    this.container.destroy({ children: true });
    this.core = this.halo = this.ring = null;
  }
}

// --- manager (the whole dirtFoodLayer) ---------------------------------------

export class NodeViews {
  // layer: stage.dirtFoodLayer. atlas: nodes Spritesheet | null. pool: ViewPool.
  constructor(layer, atlas, pool) {
    this.layer = layer;
    this.atlas = atlas || null;
    this.pool = pool;
    this.views = new Map();   // "x,y,kind" -> NodeSprite
    this._elapsed = 0;
  }

  _key(x, y, kind) { return x + ',' + y + ',' + kind; }

  _texture(name) {
    const t = this.atlas?.textures;
    return (t && t[name]) || null;
  }

  // Build the food core + halo + amount ring for a food row.
  _buildFood(v, kind, tier) {
    const PIXI = window.PIXI;
    const color = FCOL[kind] ?? 0xc8c8c8;
    // Halo (always procedural radial-ish glow via concentric fills).
    v.halo = new PIXI.Graphics();
    v.halo.circle(0, 0, TS * 1.4).fill({ color, alpha: 0.10 });
    v.halo.circle(0, 0, TS * 0.85).fill({ color, alpha: 0.14 });
    const GlowFilter = PIXI.filters?.GlowFilter || PIXI.GlowFilter;
    if (GlowFilter) {
      try { v.halo.filters = [new GlowFilter({ distance: 10, outerStrength: 1.4, color })]; }
      catch (e) { /* optional */ }
    }
    v.container.addChild(v.halo);

    // Core sprite (atlas) or a colored disc placeholder.
    const tex = this._texture(`food_${kind}.png`);
    if (tex) {
      v.core = new PIXI.Sprite(tex);
      v.core.anchor.set(0.5);
      v.core.width = TS * 0.7; v.core.height = TS * 0.7;
    } else {
      v.core = new PIXI.Graphics().circle(0, 0, TS * 0.27).fill({ color });
    }
    v.container.addChild(v.core);

    // Tier accent + amount ring (procedural).
    v.ring = new PIXI.Graphics();
    v.container.addChild(v.ring);
    v._ringColor = color;
    v._tier = tier;
  }

  _buildDirt(v, tier) {
    const PIXI = window.PIXI;
    const color = DCOL[tier] ?? DCOL.home;
    v.halo = new PIXI.Graphics();
    v.halo.circle(0, 0, TS * 1.1).fill({ color, alpha: 0.10 });
    v.container.addChild(v.halo);

    // dirt_node_0/1 atlas frames (tier 0 home / 1 frontline), else hexagon core.
    const idx = tier === 'frontline' ? 1 : 0;
    const tex = this._texture(`dirt_node_${idx}.png`);
    if (tex) {
      v.core = new PIXI.Sprite(tex);
      v.core.anchor.set(0.5);
      v.core.width = TS * 0.6; v.core.height = TS * 0.6;
    } else {
      const g = new PIXI.Graphics();
      const hr = TS * 0.2;
      for (let i = 0; i < 6; i++) {
        const a = Math.PI / 6 + i * Math.PI / 3;
        const px = Math.cos(a) * hr, py = Math.sin(a) * hr;
        if (i === 0) g.moveTo(px, py); else g.lineTo(px, py);
      }
      g.closePath().fill({ color });
      v.core = g;
    }
    v.container.addChild(v.core);

    v.ring = new PIXI.Graphics();
    v.container.addChild(v.ring);
    v._ringColor = color;
    v._tier = tier;
  }

  _buildCorpse(v) {
    const PIXI = window.PIXI;
    v.core = new PIXI.Graphics().circle(0, 0, TS * 0.28).fill({ color: CORPSE_COLOR });
    v.container.addChild(v.core);
    v._isCorpse = true;
  }

  // Draw the amount/tier ring for food & dirt nodes (depletes with amt).
  _drawRing(v, amt, max, pulse) {
    if (!v.ring) return;
    const color = v._ringColor;
    const baseR = TS * 0.5;
    const pct = Math.max(0, Math.min(1, amt / max));
    v.ring.clear();
    // Amount arc (how full the deposit is).
    v.ring.arc(0, 0, baseR, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * pct)
          .stroke({ color, width: 1.8, alpha: 0.7 });
    // Tier accent ring.
    if (v._tier === 'frontline') {
      v.ring.circle(0, 0, baseR * 1.5).stroke({ color: 0xff5032, width: 1.5, alpha: 0.45 * pulse });
    } else if (v._tier === 'approach') {
      v.ring.circle(0, 0, baseR * 1.4).stroke({ color: 0xd2aa00, width: 1, alpha: 0.3 * pulse });
    }
  }

  // Acquire a pooled NodeSprite (or create) and attach it to the layer.
  _acquire(poolKey) {
    let v = this.pool.acquire(poolKey);
    if (v) { v.reset(); } else { v = new NodeSprite(); }
    if (!v.container.parent) this.layer.addChild(v.container);
    v.container.visible = true;
    return v;
  }

  // Per-tick bind: id-diff food/dirt/corpses, (re)build views, set positions.
  // Called once per tick from the onTick handler (static — no interp needed).
  sync(store) {
    const wanted = new Set();

    // --- food ---
    for (const f of store.food || []) {
      const x = f[0], y = f[1], kind = f[3], tier = f[4] || 'home';
      const key = this._key(x, y, 'food_' + kind);
      wanted.add(key);
      let v = this.views.get(key);
      if (!v || v._kind !== 'food_' + kind) {
        if (v) this._dispose(key, v);
        v = this._acquire('food');
        this._buildFood(v, kind, tier);
        v._kind = 'food_' + kind;
        v.container.position.set(x * TS + TS / 2, y * TS + TS / 2);
        this.views.set(key, v);
      }
      v._amt = f[2]; v._max = FOOD_AMT_MAX;
    }

    // --- dirt ---
    for (const d of store.dirt || []) {
      const x = d[0], y = d[1], tier = d[3] || 'home';
      const key = this._key(x, y, 'dirt_' + tier);
      wanted.add(key);
      let v = this.views.get(key);
      if (!v || v._kind !== 'dirt_' + tier) {
        if (v) this._dispose(key, v);
        v = this._acquire('dirt');
        this._buildDirt(v, tier);
        v._kind = 'dirt_' + tier;
        v.container.position.set(x * TS + TS / 2, y * TS + TS / 2);
        this.views.set(key, v);
      }
      v._amt = d[2]; v._max = DIRT_AMT_MAX;
    }

    // --- corpses ---
    for (const c of store.corpses || []) {
      const x = c[0], y = c[1], amt = c[2];
      const key = this._key(x, y, 'corpse');
      wanted.add(key);
      let v = this.views.get(key);
      if (!v) {
        v = this._acquire('corpse');
        this._buildCorpse(v);
        v._kind = 'corpse';
        v.container.position.set(x * TS + TS / 2, y * TS + TS / 2);
        this.views.set(key, v);
      }
      // Area-proportional size + alpha fade as the pile depletes (spec §3.2).
      const s = Math.max(0.4, Math.sqrt(Math.max(0, amt) / CORPSE_AMT_MAX));
      v.container.scale.set(s);
      v.container.alpha = 0.25 + 0.75 * Math.min(1, amt / 12);
    }

    // Despawn nodes no longer present -> pool by their concern.
    for (const [key, v] of this.views) {
      if (wanted.has(key)) continue;
      this._dispose(key, v);
    }
  }

  _dispose(key, v) {
    this.views.delete(key);
    if (v.container.parent) v.container.parent.removeChild(v.container);
    const concern = v._isCorpse ? 'corpse' : (v._kind || '').startsWith('dirt') ? 'dirt' : 'food';
    v._isCorpse = false;
    v.reset();
    v.container.visible = false;
    this.pool.release(concern, v);
  }

  // Per-frame pulse: food/dirt halos breathe + amount rings redraw. Cheap.
  refreshPulse(dtMs) {
    this._elapsed += dtMs || 0;
    for (const v of this.views.values()) {
      if (v._isCorpse) continue;
      const pulse = 0.6 + 0.4 * Math.sin(this._elapsed / 420 + v._phase);
      if (v.halo) v.halo.alpha = pulse;
      if (v.ring) this._drawRing(v, v._amt || 0, v._max || 1, pulse);
    }
  }

  reset() {
    for (const v of this.views.values()) {
      if (v.container.parent) v.container.parent.removeChild(v.container);
      v.destroy();
    }
    this.views.clear();
  }
}
