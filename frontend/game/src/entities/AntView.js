// AntView: a single ant's visual — an AnimatedSprite (or static Sprite/Graphics
// placeholder) + carry pellet + HP bar, with state-machine clip selection,
// free-rotation facing, tier scaling, and colony tint. (spec §3.2, §3.3, M3)
//
// AntViews (manager, bottom of file) binds the whole antLayer from
// SnapshotStore.ants each frame: spawns/despawns via a ViewPool, lerps positions
// over the measured tick interval, and fires a death-dissolve hook on
// store.removedAntIds.
//
// Atlas: frontend/game/assets/ants.json (sheet.animations[clip]). When a clip is
// missing the view falls back to the type's idle clip, then to a colored-shape
// placeholder that still rotates and moves (frugal-assets rule — never block).
import { TS } from '../scene/Camera.js';

// --- constants (parity with the Canvas renderer, index.html) -----------------

// Colony id -> tint. Looked up, never computed, to avoid an off-by-one (spec §3.2).
// Brighter/more saturated (paired with the antLayer brightness lift in main.js) so
// RED especially pops against the warm ground. (user feedback)
const COLONY_TINT = { 0: 0xff5555, 1: 0x5577ff };
function tintFor(colony) { return COLONY_TINT[colony] ?? 0xffffff; }

// Blend two 0xRRGGBB colors by fraction t toward `b` (0=all a, 1=all b).
function blendHex(a, b, t) {
  const ar = (a >> 16) & 0xff, ag = (a >> 8) & 0xff, ab = a & 0xff;
  const br = (b >> 16) & 0xff, bg = (b >> 8) & 0xff, bb = b & 0xff;
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return (r << 16) | (g << 8) | bl;
}

// Per-tier scale multiplier (spec §2.4 "kept"; index.html:1502).
export const TIER_SCALE = [1.0, 1.25, 1.5, 1.8];

// The ant art is drawn facing UP (north). The movement heading is atan2(dy,dx),
// which is 0 for east — so an up-facing sprite must be turned +90° to lead with its
// head in the travel direction (a real ant leads with its head, not its side).
// (user feedback: ants were moving "like humans", head up while sliding sideways)
const ART_FACING_OFFSET = Math.PI / 2;

// Type idx -> atlas subject name.
const TYPE_NAME = ['worker', 'soldier', 'scout', 'queen', 'spitter', 'raider'];

// Display size (world px) of a type at tier 0, before TIER_SCALE. Derived from
// the Canvas BASE_SZ*TS/8 so the Pixi sprites read at parity sizes.
//   BASE_SZ = [2.8, 3.8, 2.6, 6.0, 3.6]  ->  *TS/8  (TS=32)
const BASE_DISPLAY = [2.8, 3.8, 2.6, 6.0, 3.6, 3.2].map((b) => (b * TS) / 8 * 2.0);
// (x2.0: BASE_SZ was a *radius* in the Canvas; sprites use width/height.)

// Placeholder body color per type (used when no atlas frame exists).
// Spitter (idx 4) is neutral-gray so the blended team tint shows correctly.
const PLACEHOLDER_COLOR = [0xcfcfcf, 0xe0e0e0, 0xb8b8b8, 0xf0f0f0, 0xe0e0e0, 0xe0e0e0];

// State values (engine/constants.py:21), verified.
const S = { IDLE: 0, FORAGING: 1, RETURNING: 2, EXPLORING: 3, FIGHTING: 4,
            PATROLLING: 5, RECRUITED: 6, BUILDING: 7 };

