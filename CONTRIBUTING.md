# Contributing

Contributions are welcome. This is a small project with a fast-moving codebase — the best way to contribute is to play with it first.

## Setup

```bash
git clone git@github.com:DatTheMaster/swarm-wars.git agants
cd agants
pip install aiohttp
cp .env.example .env
python3 server.py
```

Open http://localhost:8083, start a game, watch it run. Read `CLAUDE.md` for the full current state and design decisions — it's the authoritative source for how everything works.

## Areas for Contribution

- **Game balance** — combat constants, food economy, building costs
- **Bot AI** — `bot.py` heuristic strategy improvements
- **MCP tools** — new tools in `mcp_server.py` that agents need
- **Canvas renderer** — `index.html` UI/UX improvements
- **Phase 3+ features** — see `ROADMAP.md`

## Code Style

- Python 3.10+, no type annotations required
- No external dependencies beyond `aiohttp` and `fastmcp` (for MCP server)
- Keep `engine/constants.py` pure — no env reads, no functions, no classes
- `CLAUDE.md` is the session passdown for AI contributors; keep it up to date

## Pull Requests

- One logical change per PR
- Include a brief description of what changed and why
- If changing game constants or balance, explain the reasoning
- Run a bot-vs-bot game to verify nothing is broken

## Filing Issues

Use GitHub Issues for bugs and feature requests. For balance discussions, open a Discussion.
