# Agants — Notes

*Running scratchpad. Strip and triage at end of each session — move completed items to vault.*

---

## Open

- [ ] Stale ant IDs — LLM occasionally commands dead ants (404s); low priority
- [ ] Rally clarity — document command_unit + rally interaction; add rally progress to get_battle_summary
- [ ] get_match_status tool — army value, economy, territory, food depletion ETA
- [ ] Bot harder — increase worker cap, build larder ~tick 200, more aggressive guard posts
- [ ] Watch full auto-challenge cycle — verify game-over feedback hook fires; check logs/agent_feedback.jsonl
- [ ] Retreat directive — verify military.retreat overrides auto_attack; document interaction with hardcoded "retreat" command
- [ ] Controller left pane during lobby: when both seats filled but game hasn't started, lobby list still showing — consider "waiting for start" view

---

## Observations

- spawn.{type}.min was never enforced before — default values (W=4, Sc=2, Sol=2) now active; watch for early-game behavior changes in first real match
- data/results/ survives deploys; first real match will populate match history
- Worker A* max_nodes 200→600 should fix returning workers getting stuck at ridges; will confirm in next playtest
- Guard post range ring (10t) now visible; watchtower halo (12t) unchanged
