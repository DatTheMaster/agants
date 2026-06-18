# Agent Situation Report (sitrep) — design

*Status: design / awaiting implementation plan*
*Date: 2026-06-18*

## Context & problem

Agants is an AI-vs-AI RTS: LLM/MCP agents command colonies via a directive + unit-command
API. The agent-facing state (`get_state` / REST `/api/state/{cid}` / SDK) already exposes
counts, food, queen HP, `viable_food_nodes`, an `advisor` (contextual hints), `notifications`
(`queen_under_attack`, `rally_stalled`, `workers_idle`, …) and an events log.

Despite that, this session's live debugging showed agents **had information but played
badly**: an attack order left an army frozen on a stale rally point with nothing telling the
agent the order wasn't working; 26/35 workers sat idle while the advisor's passive hint went
unheeded. The bottleneck is **legibility** — agents can't clearly perceive the true situation
or the *effect of their own orders*.

## Goal

Add a **Situation Report (`sitrep`)**: a single, honest, factual block on the agent state that
makes the game legible. The guiding philosophy is **instrument, not coach** — surface *truth*
(what's happening, whether your orders are taking effect, where the enemy is) and never
prescribe the move. Agent skill stays meaningful.

## Non-goals

- No prescriptive coaching ("do X now"). Facts only.
- No changes to the sim/combat balance or the midfield-stalemate dynamics (that is a separate
  spec: "decisive games / flanking-lane AI").
- No new agent *controls* (this is feedback only).

## Hard constraints / principles

- **Parity:** computed server-side and identical for every agent (MCP, SDK, the DatTheMaster
  controller). No client gets information or logic another lacks. The controller is just an
  always-on looping agent that renders the same `sitrep` everyone receives.
- **Fog-of-war preserved:** your own colony = full truth; **everything enemy-derived is
  scouted-only**, with explicit staleness. Never reveal unscouted enemy state. Things that are
  fundamentally unobservable (enemy food stockpile, income) are reported as `not_observable`,
  not revealed.

## Decisions

- **Placement:** server-side, in the engine/server, attached as a `sitrep` object on the
  per-colony state response. (Rejected: controller-only — unfair asymmetry, undercuts the
  thesis; hybrid — splits logic for no benefit.)
- **Enemy info:** scouted-only, fog in effect, including territory.

## The `sitrep` structure

Attached to the colony state (the dict returned by `get_state` / `/api/state/{cid}` / SDK):

```jsonc
"sitrep": {
  "standing": {
    // your side = full truth; enemy = scouted-only, with verdict + staleness
    "military":  {"you": 1910, "enemy": 760, "enemy_seen_tick": 1080, "enemy_stale_ticks": 12,
                  "verdict": "leading", "margin": 1150},
    "economy":   {"you": {"food": 925, "income_per_s": 282},
                  "enemy": "not_observable", "enemy_proxy": {"scouted_workers": 8, "scouted_larders": 1}},
    "territory": {"you_pct": 9.4, "enemy_pct": "unknown", "verdict": "unknown"},
    "queen":     {"your_hp": 900, "your_hp_pct": 100, "threat_soldiers_near_nest": 0,
                  "enemy_queen_hp": "unknown"}   // real number only while sieging/scouted
  },
  "orders": [
    // one factual readout per ACTIVE directive intent — effect, not advice
    {"intent": "attack_target", "target": [136, 50], "status": "no_effect",
     "detail": "army center-of-mass x 48->48 over last 40t (not advancing)"},
    {"intent": "rally", "point": [48, 50], "status": "holding",
     "detail": "12/20 staged; rally_release_at=20 unmet"},
    {"intent": "spawn", "status": "drifting",
     "detail": "worker target 0.20 / actual 0.55; soldier 0.70 / 0.33"}
  ],
  "field": {
    // scouted-only battlefield facts
    "front_line": {"north": null, "center": 74, "south": 88},   // x where your units contact known enemy; null = no contact
    "enemy_army": {"seen": true, "size": 30, "pos": [120, 50], "seen_tick": 1080, "age_ticks": 12},
    "enemy_structures": [{"type": "barracks", "x": 130, "y": 48, "seen_tick": 1040}]
  }
}
```

### `standing` (you vs enemy, 4 axes)
Each axis carries a `verdict` ∈ `leading|trailing|even|unknown` and a `margin` where computable.
- **military** — your army value (full); enemy army value derived from the existing fog/intel
  system (per-unit visibility where the engine tracks it, otherwise the last-scouted snapshot),
  with `enemy_seen_tick` / `enemy_stale_ticks`. Never scouted → `enemy: "unknown"`,
  `verdict: "unknown"`. (The implementation plan confirms exactly what the intel layer exposes.)
- **economy** — your food + income (full). Enemy food/income are **not directly observable**
  → `enemy: "not_observable"`, with an honest `enemy_proxy` of scouted enemy workers + larders.
- **territory** — your territory % (full). Enemy % is fog-gated → scouted estimate or
  `"unknown"`.
- **queen** — your queen HP/% (full) + `threat_soldiers_near_nest` (enemy soldiers within the
  nest radius — observable, they're on your turf). `enemy_queen_hp` is a real number only while
  you are sieging it / it's scouted; otherwise `"unknown"`.

### `orders` (effect of your own active intents — full truth, no fog)
One entry per active directive intent, each a factual effect readout with a `status`:
- **attack_target / auto_attack** — is the army advancing toward the objective? Computed from
  the army center-of-mass delta over a window. `status` ∈ `advancing|no_effect|engaged|arrived`.
- **rally** — staged count vs `rally_release_at`; `status` ∈ `filling|holding|released`.
- **spawn** — target vs actual unit-type ratios; `status` ∈ `on_target|drifting`.
Only emitted for intents that are actually set. This is the blind spot from this session.

### `field` (scouted-only battlefield facts)
- **front_line** — per lane (north/center/south), the x where your units are in contact with
  known enemy; `null` if no contact in that lane.
- **enemy_army** — last-seen scouted enemy army cluster: size, position, `seen_tick`,
  `age_ticks`. `{"seen": false}` if never scouted.
- **enemy_structures** — scouted enemy structures (type, x, y, seen_tick).

## Order-effect tracking

To compute "advancing vs static," track a small per-colony history of the army center-of-mass
(soldier x/y mean) over a ~40-tick ring buffer. Mirrors the existing `_rally_stall_since`
tracker in pattern and cost (bounded, O(1) per tick). Initialized in `Colony.__init__`.

## Advisor → descriptive

Convert the existing `advisor` hints from imperatives to facts, consistent with
instrument-not-coach: `"buy_upgrade('worker')"` → `"worker upgrade affordable (500f, you have
925)"`; `"redistribute_workers()"` → `"26/35 workers idle"`. Keep the fact; drop the command.

## Parity cleanup (fold into this work)

This session added two **controller-only** behaviors that violate the parity principle; both
are now redundant with server-side fixes and should be removed/relocated:
- **Remove** the controller's rally-clear guard (`patch_directive` auto-clearing `rally_point`).
  The engine fix (no-release rally no longer overrides `attack_target`) fixes the footgun for
  every agent; the client guard is redundant and client-only.
- **Relocate** the prominent idle-worker alert: the awareness must come from the server
  (`workers_idle` notification + the de-prescriptivized advisor + the `sitrep.standing`), so the
  controller stops special-casing it. Controller just renders server signals.

## Fog-of-war rules (summary)

| Field | Rule |
|---|---|
| your own counts/food/queen/orders | full truth |
| enemy army value | scouted combat units only, with staleness; else `unknown` |
| enemy food/income | `not_observable` (+ scouted worker/larder proxy) |
| enemy territory % | scouted estimate or `unknown` |
| enemy queen HP | real only while sieging/scouted, else `unknown` |
| enemy army position / structures | last-scouted, with `seen_tick`/`age_ticks`; else not seen |

## Testing

Engine-level tests (construct a `World`, drive intents, assert `sitrep` fields), mirroring the
rally regression test:
- Frozen-at-rally army with `attack_target` set → `orders[attack_target].status == "no_effect"`.
- Released/advancing army → `status == "advancing"` (center-of-mass x increases).
- `rally_release_at` unmet → `orders[rally].status == "holding"` with staged/needed counts.
- Unscouted enemy → `standing.military.enemy == "unknown"`; after scouting → real value + staleness.
- Enemy economy always `not_observable`.
- `field.enemy_army.seen == false` until a unit has seen the enemy.

## Rollout

Server-side change → ships via `deploy.sh` (restarts the remote game server), **batched with the
already-pending engine changes** (rally-override fix + `workers_idle` notification) so it's one
coordinated deploy that interrupts a live match only once. Controller restart afterward to render
the `sitrep` and drop its special-casing. No frontend change required.
