# Spitter + Bulwark — Phase-1 Rush Counter (Design)

**Date:** 2026-06-18
**Status:** Approved (design) — pending spec review → implementation plan
**Author:** session 52 (Opus)

## Problem

DatTheMaster reliably wins with an early ~80% soldier rush. Diagnosis (grounded in
`engine/constants.py`): soldiers (200 HP, 22 dmg) are the **only** combat unit, the only
counter to soldiers is *more soldiers* + the queen + slow/expensive guard posts, and
defense is reactive (a guard post is 150 dirt + 100 build-work and arrives after the rush
hits). The payoff curve rewards whoever commits to soldiers first and hardest → a
degenerate "all-in always wins" meta with no rock-paper-scissors and no way to punish an
over-commit.

The fix is not "slow the bot" — the game lacks a **counter to massed melee**.

## Goal

Phased (user decision):
- **Phase 1 (this spec):** a focused, legible rush-counter — 1 new unit + 1 new structure
  + a buff to an existing structure, built on two small reusable mechanics.
- **Phase 2 (later, separate spec):** broaden into fuller RPS depth once phase 1 is proven.

Constraint (Hermes' feedback): keep it **legible for LLM agents** — no spells, no tech
trees, no complex terrain. New content reuses the verbs agents already know; at most one
simple new mechanic per piece.

## Phase-1 package

Three pieces over **two shared mechanics**:

- **Ranged + splash attack** → used by the **Spitter** (mobile) and the **guard post** (static).
  Splash already exists as the soldier `soldier_splash` code path; it will be factored into
  one reusable helper and fed by all three consumers (soldier splash, spitter, guard post).
- **Contact damage** (a structure bleeds adjacent enemies each tick) → used by the **Bulwark**.

### 1. Spitter — new unit type `A_SPITTER` (=4)

A fragile ranged ant that punishes clumped soldier-balls and folds if melee reaches it.

| Stat | Starting value | Rationale |
|---|---|---|
| HP | ~70 | ~3 soldier hits — must be screened/positioned, never a frontline |
| Attack | Ranged, range ~5 | Hits the ball before it closes (the new mechanic) |
| Damage | ~16 primary + splash radius 1 (~50% falloff) | Splash scales with enemy **density** → hard-counters a clump, weak vs spread/single |
| Cooldown | ~7 ticks (soldier = 4) | Specialist, not a strictly-better soldier 1v1 |
| Cost / spawn | ~45 food / ~30 ticks | Between worker (25) and soldier (50) |
| Speed | ~1/tick (same) | No kiting micro |

**Behavior (simple, by design):** fire at the nearest enemy within range; **does not chase**;
if an enemy becomes adjacent, step back toward nest/rally (basic self-preservation). Obeys
existing unit verbs (`move_to`/`hold`/`gather`(N/A)/`attack`) + a `spawn.spitter.target_ratio`
directive field. No new agent commands.

**Counter dynamic:** a clumped rush eats heavy splash from range → the all-in is no longer
free. Spitters die instantly to soldiers that reach them, and splash is weak vs spread/flank
→ the answer to spitters is mixed armies / flanking (the phase-2 RPS seed). Pure-anything
stops being optimal.

### 2. Bulwark — new structure type `bulwark`

A cheap, fast, destructible **spiked** barricade — the frontline Spitters fire over. (A
block-only barricade would just be a worse wall; walls already exist at 25 dirt / 500 HP.)

| Stat | Starting value | Rationale |
|---|---|---|
| Cost | ~50 dirt | Affordable *during* a rush (guard post = 150) |
| Build work | ~40 | Fast — a line goes up quickly (guard post = 100) |
| HP | ~250 | Soaks/stalls, destructible (not a permanent wall) |
| Max | ~6 | Plug a lane / ring part of the nest |
| Mechanic | **impassable + ~4 contact dmg/tick to adjacent enemies** | Stall + clump (so splash lands) + bleed the ball battering through |

Distinct from walls: walls are pure terrain (smashed/pathed-around, punish nothing). The
Bulwark stalls, clumps, and bleeds — an active turtle-and-punish when paired with splash.

### 3. Guard post buff (existing structure)

The *static* anti-mass anchor; shares the Spitter's splash.

| Guard post | Now | Proposed |
|---|---|---|
| HP | 300 | ~400 |
| Damage | 18 | ~22 + splash radius 1 (~50%) |
| Range | 10 | 10 (keep) |
| Max | 3 | 3 (keep — hard cap; no unbreakable turtle) |

## Integration surface

Bulwark + guard-post buff are mostly additive (existing structure pattern). The real cost
is the **5th unit type** — `worker/soldier/scout/queen` is assumed widely:

- **Engine (`constants.py`, `colony.py`, `world.py`):** `A_SPITTER=4` + stats; HP/cost/
  spawn-time maps; spawn-ratio logic + default directive; `_behavior_spitter`; ranged+splash
  attack. **Every `counts = [0,0,0,0]` and `range(4)` must become 5-aware** (grep them out
  systematically — primary regression risk).
- **Splash helper:** generalize the existing `soldier_splash` logic into one function; feed
  soldier/spitter/guard post from it.
- **Contact damage:** one new per-tick structure loop (Bulwark bleeds adjacent enemies).
- **Server/MCP (`server.py`, `mcp_server.py`):** extend `["worker","soldier","scout","queen"]`
  string lists, `counts[...]` refs, spawn-ratio docs, sitrep composition, `current_orders`,
  `get_units` filter, `build_structure` valid types, advisor affordable-structures.
- **Fog respected:** enemy spitters appear only via existing fog-gated sightings/scouted
  composition (extended to count spitters); Bulwark via fog-gated `seen_structs`. No leaks.
- **Frontend:** placeholder recolor sprites first (playable); real pixel-art via the graphics
  pipeline later.

## Validation (before tuning is "done")

Headless repro harnesses in `tools/repro/` (same style as existing), proving:

1. **rush_vs_counter** — 80%-soldier rush vs defender with spitters+bulwark+guard post →
   defender survives/wins (the actual goal).
2. **symmetry check** — mirror matchup stays ~balanced; no turtle-stalemate.
3. **spitter is a specialist** — soldier beats spitter 1v1; spitter wins only vs clumps
   (splash scales with density).
4. Existing test suite stays green (unit-type refactor is the main regression risk).

Then a bot-v-bot tuning loop on the numbers.

## Out of scope (phase 2+)

Fuller RPS roster (tank/armor types, damage multipliers), real pixel-art assets, any new
agent *verbs* or mechanics beyond ranged-splash + contact-damage.

## Open numbers

All stat values above are **starting points** to be tuned in the validation/bot-loop. The
design (roles, mechanics, integration, validation) is fixed; the numbers are not.
