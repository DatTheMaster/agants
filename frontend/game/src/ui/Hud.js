// Hud: in-world UI in the screen-space uiLayer (never camera-transformed).
// The big DOM #panel HUD is KEPT (spec §2.2) — this only adds the in-world
// flourishes the Canvas renderer lacked: a selection ring on click, a hover
// tooltip, and a minimap. (spec §2.3 uiLayer, M4)
//
// Picking uses the Camera's pure screen<->world transform (Camera.toWorld), then
// a nearest-ant search in the store. The minimap is a small fixed-corner panel
// showing ant/structure positions + the current viewport rectangle; clicking it
// recenters the camera.
import { TS } from '../scene/Camera.js';

const COLONY_TINT = { 0: 0xff3c3c, 1: 0x3c3cff };
function tintFor(c) { return COLONY_TINT[c] ?? 0xffffff; }
const TYPE_NAME = ['Worker', 'Soldier', 'Scout', 'Queen'];
const STATE_NAME = ['Idle', 'Foraging', 'Returning', 'Exploring',
                    'Fighting', 'Patrolling', 'Recruited', 'Building'];

export class Hud {
  // uiLayer: screen-space container. camera: Camera. store: SnapshotStore.
  // app: PIXI.Application. domEl: #stage (for pointer coords).
  constructor(uiLayer, camera, store, app, domEl) {
    const PIXI = window.PIXI;
    this.layer = uiLayer;
    this.camera = camera;
    this.store = store;
    this.app = app;
    this.el = domEl;

    // Selection ring (drawn in WORLD space — added to the camera-transformed
    // world so it tracks the selected ant). We keep a handle + a selected id.
    this.selectionRing = new PIXI.Graphics();
    this.selectedId = null;          // selected ant id (mutually exclusive with struct)
    this.selectedStructKey = null;   // selected structure key

    // Hover tooltip (screen space).
    this.tooltip = new PIXI.Container();
    this.tooltipBg = new PIXI.Graphics();
    this.tooltipText = new PIXI.Text({
      text: '', style: { fontFamily: 'monospace', fontSize: 11, fill: 0xffffff },
    });
    this.tooltipText.position.set(6, 4);
    this.tooltip.addChild(this.tooltipBg, this.tooltipText);
    this.tooltip.visible = false;
    this.layer.addChild(this.tooltip);

    // Minimap (fixed bottom-right). worldW/H set when the map arrives.
    this.minimap = new PIXI.Container();
    this.miniBg = new PIXI.Graphics();
    this.miniDots = new PIXI.Graphics();
    this.miniView = new PIXI.Graphics();
    this.minimap.addChild(this.miniBg, this.miniDots, this.miniView);
    this.layer.addChild(this.minimap);
    this.miniW = 160; this.miniH = 0;   // height set from world aspect
    this.worldW = 0; this.worldH = 0;
    this._hoverScreen = null;

    this._bindInput();
    this._layoutMinimap();
    window.addEventListener('resize', () => this._layoutMinimap());
  }

  setWorldSize(worldWpx, worldHpx) {
    this.worldW = worldWpx; this.worldH = worldHpx;
    this.miniH = worldHpx > 0 ? this.miniW * (worldHpx / worldWpx) : 100;
    this._layoutMinimap();
  }

  _layoutMinimap() {
    const pad = 12;
    // The canvas is sized to the whole WINDOW (resizeTo: window) but #stage clips it
    // and the side panel covers its right edge, so the renderer's right/bottom edge is
    // NOT the visible edge (the canvas is even centered/overflowing -> negative left).
    // Anchor the minimap to the VISIBLE stage rect, mapped into canvas-local coords, so
    // it always sits just inside the viewport regardless of panel width. (user feedback)
    const canvas = this.app.canvas || this.app.renderer?.canvas;
    const cRect = canvas && canvas.getBoundingClientRect();
    const sRect = this.el && this.el.getBoundingClientRect();
    let right, bottom;
    if (cRect && sRect && cRect.width) {
      right = sRect.right - cRect.left;
      bottom = sRect.bottom - cRect.top;
    } else {
      right = this.app.renderer.width / this.app.renderer.resolution;
      bottom = this.app.renderer.height / this.app.renderer.resolution;
    }
    this.minimap.position.set(right - this.miniW - pad, bottom - this.miniH - pad);
    this.miniBg.clear();
    this.miniBg.rect(0, 0, this.miniW, this.miniH).fill({ color: 0x000000, alpha: 0.45 });
    this.miniBg.rect(0, 0, this.miniW, this.miniH).stroke({ color: 0x555555, width: 1, alpha: 0.8 });
  }

