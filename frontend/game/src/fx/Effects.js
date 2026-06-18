// Effects: all visual FX for the Pixi client. Mostly procedural (Graphics +
// pixi-filters); only uses an fx atlas if one is present (spec §6.5 — fx.json is
// optional, the rest is procedural). (spec §3.2 beam, §6.5, M4)
//
// FX implemented:
//   - hit ring        : expanding circle, fade ~300ms (airFxLayer)
//   - death dissolve  : scale-down + alpha fade one-shot (groundFxLayer)
//   - build dust      : ParticleContainer puffs under a building worker
//   - guard-post beam  : line guard_post -> hit ant, target INFERRED from hp-drop
//                        + range to an enemy guard_post (spec §3.2), fade over a tick
//   - build-complete flash : GlowFilter pulse when a structure goes active
//   - queen-under-attack    : nest glow spike / warning ring when the queen is hit
//   - win-state ColorMatrix : desaturate grade on worldContainer when a colony dies
//
// All FX have bounded lifetimes and are cleared in update(); none leak across a
// reset() (reconnect). Effects never block on missing art.
import { TS } from '../scene/Camera.js';

const COLONY_TINT = { 0: 0xff3c3c, 1: 0x3c3cff };
function tintFor(c) { return COLONY_TINT[c] ?? 0xffffff; }

// Mirror of engine/constants.py GUARD_POST_RANGE (tiles) — client constant per
// spec §3.2 (no sim change; the beam target is inferred, not serialized).
const GUARD_POST_RANGE = 10;
const A_QUEEN = 3;

export class Effects {
  // layers: { ground, air } containers (groundFxLayer / airFxLayer).
  // world: the camera-transformed worldContainer (for the win-state ColorMatrix).
  // store: SnapshotStore (read for guard-post positions + tick interval).
  // fxAtlas: optional fx Spritesheet (dust/spark frames). When absent, dust uses
  //          procedural Graphics puffs in a plain Container (frugal-assets rule).
  constructor(layers, world, store, fxAtlas = null) {
    const PIXI = window.PIXI;
    this.ground = layers.ground;
    this.air = layers.air;
    this.world = world;
    this.store = store;

    // Active timed FX: { kind, gfx, age, life, ... }. Updated every frame.
    this.active = [];

    // Build-dust particles. Use a v8 ParticleContainer ONLY when we have a dust
    // texture to feed it (PC requires addParticle, not addChild). Without an fx
    // atlas, dust is procedural Graphics in a plain Container.
    this._dustTex = fxAtlas?.textures?.['dust.png'] || null;
    this.dust = (this._dustTex && PIXI.ParticleContainer)
      ? this._makeParticleContainer()
      : new PIXI.Container();
    this.ground.addChild(this.dust);
    this.particles = [];   // { particle, vx, vy, age, life }

    // Win-state ColorMatrix on the world root (applied once a colony dies).
    this._winGraded = false;
    this._winColony = null;

    // Build-complete tracking: remember which structures were active so we fire
    // the flash on the active 0->1 transition.
    this._wasActive = new Map();   // structKey -> bool
  }

  _makeParticleContainer() {
    const PIXI = window.PIXI;
    try {
      if (PIXI.ParticleContainer) {
        const pc = new PIXI.ParticleContainer({
          dynamicProperties: { position: true, scale: true, alpha: true },
        });
        // v8 PC skips bounds calc -> give it a generous bounds area.
        pc.boundsArea = new PIXI.Rectangle(0, 0, 4800, 3200);
        return pc;
      }
    } catch (e) { /* fall through */ }
    return new PIXI.Container();
  }

  // --- public: per-tick event intake -----------------------------------------

  // Called once per tick (from onTick) AFTER store.applyTick. Reads the store's
  // hitEvents / removedAntIds / winnerDelta / structures to spawn the right FX.
  onTick(store) {
    // Hit rings + inferred guard-post beams from ant hp-drops.
    for (const h of store.hitEvents) {
      this.hitRing(h.x * TS + TS / 2, h.y * TS + TS / 2, tintFor(h.colony));
      // Queen-under-attack warning (nest glow spike) when a queen is hit.
      if (h.type === A_QUEEN) this.queenWarning(h.colony);
      // Inferred beam: any active enemy guard_post within range of the hit ant.
      this._maybeBeam(store, h);
    }

    // Build-complete flash on active 0->1; build dust under in-progress builds.
    for (const [key, s] of store.structures) {
      const was = this._wasActive.get(key);
      if (was === false && s.active) {
        this.buildFlash(s.x * TS + TS / 2, s.y * TS + TS / 2, tintFor(s.colony));
      }
      if (!s.active) {
        // Light dust while under construction (a few puffs per tick).
        if (Math.random() < 0.6) this.buildDust(s.x * TS + TS / 2, s.y * TS + TS / 2);
      }
      this._wasActive.set(key, !!s.active);
    }
    // Drop tracking for removed structures.
    for (const key of store.removedStructKeys) this._wasActive.delete(key);

    // Win-state grade: a colony died this tick.
    if (store.winnerDelta != null && !this._winGraded) {
      this.applyWinGrade(store.winnerDelta);
    }
  }

