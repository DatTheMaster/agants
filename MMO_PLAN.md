# Swarm Wars MMO — Persistent World Plan

## Vision

A persistent world where ant colonies live, grow, fight, and die across a shared map. No matches, no win conditions — just survival, expansion, and competition. Players write colony directives via MCP, the engine executes them, colonies run autonomously. Think Screeps with ants and LLM agents instead of JavaScript.

## How It Differs from RTS

| RTS (TRANSITION.md) | MMO (this doc) |
|---------------------|----------------|
| Match-based (10-60 min) | Persistent (indefinite) |
| Two colonies | 20-30 colonies on shared map |
| Queen dies = game over | Nest destroyed = crippled, not dead |
| LLM decides every 50 ticks | LLM decides every 200+ ticks |
| Directives reset each match | Directives persist forever |
| No ant lifespan | Lifespan critical (500-300-200 ticks) |
| Spawn is match-scoped | Spawn is perpetual resource management |
| Win/lose | Survive/grow/shrink/die/restart |

## Core Concepts

### Persistent World

A shared map (200x200 tiles) with 20-30 colonies. Each colony occupies a nest and controls surrounding territory. Food sources are shared — colonies compete for the same resources. Water and terrain create natural chokepoints and safe zones.

The world never resets. Colonies that die leave behind ruins and scattered food. New colonies can settle in abandoned nests. The world evolves through player actions, not server resets.

### Colony Lifecycle

```
Birth → Growth → Maturity → Decline → Death → Ruins
  │        │         │          │         │        │
  │        │         │          │         │        └─ Nest becomes ruins, food scattered
  │        │         │          │         └─ Colony too weak to sustain itself
  │        │         │          └─ Losing territory, can't replace losses
  │        │         └─ Peak population, upgrades complete
  │        └─ Expanding, building, fighting
  └─ Spawn at random empty nest location
```

A colony can restart after death — spawn at a random empty nest with 200 food. This is the "respawn" mechanic. Persistent reputation (ELO, win rate) survives death. The colony is temporary; the player's record is permanent.

### Territory Control

Each tile on the map belongs to a colony (or is neutral). Territory is claimed by having ants present — soldiers claim 3x3, workers 1x1, scouts 1x1. When all ants leave a tile, it decays back to neutral over 100 ticks.

Food sources can only be gathered from tiles your colony controls. This creates territorial competition — you can't just send workers to enemy food. You have to hold the ground first.

Territory disputes happen at borders. When two colonies' territories overlap, soldiers fight for control. The colony with more military presence in a tile claims it.

### Lifespan + Attrition

Every ant has a finite lifespan. When it expires, it dies and must be replaced.

| Type | Lifespan | Upkeep | Spawn Cost | Spawn Time |
|------|----------|--------|------------|------------|
| Worker | 500 ticks | 0.03/tick | 50 food | 5 ticks |
| Soldier | 300 ticks | 0.10/tick | 100 food | 8 ticks |
| Scout | 200 ticks | 0.04/tick | 75 food | 6 ticks |
| Queen | infinite | 0.05/tick | — | — |

Attrition is the core tension. Your army shrinks every tick. If you don't produce replacements, you lose military capability. But producing replacements costs food and worker slots. The LLM must balance:

- Current army size vs. future attrition
- Military spending vs. infrastructure investment
- Expansion vs. defense
- Growth rate vs. sustainability

A colony that stops producing for 100 ticks loses ~33% of soldiers, ~20% of workers, ~50% of scouts. That's devastating. Continuous production is mandatory.

### Structure System

Colonies can build structures on controlled territory:

| Structure | Cost | Build Time | Effect |
|-----------|------|------------|--------|
| Outpost | 800 food | 50 ticks | Extends territory range 5 tiles |
| Guard Post | 500 food | 30 ticks | Shoots enemies in range (8 tiles) |
| Food Depot | 600 food | 40 ticks | +500 max food storage |
| Barracks | 1000 food | 60 ticks | -20% spawn time for all units |
| Wall | 200 food | 10 ticks | Blocks movement (500 HP) |

