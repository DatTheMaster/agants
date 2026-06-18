# Graphics Overhaul — M0 Foundation + M0.5 Replay Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a PixiJS v8 render-client skeleton mounted behind a `?pixi=1` flag (the existing Canvas renderer stays the default), plus an offline tick-replay harness so every later milestone is built and verified locally with zero production dependency.

**Architecture:** New ES-module client under `frontend/game/src/` reads `window.PIXI` (vendored UMD). `Connection` opens the WebSocket (live OR the local replay server), `SnapshotStore` is the single source of truth (id-diffs entities, builds interpolation segments), `Stage` builds the Pixi layer graph and mounts the canvas into `#stage`, `Loop` runs the 60fps ticker. The replay harness (`tools/replay/`) captures a *local dev* match's WS stream to JSONL and re-emits it at the recorded cadence on `ws://localhost:8765`.

**Tech Stack:** PixiJS v8 (vendored UMD), vanilla ES modules (no bundler), Node's built-in `node:test` for pure-logic unit tests, Python 3 + `websockets` for the replay harness.

## Global Constraints

- **No bundler in the deploy path** — author native `<script type="module">` ESM reading global `window.PIXI`; vendor Pixi UMD under `frontend/game/vendor/`. Verbatim from spec §2.1.
- **Non-destructive:** the new client loads ONLY when the page URL has `?pixi=1`; without it, the existing Canvas renderer in `frontend/game/index.html` runs unchanged. (M0 must not regress the live page.)
- **No Python / sim changes.** `engine/`, `server.py`, `mcp_server.py`, `controller/` are untouched. `World.serialize_tick()` (`engine/world.py`) is a fixed contract.
- **No production deploy, no live-server contact, no `git push` without explicit user approval.** A real player may be live. The replay harness captures from a LOCAL dev match only (`python3 server.py` on localhost), NEVER `api.datthemaster.com`.
- **Ant tuple layout (fixed contract, from `World.serialize_tick`):** `[id, x, y, prev_x, prev_y, colony, type, state, carrying, hp, max_hp]` — indices 0..10. Interpolation `from=(prev_x,prev_y)`, `to=(x,y)`.
- **Pin one PixiJS v8.x** for core + `pixi-filters`; record the exact version in `tools/assetgen/pixellab_validated.md`.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `frontend/game/vendor/pixi.min.js` | Vendored PixiJS v8 UMD (exposes `window.PIXI`) | New (downloaded) |
| `frontend/game/vendor/pixi-filters.min.js` | Vendored pixi-filters UMD | New (downloaded) |
| `frontend/game/src/state/SnapshotStore.js` | Single source of truth: id-diff ants/structures/nodes, build from→to interpolation segments, tick timing, reset on reconnect | New |
| `frontend/game/src/net/Connection.js` | Resolve WS URL (live vs `?replay`), connect/reconnect, parse messages, hand snapshots to a callback | New |
| `frontend/game/src/scene/Stage.js` | Build the Pixi layer container graph; mount `app.canvas` into `#stage` | New |
| `frontend/game/src/render/Loop.js` | Single ticker callback: compute interpolation `t` from measured tick interval | New |
| `frontend/game/src/main.js` | Bootstrap: `app.init()`, wire Stage+Store+Connection+Loop, keep DOM `updateSidebar` | New |
| `frontend/game/index.html` | Add `?pixi=1` branch that loads the module client; otherwise unchanged | Modify |
| `tools/replay/capture.py` | Connect to a LOCAL dev match WS, record each message + recv-timestamp to `match.jsonl` | New |
| `tools/replay/server.py` | Replay `match.jsonl` over `ws://localhost:8765` at recorded cadence; `?delay=N` to inject a late tick | New |
| `tests/js/snapshotstore.test.mjs` | `node:test` unit tests for SnapshotStore | New |
| `tests/js/connection-url.test.mjs` | `node:test` unit tests for WS URL resolution | New |

---

## Task 1: Vendor PixiJS v8 UMD

**Files:**
- Create: `frontend/game/vendor/pixi.min.js`, `frontend/game/vendor/pixi-filters.min.js`
- Create: `frontend/game/vendor/README.md` (records pinned version + source URL)