// type x state x carrying -> clip name (spec §3.3). The renderer is the single
// source of truth for this table. Each type has a `default` fallback.
const CLIP_TABLE = {
  worker: {
    [S.IDLE]: 'worker_idle',
    [S.FORAGING]: 'worker_walk',
    [S.RETURNING]: 'worker_walk',          // carry override applied below
    [S.EXPLORING]: 'worker_walk',
    [S.FIGHTING]: 'worker_attack',
    [S.PATROLLING]: 'worker_walk',
    [S.RECRUITED]: 'worker_walk',
    [S.BUILDING]: 'worker_dig',            // distinct dig clip, NOT attack
    default: 'worker_walk',
  },
  soldier: {
    [S.IDLE]: 'soldier_idle',
    [S.FIGHTING]: 'soldier_attack',
    default: 'soldier_walk',
  },
  scout: {
    [S.IDLE]: 'scout_idle',
    [S.EXPLORING]: 'scout_walk',
    [S.FIGHTING]: 'scout_attack',
    default: 'scout_walk',
  },
  queen: {
    [S.IDLE]: 'queen_idle',
    [S.FIGHTING]: 'queen_attack',
    default: 'queen_idle',
  },
  // Spitter: reuses soldier clips (no dedicated sprite yet — placeholder quality).
  // Visually distinguished by the acid-green PLACEHOLDER_COLOR[4] tint overlay below.
  spitter: {
    [S.IDLE]: 'soldier_idle',
    [S.FIGHTING]: 'soldier_attack',
    default: 'soldier_walk',
  },
  // Raider: reuses scout clips (lean ranged-siege silhouette — no dedicated sprite yet).
  // Distinguished by a rust-orange tint overlay below (see RAIDER tint).
  raider: {
    [S.IDLE]: 'scout_idle',
    [S.FIGHTING]: 'scout_attack',
    default: 'scout_walk',
  },
};

// Resolve the clip name for type x state x carrying (spec §3.3).
export function selectClip(typeIdx, state, carrying) {
  const name = TYPE_NAME[typeIdx] || 'worker';
  const table = CLIP_TABLE[name] || CLIP_TABLE.worker;
  // RETURNING worker prefers carry clip when hauling.
  if (name === 'worker' && state === S.RETURNING && carrying) return 'worker_carry';
  return table[state] || table.default;
}

// --- AntView (one ant) -------------------------------------------------------

export class AntView {
  // atlas: Spritesheet | null. layer: antLayer container.
  constructor(atlas) {
    this.atlas = atlas || null;
    const PIXI = window.PIXI;
    this.container = new PIXI.Container();
    this.body = null;          // AnimatedSprite | Sprite | Graphics
    this.pellet = null;        // carry-food child sprite/graphics
    this.hpBar = null;         // Graphics
    this._clip = null;         // current clip name (skip redundant swaps)
    this._typeIdx = -1;
    this._lastAngle = 0;       // free-rotation facing memory (idle keeps heading)
    this._tier = -1;
    this._isAnimated = false;
    this._tint = -1;           // cached colony tint (skip redundant per-frame writes)
    this._hpKey = '';          // cached hp-bar geometry signature (redraw only on change)
  }

  // Resolve an ordered texture array for a clip, with graceful fallback to the
  // type's idle clip, then null (=> placeholder).
  _clipTextures(clipName, typeIdx) {
    const a = this.atlas;
    if (!a || !a.animations) return null;
    if (a.animations[clipName]) return a.animations[clipName];
    // Fall back to the type's idle clip if the requested one isn't packed yet.
    const idle = (TYPE_NAME[typeIdx] || 'worker') + '_idle';
    if (a.animations[idle]) return a.animations[idle];
    // Last resort: any single texture for the subject.
    const subj = TYPE_NAME[typeIdx] || 'worker';
    for (const key of Object.keys(a.textures || {})) {
      if (key.startsWith(subj + '_')) return [a.textures[key]];
    }
    return null;
  }

  _makePlaceholder(typeIdx) {
    const PIXI = window.PIXI;
    const g = new PIXI.Graphics();
    const c = PLACEHOLDER_COLOR[typeIdx] ?? 0xcccccc;
    // A little teardrop-ish body so rotation is legible: a circle + a nose pointing
    // UP, matching the real art's facing convention (see ART_FACING_OFFSET).
    g.circle(0, 0, 6).fill({ color: c });
    g.moveTo(0, -6).lineTo(0, -12).stroke({ color: c, width: 3 });
    g.pivot.set(0, 0);
    g._isPlaceholder = true;
    return g;
  }

  // (Re)build the body sprite for a clip. Reuses the AnimatedSprite when only the
  // texture set changes; rebuilds when crossing the animated/placeholder boundary.
  _setClip(clipName, typeIdx) {
    if (clipName === this._clip && this.body) return;
    const PIXI = window.PIXI;
    const textures = this._clipTextures(clipName, typeIdx);

    if (textures && textures.length) {
      if (this.body && this._isAnimated) {
        // Swap textures in place (no recreate) — the cheap path (spec §5).
        this.body.textures = textures;
        this.body.gotoAndPlay?.(0);
      } else {
        if (this.body) { this.body.destroy(); this.body = null; }
        const spr = new PIXI.AnimatedSprite(textures);
        spr.anchor.set(0.5);
        spr.animationSpeed = textures.length > 1 ? 0.12 : 0;
        spr.loop = true;
        if (textures.length > 1) spr.play();
        this.body = spr;
        this._isAnimated = true;
        this._tint = -1;       // fresh sprite: force tint re-apply next update
        this.container.addChildAt(spr, 0);
      }
    } else {
      // No atlas frame -> placeholder shape (still rotates/moves).
      if (!this.body || this._isAnimated) {
        if (this.body) { this.body.destroy(); this.body = null; }
        this.body = this._makePlaceholder(typeIdx);
        this._isAnimated = false;
        this._tint = -1;       // fresh placeholder: force tint re-apply next update
        this.container.addChildAt(this.body, 0);
      }
    }
    this._clip = clipName;
  }

