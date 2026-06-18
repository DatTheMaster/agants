import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SnapshotStore, ANT } from '../../frontend/game/src/state/SnapshotStore.js';

const tick = (n, ants) => ({ tick: n, phase: 'running', ants, structures: [], colonies: [] });
// ant tuple: [id,x,y,prev_x,prev_y,colony,type,state,carrying,hp,max_hp]
const a = (id, x, y, px, py) => [id, x, y, px, py, 0, 0, 0, 0, 55, 55];

test('first tick adds ants with from=prev, to=current, justAdded', () => {
  const s = new SnapshotStore();
  s.applyTick(tick(1, [a(7, 20, 50, 19, 50)]), 1000);
  const e = s.ants.get(7);
  assert.deepEqual(e.from, { x: 19, y: 50 });
  assert.deepEqual(e.to, { x: 20, y: 50 });
  assert.equal(e.justAdded, true);
});

test('second tick updates segment and measures interval', () => {
  const s = new SnapshotStore();
  s.applyTick(tick(1, [a(7, 20, 50, 19, 50)]), 1000);
  s.applyTick(tick(2, [a(7, 21, 50, 20, 50)]), 2000);
  const e = s.ants.get(7);
  assert.deepEqual(e.from, { x: 20, y: 50 });
  assert.deepEqual(e.to, { x: 21, y: 50 });
  assert.equal(e.justAdded, false);
  assert.equal(s.tickIntervalMs, 1000);
});

test('removed ant is reported and dropped', () => {
  const s = new SnapshotStore();
  s.applyTick(tick(1, [a(7, 20, 50, 19, 50), a(8, 30, 50, 29, 50)]), 1000);
  s.applyTick(tick(2, [a(7, 21, 50, 20, 50)]), 2000);
  assert.deepEqual(s.removedAntIds, [8]);
  assert.equal(s.ants.has(8), false);
});

test('interp clamps to [0,1] and is 1 for a late tick', () => {
  const s = new SnapshotStore();
  s.applyTick(tick(1, [a(7, 20, 50, 19, 50)]), 1000);
  s.applyTick(tick(2, [a(7, 21, 50, 20, 50)]), 2000); // interval 1000
  assert.equal(s.interp(2500), 0.5);
  assert.equal(s.interp(9000), 1);   // late: rest at `to`, no overshoot
});

test('reset clears entities and timing', () => {
  const s = new SnapshotStore();
  s.applyTick(tick(1, [a(7, 20, 50, 19, 50)]), 1000);
  s.reset();
  assert.equal(s.ants.size, 0);
  assert.equal(s.removedAntIds.length, 0);
});
