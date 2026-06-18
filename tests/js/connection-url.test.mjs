import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveWsUrl } from '../../frontend/game/src/net/Connection.js';

const loc = (search) => ({ search });

test('replay flag points at local replay server', () => {
  assert.equal(resolveWsUrl(loc('?replay=true'), 'https://api.x/agants'), 'ws://localhost:8765');
});
test('replayUrl override is honored', () => {
  assert.equal(resolveWsUrl(loc('?replay=true&replayUrl=ws://localhost:9000'), 'https://x'),
               'ws://localhost:9000');
});
test('live https backend becomes wss with match path', () => {
  assert.equal(resolveWsUrl(loc('?match=abc'), 'https://api.x/agants'),
               'wss://api.x/agants/ws/abc');
});
test('live http backend becomes ws', () => {
  assert.equal(resolveWsUrl(loc('?match=abc'), 'http://localhost:8083'),
               'ws://localhost:8083/ws/abc');
});
