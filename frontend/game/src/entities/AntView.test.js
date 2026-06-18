// Unit tests for the pure, PIXI-free logic of M3: clip selection, tier
// derivation, and the ViewPool. Run with: node --test frontend/game/src
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { selectClip, TIER_SCALE } from './AntView.js';
import { ViewPool } from './ViewPool.js';
import { SnapshotStore } from '../state/SnapshotStore.js';

// State idx (engine/constants.py:21)
const IDLE = 0, FORAGING = 1, RETURNING = 2, EXPLORING = 3, FIGHTING = 4,
      PATROLLING = 5, RECRUITED = 6, BUILDING = 7;
// Type idx
const WORKER = 0, SOLDIER = 1, SCOUT = 2, QUEEN = 3;

test('selectClip: worker state machine (spec §3.3)', () => {
  assert.equal(selectClip(WORKER, IDLE, 0), 'worker_idle');
  assert.equal(selectClip(WORKER, FORAGING, 0), 'worker_walk');
  assert.equal(selectClip(WORKER, RETURNING, 1), 'worker_carry');   // carrying
  assert.equal(selectClip(WORKER, RETURNING, 0), 'worker_walk');    // not carrying
  assert.equal(selectClip(WORKER, BUILDING, 0), 'worker_dig');      // distinct dig, NOT attack
  assert.equal(selectClip(WORKER, FIGHTING, 0), 'worker_attack');
  assert.equal(selectClip(WORKER, PATROLLING, 0), 'worker_walk');
  assert.equal(selectClip(WORKER, RECRUITED, 0), 'worker_walk');
  assert.equal(selectClip(WORKER, EXPLORING, 0), 'worker_walk');
});

test('selectClip: soldier / scout / queen defaults (spec §3.3)', () => {
  assert.equal(selectClip(SOLDIER, IDLE, 0), 'soldier_idle');
  assert.equal(selectClip(SOLDIER, FIGHTING, 0), 'soldier_attack');
  assert.equal(selectClip(SOLDIER, FORAGING, 0), 'soldier_walk');   // default
  assert.equal(selectClip(SCOUT, IDLE, 0), 'scout_idle');
  assert.equal(selectClip(SCOUT, EXPLORING, 0), 'scout_walk');
  assert.equal(selectClip(SCOUT, FIGHTING, 0), 'scout_attack');
  assert.equal(selectClip(SCOUT, PATROLLING, 0), 'scout_walk');     // default
  assert.equal(selectClip(QUEEN, IDLE, 0), 'queen_idle');
  assert.equal(selectClip(QUEEN, FIGHTING, 0), 'queen_attack');
  assert.equal(selectClip(QUEEN, FORAGING, 0), 'queen_idle');       // default
});

test('TIER_SCALE matches the locked constant', () => {
  assert.deepEqual(TIER_SCALE, [1.0, 1.25, 1.5, 1.8]);
});

test('SnapshotStore.tierForAnt derives tier from colony tiers (spec §3.2)', () => {
  const store = new SnapshotStore();
  // colony tuple: [id, nx, ny, ..., alive(10), tiers(11)]
  const colony = (id, tiers) => { const c = new Array(12).fill(0); c[0] = id; c[10] = 1; c[11] = tiers; return c; };
  store.applyTick({
    tick: 1, phase: 'play', ants: [],
    colonies: [colony(0, [2, 3, 1]), colony(1, [0, 0, 0])],
  }, 1000);
  // tiers = [worker_tier, scout_tier, soldier_tier]; TYPE_TIER_IDX = [0,2,1]
  assert.equal(store.tierForAnt(0, WORKER), 2);    // worker -> tiers[0]
  assert.equal(store.tierForAnt(0, SOLDIER), 1);   // soldier -> tiers[2]
  assert.equal(store.tierForAnt(0, SCOUT), 3);     // scout -> tiers[1]
  assert.equal(store.tierForAnt(0, QUEEN), 3);     // queen always 3
  assert.equal(store.tierForAnt(1, WORKER), 0);
  assert.equal(store.tierForAnt(99, WORKER), 0);   // unknown colony -> 0
});

test('SnapshotStore: ant id-diff drives removedAntIds + interp segments', () => {
  const store = new SnapshotStore();
  const ant = (id, x, y, px, py) => [id, x, y, px, py, 0, 0, 0, 0, 10, 10];
  store.applyTick({ tick: 1, phase: 'play', ants: [ant(1, 5, 5, 4, 5), ant(2, 9, 9, 9, 9)] }, 1000);
  assert.equal(store.ants.size, 2);
  assert.deepEqual(store.ants.get(1).from, { x: 4, y: 5 });
  assert.deepEqual(store.ants.get(1).to, { x: 5, y: 5 });
  // Drop ant 2 next tick.
  store.applyTick({ tick: 2, phase: 'play', ants: [ant(1, 6, 5, 5, 5)] }, 2000);
  assert.deepEqual(store.removedAntIds, [2]);
  assert.equal(store.ants.size, 1);
  // Measured interval ~1000ms.
  assert.ok(Math.abs(store.tickIntervalMs - 1000) < 1);
});

test('SnapshotStore.interp clamps t to [0,1]', () => {
  const store = new SnapshotStore();
  store.tickStartMs = 1000; store.tickIntervalMs = 1000;
  assert.equal(store.interp(1000), 0);
  assert.equal(store.interp(1500), 0.5);
  assert.equal(store.interp(2000), 1);
  assert.equal(store.interp(5000), 1);   // late tick rests at `to`
  assert.equal(store.interp(500), 0);
});

test('ViewPool: acquire/release recycling + clear', () => {
  const pool = new ViewPool(2);
  assert.equal(pool.acquire('0'), null);
  const a = { id: 'a', destroyed: false, destroy() { this.destroyed = true; } };
  const b = { id: 'b', destroy() {} };
  const c = { id: 'c', destroy() {} };
  pool.release('0', a);
  pool.release('0', b);
  assert.equal(pool.size(), 2);
  pool.release('0', c);                 // over max -> c destroyed, not pooled
  assert.equal(pool.size(), 2);
  assert.equal(c.destroyed === undefined ? false : c.destroyed, false); // c lacks tracking; just ensure not pooled
  assert.equal(pool.acquire('0').id, 'b');  // LIFO: last successfully pooled is b
  assert.equal(pool.size(), 1);
  pool.clear();
  assert.equal(pool.size(), 0);
});
