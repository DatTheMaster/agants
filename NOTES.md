# Agants — Notes

*Running scratchpad. Strip and triage at end of each session — move completed items to vault.*

---

## Open

- [ ] Test opponent-auto-start fix: run match where Hermes calls `game_control(start)` from MCP before controller presses `[s]` — verify agent loop auto-launches
- [ ] Zombie match `a82f20cd` on server (tick 986, no agents) — close or let it time out
- [ ] Forfeit MCP tool — endpoint + controller key exist, `mcp_server.py` not wired yet
- [ ] Stale ant IDs — LLM occasionally commands dead ants (404s); low priority

---

## Observations

- Ant shapes at 1:1 scale worth checking in a real game — triangle soldier head may need more mass to read clearly at small sizes
- Workers defending the queen by physically blocking is effective — consider a counter eventually (AoE unit? knockback?)
- Controller left pane during lobby: when both seats are filled but game hasn't started yet, the lobby list is still showing — consider collapsing or swapping to a "waiting for start" view