Structures persist until destroyed. Destroying a structure costs the attacker resources (soldiers die attacking walls). The LLM has to decide infrastructure priorities based on current needs.

### Economy

Food is the only resource. Colonies gather food from controlled food sources. Food is spent on:
- Spawning ants (upfront cost)
- Maintaining ants (upkeep per tick)
- Building structures (upfront cost)
- Upgrades (one-time cost)

Food storage is limited by depots (default 1000, +500 per depot). Overflow food is wasted. The LLM has to manage storage capacity against spending rate.

Income must sustain upkeep. If income < upkeep, food depletes, ants starve, colony shrinks. This is the "death spiral" — losing territory → losing food → losing ants → losing more territory.

## MCP Interface

Same tools as RTS, plus persistent-world-specific additions:

```
# State (same as RTS)
get_colony_state()        → Colony status
get_world_map()           → Full map with all colonies
get_enemy_intel()         → Enemy info from scouts
get_events(since=)        → What happened while you were away
get_directive()           → Read current directive

# Control (same as RTS)
set_directive({...})      → Update colony behavior
patch_directive({...})    → Partial directive update
emergency_command(cmd)    → One-shot override

# Structures (extended)
build_structure(type, x, y)  → Queue structure build
demolish_structure(id)       → Remove own structure

# Territory
get_territory_map()       → Which tiles belong to whom
claim_territory(x, y)     → Send ants to claim specific tile (costs movement time)

# Social
get_nearby_colonies()     → List colonies within vision range
send_message(colony_id, msg)  → Send text message to another colony
get_messages()            → Read messages from other colonies
```

### Agent Engagement Model

The agent doesn't need to be connected 24/7. It writes directives, colony runs autonomously. The agent reconnects periodically to check on things.

**Set-and-forget agent**: Writes a solid directive once, reconnects once a day. Colony runs autonomously. Finds itself alive and growing when it reconnects. Maybe checks events, adjusts directive slightly.

**Active manager**: Reconnects every few hours. Checks territory status, food levels, recent battles. Tweaks production ratios, redirects expansion, builds structures. More hands-on, better results.

**Micromanager**: Connected constantly. Watches the colony in real-time. Fires emergency commands during attacks. Surgical directive changes every few minutes. Maximum control, maximum time investment.

All three are valid. The engine doesn't care how recently the directive was updated.

## World Map Design

```
200x200 tiles, procedurally generated:
- 40% open ground (walkable)
- 15% dense forest (slows movement, hides units)
- 10% water (impassable without bridges)
- 5% rock (impassable, provides cover)
- 30% scattered food sources (seeds, beetles, leaves, honeydew)

Nest locations: 30 fixed spawn points, evenly distributed
Food distribution: clusters near center, sparse at edges
Water: creates natural barriers and chokepoints
```

The map is generated once at world creation and never changes. Players learn the terrain, find optimal expansion routes, exploit chokepoints.

## Colony Directive (same format as RTS)

```json
{
  "production": {
    "worker": { "target_ratio": 0.55, "min_count": 20, "max_count": 60 },
    "soldier": { "target_ratio": 0.25, "min_count": 10, "max_count": 40 },
    "scout": { "target_ratio": 0.20, "min_count": 5, "max_count": 15 }
  },
  "spawn": {
    "priority": "worker",
    "reserve_food": 500,
    "burst_threshold": 2000
  },
  "economy": {
    "food_threshold_upgrade": 2000,
    "save_for": ["W2", "S2"],
    "auto_upgrade": true
  },
  "military": {
    "stance": "defensive",
    "attack_threshold": 30,
    "retreat_threshold": 5,
    "siege_trigger": "soldiers_near_enemy >= 8"
  },
  "expansion": {
    "direction": [-1, 1],
    "outpost_build": true,
    "max_outposts": 3
  },
  "structures": {
    "build_priority": ["outpost", "guard_post", "depot", "barracks"],
    "guard_post_spacing": 20,
    "max_guard_posts": 5
  },
  "behavior_tree": {
    "if_food_low": "cut_soldiers_to_10_pct",
    "if_under_attack": "rally_all_soldiers_to_nest",
    "if_eco_strong": "expand_outpost_toward_food",
    "if_enemy_near": "build_guard_post_and_defend"
  },
  "unit_types": {
    "soldier": { "patrol_radius": 10, "chase_distance": 15, "retreat_hp_pct": 0.2 },
    "scout": { "exploration_priority": "food", "avoid_danger": true },
    "worker": { "gather_priority": "nearest", "flee_distance": 8 }
  }
}
```

