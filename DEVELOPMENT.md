# Development Guide

## Project Structure

```
agants/
├── server.py          # Sim engine + WebSocket + REST API
├── mcp_server.py      # FastMCP server — 20 tools for agent control
├── index.html         # Canvas renderer + sidebar + lobby UI
├── bot.py             # Heuristic bot: update_bot_strategy(world, colony_id)
├── engine/
│   ├── constants.py   # Pure game constants — no env reads, no functions
│   ├── colony.py      # Ant, DirectiveEngine, Colony
│   ├── world.py       # World, Predator, gen_terrain
│   └── __init__.py    # Re-exports from all engine modules
├── .env               # Runtime config (gitignored)
├── .env.example       # Template
├── logs/              # Per-run logs (gitignored)
└── data/              # Persistent data (gitignored)
```

## Running

```bash
python3 server.py                      # game server → http://localhost:8083
python3 mcp_server.py                  # MCP stdio server
python3 mcp_server.py --port 8084      # MCP HTTP+SSE server
```

## Key Files

### server.py
The monolith. Contains:
- `_load_dotenv()` — must run before any env reads (no external dependency)
- Brain config (`RED_BRAIN`, `BLUE_BRAIN`, `LLM_INTERVAL`, `TPS`)
- LLM system prompt and helpers (`build_llm_prompt`, `parse_llm_response`)
- `RunLogger` — writes `logs/run_TIMESTAMP.log`
- `Server` class — WebSocket handler, REST API, `tick_loop`, `llm_loop_for`

The `engine/` split is in place but `server.py` still carries its own copies of the game classes. Phase 3 task: wire server.py to import from `engine/` and remove the duplicates.

### engine/constants.py
All game constants. **No env reads** — `TPS` and `LLM_INTERVAL` are runtime config and live in `server.py`. If a constant doesn't depend on env, it belongs here.

### engine/colony.py
`Ant`, `DirectiveEngine`, `Colony`. The directive schema lives in `DirectiveEngine.default_directive()`. `Colony.get_state()` is the main LLM/agent visibility surface.

### engine/world.py
`World.step()` is the main tick. `World.serialize_tick()` produces the WebSocket payload. `_behavior_*` methods are per-unit AI. `_assign_builders_to_site()` handles auto-build worker dispatch.

### bot.py
`update_bot_strategy(world, colony_id)` — the heuristic bot. Called by `tick_loop` every `LLM_INTERVAL` ticks for bot-brain colonies. Extracted from `server.py._update_bot_strategy` in v0.1.0.

### mcp_server.py
FastMCP server with 20 tools. Each tool maps to a REST endpoint or WebSocket command. Start with `python3 mcp_server.py` for stdio (Claude Code) or `--port N` for HTTP+SSE.

## Directive Schema

Full schema in `engine/colony.py → DirectiveEngine.default_directive()`. Key fields:

```python
{
  "spawn":    { "worker": {...}, "soldier": {...}, "scout": {...}, "reserve_food": 50 },
  "economy":  { "priority_food": None, "gather_dirt": False, "auto_upgrade": True },
  "military": { "stance": "balanced", "rally_point": None, "attack_target": None, ... },
  "unit_types": { "scout": { "expansion": [...], "patrol_waypoints": None }, ... },
  "triggers": [],
  "alerts":   []
}
```

## Adding a New Building

1. Add cost/HP/max constants to `engine/constants.py`
2. Add `BUILD_WORK_REQUIRED` entry
3. Handle the structure in `World.step()` (cost deduction, activation, per-tick effects)
4. Add the renderer case in `index.html`
5. Expose via `build_structure` MCP tool and `api_command` REST handler

## Adding a Trigger Variable

1. Add to `DirectiveEngine.eval_triggers()` in `engine/colony.py` — the `env` dict
2. Document it in the LLM system prompt in `server.py` (`_LLM_SYSTEM_TEMPLATE`)
3. Add to the trigger variable list in `mcp_server.py` tool docstrings

## Log Format

`logs/run_TIMESTAMP.log` contains:
- Header: map config, brain assignments, nest positions
- Per-10-tick snapshot: army counts, food, income for both colonies
- LLM calls: full prompt, reasoning (`<think>`), decision, feedback
- Post-game debrief (LLM colonies only)
- Final stats and token counts

Read the log first when debugging unexpected colony behavior.
