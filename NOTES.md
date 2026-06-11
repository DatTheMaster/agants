# Agants — Notes

*Running scratchpad. Strip and triage at end of each session — move completed items to vault.*

---

## Open

- [ ] Stale ant IDs — LLM occasionally commands dead ants (404s); low priority
- [ ] Feedback jsonl routing — 10 items from session 36 didn't land in `logs/agent_feedback.jsonl`; race fix is in controller but server-side write may be broken; check `POST /api/feedback` handler
- [ ] Larder 20-tile min — added to `build_structure` docstring this session; confirm it flows into MCP tool descriptions seen by agents
- [ ] Rally + attack_target interference — setting `attack_target` while rallying scatters soldiers before release; needs a doc note: set attack_target AFTER rally releases
- [ ] Legend for buildings and ant types on the frontend
- [ ] Waypoints as a pathfinding structure — deferred design idea
- [ ] Worker idle redistribution — 25-30 of 40+ workers idle despite open food nodes; priority_food directive doesn't pull workers already committed to far-away nodes (known)

## Done this session

- [x] Dirt gather bug — **CONFIRMED** via live local test: `carrying_type=dirt` at tick 49, 3 deliveries × 8 dirt in 30 ticks
- [x] upgrade_reserve stale-after-purchase — confirmed working (Scout T0→T1, reserve auto-cleared)
- [x] Match persistence — immediate save at game start; stale .tmp cleanup on load; match_brains/finished_seats round-trip
- [x] build_structure docstring — PLACEMENT CONSTRAINTS added: larder ≥20 tiles, guard post ≤35 tiles from nest
- [x] carrying_type added to unit state REST serialization (was missing; made dirt debugging impossible)
- [x] Test script fixed — command format was wrong (command_unit → unit_command, id → ant_id, cmd → command); dirt node coords fixed (pos[0]/pos[1])
- [x] Deployed full session 37 batch (bash deploy.sh); remote back up

---

## Observations

- Dirt gather cycle: pick up at dist ≤ 4 → return to nest (5 tiles) → deliver → repeat. ~8–10 ticks per trip = 8 dirt/trip at DIRT_DELIVER=8. Fast turnaround because home dirt node (22,50) is only 8 tiles from nest.
- upgrade_reserve auto-clear: previously agents had to manually clear after buying — now automatic. May change how aggressive Hermes is with upgrade planning.
- Remote server was down this whole session; deploy brought it back. Build shows "dev" instead of git hash — likely git not available on remote or working dir isn't a git repo.
