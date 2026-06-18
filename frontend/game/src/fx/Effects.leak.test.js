// Regression test for the Session-49 #1 memory leak: Effects allocated a fresh
// GlowFilter per buildFlash()/queenWarning() and never disposed it — gfx.destroy()
// does NOT destroy attached filters, and Pixi filters hold GPU render-textures, so
// a long siege (queenWarning every queen hit) grew GPU memory without bound.
//
// We mock window.PIXI (Effects needs a browser) and assert that firing MANY FX
// allocates only a BOUNDED set of GlowFilters (reused, not per-FX), and that the
// win-grade ColorMatrixFilter is disposed on reset(). Run: node --test frontend/game/src
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

// --- counters the mock updates so we can assert on filter lifecycle -----------
let glowCreated, cmfCreated, cmfDestroyed;

class MockGraphics {
  constructor() {
    this.position = { set() {} };
    this.scale = { set() {} };
    this.alpha = 1;
    this.filters = null;
    this.parent = null;
    this.destroyed = false;
  }
  circle() { return this; }
  fill() { return this; }
  stroke() { return this; }
  moveTo() { return this; }
  lineTo() { return this; }
  clear() { return this; }
  destroy() { this.destroyed = true; }   // mirrors Pixi: does NOT touch this.filters
}

class MockContainer {
  constructor() { this.children = []; }
  addChild(c) { this.children.push(c); if (c) c.parent = this; return c; }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); }
}

class MockGlowFilter {
  constructor(opts) { this.opts = opts; this.destroyed = false; glowCreated++; }
  destroy() { this.destroyed = true; }
}

class MockColorMatrixFilter {
  constructor() { this.destroyed = false; cmfCreated++; }
  desaturate() {}
  brightness() {}
  destroy() { this.destroyed = true; cmfDestroyed++; }
}

function installPixi() {
  globalThis.window = {
    PIXI: {
      Graphics: MockGraphics,
      Container: MockContainer,
      Rectangle: class { constructor() {} },
      GlowFilter: MockGlowFilter,
      ColorMatrixFilter: MockColorMatrixFilter,
    },
  };
}

let Effects;
beforeEach(async () => {
  glowCreated = cmfCreated = cmfDestroyed = 0;
  installPixi();
  ({ Effects } = await import('./Effects.js'));
});
afterEach(() => { delete globalThis.window; });

function makeEffects() {
  const layers = { ground: new MockContainer(), air: new MockContainer() };
  const world = new MockContainer();
  const store = {
    colonies: new Map([[0, { nestX: 10, nestY: 10 }], [1, { nestX: 140, nestY: 90 }]]),
    structures: new Map(),
    tickIntervalMs: 300,
  };
  return new Effects(layers, world, store, null);
}

test('queenWarning reuses one GlowFilter across many fires (no per-FX leak)', () => {
  const fx = makeEffects();
  for (let i = 0; i < 50; i++) fx.queenWarning(0);   // 50 queen hits in a siege
  // Expire everything.
  fx.update(10_000);
  // The leak: 50 GlowFilters created, none disposed. Fixed: a bounded, reused set.
  assert.ok(glowCreated <= 2, `expected <=2 GlowFilters created, got ${glowCreated}`);
});

test('buildFlash reuses GlowFilters per color, not per FX', () => {
  const fx = makeEffects();
  for (let i = 0; i < 30; i++) {
    fx.buildFlash(0, 0, 0xff3c3c);   // red
    fx.buildFlash(0, 0, 0x3c3cff);   // blue
  }
  fx.update(10_000);
  assert.ok(glowCreated <= 3, `expected <=3 GlowFilters created, got ${glowCreated}`);
});

test('win-grade ColorMatrixFilter is disposed on reset()', () => {
  const fx = makeEffects();
  fx.applyWinGrade(1);
  assert.equal(cmfCreated, 1);
  fx.reset();
  assert.equal(cmfDestroyed, 1, 'win-grade ColorMatrixFilter must be destroyed on reset');
});