  // Death-dissolve hook handed to AntViews (called on despawn).
  // x,y in world px (the view's last lerped position).
  deathFx(x, y, typeIdx, colony) {
    this.deathDissolve(x, y, colony == null ? 0xbbbbbb : tintFor(colony), typeIdx);
  }

  // --- individual FX ----------------------------------------------------------

  hitRing(x, y, color) {
    const PIXI = window.PIXI;
    const g = new PIXI.Graphics();
    g.position.set(x, y);
    this.air.addChild(g);
    this.active.push({ kind: 'ring', gfx: g, age: 0, life: 300, color, r0: TS * 0.15, r1: TS * 0.7 });
  }

  deathDissolve(x, y, color, typeIdx) {
    const PIXI = window.PIXI;
    const g = new PIXI.Graphics();
    g.position.set(x, y);
    const r = TS * 0.3 * (typeIdx === A_QUEEN ? 2 : 1);
    g.circle(0, 0, r).fill({ color, alpha: 0.85 });
    g.circle(0, 0, r * 0.5).fill({ color: 0xffffff, alpha: 0.4 });
    this.ground.addChild(g);
    this.active.push({ kind: 'dissolve', gfx: g, age: 0, life: 320 });
  }

  buildDust(x, y) {
    const PIXI = window.PIXI;
    let p;
    const tex = this._dustTex;
    if (this.dust.addParticle && PIXI.Particle && tex) {
      p = new PIXI.Particle({ texture: tex, x, y, anchorX: 0.5, anchorY: 0.5 });
      this.dust.addParticle(p);
    } else {
      // Fallback: a small Graphics puff in the (plain Container) dust layer.
      p = new PIXI.Graphics().circle(0, 0, 2.2).fill({ color: 0xb8a07a, alpha: 0.8 });
      p.position.set(x, y);
      this.dust.addChild(p);
    }
    const ang = Math.random() * Math.PI * 2;
    const spd = 0.2 + Math.random() * 0.4;
    this.particles.push({
      particle: p, vx: Math.cos(ang) * spd, vy: Math.sin(ang) * spd - 0.3,
      age: 0, life: 500 + Math.random() * 300,
    });
  }

  buildFlash(x, y, color) {
    const PIXI = window.PIXI;
    const g = new PIXI.Graphics();
    g.position.set(x, y);
    g.circle(0, 0, TS * 0.7).fill({ color: 0xffffff, alpha: 0.5 });
    const GlowFilter = PIXI.filters?.GlowFilter || PIXI.GlowFilter;
    if (GlowFilter) {
      try { g.filters = [new GlowFilter({ distance: 14, outerStrength: 2.2, color })]; }
      catch (e) { /* optional */ }
    }
    this.air.addChild(g);
    this.active.push({ kind: 'flash', gfx: g, age: 0, life: 450 });
  }

  queenWarning(colony) {
    const c = this.store.colonies.get(colony);
    if (!c) return;
    const PIXI = window.PIXI;
    const x = c.nestX * TS + TS / 2, y = c.nestY * TS + TS / 2;
    const g = new PIXI.Graphics();
    g.position.set(x, y);
    const tint = tintFor(colony);
    g.circle(0, 0, TS * 2.4).stroke({ color: 0xff3030, width: 3, alpha: 0.8 });
    const GlowFilter = PIXI.filters?.GlowFilter || PIXI.GlowFilter;
    if (GlowFilter) {
      try { g.filters = [new GlowFilter({ distance: 18, outerStrength: 2.5, color: 0xff3030 })]; }
      catch (e) { /* optional */ }
    }
    this.air.addChild(g);
    this.active.push({ kind: 'warn', gfx: g, age: 0, life: 600, r0: TS * 1.8, r1: TS * 3.0, tint });
  }

