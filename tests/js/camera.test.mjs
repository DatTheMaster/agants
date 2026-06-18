// Unit tests for the pure camera math (screen<->world, clamp, zoom-to-cursor,
// fit). These run without PIXI. (spec §4)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  TS, fitScale, clampScale, screenToWorld, worldToScreen,
  zoomToCursor, clampOffset, fitView,
} from '../../frontend/game/src/scene/Camera.js';

test('TS world basis is 32px', () => {
  assert.equal(TS, 32);
});

test('screenToWorld / worldToScreen are inverse', () => {
  const posX = 100, posY = 50, scale = 2;
  const w = screenToWorld(420, 250, posX, posY, scale);
  assert.deepEqual(w, { x: 160, y: 100 });
  const s = worldToScreen(w.x, w.y, posX, posY, scale);
  assert.deepEqual(s, { x: 420, y: 250 });
});

test('fitScale picks the smaller axis ratio so the whole world fits', () => {
  // world 4800x3200, screen 800x600 -> min(800/4800, 600/3200) = 0.1666.. vs 0.1875
  assert.equal(fitScale(800, 600, 4800, 3200), 800 / 4800);
  assert.equal(fitScale(800, 600, 0, 0), 1);   // degenerate world -> guarded to 1
});

test('clampScale bounds to [min,max]', () => {
  assert.equal(clampScale(0.01, 0.1, 4), 0.1);
  assert.equal(clampScale(10, 0.1, 4), 4);
  assert.equal(clampScale(2, 0.1, 4), 2);
});

test('zoomToCursor keeps the world point under the cursor fixed', () => {
  const posX = 0, posY = 0, scale = 1;
  const cursorX = 300, cursorY = 200;
  // world point under cursor before zoom
  const before = screenToWorld(cursorX, cursorY, posX, posY, scale);
  const z = zoomToCursor(cursorX, cursorY, posX, posY, scale, 2, 0.1, 4);
  assert.equal(z.scale, 2);
  // after applying the new camera, the same world point must map back to the cursor
  const after = worldToScreen(before.x, before.y, z.posX, z.posY, z.scale);
  assert.ok(Math.abs(after.x - cursorX) < 1e-9);
  assert.ok(Math.abs(after.y - cursorY) < 1e-9);
});

test('zoomToCursor respects the zoom clamp', () => {
  const z = zoomToCursor(0, 0, 0, 0, 3.9, 2, 0.1, 4); // 3.9*2=7.8 -> clamp 4
  assert.equal(z.scale, 4);
});

test('clampOffset keeps world edges within viewport + margin', () => {
  // world 4800x3200 at scale 1, screen 800x600, margin 80.
  // posX upper bound = screenW - margin = 720; lower = -(4800 - 80) = -4720.
  const a = clampOffset(5000, 0, 1, 800, 600, 4800, 3200, 80);
  assert.equal(a.x, 720);
  const b = clampOffset(-9999, 0, 1, 800, 600, 4800, 3200, 80);
  assert.equal(b.x, -4720);
  const c = clampOffset(-9999, 9999, 1, 800, 600, 4800, 3200, 80);
  assert.equal(c.y, 600 - 80);          // upper y bound
  const d = clampOffset(100, -9999, 1, 800, 600, 4800, 3200, 80);
  assert.equal(d.y, -(3200 - 80));      // lower y bound
  // in-range value passes through
  const e = clampOffset(-100, -100, 1, 800, 600, 4800, 3200, 80);
  assert.deepEqual(e, { x: -100, y: -100 });
});

test('fitView centers the world in the viewport', () => {
  const v = fitView(800, 600, 4800, 3200);
  assert.equal(v.scale, 800 / 4800);
  // width fills exactly -> posX 0; height leaves vertical letterbox -> posY > 0
  assert.ok(Math.abs(v.posX - (800 - 4800 * v.scale) / 2) < 1e-9);
  assert.ok(Math.abs(v.posX) < 1e-9);
  assert.ok(v.posY > 0);
});