  // Size the body to the type+tier (sprite width/height in world px).
  _applySize(typeIdx, tier) {
    if (!this.body || this._isAnimated === false) {
      // Placeholders are sized via container.scale below.
    }
    const base = BASE_DISPLAY[typeIdx] ?? 16;
    const sz = base * (TIER_SCALE[tier] || 1);
    if (this._isAnimated && this.body) {
      this.body.width = sz;
      this.body.height = sz;
    } else if (this.body) {
      // Placeholder authored at ~12px radius -> scale to target.
      const s = sz / 24;
      this.body.scale.set(s);
    }
  }

  _setPellet(carrying) {
    const PIXI = window.PIXI;
    if (carrying && !this.pellet) {
      const tex = this.atlas?.textures?.['food_pellet.png'];
      let p;
      if (tex) { p = new PIXI.Sprite(tex); p.anchor.set(0.5); p.width = TS * 0.3; p.height = TS * 0.3; }
      else { p = new PIXI.Graphics().circle(0, 0, 3).fill({ color: 0x8fd14f }); }
      p.position.set(0, -8);   // held in front/above
      this.pellet = p;
      this.container.addChild(p);
    } else if (!carrying && this.pellet) {
      this.pellet.destroy();
      this.pellet = null;
    }
  }

  _setHpBar(hp, maxHp, typeIdx, tier) {
    const PIXI = window.PIXI;
    const damaged = hp < maxHp && maxHp > 0;
    if (!damaged) {
      if (this.hpBar) { this.hpBar.destroy(); this.hpBar = null; this._hpKey = ''; }
      return;
    }
    if (!this.hpBar) { this.hpBar = new PIXI.Graphics(); this.container.addChild(this.hpBar); }
    // Redraw the bar geometry ONLY when hp/maxHp/type/tier change — at peak ant
    // counts this avoids 2000 clear()+rect()+fill() rebuilds every frame. The
    // cheap counter-rotation (below) still runs each frame so the bar stays
    // screen-up as the body rotates.
    const key = `${hp}/${maxHp}/${typeIdx}/${tier}`;
    if (key !== this._hpKey) {
      const pct = Math.max(0, Math.min(1, hp / maxHp));
      const base = (BASE_DISPLAY[typeIdx] ?? 16) * (TIER_SCALE[tier] || 1);
      const w = Math.max(8, base * 0.8);
      const y = -base * 0.6 - 3;
      const r = Math.round(255 * (1 - pct)), g = Math.round(210 * pct);
      this.hpBar.clear();
      this.hpBar.rect(-w / 2, y, w, 2).fill({ color: 0x000000, alpha: 0.55 });
      this.hpBar.rect(-w / 2, y, w * pct, 2).fill({ color: (r << 16) | (g << 8) });
      this._hpKey = key;
    }
    // HP bar must not rotate with the body (it's screen-up). Counter-rotate.
    this.hpBar.rotation = -this.container.rotation;
  }