**Interfaces:**
- Produces: global `window.PIXI` (v8) when the file is loaded via `<script src>`.

- [ ] **Step 1: Download the pinned UMD builds**

```bash
mkdir -p frontend/game/vendor
# Pin a single v8.x (record the exact version you fetch in the README + pixellab_validated.md)
curl -fsSL https://pixijs.download/v8.6.6/pixi.min.js -o frontend/game/vendor/pixi.min.js
curl -fsSL https://github.com/pixijs/filters/releases/download/v6.0.5/pixi-filters.min.js -o frontend/game/vendor/pixi-filters.min.js || \
  echo "if that asset URL 404s, fetch the matching pixi-filters UMD for the chosen Pixi v8.x and update README"
```

- [ ] **Step 2: Verify the file is a real UMD bundle (not an error page)**

Run: `head -c 80 frontend/game/vendor/pixi.min.js; echo; wc -c frontend/game/vendor/pixi.min.js`
Expected: minified JS (starts with `/*!` or `!function`), size > 300 KB. If it's HTML/tiny, the URL was wrong — fix the version.

- [ ] **Step 3: Verify it exposes `window.PIXI` in a browser**

Create a throwaway `frontend/game/vendor/_probe.html`:
```html
<!doctype html><script src="./pixi.min.js"></script>
<script>document.title = 'PIXI ' + (window.PIXI && PIXI.VERSION)</script>
```
Run: `cd frontend && python3 -m http.server 8090` then open `http://localhost:8090/game/vendor/_probe.html`; the tab title shows `PIXI 8.x.x`. Delete `_probe.html` after.

- [ ] **Step 4: Record the version**

Write `frontend/game/vendor/README.md` with the exact Pixi + pixi-filters versions and source URLs. Append the same versions to `tools/assetgen/pixellab_validated.md` under a "Pinned versions" line.

- [ ] **Step 5: Commit**

```bash
git add frontend/game/vendor/ tools/assetgen/pixellab_validated.md
git commit -m "feat(graphics): vendor PixiJS v8 UMD + pixi-filters"
```

---

## Task 2: SnapshotStore (id-diff + interpolation segments)

**Files:**
- Create: `frontend/game/src/state/SnapshotStore.js`
- Test: `tests/js/snapshotstore.test.mjs`

**Interfaces:**
- Consumes: tick snapshot objects shaped like `World.serialize_tick()` output (`{tick, phase, ants:[[...11 fields]], structures, colonies, ...}`).
- Produces:
  - `new SnapshotStore()`
  - `store.applyTick(snapshot, nowMs)` → updates internal maps; records `tickStartMs=nowMs` and measures `tickIntervalMs` from the previous applyTick.
  - `store.reset()` → clears all entity maps + timing (called on WS (re)connect/`init`).
  - `store.ants` → `Map<id, {from:{x,y}, to:{x,y}, colony, type, state, carrying, hp, maxHp, justAdded:bool}>`
  - `store.removedAntIds` → array of ids removed in the last applyTick (for death FX/pooling later)
  - `store.interp(nowMs)` → `0..1` clamped progress for the current segment.
  - Named index constants exported: `ANT = {ID:0,X:1,Y:2,PX:3,PY:4,COLONY:5,TYPE:6,STATE:7,CARRYING:8,HP:9,MAXHP:10}`

- [ ] **Step 1: Write the failing tests**

```js
// tests/js/snapshotstore.test.mjs
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test tests/js/snapshotstore.test.mjs`
Expected: FAIL — `Cannot find module .../SnapshotStore.js`.

- [ ] **Step 3: Implement SnapshotStore**

