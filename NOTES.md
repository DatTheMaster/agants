# Agants — Notes

*Running scratchpad. Strip and triage at end of each session — move completed items to vault.*

---

## Open

- [ ] Stale ant IDs — LLM occasionally commands dead ants (404s); low priority
- [ ] Larder 20-tile min — in `build_structure` docstring; confirm it flows into MCP tool descriptions seen by agents
- [ ] Rally + attack_target interference — set `attack_target` AFTER rally releases; needs a doc note in mcp_server.py
- [ ] Worker idle redistribution — 25-30 of 40+ workers idle despite open food nodes; `priority_food` doesn't pull workers already committed to far-away nodes
- [ ] Waypoints as a pathfinding structure — deferred design idea

---

## Observations

- CF Pages auto-deploys from GitHub push break `AGANTS_BACKEND` (middleware strips to empty). Fixed: static `config.js` now has hardcoded production defaults; middleware falls through when env vars not baked in. Still: run `bash deploy.sh --pages` after any frontend change rather than relying on git push.
- History API returns `ended_at` (not `finished_at`) for finished matches.
- `brain_type` is always `"mcp"` in API responses — even for bot seats. Null agent + non-lobby phase = bot is the only reliable detection.