  // Inferred guard-post beam (spec §3.2): if any active enemy guard_post is in
  // range of the hit ant, draw a beam guard_post -> ant for ~one tick.
  _maybeBeam(store, hit) {
    let best = null, bestD = (GUARD_POST_RANGE + 1) * (GUARD_POST_RANGE + 1);
    for (const s of store.structures.values()) {
      if (s.type !== 'guard_post' || !s.active) continue;
      if (s.colony === hit.colony) continue;  // enemy guard posts only
      const dx = s.x - hit.x, dy = s.y - hit.y;
      const d2 = dx * dx + dy * dy;
      if (d2 <= bestD) { bestD = d2; best = s; }
    }
    if (!best) return;
    this.beam(best.x * TS + TS / 2, best.y * TS + TS / 2,
              hit.x * TS + TS / 2, hit.y * TS + TS / 2, tintFor(best.colony));
  }

  beam(x0, y0, x1, y1, color) {
    const PIXI = window.PIXI;
    const g = new PIXI.Graphics();
    this.air.addChild(g);
    const life = Math.max(120, this.store.tickIntervalMs || 300);
    this.active.push({ kind: 'beam', gfx: g, age: 0, life, x0, y0, x1, y1, color });
  }

  applyWinGrade(colony) {
    const PIXI = window.PIXI;
    const CMF = PIXI.ColorMatrixFilter || PIXI.filters?.ColorMatrixFilter;
    if (!CMF) return;
    try {
      const f = new CMF();
      f.desaturate();
      f.brightness(0.75, true);
      this.world.filters = [f];
      this._winGraded = true;
      this._winColony = colony;
    } catch (e) { /* grade optional */ }
  }

  // --- per-frame update -------------------------------------------------------

  // dtMs: ms since last frame. Advances all FX lifetimes; removes expired ones.
  update(dtMs) {
    const dt = dtMs || 16;
    // Timed Graphics FX.
    for (let i = this.active.length - 1; i >= 0; i--) {
      const fx = this.active[i];
      fx.age += dt;
      const t = Math.min(1, fx.age / fx.life);
      if (fx.kind === 'ring') {
        const r = fx.r0 + (fx.r1 - fx.r0) * t;
        fx.gfx.clear();
        fx.gfx.circle(0, 0, r).stroke({ color: fx.color, width: 2, alpha: 1 - t });
      } else if (fx.kind === 'dissolve') {
        fx.gfx.scale.set(1 - 0.7 * t);
        fx.gfx.alpha = 1 - t;
      } else if (fx.kind === 'flash') {
        fx.gfx.scale.set(1 + 0.6 * t);
        fx.gfx.alpha = 0.5 * (1 - t);
      } else if (fx.kind === 'warn') {
        const r = fx.r0 + (fx.r1 - fx.r0) * t;
        fx.gfx.clear();
        fx.gfx.circle(0, 0, r).stroke({ color: 0xff3030, width: 3, alpha: 0.8 * (1 - t) });
      } else if (fx.kind === 'beam') {
        fx.gfx.clear();
        fx.gfx.moveTo(fx.x0, fx.y0).lineTo(fx.x1, fx.y1)
              .stroke({ color: fx.color, width: 2.5, alpha: 1 - t });
        fx.gfx.moveTo(fx.x0, fx.y0).lineTo(fx.x1, fx.y1)
              .stroke({ color: 0xffffff, width: 1, alpha: 0.8 * (1 - t) });
      }
      if (fx.age >= fx.life) {
        fx.gfx.destroy();
        this.active.splice(i, 1);
      }
    }

    // Particles (build dust).
    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];
      p.age += dt;
      const t = Math.min(1, p.age / p.life);
      const pr = p.particle;
      pr.x += p.vx * dt * 0.06;
      pr.y += p.vy * dt * 0.06;
      if ('alpha' in pr) pr.alpha = 0.8 * (1 - t);
      if (p.age >= p.life) {
        if (this.dust.removeParticle) this.dust.removeParticle(pr);
        else if (pr.destroy) pr.destroy();
        else if (pr.parent) pr.parent.removeChild(pr);
        this.particles.splice(i, 1);
      }
    }
  }

  // Clear all FX (on reset/reconnect). Removes the win grade too.
  reset() {
    for (const fx of this.active) fx.gfx.destroy();
    this.active = [];
    for (const p of this.particles) {
      if (this.dust.removeParticle) this.dust.removeParticle(p.particle);
      else if (p.particle.destroy) p.particle.destroy();
    }
    this.particles = [];
    this._wasActive.clear();
    if (this._winGraded) { this.world.filters = []; this._winGraded = false; this._winColony = null; }
  }
}
