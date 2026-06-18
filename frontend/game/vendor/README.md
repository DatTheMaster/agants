# Vendored libraries (graphics overhaul)

These are pinned UMD builds loaded via `<script src>` (no bundler). They expose the
global `window.PIXI`. See spec §2.1 — PixiJS v8's ESM-from-CDN path is broken, so we
vendor the UMD build and serve it from our own origin.

| File | Package | Version | Source |
|---|---|---|---|
| `pixi.min.js` | pixi.js | **8.6.6** | https://pixijs.download/v8.6.6/pixi.min.js |
| `pixi-filters.min.js` | pixi-filters | **6.0.5** | https://cdn.jsdelivr.net/npm/pixi-filters@6.0.5/dist/pixi-filters.js |

`pixi-filters` v6.x targets Pixi v8 and extends `PIXI.filters` (e.g. `PIXI.filters.GlowFilter`).

Verified: loading `pixi.min.js` defines `PIXI.VERSION === "8.6.6"`; `pixi-filters.min.js`
sets `this.PIXI.filters` (UMD).

Do not hot-link a CDN at runtime — keep these on-origin (cache-controlled by the CF Worker).