  _bindInput() {
    const el = this.el;
    el.addEventListener('mousemove', (e) => {
      const rect = el.getBoundingClientRect();
      this._hoverScreen = { x: e.clientX - rect.left, y: e.clientY - rect.top, clientX: e.clientX, clientY: e.clientY };
    });
    el.addEventListener('mouseleave', () => { this._hoverScreen = null; });
    // Click to select (left) — but not when it's the end of a drag-pan. We use a
    // small movement threshold tracked on pointerdown.
    let downX = 0, downY = 0, moved = false;
    el.addEventListener('mousedown', (e) => { if (e.button === 0) { downX = e.clientX; downY = e.clientY; moved = false; } });
    el.addEventListener('mousemove', (e) => {
      if (Math.abs(e.clientX - downX) + Math.abs(e.clientY - downY) > 4) moved = true;
    });
    el.addEventListener('click', (e) => {
      if (e.button !== 0 || moved) return;
      const rect = el.getBoundingClientRect();
      const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
      // Minimap click recenters.
      if (this._minimapHit(sx, sy)) { this._minimapClick(sx, sy); return; }
      const w = this.camera.toWorld(sx, sy);
      // Ant first; if none under the cursor, try a structure; empty space deselects.
      const id = this._pickAnt(w.x, w.y);
      if (id != null) { this.selectedId = id; this.selectedStructKey = null; }
      else { this.selectedId = null; this.selectedStructKey = this._pickStructure(w.x, w.y); }
    });
  }

  _minimapHit(sx, sy) {
    const mx = this.minimap.position.x, my = this.minimap.position.y;
    return sx >= mx && sx <= mx + this.miniW && sy >= my && sy <= my + this.miniH;
  }

  _minimapClick(sx, sy) {
    if (!this.worldW) return;
    const lx = sx - this.minimap.position.x, ly = sy - this.minimap.position.y;
    const wx = (lx / this.miniW) * this.worldW;
    const wy = (ly / this.miniH) * this.worldH;
    // Recenter camera on (wx, wy) at the current scale.
    const scale = this.camera.world.scale.x;
    const sw = this.app.renderer.width / this.app.renderer.resolution;
    const sh = this.app.renderer.height / this.app.renderer.resolution;
    this.camera.world.position.set(sw / 2 - wx * scale, sh / 2 - wy * scale);
    this.camera.clamp();
  }

  // Nearest ant within a small world-pixel radius of (wx, wy). Returns id|null.
  _pickAnt(wx, wy) {
    let best = null, bestD = (TS * 1.2) * (TS * 1.2);
    for (const [id, e] of this.store.ants) {
      const ex = e.to.x * TS + TS / 2, ey = e.to.y * TS + TS / 2;
      const dx = ex - wx, dy = ey - wy;
      const d2 = dx * dx + dy * dy;
      if (d2 < bestD) { bestD = d2; best = id; }
    }
    return best;
  }

  // Nearest structure within ~1.4 tiles of (wx, wy). Returns its key|null.
  _pickStructure(wx, wy) {
    let best = null, bestD = (TS * 1.4) * (TS * 1.4);
    for (const [k, e] of this.store.structures) {
      const ex = e.x * TS + TS / 2, ey = e.y * TS + TS / 2;
      const dx = ex - wx, dy = ey - wy;
      const d2 = dx * dx + dy * dy;
      if (d2 < bestD) { bestD = d2; best = k; }
    }
    return best;
  }

  // Per-frame update: selection ring follows the selected ant; tooltip on hover;
  // minimap dots + viewport rect. Cheap; called from the Loop ticker.
  update() {
    this._updateSelection();
    this._updateTooltip();
    this._updateMinimap();
  }