```js
// frontend/game/src/state/SnapshotStore.js
export const ANT = { ID:0, X:1, Y:2, PX:3, PY:4, COLONY:5, TYPE:6, STATE:7, CARRYING:8, HP:9, MAXHP:10 };

export class SnapshotStore {
  constructor() {
    this.ants = new Map();
    this.removedAntIds = [];
    this.tick = 0;
    this.phase = 'lobby';
    this.tickStartMs = 0;
    this.tickIntervalMs = 1000; // sensible default until measured
    this._lastStartMs = 0;
  }

  reset() {
    this.ants.clear();
    this.removedAntIds = [];
    this.tick = 0;
    this.tickStartMs = 0;
    this._lastStartMs = 0;
    this.tickIntervalMs = 1000;
  }

  applyTick(snap, nowMs) {
    this.tick = snap.tick;
    this.phase = snap.phase;
    if (this._lastStartMs) {
      const dt = nowMs - this._lastStartMs;
      if (dt > 0) this.tickIntervalMs = dt;
    }
    this._lastStartMs = nowMs;
    this.tickStartMs = nowMs;

    const seen = new Set();
    for (const t of (snap.ants || [])) {
      const id = t[ANT.ID];
      seen.add(id);
      const existing = this.ants.get(id);
      const entry = existing || { justAdded: true };
      entry.from = { x: t[ANT.PX], y: t[ANT.PY] };
      entry.to = { x: t[ANT.X], y: t[ANT.Y] };
      entry.colony = t[ANT.COLONY];
      entry.type = t[ANT.TYPE];
      entry.state = t[ANT.STATE];
      entry.carrying = !!t[ANT.CARRYING];
      entry.hp = t[ANT.HP];
      entry.maxHp = t[ANT.MAXHP];
      if (existing) entry.justAdded = false;
      this.ants.set(id, entry);
    }
    this.removedAntIds = [];
    for (const id of this.ants.keys()) {
      if (!seen.has(id)) this.removedAntIds.push(id);
    }
    for (const id of this.removedAntIds) this.ants.delete(id);
  }

  interp(nowMs) {
    const t = (nowMs - this.tickStartMs) / Math.max(1, this.tickIntervalMs);
    return t < 0 ? 0 : t > 1 ? 1 : t;
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test tests/js/snapshotstore.test.mjs`
Expected: PASS — 5/5.

- [ ] **Step 5: Commit**

```bash
git add frontend/game/src/state/SnapshotStore.js tests/js/snapshotstore.test.mjs
git commit -m "feat(graphics): SnapshotStore with id-diff + interpolation segments"
```

---

## Task 3: Connection (WS URL resolution + connect)

**Files:**
- Create: `frontend/game/src/net/Connection.js`
- Test: `tests/js/connection-url.test.mjs`

**Interfaces:**
- Consumes: page `location` (search params), the live WS base from `window.AGANTS_BACKEND` (existing global; see `frontend/game/index.html`).
- Produces:
  - `resolveWsUrl(location, backend)` → string. If `?replay=true` → `ws://localhost:8765` (or `&replayUrl=` override). Else convert `backend` http(s)→ws(s) + `/ws/<matchId>` when `?match=<id>` present, mirroring current logic.
  - `class Connection { constructor(url, handlers); connect(); }` where `handlers = {onMap, onTick, onReset}`. On open after a reconnect it calls `handlers.onReset()`.

- [ ] **Step 1: Write the failing test (pure URL logic only — no live socket)**

```js
// tests/js/connection-url.test.mjs
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/js/connection-url.test.mjs`
Expected: FAIL — module/export missing.

- [ ] **Step 3: Implement Connection (URL resolver pure + socket wrapper)**

```js
// frontend/game/src/net/Connection.js
export function resolveWsUrl(location, backend) {
  const p = new URLSearchParams(location.search || '');
  if (p.get('replay') === 'true') {
    return p.get('replayUrl') || 'ws://localhost:8765';
  }
  const ws = String(backend || '').replace(/^http/, 'ws');
  const match = p.get('match');
  return match ? `${ws}/ws/${match}` : ws;
}

export class Connection {
  constructor(url, handlers = {}) {
    this.url = url;
    this.handlers = handlers;
    this.ws = null;
    this._opened = false;
  }
  connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      if (this._opened) this.handlers.onReset?.();   // reconnect → clear ghosts
      this._opened = true;
    };
    this.ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === 'map' || m.type === 'init' || m.map) this.handlers.onMap?.(m);
      else this.handlers.onTick?.(m);   // tick dict (serialize_tick)
    };
    this.ws.onclose = () => setTimeout(() => this.connect(), 1000); // simple reconnect
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/js/connection-url.test.mjs`
Expected: PASS — 4/4. (`URLSearchParams`/`URL` exist in Node; `WebSocket` is only referenced inside `connect()`, never called by the test.)

