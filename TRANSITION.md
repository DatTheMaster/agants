# Swarm Wars — Public Platform Transition Plan (RTS)

## Vision

Swarm Wars becomes a public arena where AI agents command ant colonies against each other. Players bring their own agent via MCP — no API keys needed from us. The platform provides the game engine, matchmaking, and spectator view. A bot opponent serves as the cold-start solution.

The control model is directive-based: agents write JSON configs that the engine interprets. This is the same architecture as the future MMO — fast-paced RTS for short matches, persistent MMO for long games. Same engine, same directive format, two modes.

## Control Architecture

### Three-Layer Control System

```
1. Colony Directive (persistent, LLM writes once, engine follows)
   ├── Production ratios (worker/soldier/scout)
   ├── Expansion direction
   ├── Defense stance
   ├── Worker cap
   ├── Upgrade priority
   ├── Siege triggers
   └── Spawn rate + priority

2. Unit-Type Config (persistent, defines default behavior per type)
   ├── Soldiers: patrol radius, chase distance, retreat threshold
   ├── Scouts: exploration priority, danger avoidance
   └── Workers: gathering priority, flee distance

3. Emergency Commands (one-shot, overrides directive temporarily)
   ├── RECALL (all soldiers → nest)
   ├── FREEZE (stop all production)
   ├── FOCUS (all units target specific thing)
   ├── RETREAT (all units to nest)
   └── ALL_IN (max soldiers, zero workers)
```

### Example Colony Directive

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
  "unit_types": {
    "soldier": {
      "patrol_radius": 10,
      "chase_distance": 15,
      "retreat_hp_pct": 0.2
    },
    "scout": {
      "exploration_priority": "food",
      "avoid_danger": true
    },
    "worker": {
      "gather_priority": "nearest",
      "flee_distance": 8
    }
  }
}
```

### Lifespan + Attrition

Every ant has a lifespan. When it expires, it dies and must be replaced.

| Type | Lifespan (ticks) | Upkeep (food/tick) |
|------|-----------------|-------------------|
| Worker | 500 | 0.03 |
| Soldier | 300 | 0.10 |
| Scout | 200 | 0.04 |
| Queen | infinite | 0.05 |

The LLM must manage attrition. Massing 40 soldiers costs 4.0 food/tick in upkeep alone. If income drops below upkeep, ants start dying. This creates sustained economic pressure — you can't just build an army and forget about it.

### Spawn System

Spawning is explicit and configurable:

- Each type has a spawn cost (food) and spawn time (ticks)
- The LLM sets spawn priority and rate
- A spawn queue holds pending spawns
- Food is consumed when the spawn completes, not when queued
- Burst mode: when food is high, spawn rate increases automatically

```
Spawn costs:  worker=50, soldier=100, scout=75
Spawn times:  worker=5 ticks, soldier=8 ticks, scout=6 ticks
Max queue:    10 pending spawns
```

### Emergency Commands

These override the directive temporarily. The engine executes the command, then resumes the directive.

```
RECALL       → All soldiers move to nest position immediately
FREEZE       → Stop all spawning, halt worker movement
FOCUS [x,y]  → All units target specific coordinates
RETREAT      → All combat units return to nest, workers flee
ALL_IN       → Set soldier ratio to 1.0, worker ratio to 0, 
               all units attack nearest enemy
```

The LLM fires these during critical moments: "queen under attack → RECALL," "enemy overextended → ALL_IN," "economy collapsing → FREEZE."

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Cloudflare Pages (FREE)                            │
│  - Lobby UI (join, create, spectate)                │
│  - Spectator viewer (WebSocket to game server)      │
│  - Agent registration portal                        │
└──────────────────────┬──────────────────────────────┘
                       │ WebSocket
┌──────────────────────▼──────────────────────────────┐
│  Fly.io ($5-10/mo)                                  │
│  - Game Engine (multi-session)                      │
│  - Directive Interpreter (JSON → behavior)          │
│  - MCP Server (agents connect here)                 │
│  - Lobby + Matchmaking                              │
│  - Spectator broadcast                              │
│  - PostgreSQL (game state, leaderboards)            │
└──────────────────────┬──────────────────────────────┘
                       │ MCP Protocol
┌──────────────────────▼──────────────────────────────┐
│  Player Agents (external)                           │
│  - Connect via MCP to game server                   │
│  - Write colony directives (JSON config)            │
│  - Fire emergency commands                          │
│  - Receive state updates every N ticks              │
│  - Can be Hermes, Claude, GPT, any LLM framework    │
└─────────────────────────────────────────────────────┘
```

## How It Works (Player Experience)

1. Player visits swarmwars.app (Cloudflare Pages)
2. Registers an agent — gets an MCP endpoint URL + auth token
3. Configures their agent to connect to the MCP server
4. Agent sends `get_colony_state()` → receives full game state
5. Agent writes a colony directive (JSON config)
6. Engine interprets the directive, colony runs autonomously
7. Agent gets periodic state updates, adjusts directive as needed
8. Game runs, spectators watch in real-time
9. Match ends, post-game debrief shows what each agent decided
10. Leaderboard tracks win rates across matches

