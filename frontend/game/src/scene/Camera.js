// Camera: pan / zoom / clamp / zoom-to-cursor for the camera-transformed
// worldContainer. screen<->world conversion + clamp math are pure functions so
// they can be unit-tested under node:test without PIXI. (spec §4)
//
// World extent is MW*TS x MH*TS pixels (tile units * TS). The camera sets
// worldContainer.position (screen offset) and worldContainer.scale (uniform).

// World-pixels per tile. The server reports ant positions in TILE units; we
// render at a fixed 32px tile basis for crisp pixel-art texels (spec §5.2),
// independent of the server's logical map.tile value.
export const TS = 32;

// --- pure math (no PIXI) -----------------------------------------------------

// Lowest allowed zoom: whole world fits the viewport (spec §4). A small factor
// below fit is permitted by clamp() via the over-scroll margin, but minZoom is
// the fit scale so the world can always be framed.
export function fitScale(screenW, screenH, worldW, worldH) {
  if (worldW <= 0 || worldH <= 0) return 1;
  return Math.min(screenW / worldW, screenH / worldH);
}

export function clampScale(scale, minZoom, maxZoom) {
  return Math.max(minZoom, Math.min(maxZoom, scale));
}

// Screen point -> world point given the current camera offset (pos) and scale.
export function screenToWorld(sx, sy, posX, posY, scale) {
  return { x: (sx - posX) / scale, y: (sy - posY) / scale };
}

// World point -> screen point.
export function worldToScreen(wx, wy, posX, posY, scale) {
  return { x: wx * scale + posX, y: wy * scale + posY };
}

// Zoom toward a cursor: keep the world point under the cursor fixed. Returns the
// new {posX, posY, scale}. (spec §4 zoom-to-cursor block)
export function zoomToCursor(cursorX, cursorY, posX, posY, scale, zoomFactor, minZoom, maxZoom) {
  const wp = screenToWorld(cursorX, cursorY, posX, posY, scale);
  const s = clampScale(scale * zoomFactor, minZoom, maxZoom);
  return { scale: s, posX: cursorX - wp.x * s, posY: cursorY - wp.y * s };
}

// Clamp the camera offset so the world edges can't pull past the viewport, with
// a small over-scroll margin. Mirrors the Canvas renderer's clampCamera()
// semantics (margin in screen px). Returns the clamped {posX, posY}.
export function clampOffset(posX, posY, scale, screenW, screenH, worldW, worldH, margin = 80) {
  const mapW = worldW * scale;
  const mapH = worldH * scale;
  const px = Math.max(-(mapW - margin), Math.min(screenW - margin, posX));
  const py = Math.max(-(mapH - margin), Math.min(screenH - margin, posY));
  return { x: px, y: py };
}

// Centered fit-to-world offset (resetCamera equivalent). Returns {posX,posY,scale}.
export function fitView(screenW, screenH, worldW, worldH) {
  const scale = fitScale(screenW, screenH, worldW, worldH);
  return {
    scale,
    posX: (screenW - worldW * scale) / 2,
    posY: (screenH - worldH * scale) / 2,
  };
}

// --- Camera (binds the pure math to a PIXI worldContainer + DOM input) --------

export class Camera {
  // worldContainer: the camera-transformed PIXI.Container.
  // domEl: the element to bind pointer/wheel listeners to (#stage).
  // app: the PIXI.Application (for screen size).
  constructor(worldContainer, domEl, app) {
    this.world = worldContainer;
    this.el = domEl;
    this.app = app;
    this.worldW = 0;       // set via setWorldSize when the map arrives
    this.worldH = 0;
    this.maxZoom = 6;      // matches Canvas clamp upper bound
    this._dragging = false;
    this._dragSX = 0; this._dragSY = 0;
    this._camSX = 0; this._camSY = 0;
    // onPovToggle: optional callback invoked when the user requests a POV change
    // (wired by Stage/main to update the fog overlay).
    this.onPovToggle = null;
  }

  get screenW() { return this.app.renderer.width / this.app.renderer.resolution; }
  get screenH() { return this.app.renderer.height / this.app.renderer.resolution; }

  setWorldSize(mw, mh) {
    this.worldW = mw * TS;
    this.worldH = mh * TS;
    this.reset();
  }

  get minZoom() {
    // Allow a little below fit (over-scroll) so the world frames cleanly.
    return fitScale(this.screenW, this.screenH, this.worldW, this.worldH) * 0.7;
  }

  _apply(posX, posY, scale) {
    if (scale != null) this.world.scale.set(scale);
    this.world.position.set(posX, posY);
  }

  clamp() {
    if (!this.worldW) return;
    const c = clampOffset(
      this.world.position.x, this.world.position.y, this.world.scale.x,
      this.screenW, this.screenH, this.worldW, this.worldH);
    this.world.position.set(c.x, c.y);
  }

  reset() {
    if (!this.worldW) return;
    const v = fitView(this.screenW, this.screenH, this.worldW, this.worldH);
    this._apply(v.posX, v.posY, v.scale);
  }

  zoom(cursorX, cursorY, zoomFactor) {
    if (!this.worldW) return;
    const v = zoomToCursor(
      cursorX, cursorY, this.world.position.x, this.world.position.y,
      this.world.scale.x, zoomFactor, this.minZoom, this.maxZoom);
    this._apply(v.posX, v.posY, v.scale);
    this.clamp();
  }

  // screen<->world via the pure helpers (parity with Pixi toLocal/toGlobal but
  // keeps the camera self-contained and testable).
  toWorld(sx, sy) {
    return screenToWorld(sx, sy, this.world.position.x, this.world.position.y, this.world.scale.x);
  }
  toScreen(wx, wy) {
    return worldToScreen(wx, wy, this.world.position.x, this.world.position.y, this.world.scale.x);
  }

  attachInput() {
    const el = this.el;
    el.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      this.zoom(e.clientX - rect.left, e.clientY - rect.top, f);
    }, { passive: false });

    el.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      this._dragging = true;
      this._dragSX = e.clientX; this._dragSY = e.clientY;
      this._camSX = this.world.position.x; this._camSY = this.world.position.y;
      el.classList.add('dragging');
    });
    window.addEventListener('mousemove', (e) => {
      if (!this._dragging) return;
      this.world.position.set(
        this._camSX + (e.clientX - this._dragSX),
        this._camSY + (e.clientY - this._dragSY));
      this.clamp();
    });
    window.addEventListener('mouseup', () => {
      this._dragging = false;
      el.classList.remove('dragging');
    });
    el.addEventListener('dblclick', () => this.reset());
    window.addEventListener('resize', () => this.clamp());
  }
}
