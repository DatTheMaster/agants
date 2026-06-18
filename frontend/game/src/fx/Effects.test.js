// Unit tests for the PIXI-free logic feeding M4 FX/nodes: SnapshotStore now emits
// hit events (hp-drop), stores resource nodes, and reports a winnerDelta when a
// colony dies. These are the signals Effects/NodeView/Hud consume. The Effects /
// NodeView / Hud classes themselves require window.PIXI (browser) and are verified
// via the replay harness; here we lock the data contract they depend on.
// Run with: node --test frontend/game/src
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SnapshotStore } from '../state/SnapshotStore.js';

// ant tuple: [id,x,y,prev_x,prev_y,colony,type,state,carrying,hp,max_hp]
const ant = (id, x, y, colony, hp, type = 0) => [id, x, y, x, y, colony, type, 0, 0, hp, 100];
// struct tuple: [x,y,colony,hp,max_hp,type,active,build_progress,build_required]
const struct = (x, y, colony, type, active = 1) => [x, y, colony, 100, 100, type, active, 0, 0];
const tick = (n, extra = {}) => ({
  tick: n, phase: 'running', ants: [], structures: [], colonies: [],
  food: [], dirt: [], corpses: [], ...extra,
});

test('hitEvents: empty on first sighting, fires when hp drops', () => {
  const s = new SnapshotStore();
  s.applyTick(tick(1, { ants: [ant(5, 10, 10, 0, 80)] }), 1000);
  assert.equal(s.hitEvents.length, 0);            // no previous hp to compare
  s.applyTick(tick(2, { ants: [ant(5, 10, 10, 0, 60)] }), 2000);
  assert.equal(s.hitEvents.length, 1);
  assert.deepEqual(
    { id: s.hitEvents[0].id, prevHp: s.hitEvents[0].prevHp, hp: s.hitEvents[0].hp },
    { id: 5, prevHp: 80, hp: 60 });
});

test('hitEvents: no event when hp is unchanged or heals', () => {
  const s = new SnapshotStore();
  s.applyTick(tick(1, { ants: [ant(5, 10, 10, 0, 60)] }), 1000);
  s.applyTick(tick(2, { ants: [ant(5, 10, 10, 0, 60)] }), 2000);
  assert.equal(s.hitEvents.length, 0);
  s.applyTick(tick(3, { ants: [ant(5, 10, 10, 0, 90)] }), 3000);
  assert.equal(s.hitEvents.length, 0);
});

test('hitEvents reset each tick', () => {
  const s = new SnapshotStore();
  s.applyTick(tick(1, { ants: [ant(5, 10, 10, 0, 80)] }), 1000);
  s.applyTick(tick(2, { ants: [ant(5, 10, 10, 0, 60)] }), 2000);
  assert.equal(s.hitEvents.length, 1);
  s.applyTick(tick(3, { ants: [ant(5, 10, 10, 0, 60)] }), 3000);
  assert.equal(s.hitEvents.length, 0);
});

test('resource nodes are stored from the snapshot', () => {
  const s = new SnapshotStore();
  s.applyTick(tick(1, {
    food: [[3, 4, 200, 'seeds', 'home']],
    dirt: [[5, 6, 100, 'frontline']],
    corpses: [[7, 8, 12]],
  }), 1000);
  assert.equal(s.food[0][3], 'seeds');
  assert.equal(s.dirt[0][3], 'frontline');
  assert.equal(s.corpses[0][2], 12);
});

test('winnerDelta fires once when a colony transitions alive 1->0', () => {
  // colony tuple subset: id at 0, alive at idx 10.
  const col = (id, alive) => {
    const c = new Array(12).fill(0);
    c[0] = id; c[1] = 0; c[2] = 0; c[10] = alive ? 1 : 0; c[11] = [0, 0, 0];
    return c;
  };
  const s = new SnapshotStore();
  s.applyTick(tick(1, { colonies: [col(0, true), col(1, true)] }), 1000);
  assert.equal(s.winnerDelta, null);
  s.applyTick(tick(2, { colonies: [col(0, true), col(1, false)] }), 2000);
  assert.equal(s.winnerDelta, 1);                 // colony 1 just died
  s.applyTick(tick(3, { colonies: [col(0, true), col(1, false)] }), 3000);
  assert.equal(s.winnerDelta, null);              // no new transition
});

test('reset clears nodes + hit events + winner state', () => {
  const s = new SnapshotStore();
  s.applyTick(tick(1, { ants: [ant(5, 10, 10, 0, 80)], food: [[1, 1, 5, 'leaf', 'home']] }), 1000);
  s.reset();
  assert.equal(s.food.length, 0);
  assert.equal(s.dirt.length, 0);
  assert.equal(s.corpses.length, 0);
  assert.equal(s.hitEvents.length, 0);
  assert.equal(s.winnerDelta, null);
});