  // Bind this view to an ant entry from the store (called per frame after lerp).
  // entry: { from, to, colony, type, state, carrying, hp, maxHp } ; tier from store.
  update(entry, tier, x, y, angle) {
    const PIXI = window.PIXI;
    this._typeIdx = entry.type;

    const clip = selectClip(entry.type, entry.state, entry.carrying);
    this._setClip(clip, entry.type);

    if (tier !== this._tier) { this._applySize(entry.type, tier); this._tier = tier; }

    // Colony tint on the (grayscale) base art. Cache to skip redundant writes.
    // Spitter (type 4): blend team color with acid-green (55% green / 45% team)
    // so RED vs BLUE spitters remain distinguishable while still reading as a
    // distinct type (green cast). blendHex(team, green, 0.55).
    // Raider (type 5): rust-orange cast (aggressive harasser); spitter (4): acid-green.
    const tint = entry.type === 4
      ? blendHex(tintFor(entry.colony), 0x88ee22, 0.55)
      : entry.type === 5
      ? blendHex(tintFor(entry.colony), 0xff8c2a, 0.5)
      : tintFor(entry.colony);
    if (tint !== this._tint && this.body && 'tint' in this.body) {
      this.body.tint = tint; this._tint = tint;
    }

    this._setPellet(entry.carrying);

    // Position (already lerped by the caller).
    this.container.position.set(x, y);

    // Facing: free rotation toward heading; idle keeps last angle (caller passes
    // null angle when not moving). Queen rotation kept subtle (rarely moves).
    if (angle !== null && angle !== undefined) {
      this._lastAngle = angle;
    }
    this.container.rotation = this._lastAngle + ART_FACING_OFFSET;

    this._setHpBar(entry.hp, entry.maxHp, entry.type, tier);
  }

  // Reset to a clean state when recycled from the pool (avoid stale visuals).
  reset() {
    if (this.pellet) { this.pellet.destroy(); this.pellet = null; }
    if (this.hpBar) { this.hpBar.destroy(); this.hpBar = null; }
    this._clip = null;
    this._tier = -1;
    this._tint = -1;
    this._hpKey = '';
    this._lastAngle = 0;
    this.container.rotation = 0;
    this.container.visible = true;
  }

  destroy() {
    this.container.destroy({ children: true });
    this.body = null; this.pellet = null; this.hpBar = null;
  }
}

// --- AntViews (manager for the whole antLayer) -------------------------------

export class AntViews {
  // layer: stage.antLayer. atlas: ants Spritesheet | null. pool: ViewPool.
  // effects: optional { deathFx(x,y,typeIdx,colony) } hook for M4 death FX.
  constructor(layer, atlas, pool, effects = null) {
    this.layer = layer;
    this.atlas = atlas || null;
    this.pool = pool;
    this.effects = effects;
    this.views = new Map();   // ant id -> AntView
  }

  // Per-frame bind: spawn/despawn from id-diff, lerp positions by `t`.
  // store: SnapshotStore. t: interpolation progress in [0,1].
  sync(store, t) {
    // Despawn: ants removed this tick -> death FX hook + recycle to pool.
    for (const id of store.removedAntIds) {
      const v = this.views.get(id);
      if (!v) continue;
      this.views.delete(id);
      if (this.effects && this.effects.deathFx) {
        const p = v.container.position;
        this.effects.deathFx(p.x, p.y, v._typeIdx, undefined);
      }
      v.reset();
      v.container.visible = false;
      this.pool.release(String(v._typeIdx >= 0 ? v._typeIdx : 'x'), v);
      if (v.container.parent) v.container.parent.removeChild(v.container);
    }

    // Spawn + update.
    for (const [id, entry] of store.ants) {
      let v = this.views.get(id);
      if (!v) {
        v = this.pool.acquire(String(entry.type));
        if (v) { v.atlas = this.atlas; v.reset(); }
        else v = new AntView(this.atlas);
        if (!v.container.parent) this.layer.addChild(v.container);
        v.container.visible = true;
        this.views.set(id, v);
      }

      const from = entry.from, to = entry.to;
      // Lerp position (tile units -> world px). Only x/y interpolate (spec §6).
      const wx = (from.x + (to.x - from.x) * t) * TS + TS / 2;
      const wy = (from.y + (to.y - from.y) * t) * TS + TS / 2;

      // Facing from the tick segment heading; null when not moving (keep last).
      const dx = to.x - from.x, dy = to.y - from.y;
      const moved = (dx * dx + dy * dy) > 1e-6;
      let angle = moved ? Math.atan2(dy, dx) : null;
      // The queen is fixed at her nest, so she keeps the default art orientation —
      // which points her head OUTWARD for the left (RED) colony. Force her to face
      // midfield (toward the enemy): RED (colony 0) looks east, BLUE (colony 1) west.
      if (entry.type === 3) angle = entry.colony === 0 ? Math.PI : 0;

      const tier = store.tierForAnt(entry.colony, entry.type);
      v.update(entry, tier, wx, wy, angle);
    }
  }

  // Clear all views (on reset/reconnect) so no ghost sprites remain.
  reset() {
    for (const v of this.views.values()) {
      if (v.container.parent) v.container.parent.removeChild(v.container);
      v.destroy();
    }
    this.views.clear();
  }
}