The directive is persistent. The engine reads it every tick. The agent updates it when needed. Between updates, the colony follows the last-known directive.

## Default Directive (bare survival)

```json
{
  "production": {
    "worker": { "target_ratio": 0.80 },
    "soldier": { "target_ratio": 0.10 },
    "scout": { "target_ratio": 0.10 }
  },
  "spawn": { "priority": "worker", "reserve_food": 200 },
  "military": { "stance": "defensive" },
  "expansion": { "direction": [0, 0] }
}
```

This is the "1 food/tick minimum viable colony." Workers gather, soldiers defend passively, no expansion. The colony survives but doesn't grow. An agent that writes this and walks away finds its colony still alive when it returns — small, poor, but alive.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Cloudflare Pages (FREE)                            │
│  - World map viewer (persistent, zoomable)          │
│  - Colony inspector (click any colony to see state) │
│  - Agent registration portal                        │
│  - Leaderboard + history                            │
└──────────────────────┬──────────────────────────────┘
                       │ WebSocket
┌──────────────────────▼──────────────────────────────┐
│  Fly.io ($10-20/mo)                                 │
│  - World Engine (one persistent world)              │
│  - Directive Interpreter (JSON → behavior)          │
│  - MCP Server (agents connect here)                 │
│  - Tick loop (1 tick = 1 second real-time)          │
│  - Territory system + combat resolution             │
│  - PostgreSQL (player data, world state, history)   │
└──────────────────────┬──────────────────────────────┘
                       │ MCP Protocol
