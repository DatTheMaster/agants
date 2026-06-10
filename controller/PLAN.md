# Agants Controller — Build Plan

Standalone local tool for running an AI agent in Agants matches.
Fully independent of game server code — talks via REST API only.
Distributable separately from the game (own requirements.txt, own README).

---

## File layout

```
controller/
  controller.py      # single-file implementation (~500-700 lines)
  requirements.txt   # openai, rich, httpx
  README.md          # quickstart: config → run → play
  config.example.json
```

---

## Config

Stored at `~/.config/agants/config.json` (or `./controller.json` if present locally).
Created interactively on first run (`controller.py --setup`).

```json
{
  "game_url": "https://api.datthemaster.com",
  "api_key":  "your-agants-api-key",
  "llm": {
    "base_url": "https://api.openai.com/v1",
    "api_key":  "sk-...",
    "model":    "gpt-4o"
  }
}
```

Same `base_url` + `model` pattern as the server's `providers.json`.
Supports any OpenAI-compatible provider (Anthropic, OpenRouter, Groq, Ollama, etc.).

---

## TUI layout (rich.live)

```
┌─ AGANTS CONTROLLER ──────────────────────────────────────────────────┐
│ ┌─ Matches ──────────────────┐  ┌─ Colony State ───────────────────┐ │
│ │ [●] a1b2c3d4  RED: Hermes  │  │ Tick 247  RED  food:450 dirt:120 │ │
│ │     BLUE: open             │  │ Workers:18 Soldiers:12 Scouts:4  │ │
│ │ [○] ff112233  ended        │  │ Income: 8.2/s  Army value: 2400  │ │
│ │                            │  │ ── Recent events ──────────────  │ │
│ │ Online agents (2):         │  │ t247 rally released (14 soldiers)│ │
│ │  Hermes    RED  3s ago     │  │ t243 watchtower built at (48,50) │ │
│ │  SkyAgent  —    41s ago    │  │ t240 queen under attack! 720hp   │ │
│ └────────────────────────────┘  └──────────────────────────────────┘ │
│ ┌─ Agent log ───────────────────────────────────────────────────────┐ │
│ │ [t247] get_state → ok                                             │ │
│ │ [t247] patch_directive military.attack_target=[136,50] → ok       │ │
│ │ [t247] thinking: enemy queen at 720hp, 14 soldiers in siege...    │ │
│ └───────────────────────────────────────────────────────────────────┘ │
│  [j] join  [n] new match  [w] watch  [q] quit                         │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Tool loop

Uses `openai` SDK (provider-agnostic via `base_url`).
Tools are defined inline as JSON schema — NOT imported from mcp_server.py.

```python
TOOLS = [
    patch_directive_schema,   # PATCH /api/matches/{mid}/directive/{cid}
    send_command_schema,      # POST  /api/matches/{mid}/command/{cid}
    get_intel_map_schema,     # GET   /api/matches/{mid}/intel_map/{cid}
    send_chat_schema,         # POST  /api/chat
]

async def agent_loop(match_id, colony_id):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    last_tick = -1
    while running:
        state   = GET /api/matches/{mid}/state/{cid}   # includes Authorization header
        notifs  = GET /api/matches/{mid}/notifications/{cid}
        if state["tick"] == last_tick:
            await asyncio.sleep(0.2); continue
        last_tick = state["tick"]

        # Inject current state as a user message (trim old state messages)
        messages = trim_context(messages)
        messages.append({"role": "user", "content": format_state(state, notifs)})

        response = llm.chat.completions.create(model=..., messages=messages, tools=TOOLS)
        messages.append(response.choices[0].message)

        for call in (response.choices[0].message.tool_calls or []):
            result = execute_tool(call, match_id, colony_id)
            messages.append({"role": "tool", "content": json.dumps(result), "tool_call_id": call.id})
            log(f"[t{last_tick}] {call.function.name} → {result.get('ok','err')}")
```

Key design choices:
- State is injected as a user message, not system (avoids prompt caching issues)
- Old state messages pruned to keep context window lean (keep last 6 turns + system)
- All REST calls include `Authorization: Bearer <token>` so presence is tracked
- Loop polls at 5 Hz between ticks, idles when `tick == last_tick`

---

## Match management commands

Keyboard shortcuts in the TUI:
- `j` — join: prompts for match_id + colony (0=RED, 1=BLUE), calls POST /api/matches/{mid}/seat/{cid}
- `n` — new match: calls POST /api/matches, then auto-joins colony 0
- `w` — watch: opens the match in the browser (system `open` / `xdg-open`)
- `q` — quit: releases seat gracefully then exits

---

## System prompt

Tight, game-specific. Key sections:
1. What you are (AI agent, colony colour, match context)
2. Map constants (nest positions, ridge positions, key distances)
3. Directive schema (spawn ratios, military fields, triggers)
4. Strategy heuristics (when to rally, when to build larder, siege_priority)
5. Tool list with one-liner per tool

No general reasoning preamble — the model is already capable; waste no tokens.

---

## Requirements

```
openai>=1.0
rich>=13.0
httpx>=0.25
```

No game server imports. No mcp package. No anthropic package.

---

## Build order

1. Config loading + `--setup` wizard
2. REST client helpers (get/post/patch/delete with base_url + auth headers)
3. Tool definitions (JSON schema dicts for the 4 core tools)
4. Agent loop (no TUI yet — just prints to stdout)
5. Rich TUI layout (live panels)
6. Match management (join/new/watch)
7. README + config.example.json

Start with step 1-4 to get a working (headless) agent, then layer in the UI.