- [ ] **Step 5: Commit**

```bash
git add frontend/game/src/net/Connection.js tests/js/connection-url.test.mjs
git commit -m "feat(graphics): Connection WS url resolution (live + replay)"
```

---

## Task 4: Replay capture (local dev match → JSONL)

**Files:**
- Create: `tools/replay/capture.py`
- Create: `tools/replay/README.md`

**Interfaces:**
- Produces: `tools/replay/match.jsonl` — one JSON object per line: `{"t": <recv_epoch_float>, "msg": <raw message string>}`, in arrival order, including the initial map/lobby message and every tick.

- [ ] **Step 1: Write capture.py**

```python
# tools/replay/capture.py
"""Record a LOCAL dev match's WebSocket stream to match.jsonl.
NEVER point this at production. Usage:
  python3 server.py                      # in another shell (local dev game server, :8083)
  python3 tools/replay/capture.py --base http://localhost:8083 --seconds 90
"""
import argparse, asyncio, json, time, urllib.request
import websockets  # pip install websockets

def _post(url, body):
    r = urllib.request.Request(url, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(r, timeout=15).read())

async def main(base, seconds):
    assert "datthemaster.com" not in base, "refusing to capture from production"
    m = _post(f"{base}/api/matches", {"config": {"brains": {"0": "bot", "1": "bot"}}})
    mid = m["match_id"]
    ws_url = base.replace("http", "ws") + f"/ws/{mid}"
    tok = _post(f"{base}/api/matches/{mid}/seat/0", {"agent_name": "capture"})["token"]
    _post(f"{base}/api/matches/{mid}/control", {"action": "start"})
    print(f"capturing {ws_url} for {seconds}s -> tools/replay/match.jsonl")
    out = open("tools/replay/match.jsonl", "w", buffering=1)
    deadline = time.time() + seconds
    async with websockets.connect(ws_url) as ws:
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            out.write(json.dumps({"t": time.time(), "msg": raw}) + "\n")
    out.close()
    print("done")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8083")
    ap.add_argument("--seconds", type=int, default=90)
    a = ap.parse_args()
    asyncio.run(main(a.base, a.seconds))
```

- [ ] **Step 2: Capture a real local sample**

Run (two shells):
```bash
# shell A
python3 server.py
# shell B
pip install websockets
python3 tools/replay/capture.py --base http://localhost:8083 --seconds 90
```
Expected: `tools/replay/match.jsonl` exists, > 50 lines, first line contains a `map`/`lobby` message, later lines contain `"ants"`.
Verify: `wc -l tools/replay/match.jsonl; head -1 tools/replay/match.jsonl | head -c 120`

- [ ] **Step 3: Document + gitignore the capture**

