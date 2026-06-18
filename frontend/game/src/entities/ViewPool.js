// ViewPool: type-keyed object pools to recycle entity views across spawn/despawn
// churn, avoiding GC pressure at high ant counts. (spec §2.2, §5 "Object pooling")
//
// Generic and view-agnostic: it just stores free instances bucketed by a string
// key (e.g. the ant `type`). Callers create/reset the views; the pool only owns
// the free lists. `acquire(key)` returns a recycled view or null; `release(key,
// view)` returns one to the bucket. A `max` per bucket caps memory.
export class ViewPool {
  constructor(maxPerKey = 256) {
    this.free = new Map();   // key -> [view, view, ...]
    this.maxPerKey = maxPerKey;
  }

  acquire(key) {
    const bucket = this.free.get(key);
    if (bucket && bucket.length) return bucket.pop();
    return null;
  }

  release(key, view) {
    let bucket = this.free.get(key);
    if (!bucket) { bucket = []; this.free.set(key, bucket); }
    if (bucket.length < this.maxPerKey) bucket.push(view);
    else if (view && typeof view.destroy === 'function') view.destroy();
  }

  // Drop and destroy every pooled view (called on reset/reconnect to avoid
  // ghost sprites lingering in the pool).
  clear() {
    for (const bucket of this.free.values()) {
      for (const v of bucket) if (v && typeof v.destroy === 'function') v.destroy();
    }
    this.free.clear();
  }

  size() {
    let n = 0;
    for (const bucket of this.free.values()) n += bucket.length;
    return n;
  }
}
