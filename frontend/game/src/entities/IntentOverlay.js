// IntentOverlay: draws each colony's CURRENT military intent on the map so a
// spectator can read the plan, not just the ants. Reads the directive carried in
// the tick snapshot (colonies[i][5].military) — rally_point and attack_target — and
// draws a faded line from the nest plus a pulsing marker at the destination:
//   • rally_point   → soft ring (massing here)
//   • attack_target → crosshair ring (committing here)
// Colored by team, alpha-pulsed so it reads as "live intent" without clutter.
// Render-only and fail-safe: any bad/partial data is skipped, never throws.
import { TS } from '../scene/Camera.js';

const TEAM = { 0: 0xff5040, 1: 0x5070ff };

// Normalize a directive point that may be [x,y] or [[x,y], ...] or null.
function xy(p) {
  if (!p) return null;
  if (Array.isArray(p[0])) p = p[0];
  const x = p[0], y = p[1];
  if (typeof x !== 'number' || typeof y !== 'number') return null;
  return [x, y];
}

export class IntentOverlay {
  // world: the camera-transformed container (stage.world). Markers are drawn in
  // world pixels (tile * TS), so they pan/zoom with everything else.
  constructor(world) {
    const PIXI = window.PIXI;
    this.g = new PIXI.Graphics();
    this.g.eventMode = 'none';   // never intercept clicks
    world.addChild(this.g);
    this._phase = 0;
  }

  reset() { try { this.g.clear(); } catch (e) {} }

  // Called every frame from the render Loop. store._lastSnap is the latest tick.
  update(store, dtMS) {
    this._phase += (dtMS || 16) / 1000;
    const g = this.g;
    try {
      g.clear();
      const snap = store && store._lastSnap;
      if (!snap || !Array.isArray(snap.colonies)) return;
      const pulse = 0.5 + 0.5 * Math.sin(this._phase * 2.2);
      for (const col of snap.colonies) {
        if (!Array.isArray(col)) continue;
        const id = col[0];
        const nest = [col[1], col[2]];
        const dir = col[5];
        const mil = dir && dir.military;
        if (!mil) continue;
        if (col[10] === 0) continue;   // dead colony (alive flag) — skip
        const color = TEAM[id] ?? 0xffffff;
        const nx = nest[0] * TS, ny = nest[1] * TS;

        const at = xy(mil.attack_target);
        if (at) {
          const ax = at[0] * TS, ay = at[1] * TS;
          g.moveTo(nx, ny).lineTo(ax, ay).stroke({ color, width: 2, alpha: 0.30 });
          const rr = TS * (0.7 + 0.55 * pulse);
          g.circle(ax, ay, rr).stroke({ color, width: 2.5, alpha: 0.55 });
          g.circle(ax, ay, rr * 0.5).stroke({ color, width: 1.5, alpha: 0.4 });
          // crosshair ticks (reads as "attacking here")
          for (const [dx, dy] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
            g.moveTo(ax + dx * rr * 0.55, ay + dy * rr * 0.55)
             .lineTo(ax + dx * rr, ay + dy * rr)
             .stroke({ color, width: 2, alpha: 0.6 });
          }
        }

        const rp = xy(mil.rally_point);
        if (rp) {
          const rx = rp[0] * TS, ry = rp[1] * TS;
          g.moveTo(nx, ny).lineTo(rx, ry).stroke({ color, width: 2, alpha: 0.18 });
          g.circle(rx, ry, TS * (0.55 + 0.45 * pulse)).stroke({ color, width: 2, alpha: 0.30 + 0.2 * pulse });
          g.circle(rx, ry, TS * 0.22).fill({ color, alpha: 0.45 });
        }
      }
    } catch (e) { /* render-only: never break the loop */ }
  }
}