Write `tools/replay/README.md` (how to capture/replay; "local only, never production"). Add `tools/replay/match.jsonl` to `.gitignore` (it's a large local artifact, not source).

- [ ] **Step 4: Commit**

```bash
git add tools/replay/capture.py tools/replay/README.md .gitignore
git commit -m "feat(replay): local dev-match WS capture to JSONL"
```

---

## Task 5: Replay server (JSONL → ws://localhost:8765 at recorded cadence)

**Files:**
- Create: `tools/replay/server.py`

**Interfaces:**
- Consumes: `tools/replay/match.jsonl` (from Task 4).
- Produces: a WS server on `ws://localhost:8765` that, per client connection, re-emits each `msg` at the recorded inter-arrival cadence, looping at EOF. `?delay=<ms>` (query on the WS URL) injects an extra sleep before one mid-stream tick to test late-tick handling.

- [ ] **Step 1: Write server.py**

```python
# tools/replay/server.py
"""Replay tools/replay/match.jsonl over ws://localhost:8765 at the recorded cadence.
  python3 tools/replay/server.py
Then load the client with ?replay=true (Connection points at ws://localhost:8765)."""
import asyncio, json
import websockets  # pip install websockets

def load():
    rows = []
    with open("tools/replay/match.jsonl") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows

async def handler(ws):
    rows = load()
    if not rows:
        await ws.close(); return
    qs = getattr(ws, "request", None)
    delay_ms = 0
    try:
        path = ws.request.path if hasattr(ws, "request") else ""
        if "delay=" in path:
            delay_ms = int(path.split("delay=")[1].split("&")[0])
    except Exception:
        delay_ms = 0
    while True:  # loop forever
        prev_t = rows[0]["t"]
        for i, row in enumerate(rows):
            gap = max(0.0, row["t"] - prev_t)
            prev_t = row["t"]
            await asyncio.sleep(gap if gap < 5 else 1.0)  # cap pathological gaps
            if delay_ms and i == len(rows) // 2:
                await asyncio.sleep(delay_ms / 1000.0)     # inject one late tick
            try:
                await ws.send(row["msg"])
            except websockets.ConnectionClosed:
                return

async def main():
    print("replay server on ws://localhost:8765 (loops match.jsonl)")
    async with websockets.serve(handler, "localhost", 8765):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify it serves the recorded stream**

Run (two shells):
```bash
# shell A
python3 tools/replay/server.py
# shell B — tiny probe client
python3 - <<'PY'
import asyncio, websockets, json
async def go():
    async with websockets.connect("ws://localhost:8765") as ws:
        for _ in range(5):
            print(json.loads(await ws.recv()).get("type", "tick"))
asyncio.run(go())
PY
```
Expected: prints 5 message types (first `map`/`lobby`, then ticks), arriving spaced out (not all at once).

- [ ] **Step 3: Commit**

```bash
git add tools/replay/server.py
git commit -m "feat(replay): cadence-accurate JSONL replay server with late-tick injection"
```

---

## Task 6: Stage + main + `?pixi=1` mount (non-destructive)

**Files:**
- Create: `frontend/game/src/scene/Stage.js`
- Create: `frontend/game/src/render/Loop.js`
- Create: `frontend/game/src/main.js`
- Modify: `frontend/game/index.html` (add the `?pixi=1` branch only)

**Interfaces:**
- Consumes: `window.PIXI`, `SnapshotStore`, `Connection`, `resolveWsUrl`, `window.AGANTS_BACKEND`.
- Produces: `Stage` with named layers (`worldContainer`, `antLayer`, `uiLayer`, …) and `mount(app, el)`; `Loop.start(app, store, onFrame)`; `main()` bootstrap. On `?pixi=1` the Pixi canvas mounts into `#stage`; without it nothing in `src/` loads.

- [ ] **Step 1: Implement Stage.js (layer graph + mount)**

```js
// frontend/game/src/scene/Stage.js
const { Container } = window.PIXI;
export class Stage {
  constructor() {
    this.world = new Container();        // camera will transform this in M1
    this.world.isRenderGroup = true;
    this.terrainLayer = new Container();
    this.structureLayer = new Container();
    this.antLayer = new Container(); this.antLayer.cullableChildren = true;
    this.fxLayer = new Container();
    this.fogLayer = new Container();
    this.world.addChild(this.terrainLayer, this.structureLayer, this.antLayer,
                        this.fxLayer, this.fogLayer);
    this.uiLayer = new Container();      // screen space, never camera-transformed
  }
  attach(app) { app.stage.addChild(this.world, this.uiLayer); }
  mount(app, el) { el.innerHTML = ''; el.appendChild(app.canvas); }
}
```

- [ ] **Step 2: Implement Loop.js (ticker → interpolation progress)**

```js
// frontend/game/src/render/Loop.js
export class Loop {
  start(app, store, onFrame) {
    app.ticker.add(() => {
      const t = store.interp(performance.now());
      onFrame(t);                        // M1+ uses t to lerp sprite positions
    });
  }
}
```

- [ ] **Step 3: Implement main.js (bootstrap)**

```js
// frontend/game/src/main.js
import { SnapshotStore } from './state/SnapshotStore.js';
import { Connection, resolveWsUrl } from './net/Connection.js';
import { Stage } from './scene/Stage.js';
import { Loop } from './render/Loop.js';

export async function main() {
  const PIXI = window.PIXI;
  const app = new PIXI.Application();
  await app.init({ resizeTo: window, antialias: false, roundPixels: true,
                   background: 0x0b0d10 });
  if (PIXI.TextureSource?.defaultOptions) PIXI.TextureSource.defaultOptions.scaleMode = 'nearest';

  const stage = new Stage();
  stage.attach(app);
  stage.mount(app, document.getElementById('stage'));

  const store = new SnapshotStore();
  const url = resolveWsUrl(window.location, window.AGANTS_BACKEND);
  const conn = new Connection(url, {
    onReset: () => store.reset(),
    onMap:   (m) => { window.__lastMap = m; },          // M1 consumes this
    onTick:  (snap) => { store.applyTick(snap, performance.now());
                         window.updateSidebar?.(snap); }, // reuse existing DOM HUD
  });
  conn.connect();

  new Loop().start(app, store, (_t) => { /* M1 binds sprites here */ });
  window.__agants = { app, stage, store, conn };          // debug handle
  console.log('[pixi] client mounted; ws=', url);
}
main();
```

- [ ] **Step 4: Add the `?pixi=1` branch to index.html (non-destructive)**

In `frontend/game/index.html`, immediately before the existing `<script>` that boots the Canvas renderer, add:
```html
<script>
  // Opt-in Pixi client: ?pixi=1 loads the new ESM renderer and SKIPS the Canvas path.
  window.__USE_PIXI = new URLSearchParams(location.search).get('pixi') === '1';
</script>
<script>
  if (window.__USE_PIXI) {
    const s1 = document.createElement('script'); s1.src = './vendor/pixi.min.js';
    s1.onload = () => {
      const s2 = document.createElement('script'); s2.type = 'module'; s2.src = './src/main.js';
      document.body.appendChild(s2);
    };
    document.body.appendChild(s1);
  }
</script>
```
Then guard the existing Canvas boot so it does nothing when Pixi is active: wrap its entry (e.g. the `render()` start / `connect()` call) in `if (!window.__USE_PIXI) { ... }`. Make the SMALLEST change that prevents the Canvas path from running under `?pixi=1` — do not delete Canvas code.

- [ ] **Step 5: Verify both paths (manual, local, replay)**

Run:
```bash
python3 tools/replay/server.py          # shell A
cd frontend && python3 -m http.server 8090   # shell B
```
- Open `http://localhost:8090/game/?pixi=1&replay=true` → console shows `[pixi] client mounted`, a dark Pixi canvas fills `#stage`, the DOM sidebar updates as replay ticks arrive, FPS is smooth, **no Canvas-renderer errors**.
- Open `http://localhost:8090/game/` (no flag) → the original Canvas renderer still runs unchanged (regression check).

- [ ] **Step 6: Commit**

```bash
git add frontend/game/src/ frontend/game/index.html
git commit -m "feat(graphics): Pixi client scaffold mounted behind ?pixi=1 (non-destructive)"
```

---

## Definition of Done (M0 + M0.5)

- `node --test tests/js/` is green (SnapshotStore + Connection URL).
- `tools/replay/` captures a local dev match and replays it at cadence on `ws://localhost:8765`.
- `http://localhost:8090/game/?pixi=1&replay=true` mounts the Pixi canvas, drives `SnapshotStore` from replayed data, updates the DOM HUD, and runs at 60fps — with **zero production contact**.
- `http://localhost:8090/game/` (no flag) renders exactly as before (no regression).
- Nothing pushed/deployed.

## Self-Review notes (author)
- Spec coverage: this plan implements spec §2.1 (CF-Worker mount, vendored UMD, no bundler), §2.2 modules (Connection/SnapshotStore/Stage/Loop/main — Camera/TileGrid/entity views are M1+), §3.1 data-flow + interpolation, §7 M0 + M0.5, §8 replay harness. Out of scope here (own plans): M1 terrain+camera, M2 structures, M3 ants+animation, M4 FX+HUD, M5 asset finalization.
- Types consistent: `ANT` indices, `applyTick(snap, nowMs)`, `interp(nowMs)`, `resolveWsUrl(location, backend)`, `Stage.attach/mount`, `Loop.start` used identically across tasks.
- No placeholders: every code step has full code; verification steps have exact commands + expected output.