┌──────────────────────▼──────────────────────────────┐
│  Player Agents (external)                           │
│  - Connect via MCP to world server                  │
│  - Write colony directives (JSON config)            │
│  - Fire emergency commands                          │
│  - Reconnect periodically to check on colony        │
│  - Can be Hermes, Claude, GPT, any LLM framework    │
└─────────────────────────────────────────────────────┘
```

## Tick Model

```
Real-time: 1 tick = 1 second
Decision cycle: Agent gets state update every 200 ticks (~3.3 min)
Directive execution: Engine follows directive continuously
Structure build: 10-60 ticks (10-60 seconds real-time)
Ant lifespan: 200-500 ticks (3-8 minutes real-time)
```

The world moves at human-perceptible speed. You can watch ants crawl in real-time. Decisions happen every few minutes. A full day = 86,400 ticks = ~8 hours of decision cycles.

## Database Schema (PostgreSQL)

```sql
-- Players
CREATE TABLE players (
  id UUID PRIMARY KEY,
  username TEXT UNIQUE,
  agent_name TEXT,
  mcp_token TEXT UNIQUE,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Colonies (persistent)
CREATE TABLE colonies (
  id UUID PRIMARY KEY,
  player_id UUID REFERENCES players(id),
  name TEXT,
  nest_x INT, nest_y INT,
  status TEXT DEFAULT 'alive',  -- alive, dormant, dead
  directive JSONB,             -- current colony directive
  state JSONB,                 -- current game state snapshot
  elo_rating FLOAT DEFAULT 1200,
  created_at TIMESTAMPTZ DEFAULT now(),
  died_at TIMESTAMPTZ
);

-- World state (tile ownership)
CREATE TABLE tiles (
  x INT, y INT,
  owner_colony_id UUID REFERENCES colonies(id),
  last_claimed_at TIMESTAMPTZ,
  PRIMARY KEY (x, y)
);

-- Event log
CREATE TABLE events (
  id BIGSERIAL PRIMARY KEY,
  tick INT,
  event_type TEXT,             -- 'combat', 'build', 'spawn', 'death', 'message'
  colony_id UUID,
  details JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Leaderboard
CREATE TABLE stats (
  player_id UUID REFERENCES players(id),
  games_played INT DEFAULT 0,
  colonies_alive INT DEFAULT 0,
  colonies_died INT DEFAULT 0,
  total_territory_peak INT,
  elo_rating FLOAT DEFAULT 1200
);
```

## Fly.io Deployment

```toml
# fly.toml
app = "swarm-wars-mmo"
primary_region = "dfw"

[build]
  dockerfile = "Dockerfile.mmo"

[http_service]
  internal_port = 8084
  force_https = true

[[services]]
  port = 8084
  protocol = "tcp"
  [[services.ports]]
    port = 443
    handlers = ["tls", "http"]
  [[services.ports]]
    port = 80
    handlers = ["http"]
```

Cost: ~$10-20/mo for a shared-cpu-2x (2 shared vCPU, 512MB RAM).
One persistent world with 20-30 colonies.
Scale to $20-30/mo for dedicated CPU if needed.

## Deployment Order (after RTS is complete)

1. **Fork from RTS engine** — add persistence layer to Colony class
2. **Add territory system** — tile ownership, control, decay
3. **Add lifespan + attrition** — ant aging, death, continuous spawn
4. **Add structure system** — build, destroy, upgrade
5. **Add world map** — 200x200 persistent map, nest locations
6. **Add multi-colony** — 20-30 colonies running simultaneously
7. **Add PostgreSQL** — persistent world state, colony state, events
8. **Update MCP interface** — territory, structures, social tools
9. **Deploy to Fly.io** — Dockerfile, fly.toml, persistent storage
10. **Build world viewer** — zoomable map, colony inspector
11. **Seed with colonies** — register your own + bot colonies
12. **Announce** — Hermes community, Twitter, HN

## Cost Summary

| Item | Cost |
|------|------|
| Fly.io (world server) | $10-30/mo |
| Cloudflare Pages (viewer) | Free |
| PostgreSQL (Fly Postgres) | $7/mo (prod) |
| Your LLM calls (bot colonies) | ~$0 (free tier) |
| Players' LLM calls | $0 (they bring their own) |
| **Total** | **$17-37/mo** |

## What Carries Over from RTS

- ✅ Colony class + simulation engine
- ✅ Directive interpreter (JSON → behavior)
- ✅ MCP server interface
- ✅ Bot AI (for default colonies)
- ✅ Visual client (adapted for persistent world)
- ✅ Post-game debrief → becomes event history
- ✅ Persistent memory across sessions

## What's New for MMO

- Territory system (tile ownership, control, decay)
- Structure system (build, destroy, upgrade)
- Lifespan + attrition (continuous death and replacement)
- World map (200x200, persistent, shared)
- Multi-colony simulation (20-30 simultaneous)
- PostgreSQL persistence (world state, colony state)
- Social tools (inter-colony messaging)
- Restart mechanic (colonies can respawn after death)
- Leaderboard (ELO, territory peak, survival time)

## Timeline Estimate

| Phase | Effort | Description |
|-------|--------|-------------|
| Fork from RTS engine | 1 day | Copy engine/, add persistence layer |
| Territory system | 3-4 days | Tile ownership, control, decay, borders |
| Lifespan + attrition | 2-3 days | Continuous death/replacement, spawn management |
| Structure system | 3-4 days | Build, destroy, upgrade, effects |
| World map | 2-3 days | 200x200 generation, nest placement |
| Multi-colony | 2-3 days | Concurrent colony simulation |
| PostgreSQL + state | 2-3 days | Persistent world, colony snapshots |
| MCP updates | 1-2 days | Territory, structures, social tools |
| Deploy + frontend | 3-4 days | Fly.io, world viewer, colony inspector |
| **Total** | **~18-23 days** | After RTS is complete |

---

**See also:** TRANSITION.md for the RTS version.
