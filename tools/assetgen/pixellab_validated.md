# pixellab.ai — validated facts (pre-M0 gate)

## Pinned versions (graphics overhaul)
- PixiJS: **v8.6.6** (UMD) — `frontend/game/vendor/pixi.min.js`
- pixi-filters: **v6.0.5** (UMD, targets Pixi v8) — `frontend/game/vendor/pixi-filters.min.js`


Date: 2026-06-17. Key in `.env` as `PIXELLAB_API_TOKEN` (gitignored, never committed).

## Confirmed by live API calls
- **Free tier bills in GENERATIONS, not USD.** A `POST /v1/generate-image-pixflux` returned
  `usage: {type:"generations", generations:1.0}` and the USD balance stayed `$0.00`.
  → "40 credits/month" = **40 generations/month**. 1 still image = **1 generation**.
- `GET /v1/balance` → `{type:"usd", usd:0.0}` (USD balance is for PAID usage; separate from the
  free generation quota — free calls do not draw it down).
- Auth: `Authorization: Bearer <token>`. Base: `https://api.pixellab.ai/v1/`.
- First asset generated and saved: `tools/assetgen/worker_anchor.png` (64×64, transparent,
  good silhouette; came out near-black — tune prompt/`color_image` for a mid-gray tint base).

## Per-generation USD cost (from pricing page; 1 generation ≈ these)
| Endpoint | Size | USD/gen | ~credits (est) |
|---|---|---|---|
| generate-image-pixflux (text→sprite) | 64×64 | $0.00793 | 1 |
| generate-image-bitforge (style-ref) | 64×64 | $0.00716 | 1 |
| animate-with-skeleton (4 frames) | 64×64 | $0.01433 | ~2 (confirm) |
| rotate | 64×64 | $0.01057 | ~1.3 (confirm) |
| generate-8-rotations v3 | 64×64 | $0.0337 | ~4 (confirm) |
| animate-with-text v3 (4 frames) | 128×128 | $0.0302 | ~4 (confirm) |

Still = 1 generation (confirmed). Animation/rotation credit cost is ESTIMATED from the USD ratio
and must be confirmed when we run the first real animation (record actual `usage.generations`).

## pixflux request shape (confirmed via openapi)
`POST /v1/generate-image-pixflux` — required: `description`, `image_size:{width,height}` (16–400).
Optional: `no_background`, `view` ("high top-down"), `outline`, `shading`, `detail`,
`text_guidance_scale` (1–20, def 8), `color_image` (forced palette), `seed`, `init_image`.
Response: `{usage, image:{base64}}`.

## Still TODO in the pre-M0 gate
- Confirm actual `usage.generations` for one skeleton animation + one 8-rotation.
- Grayscale-base + Pixi tint legibility test at 32px (RED vs BLUE).
- Free-rotation legibility test on a real generated ant (and the 128×128 queen).
