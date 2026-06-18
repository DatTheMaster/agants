// SnapshotStore: single source of truth for game state.
// Diffs ant id-sets each tick, builds from→to interpolation segments, and
// measures the tick interval client-side (server runs 1–10 TPS).
//
// Ant tuple layout (fixed contract from World.serialize_tick):
//   [id, x, y, prev_x, prev_y, colony, type, state, carrying, hp, max_hp]
export const ANT = { ID: 0, X: 1, Y: 2, PX: 3, PY: 4, COLONY: 5, TYPE: 6, STATE: 7, CARRYING: 8, HP: 9, MAXHP: 10 };

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
