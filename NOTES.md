# Agants — Notes

*Running scratchpad. Strip and triage at end of each session — move completed items to vault.*

---

## Open

- [ ] Worker idle redistribution — 25-30+ workers idle despite open food nodes after priority_food depletes; workers committed to far-away nodes don't re-scan for nearby alternatives
- [ ] Rally auto-release timeout — if staged count never reaches release_at, no warning; needs notification after N ticks of no progress
- [ ] Rally + attack_target doc gap — agents should set attack_target AFTER rally releases; needs a note in mcp_server.py docstring
- [ ] Larder 20-tile min — noted in build_structure docstring; confirm it's visible in MCP tool descriptions agents actually see
- [ ] Stale ant IDs — LLM occasionally commands dead ants (404s); low priority, needs cleaner error message

---

## Observations

- CF Pages auto-deploys from GitHub push break `AGANTS_BACKEND` (middleware strips to empty). Fixed: static `config.js` now has hardcoded production defaults; middleware falls through when env vars not baked in. **Always** use `bash deploy.sh --pages` for frontend deploys — never rely on git push auto-deploy.
- CF tunnel does NOT support long-lived SSE (HTTP/2 breaks the stream). All MCP clients must use streamable-http. `"type": "sse"` returns 406.
- History API returns `ended_at` (not `finished_at`) for finished matches.
- `brain_type` is always `"mcp"` in API responses — even for bot seats. Null agent + non-lobby phase = bot is the only reliable detection.
- CF API token in .env is tunnel-scoped only — cannot read/write DNS records. DNS changes require the CF dashboard manually.
