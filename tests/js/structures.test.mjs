// Unit tests for SnapshotStore structure + colony diffing (M2).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SnapshotStore, STRUCT, COLONY } from '../../frontend/game/src/state/SnapshotStore.js';

// structure tuple: [x,y,colony,hp,max_hp,type,active,build_progress,build_required]
const s = (x, y, type, active = 1, hp = 100, maxHp = 100, bp = 0, br = 0) =>
  [x, y, 0, hp, maxHp, type, active, bp, br];
// colony tuple subset: [id, nest_x, nest_y, ..., alive@10, ...]
const col = (id, nx, ny, alive = 1) => {
  const c = new Array(17).fill(0);
  c[COLONY.ID] = id; c[COLONY.NEST_X] = nx; c[COLONY.NEST_Y] = ny; c[COLONY.ALIVE] = alive;
  return c;
};
const tick = (n, structures = [], colonies = []) =>
  ({ tick: n, phase: 'running', ants: [], structures, colonies });

test('structure index constants match the contract', () => {
  assert.deepEqual(STRUCT, {
    X: 0, Y: 1, COLONY: 2, HP: 3, MAXHP: 4, TYPE: 5,
    ACTIVE: 6, BUILD_PROGRESS: 7, BUILD_REQUIRED: 8,
  });
});

test('first tick adds structures keyed by x,y,type with justAdded', () => {
  const st = new SnapshotStore();
  st.applyTick(tick(1, [s(45, 50, 'watchtower')]), 1000);
  const e = st.structures.get('45,50,watchtower');
  assert.ok(e);
  assert.equal(e.type, 'watchtower');
  assert.equal(e.colony, 0);
  assert.equal(e.active, true);
  assert.equal(e.justAdded, true);
});

test('type is read as a string name (not an index)', () => {
  const st = new SnapshotStore();
  st.applyTick(tick(1, [s(10, 10, 'guard_post'), s(11, 10, 'larder')]), 1000);
  assert.equal(st.structures.get('10,10,guard_post').type, 'guard_post');
  assert.equal(st.structures.get('11,10,larder').type, 'larder');
});

test('under-construction structure exposes build progress + required', () => {
  const st = new SnapshotStore();
  st.applyTick(tick(1, [s(20, 20, 'barracks', 0, 1, 150, 30, 60)]), 1000);
  const e = st.structures.get('20,20,barracks');
  assert.equal(e.active, false);
  assert.equal(e.buildProgress, 30);
  assert.equal(e.buildRequired, 60);
});

test('updating an existing structure clears justAdded and updates hp', () => {
  const st = new SnapshotStore();
  st.applyTick(tick(1, [s(5, 5, 'wall', 1, 100, 100)]), 1000);
  st.applyTick(tick(2, [s(5, 5, 'wall', 1, 40, 100)]), 2000);
  const e = st.structures.get('5,5,wall');
  assert.equal(e.justAdded, false);
  assert.equal(e.hp, 40);
});

test('removed structure is reported and dropped', () => {
  const st = new SnapshotStore();
  st.applyTick(tick(1, [s(5, 5, 'wall'), s(6, 5, 'wall')]), 1000);
  st.applyTick(tick(2, [s(5, 5, 'wall')]), 2000);
  assert.deepEqual(st.removedStructKeys, ['6,5,wall']);
  assert.equal(st.structures.has('6,5,wall'), false);
});

test('colonies are keyed by id with nest + alive state', () => {
  const st = new SnapshotStore();
  st.applyTick(tick(1, [], [col(0, 14, 50, 1), col(1, 135, 50, 0)]), 1000);
  assert.deepEqual(st.colonies.get(0), { id: 0, nestX: 14, nestY: 50, alive: true, tiers: [0, 0, 0] });
  assert.deepEqual(st.colonies.get(1), { id: 1, nestX: 135, nestY: 50, alive: false, tiers: [0, 0, 0] });
});

test('reset clears structures and colonies', () => {
  const st = new SnapshotStore();
  st.applyTick(tick(1, [s(5, 5, 'wall')], [col(0, 14, 50)]), 1000);
  st.reset();
  assert.equal(st.structures.size, 0);
  assert.equal(st.colonies.size, 0);
  assert.equal(st.removedStructKeys.length, 0);
});
