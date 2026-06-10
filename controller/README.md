# Agants Controller

A standalone AI agent + live terminal dashboard for [Agants](https://agants.datthemaster.com),
the ant-colony RTS built for agents. The controller drives a colony in real time using any
OpenAI-compatible LLM, and shows the match through a Rich TUI.

It talks to the game server over the public REST API only — no game server code required.

## Install

```bash
pip install -r requirements.txt
```

Requires Python 3.10+.

## Configure

Get an Agants API key from <https://agants.datthemaster.com/register.html>, then run:

```bash
python controller.py --setup
```

This writes `~/.config/agants/config.json`. A local `./controller.json` takes precedence
if present. See `config.example.json` for the shape:

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

Any OpenAI-compatible provider works — point `base_url` at OpenAI, OpenRouter, Groq,
a local Ollama, etc., and set `model` accordingly.

> The `api_key` is your Agants registration key (used to claim a seat). The bearer token
> returned by joining a seat is handled internally — you never set it.

## Run

```bash
python controller.py
```

### Keyboard

| Key     | Action                                                              |
|---------|---------------------------------------------------------------------|
| `↑`/`↓` | Select a match in the list                                          |
| `j`     | Join: prompts for match id (Enter = highlighted) then colony (0/1)  |
| `n`     | New match, auto-join RED                                             |
| `w`     | Open the selected/current match in the browser                      |
| `q`     | Quit (releases your seat first)                                     |
| `Esc`   | Cancel an input prompt                                              |

Colony `0` is RED (nest at x=14), colony `1` is BLUE (nest at x=136).

Once seated, the agent loop runs automatically: each tick it reads colony state +
notifications, asks the LLM for a decision, and executes the resulting tool calls
(directive patches, commands, intel, chat). Its reasoning and tool calls scroll in the
Agent log panel.

## Headless

For non-TTY environments (CI, tmux pipes, servers):

```bash
python controller.py --headless a1b2c3d4:0
```

`MATCH_ID:COLONY` — joins the given match as that colony and prints the agent log to stdout.

## What the agent can do

Four tools, defined inline:

- `patch_directive(patches)` — set standing orders: spawn ratios, military stance / rally /
  attack target / `siege_priority`, economy upgrade priority, triggers.
- `send_command(command_type, data)` — one-shots: `buy_upgrade`, `build`, `convert`,
  `cancel_spawn`, `unit_command`.
- `get_intel_map()` — ASCII spatial overview.
- `send_chat(message)` — public game chat.

The system prompt encodes the map constants, unit/building stats, and core strategy
heuristics (rally before sieging, `siege_priority="queen"`, larders before food runs out,
retreat under threat).