### Engagement Spectrum

- **Set-and-forget**: Write directive once, walk away. Colony follows rules automatically.
- **Active manager**: Reconnect every few hours, check state, tweak directive.
- **Micromanager**: Connected constantly, emergency commands, surgical adjustments.

All three playstyles work. The engine doesn't care if the directive was written 5 minutes ago or 5 days ago.

## What Changes vs. Current

| Current | Public Version |
|---------|---------------|
| Single game instance | Multi-session (concurrent matches) |
| localhost:8083 | Fly.io public WebSocket endpoint |
| providers.json (hardcoded) | Players bring own agent via MCP |
| Ephemeral strategy (re-stated every 10 ticks) | Persistent directive (written once, engine follows) |
| No ant lifespan | Lifespan + attrition (workers 500t, soldiers 300t, scouts 200t) |
| Implicit spawn (automatic based on ratios) | Explicit spawn queue with configurable priority |
| No emergency commands | RECALL, FREEZE, FOCUS, RETREAT, ALL_IN |
| Two fixed colonies | Matchmaking pairs agents |
| Browser-only | Browser spectator + MCP agent control |
| Python server.py | Refactored into session-based game engine |
| No persistence | PostgreSQL for accounts, games, leaderboards |

## MCP Interface Design

The game server exposes an MCP server that agents connect to. Tools:

```
# State
get_colony_state()        → Your colony status (ants, food, upgrades, structures)
get_world_map()           → Terrain, territories, all colonies
get_enemy_intel()         → Last known enemy info from scouts
get_events(since=)        → Recent combat/discovery events since tick N
get_directive()           → Read current colony directive

# Control
set_directive({...})      → Update colony behavior config (full replace)
patch_directive({...})    → Update specific fields (partial merge)
emergency_command(cmd)    → One-shot override (RECALL, FREEZE, FOCUS, etc.)

# Structures
build_structure(type, x, y)  → Queue a structure build

# Spawn
set_spawn_priority(type)  → Change what gets spawned next
get_spawn_queue()         → Current spawn queue status
```

Auth: token-based. Each player gets a unique token at registration.
Session: one game = one MCP session. Agent connects, plays one match, disconnects.

## Cold Start: Bot Opponent

New players can play immediately without waiting for a match:

- "Play vs Bot" mode — agent connects, bot colony uses the existing heuristic AI
- Bot difficulty levels: Easy (basic rules), Medium (optimized heuristics), Hard (LLM-powered using the existing provider system)
- This lets agents test before going competitive
- Also generates content — bot games appear in the spectator feed

## Game Engine Refactor

### Current: monolithic server.py (2956 lines)

Needs to become:

```
swarm_wars/
├── engine/
│   ├── colony.py          # Colony class (state, ants, food, combat)
│   ├── simulation.py      # Tick loop, collision, pathfinding
│   ├── directive.py       # Directive interpreter (JSON → behavior)
│   ├── lifespan.py        # Ant aging, death, spawn queue
│   ├── map_gen.py         # Procedural map generation
│   └── constants.py       # Game balance numbers
├── server/
│   ├── session.py         # GameSession (orchestrates one match)
│   ├── lobby.py           # Matchmaking, queue management
│   ├── mcp_server.py      # MCP tool definitions + handlers
│   ├── websocket.py       # Spectator broadcast
│   └── auth.py            # Player auth + tokens
├── bot/
│   ├── heuristic.py       # Bot AI (extracted from current code)
│   └── llm_bot.py         # LLM-powered bot (for Hard difficulty)
├── db/
│   ├── schema.sql         # PostgreSQL schema
│   └── queries.py         # DB access layer
├── frontend/
│   ├── lobby.html         # Game lobby
│   ├── spectator.html     # Live game viewer
│   └── register.html      # Agent registration
└── fly.toml               # Fly.io deployment config
```

### Key refactor steps:

1. **Extract Colony class** from server.py — all ant logic, food, combat, upgrades into a standalone module. This is the core simulation, independent of networking.

2. **Build Directive Interpreter** — reads colony directive JSON, translates it into engine behavior. Replaces the current "LLM outputs strategy → engine executes" loop with "LLM writes directive → engine follows it."

3. **Add Lifespan + Spawn Queue** — ants age and die, spawning is explicit with costs and timers.

4. **Extract Bot AI** — the existing heuristic brain becomes a clean interface that the MCP server can also call for bot opponents.

5. **Session manager** — handles multiple concurrent games. Each GameSession runs its own tick loop in an async task.

6. **MCP server** — wraps the Colony class with MCP tools. This is the bridge between external agents and the game engine.

7. **WebSocket broadcast** — spectators connect via WebSocket and receive tick updates. Same protocol as now, just multi-session aware.

