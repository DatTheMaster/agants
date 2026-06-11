# Agants — Claude Reference

Ant colony RTS/MMO simulation. Two colonies (RED/BLUE) compete on "The Crossing",
a fixed 150×100 3-lane map. LLMs and MCP agents command colonies via a persistent
**directive** system plus direct unit commands.

- **Vault** → `projects/agants/passdown.md` — current session handoff + next priorities  *(read first)*
- **Vault** → `projects/agants/overview.md` — full reference (infra, arch, schema, constants, design decisions)
- **Vault** → `projects/agants/history.md` — all session changelogs + lessons learned
- **Vault** → `projects/agants/hermes-feedback.md` — agent playtest feedback
- **NOTES.md** — open items + raw observations (keep lean; triage at session end)
- **ROADMAP.md** — Phase 3–5 scope

**Model dispatch:** Sonnet is the default. Spawn Fable or Opus via the Agent tool
for multi-file architecture, deep reasoning, or long-context review — without asking first.

---

## Deployment

- Frontend: `agants.datthemaster.com` (CF Pages)
- Game server: `api.datthemaster.com` (CF named tunnel — stable across restarts)
- Auth worker: `agants-auth.hermesagent424.workers.dev` (D1-backed)
- Deploy: `bash deploy.sh` (sync + restart) · `--pages` to redeploy frontend
- **`AGANTS_AUTH_URL` + `AGANTS_AUTH_SECRET` not synced by deploy.sh** — set manually via SSH if `.env` is recreated
- **`PUBLIC_URL=https://api.datthemaster.com`** must be in remote `.env`
- `VERSION = "0.1.0"` — bump only at real releases. `BUILD` = git short hash.

## Run

```bash
python3 server.py                        # binds 0.0.0.0:8083
python3 mcp_server.py                    # stdio MCP (default: api.datthemaster.com)
python3 mcp_server.py --port 8084        # HTTP+SSE MCP
python3 controller/controller.py --setup # first-time wizard
```

Logs → `logs/run_TIMESTAMP.log`

---

## Session Handoff Protocol

1. Update **vault** `projects/agants/passdown.md` — replace with lean handoff (what changed, bugs, next priorities)
2. Append session summary to **vault** `projects/agants/history.md` under a new `## Session N` heading
3. Strip **NOTES.md** — move resolved items to history, keep only open items + fresh observations
4. Update **Deployment** section above if URLs/env vars changed
5. New session reads: this file → `passdown.md` → `overview.md` (if architecting) → `history.md` (if tuning)
