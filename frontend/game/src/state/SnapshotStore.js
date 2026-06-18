// SnapshotStore: single source of truth for game state.
// Diffs ant id-sets each tick, builds from→to interpolation segments, and
// measures the tick interval client-side (server runs 1–10 TPS).
//
// Ant tuple layout (fixed contract from World.serialize_tick):
//   [id, x, y, prev_x, prev_y, colony, type, state, carrying, hp, max_hp]
export const ANT = { ID: 0, X: 1, Y: 2, PX: 3, PY: 4, COLONY: 5, TYPE: 6, STATE: 7, CARRYING: 8, HP: 9, MAXHP: 10 };

// Structure tuple layout (fixed contract from World.serialize_tick):
//   [x, y, colony, hp, max_hp, type, active, build_progress, build_required]
// NOTE: `type` (index 5) is a STRING name ("guard_post" | "watchtower" |
// "barracks" | "wall" | "larder"), NOT an index. `active` is 0/1.
export const STRUCT = { X: 0, Y: 1, COLONY: 2, HP: 3, MAXHP: 4, TYPE: 5, ACTIVE: 6, BUILD_PROGRESS: 7, BUILD_REQUIRED: 8 };

// Colony tuple layout (subset used by the renderer):
//   [id, nest_x, nest_y, ... , alive(idx 10), tiers(idx 11), ...]
// `tiers` (idx 11) is [worker_tier, scout_tier, soldier_tier] per the sim
// (engine/world.py colonies serialization; verified against replay data).
export const COLONY = { ID: 0, NEST_X: 1, NEST_Y: 2, ALIVE: 10, TIERS: 11 };

// Ant type idx -> index into a colony's `tiers` triple. worker(0)->0,
// soldier(1)->2, scout(2)->1. Queen(3) is always tier 3 (handled separately).
// Mirrors the Canvas renderer's TYPE_TIER_IDX = [0, 2, 1] (index.html:1461).
export const TYPE_TIER_IDX = [0, 2, 1];

export class SnapshotStore {
  constructor() {
    this.ants = new Map();
    this.removedAntIds = [];
    // Structures keyed by "x,y,type" — they are tile-snapped and never move, so
    // no interpolation segment is needed; only the row tuple is stored. Diffing
    // the key set each tick drives spawn (build) / despawn (destroy).
    this.structures = new Map();
    this.removedStructKeys = [];
    // Colonies, keyed by colony id (for nests + alive state).
    this.colonies = new Map();
    this.tick = 0;
    this.phase = 'lobby';
    this.tickStartMs = 0;
    this.tickIntervalMs = 1000; // sensible default until measured
    this._lastStartMs = 0;
  }

  reset() {
    this.ants.clear();
    this.removedAntIds = [];
    this.structures.clear();
    this.removedStructKeys = [];
    this.colonies.clear();
    this.tick = 0;
    this.tickStartMs = 0;
    this._lastStartMs = 0;
    this.tickIntervalMs = 1000;
  }

  // Stable key for a structure row (tile-snapped position + type).
  static structKey(row) {
    return row[STRUCT.X] + ',' + row[STRUCT.Y] + ',' + row[STRUCT.TYPE];
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

    // --- structures (id-diff by position+type key) ---
    const seenStruct = new Set();
    for (const row of (snap.structures || [])) {
      const key = SnapshotStore.structKey(row);
      seenStruct.add(key);
      const existing = this.structures.get(key);
      const entry = existing || { justAdded: true };
      entry.x = row[STRUCT.X];
      entry.y = row[STRUCT.Y];
      entry.colony = row[STRUCT.COLONY];
      entry.hp = row[STRUCT.HP];
      entry.maxHp = row[STRUCT.MAXHP];
      entry.type = row[STRUCT.TYPE];
      entry.active = !!row[STRUCT.ACTIVE];
      entry.buildProgress = row[STRUCT.BUILD_PROGRESS] || 0;
      entry.buildRequired = row[STRUCT.BUILD_REQUIRED] || 0;
      if (existing) entry.justAdded = false;
      this.structures.set(key, entry);
    }
    this.removedStructKeys = [];
    for (const key of this.structures.keys()) {
      if (!seenStruct.has(key)) this.removedStructKeys.push(key);
    }
    for (const key of this.removedStructKeys) this.structures.delete(key);

    // --- colonies (keyed by id; nests + alive state + per-class tiers) ---
    for (const c of (snap.colonies || [])) {
      const id = c[COLONY.ID];
      this.colonies.set(id, {
        id,
        nestX: c[COLONY.NEST_X],
        nestY: c[COLONY.NEST_Y],
        alive: c[COLONY.ALIVE] !== 0,
        // [worker_tier, scout_tier, soldier_tier]; default flat if absent.
        tiers: Array.isArray(c[COLONY.TIERS]) ? c[COLONY.TIERS] : [0, 0, 0],
      });
    }
  }

  // Tier for an ant, derived from its colony's per-class tiers (tier is NOT in
  // the ant tuple). Queen (type 3) is always tier 3. Returns 0..3. (spec §3.2)
  tierForAnt(colony, type) {
    if (type === 3) return 3;
    const c = this.colonies.get(colony);
    if (!c || !c.tiers) return 0;
    return c.tiers[TYPE_TIER_IDX[type] ?? 0] || 0;
  }

  interp(nowMs) {
    const t = (nowMs - this.tickStartMs) / Math.max(1, this.tickIntervalMs);
    return t < 0 ? 0 : t > 1 ? 1 : t;
  }
}