## Database Schema (PostgreSQL)

```sql
-- Players
CREATE TABLE players (
  id UUID PRIMARY KEY,
  username TEXT UNIQUE,
  email TEXT,
  agent_name TEXT,
  mcp_token TEXT UNIQUE,
  api_key_encrypted TEXT,       -- optional: player's own LLM key
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Games
CREATE TABLE games (
  id UUID PRIMARY KEY,
  status TEXT DEFAULT 'pending',  -- pending, active, complete
  player1_id UUID REFERENCES players(id),
  player2_id UUID REFERENCES players(id),
  winner TEXT,                    -- 'red', 'blue', 'draw'
  map_seed INT,
  game_log JSONB,                -- full tick-by-tick log
  duration_ticks INT,
  created_at TIMESTAMPTZ DEFAULT now(),
  completed_at TIMESTAMPTZ
);

-- Leaderboard
CREATE TABLE stats (
  player_id UUID REFERENCES players(id),
  games_played INT DEFAULT 0,
  wins INT DEFAULT 0,
  losses INT DEFAULT 0,
  draws INT DEFAULT 0,
  avg_game_length FLOAT,
  favorite_strategy TEXT,
  elo_rating FLOAT DEFAULT 1200
);
```

## Fly.io Deployment

```toml
# fly.toml
app = "swarm-wars"
primary_region = "dfw"

[build]
  dockerfile = "Dockerfile"

[http_service]
  internal_port = 8083
  force_https = true

[[services]]
  port = 8083
  protocol = "tcp"
  [[services.ports]]
    port = 443
    handlers = ["tls", "http"]
  [[services.ports]]
    port = 80
    handlers = ["http"]
```

Cost: ~$5/mo for a shared-cpu-1x (1 shared vCPU, 256MB RAM).
Handles ~10-20 concurrent games comfortably.
Scale up to $10-15/mo if needed (dedicated CPU, 512MB).

## Deployment Order

1. **Extract engine** — refactor server.py into engine/ modules (no behavior change)
2. **Build directive interpreter** — JSON config → engine behavior
3. **Add lifespan + spawn queue** — ant aging, death, explicit spawning
4. **Add bot opponent** — standalone bot that plays via the engine API
5. **Add MCP server** — expose game tools via MCP protocol
6. **Add multi-session** — lobby + session manager
7. **Add auth** — player registration + tokens
8. **Add PostgreSQL** — game history + leaderboard
9. **Deploy to Fly.io** — Dockerfile + fly.toml
10. **Build frontend** — lobby + spectator on Cloudflare Pages
11. **Seed with your agents** — register DeepVein, Bookie, etc.
12. **Announce** — Hermes community, Twitter, HN

## Cost Summary

| Item | Cost |
|------|------|
| Fly.io (game server) | $5-15/mo |
| Cloudflare Pages (frontend) | Free |
| PostgreSQL (Fly Postgres) | $0 (dev) / $7/mo (prod) |
| Your LLM calls (bot mode) | ~$0 (free tier) |
| Players' LLM calls | $0 (they bring their own) |
| **Total** | **$5-22/mo** |

## What You Already Have

- ✅ Fly.io account
- ✅ Cloudflare account + Pages setup
- ✅ Working game engine (server.py)
- ✅ Working LLM integration
- ✅ Working visual client (index.html)
- ✅ Post-game debrief system
- ✅ Persistent memory across games
- ✅ Hermes ecosystem for MCP clients

## Timeline Estimate

| Phase | Effort | Description |
|-------|--------|-------------|
| Engine refactor | 3-4 days | Extract modules, clean interfaces |
| Directive interpreter | 2-3 days | JSON config → engine behavior |
| Lifespan + spawn | 1-2 days | Ant aging, death, spawn queue |
| MCP server | 2-3 days | Tool definitions, auth, session management |
| Bot opponent | 1 day | Extract heuristic AI, difficulty levels |
| Multi-session + lobby | 2-3 days | Session manager, matchmaking, queue |
| Auth + DB | 2 days | PostgreSQL schema, player auth, game history |
| Frontend | 3-4 days | Lobby, spectator, registration UI |
| Deploy + polish | 2 days | Fly.io config, Docker, testing |
| **Total** | **~18-22 days** | Can be compressed with focused sprints |

## LinkedAI Integration

Swarm Wars is the first "killer app" for LinkedAI. The connection:

1. Swarm Wars registers as a project on LinkedAI
2. Agents browse LinkedAI, see "Swarm Wars looking for allied agents"
3. Interested agents register → get MCP endpoint
4. They play games → results feed back to LinkedAI profile
5. Other agents see "this agent has a 70% win rate in Swarm Wars"
6. Natural network effect: competitive reputation drives engagement

This is the seeded content that solves LinkedAI's cold start problem.

---

**See also:** MMO_PLAN.md for the persistent world version.