  _updateSelection() {
    // Ant selection (takes priority while the ant is alive).
    if (this.selectedId != null && this.store.ants.has(this.selectedId)) {
      if (!this.selectionRing.parent) this.camera.world.addChild(this.selectionRing);
      const e = this.store.ants.get(this.selectedId);
      const t = this.store.interp(performance.now());
      const wx = (e.from.x + (e.to.x - e.from.x) * t) * TS + TS / 2;
      const wy = (e.from.y + (e.to.y - e.from.y) * t) * TS + TS / 2;
      this.selectionRing.clear();
      this.selectionRing.circle(wx, wy, TS * 0.55).stroke({ color: 0xffffff, width: 2, alpha: 0.9 });
      this.selectionRing.circle(wx, wy, TS * 0.7).stroke({ color: tintFor(e.colony), width: 1.5, alpha: 0.6 });
      return;
    }
    // Structure selection (square outline around its footprint).
    if (this.selectedStructKey != null && this.store.structures.has(this.selectedStructKey)) {
      if (!this.selectionRing.parent) this.camera.world.addChild(this.selectionRing);
      const e = this.store.structures.get(this.selectedStructKey);
      const fw = (e.type === 'nest' ? 5 : 1) * TS;          // nest spans 5 tiles
      const cx = e.x * TS + TS / 2, cy = e.y * TS + TS / 2;
      this.selectionRing.clear();
      this.selectionRing.rect(cx - fw / 2, cy - fw / 2, fw, fw)
        .stroke({ color: 0xffffff, width: 2, alpha: 0.9 });
      this.selectionRing.rect(cx - fw / 2 - 3, cy - fw / 2 - 3, fw + 6, fw + 6)
        .stroke({ color: tintFor(e.colony), width: 1.5, alpha: 0.6 });
      return;
    }
    // Nothing valid selected: hide the ring + clear any stale ids.
    if (this.selectionRing.parent) this.selectionRing.parent.removeChild(this.selectionRing);
    if (this.selectedId != null && !this.store.ants.has(this.selectedId)) this.selectedId = null;
    if (this.selectedStructKey != null && !this.store.structures.has(this.selectedStructKey)) this.selectedStructKey = null;
  }

  _updateTooltip() {
    const h = this._hoverScreen;
    if (!h) { this.tooltip.visible = false; return; }
    const w = this.camera.toWorld(h.x, h.y);
    // Ant tooltip; else structure tooltip; else hide.
    let txt = null, color = 0xffffff;
    const id = this._pickAnt(w.x, w.y);
    if (id != null && this.store.ants.has(id)) {
      const e = this.store.ants.get(id);
      const tier = this.store.tierForAnt(e.colony, e.type);
      txt = `${TYPE_NAME[e.type] || '?'} T${tier}\n${STATE_NAME[e.state] || '?'}  HP ${e.hp}/${e.maxHp}${e.carrying ? '\ncarrying' : ''}`;
      color = tintFor(e.colony);
    } else {
      const sk = this._pickStructure(w.x, w.y);
      if (sk != null && this.store.structures.has(sk)) {
        const e = this.store.structures.get(sk);
        const nm = String(e.type || 'structure').replace(/_/g, ' ');
        const status = (!e.active && e.buildRequired)
          ? `building ${Math.round(100 * (e.buildProgress / Math.max(1, e.buildRequired)))}%`
          : (e.hp < e.maxHp ? 'damaged' : 'active');
        txt = `${nm}\n${status}  HP ${e.hp}/${e.maxHp}`;
        color = tintFor(e.colony);
      }
    }
    if (!txt) { this.tooltip.visible = false; return; }
    this.tooltipText.text = txt;
    const b = this.tooltipText.getLocalBounds();
    this.tooltipBg.clear();
    this.tooltipBg.rect(0, 0, b.width + 12, b.height + 8)
                  .fill({ color: 0x000000, alpha: 0.7 })
                  .stroke({ color, width: 1, alpha: 0.8 });
    this.tooltip.position.set(h.x + 14, h.y + 14);
    this.tooltip.visible = true;
  }

  _updateMinimap() {
    if (!this.worldW) return;
    const sx = this.miniW / this.worldW, sy = this.miniH / this.worldH;
    const g = this.miniDots;
    g.clear();
    // Ants as 1px dots.
    for (const e of this.store.ants.values()) {
      const px = (e.to.x * TS + TS / 2) * sx, py = (e.to.y * TS + TS / 2) * sy;
      g.rect(px, py, 1.4, 1.4).fill({ color: tintFor(e.colony), alpha: 0.9 });
    }
    // Structures as small squares.
    for (const s of this.store.structures.values()) {
      const px = (s.x * TS + TS / 2) * sx, py = (s.y * TS + TS / 2) * sy;
      g.rect(px - 1, py - 1, 2.4, 2.4).fill({ color: tintFor(s.colony), alpha: 0.8 });
    }
    // Viewport rectangle (what the camera currently shows).
    const cam = this.camera;
    const swp = this.app.renderer.width / this.app.renderer.resolution;
    const shp = this.app.renderer.height / this.app.renderer.resolution;
    const tl = cam.toWorld(0, 0), br = cam.toWorld(swp, shp);
    this.miniView.clear();
    this.miniView.rect(tl.x * sx, tl.y * sy, (br.x - tl.x) * sx, (br.y - tl.y) * sy)
                 .stroke({ color: 0xffffff, width: 1, alpha: 0.7 });
  }

  reset() {
    this.selectedId = null;
    this.selectionRing.clear();
    if (this.selectionRing.parent) this.selectionRing.parent.removeChild(this.selectionRing);
    this.tooltip.visible = false;
    this.miniDots.clear();
  }
}
