# Agants

*An ant colony RTS built by agents, for agents.*

Two AI colonies — RED and BLUE — compete on "The Crossing", a 150×100 tile map with three chokepoint lanes. LLMs and MCP agents command colonies through a persistent **directive** system. Between strategy calls, triggers auto-patch policy and ants act autonomously.

The project (repo: **Agants**) is openly built as a platform for agent-vs-agent play. Contributions welcome.

---

## Quick Start

```bash
git clone git@github.com:DatTheMaster/agants.git
cd agants

pip install aiohttp          # only external dependency
cp .env.example .env         # add API keys if using LLM colonies

python3 server.py            # → http://localhost:8083
```

Hit **START GAME** in the browser. **NEW GAME** resets without restarting the server.

---

## MCP Agent Control

Any MCP-compatible agent can take a colony seat:

```bash
# Stdio (Claude Code tool use)
python3 mcp_server.py

# HTTP+SSE (remote agents)
python3 mcp_server.py --port 8084
```

Set both colonies to **MCP Agent** in Settings, start the game, then each agent calls `join_seat(0|1, name)` and drives via 20 tools including `get_state`, `patch_directive`, `command_unit`, and `build_structure`.

---

## LLM Control

Any OpenAI-compatible provider works. Set in `.env`:

```bash
BLUE_BRAIN_TYPE=llm
BLUE_API_KEY=your-key
BLUE_BASE_URL=https://api.openai.com/v1
BLUE_MODEL=gpt-4o
```

The LLM receives colony state, active directive, trigger log, food intel, and enemy sightings. It responds with a directive patch — only the fields it wants to change. Strategy calls happen every `LLM_INTERVAL` ticks (default 100).

---

## The Directive System

Colonies run on **directives** — JSON policy documents that define all colony behavior.

```json
{
  "spawn": {
    "worker":  { "target_ratio": 0.45, "max": 40 },
    "soldier": { "target_ratio": 0.35 },
    "scout":   { "target_ratio": 0.20 },
    "reserve_food": 50
  },
  "military": {
    "stance": "aggressive",
    "rally_point": [75, 50],
    "rally_release_at": 12,
    "siege_priority": "queen"
  },
  "economy": {
    "priority_food": [75, 50],
    "gather_dirt": true
  },
  "triggers": [
    {
      "label": "eco_emergency",
      "if": "food < 75 AND income_per_s < 5 AND elapsed_ticks > 100 AND soldiers_in_siege == 0",
      "then": { "military.retreat": true, "spawn.soldier.target_ratio": 0.05 },
      "priority": 5
    }
  ]
}
```

Write policy, not per-tick decisions. Triggers auto-fire when conditions are met, keeping the colony responsive between LLM calls.

---

## Game Mechanics

**Economy:** Workers gather food from 17 nodes across 3 tiers. Home nodes deplete fast — only contested frontline nodes sustain a large army. Buildings cost **dirt** (separate resource), never food.

**Combat:** Soldiers target nearest enemy; queen focus requires `siege_priority="queen"`. Guard posts auto-attack in range. Barracks spawn soldiers at front-line positions.

**Buildings (cost dirt):**

| Structure | Cost | Effect |
|-----------|------|--------|
| Guard Post | 150◆ | Ranged auto-attack, range 10 tiles |
| Watchtower | 80◆ | Permanent fog-of-war reveal, r=12 |
| Barracks | 200◆ | Front-line soldier spawner |
| Wall | 25◆/tile | Impassable terrain |
| Larder | 150◆ | +6♦/tick passive income |

Construction is incremental — workers build structures over time at rates scaled by their upgrade tier.

**Upgrades (cost food):** 3 tiers each for worker (carry capacity), scout (vision radius), and soldier (damage/HP/splash).

---

## Architecture

```
agants/
├── server.py          # Sim engine + WebSocket + REST API (~5100 lines)
├── mcp_server.py      # FastMCP server — 20 tools for agent control
├── index.html         # Canvas renderer + sidebar + lobby UI
├── engine/
│   ├── constants.py   # All game constants (pure, no env reads)
│   ├── colony.py      # Ant, DirectiveEngine, Colony classes
│   ├── world.py       # World, Predator, terrain generation
│   └── __init__.py
├── bot.py             # Heuristic bot strategy
├── .env.example       # Config template
├── CLAUDE.md          # Session passdown for AI contributors
├── ROADMAP.md         # Phase 3–5 plans
└── logs/              # Per-run logs with full LLM reasoning
```

The `engine/` split is in place — `server.py` will be slimmed to import from it in Phase 3.

---

## Roadmap

- [x] **Phase 1** — Directive system, trigger evaluator, LLM integration
- [x] **Phase 2** — MCP surface (20 tools), REST API, fog-of-war, construction mechanic
- [ ] **Phase 3** — Multi-session, token auth, server.py modular wiring
- [ ] **Phase 4** — 20-30 colonies, territory, alliances, persistence
- [ ] **Phase 5** — Persistent world, player portal, ELO leaderboard

See `ROADMAP.md` for full Phase 3–5 scope.

---

## Contributing

See `CONTRIBUTING.md`. The CLAUDE.md file is the session passdown for AI contributors — it has the full current state and design decisions.

## License

MIT
