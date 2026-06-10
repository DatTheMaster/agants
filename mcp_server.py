"""
Swarm Wars MCP Server

Exposes MCP tools for AI agents to control a colony in a running Swarm Wars game.
Run alongside server.py (which must be running on localhost:8083).

Usage:
  python3 mcp_server.py               # stdio transport (for Claude Code / claude CLI)
  python3 mcp_server.py --port 8084   # HTTP+SSE transport

Seat discovery: agents can call list_seats() to see open seats, then join_seat() to claim one.
Two separate agents can each claim a different colony (RED=0, BLUE=1).
"""

import sys
import json
import argparse
import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = "http://localhost:8083/api"

mcp = FastMCP(
    "Swarm Wars",
    instructions=(
        "You are an AI commander controlling an ant colony in Swarm Wars.\n"
        "Two colonies (RED=0, BLUE=1) compete on 'The Crossing' — a 150x100 map.\n"
        "Core loop: get_state → analyze → patch_directive / issue_command → repeat.\n"
        "Check get_notifications periodically for urgent alerts (queen_under_attack, etc.).\n"
        "Use get_intel_map to see food sources, enemy sightings, and structures spatially.\n"
        "Directives persist between calls — only patch fields you want to change.\n"
        "SIEGE: soldiers attack the NEAREST enemy by default, so defenders shield the "
        "enemy queen. When soldiers_in_siege > 0, set military.siege_priority='queen' "
        "and watch combat.queen_dps_actual to confirm damage is landing.\n"
        "PLAY THE FULL GAME — winning agents use every lever, not just spawn ratios:\n"
        "  - Spend dirt: larders sustain late-game food; watchtowers buy vision; "
        "guard posts hold lanes; idle dirt is wasted tempo.\n"
        "  - Buy upgrades: permanent and compounding — usually better than more units. "
        "Hold reserve_food high enough to actually afford them.\n"
        "  - Mass attacks: soldiers sent one-by-one die one-by-one. Rally "
        "(rally_point + rally_release_at), then push together.\n"
        "  - Watch the 'advisor' list in get_state — it flags neglected levers.\n"
        "AVOID: economy.priority_food forces ALL workers to one node (it auto-clears "
        "when the node depletes); prefer letting saturation spread workers naturally."
    ),
)


def _result(r: httpx.Response) -> dict:
    """Return the JSON body even on HTTP error statuses — the server puts the
    actual reason (e.g. 'insufficient dirt: need 80◆, have 0◆') in the body."""
    try:
        body = r.json()
    except Exception:
        body = {}
    if r.status_code >= 400:
        body.setdefault("error", f"HTTP {r.status_code}")
        body["http_status"] = r.status_code
    return body


def _get(path: str, params: dict = None) -> dict:
    """Synchronous HTTP GET to game server REST API."""
    try:
        return _result(httpx.get(f"{BASE_URL}{path}", params=params, timeout=5.0))
    except httpx.HTTPError as e:
        return {"error": str(e)}


def _post(path: str, body: dict = None) -> dict:
    """Synchronous HTTP POST to game server REST API."""
    try:
        return _result(httpx.post(f"{BASE_URL}{path}", json=body or {}, timeout=5.0))
    except httpx.HTTPError as e:
        return {"error": str(e)}


def _delete(path: str) -> dict:
    try:
        return _result(httpx.delete(f"{BASE_URL}{path}", timeout=5.0))
    except httpx.HTTPError as e:
        return {"error": str(e)}


# ─── Game control ────────────────────────────────────────────────────────────

@mcp.tool()
def get_tick() -> dict:
    """Get current game tick, phase, winner, and elapsed time.

    Returns:
        tick: current simulation tick
        phase: "lobby" | "running" | "paused"
        winner: None | 0 | 1 | "draw"
        elapsed_s: real seconds since game start
        seats: {0: agent_name|null, 1: agent_name|null}
    """
    return _get("/tick")


@mcp.tool()
def list_seats() -> dict:
    """List colony seats with occupancy and brain type.

    Each seat includes:
    - agent: null (open) or the agent name that claimed it
    - brain_type: "mcp" (ready for agent control) | "llm" (LLM-loop) | "bot" (bot AI)

    Only seats with brain_type "mcp" accept join_seat(). Use list_matches() for
    discovery across potentially multiple game servers.
    """
    return _get("/seats")


@mcp.tool()
def list_matches() -> dict:
    """Discover open games and available seats across game servers.

    Returns a list of matches, each with:
    - game_url: the server URL to use as BASE_URL
    - phase: "lobby" | "running" | "paused"
    - tick: current game tick
    - map: map name and size
    - seats: per-colony {agent, brain_type}

    Use this to find a game before calling join_seat(). In the current single-server
    deployment this returns one entry; in a future multi-server setup it will list all.
    """
    return _get("/matches")


@mcp.tool()
def join_seat(colony_id: int, agent_name: str) -> dict:
    """Claim a colony seat as an MCP agent. Only works if the seat is currently unoccupied.

    Args:
        colony_id: 0 for RED, 1 for BLUE
        agent_name: your agent's name (displayed in UI, used for identification)

    Once joined, the colony's brain type is switched to "mcp" and the LLM loop stops.
    You must actively call patch_directive / issue_command to control the colony.
    Call release_seat() when done to hand control back.
    """
    return _post(f"/seat/{colony_id}", {"agent_name": agent_name})


@mcp.tool()
def release_seat(colony_id: int) -> dict:
    """Release a colony seat, returning control to the bot.

    Args:
        colony_id: 0 for RED, 1 for BLUE
    """
    return _delete(f"/seat/{colony_id}")


@mcp.tool()
def game_control(action: str) -> dict:
    """Send a game control command.

    Args:
        action: one of:
            "start"  — start the game (from lobby; initiates colony placement)
            "pause"  — pause the simulation (game state frozen, directives still accepted)
            "resume" — resume a paused game
            "end"    — end the current game; if no queen has died, the winner is
                       adjudicated by score (food collected + army value + stock).
                       Response includes "winner" (0=RED, 1=BLUE, "draw") and "scores".
            "reset"  — reset to lobby (new map, all colonies reset)
    """
    return _post("/control", {"action": action})


# ─── Colony state ─────────────────────────────────────────────────────────────

@mcp.tool()
def get_state(colony_id: int) -> dict:
    """Get full colony state for strategic decision-making.

    Args:
        colony_id: 0 for RED, 1 for BLUE

    Returns a comprehensive state snapshot including:
    - food, dirt, income_per_s, dirt_per_s
    - counts: {workers, soldiers, scouts, queen}
    - tiers: current upgrade level per unit type (0-3)
    - spawn_queue: queued units and reserved food
    - aging_soon: units near end of lifespan
    - combat: soldiers_in_siege, soldiers_adjacent_queen, enemy_queen_hp,
      queen_dps_actual (real damage/s to the enemy queen — the number that matters),
      siege_dps_potential (theoretical max if all siege soldiers were hitting her).
      IMPORTANT: if soldiers_in_siege is high but queen_dps_actual is 0, your soldiers
      are fighting defenders instead of the queen — set military.siege_priority="queen".
    - directive: current full directive JSON
    - trigger_log: recent trigger fires
    - events: recent game events (newest first)
    - advisor: list of contextual strategy hints (unspent dirt, idle workers, affordable
      upgrades, larder timing, massing attacks). Treat these as high-value prompts —
      they fire only when a game lever is being neglected.
    - food_intel: dict of known food node coords → {amt, max, tier, last_seen}
    - enemy_sightings: [(cx, cy, soldiers, total, tick)] — recent enemy presence zones
    - seen_structs: known enemy structure positions and types
    - own_structures: list of own built structures with HP and build_progress (if under construction)
    - military_summary: {total_soldiers, fighting, patrolling, idle, healthy, wounded, avg_hp_pct, building}
      Use this instead of scrolling units — "10 healthy, 3 fighting, 2 wounded" at a glance.
    - income_per_s: food delivered in last 10 ticks (reset each window, smoothed over 5 windows).
      Value reflects recent delivery rate — multiply by TPS÷10 for per-tick estimate.
    - units: full list of ants with id, type, state, hp, max_hp, x, y, carrying, override.
      Each unit's "state" field is now: "idle"|"foraging"|"returning"|"exploring"|"fighting"|"patrolling"|"recruited"|"building"
    """
    return _get(f"/state/{colony_id}")


@mcp.tool()
def get_notifications(colony_id: int) -> dict:
    """Consume and return all pending notifications for a colony (clears the tray on read).

    Notification types:
    - structure_complete: {type, x, y} — a building finished
    - upgrade_complete: {unit, tier, label, effect} — upgrade bought
    - queen_under_attack: {hp, old_hp, dmg} — your queen is taking damage RIGHT NOW
    - enemy_contact: {cx, cy, soldiers, total} — enemy soldiers spotted in vision
    - food_depleted: {x, y, tier} — a food node ran dry
    - game_over: {winner, outcome: "victory"|"defeat"|"draw", reason} — the game ended;
      always check this before concluding a game's result yourself
    - priority_food_cleared: {x, y, reason} — your economy.priority_food node depleted
      and was auto-cleared; workers have resumed spreading on their own

    Call this regularly (e.g., every few seconds) to catch high-priority alerts.
    Notifications disappear once read — if you miss them, check events via get_events.
    """
    return _get(f"/notifications/{colony_id}")


@mcp.tool()
def get_intel_map(colony_id: int) -> dict:
    """Get a 30x20 ASCII intel map showing explored terrain, food sources, and enemy sightings.

    The map covers the 150x100 game world (each cell = 5x5 tiles).
    Row 00 = top (y=0), row 19 = bottom (y=100).

    Legend:
      R/B   = RED/BLUE nest positions
      h     = home food node (depletes ~3 min)
      a     = approach food node (depletes ~5 min)
      F     = frontline food (inexhaustible, regrows 20/tick — the key objective)
      x     = depleted food node
      #     = impassable rock
      W/G/K = own watchtower / guard_post / barracks
      w/g/k = enemy structures you've spotted
      !     = enemy soldiers (3+) spotted here recently
      ?     = enemy units spotted here recently

    Also returns food_intel, enemy_sightings, and seen_structs as structured data.
    """
    return _get(f"/intel_map/{colony_id}")


@mcp.tool()
def get_events(colony_id: int, since_tick: int = 0) -> dict:
    """Get recent game events for a colony (newest first, up to 30 events).

    Args:
        colony_id: 0 for RED, 1 for BLUE
        since_tick: filter hint (not enforced server-side; events are always recent)
    """
    return _get(f"/events/{colony_id}", {"since_tick": since_tick})


# ─── Directive control ───────────────────────────────────────────────────────

@mcp.tool()
def get_directive(colony_id: int) -> dict:
    """Get the current full directive for a colony.

    The directive is the persistent policy document that controls colony behavior.
    All fields persist between calls — use patch_directive to change specific fields.

    Key directive sections:
    - spawn: target ratios, min ratios, reserve_food, burst_at, per-type min/max/pause
        spawn.{type}.pause=true  — stop adding this type to the queue (save food for upgrades)
    - economy: upgrade_priority, auto_upgrade, priority_food, gather_dirt, upgrade_reserve
        economy.upgrade_reserve={"scout": 450}  — protect 450♦ from spawn queue for scout T1
    - military: stance, formation, rally_point, rally_release_at, auto_attack, retreat
    - unit_types: per-type config (flee_distance, expansion, patrol_waypoints)
    - triggers: [{label, if, then, priority}] — auto-fired policy rules
        triggers support "buy_upgrade": "worker"|"scout"|"soldier" as a then-action
    """
    return _get(f"/directive/{colony_id}")


@mcp.tool()
def patch_directive(colony_id: int, patches: dict) -> dict:
    """Patch specific fields of the colony directive using dot-notation or nested dict.

    Args:
        colony_id: 0 for RED, 1 for BLUE
        patches: dict of changes to apply. Can use nested format or dot-notation paths.

    Examples:
        {"military": {"stance": "aggressive", "auto_attack": true}}
        {"military": {"siege_priority": "queen"}}  ← CRITICAL when sieging: without this,
            soldiers target the NEAREST enemy, and defenders/workers near the enemy nest
            shield the queen indefinitely. Set it as soon as soldiers_in_siege > 0.
        {"spawn": {"soldier": {"target_ratio": 0.5}, "reserve_food": 100}}
        {"spawn": {"scout": {"pause": true}}, "economy": {"upgrade_reserve": {"scout": 450}}}
            ← pause scout spawning and protect 450♦ so you can afford the scout T1 upgrade
        {"triggers": [{"label": "eco_emergency", "if": "food < 75 AND elapsed_ticks > 100",
                       "then": {"military.retreat": true}, "priority": 5}]}
        {"triggers": [{"label": "auto_buy_scout_t1",
                       "if": "food > 600 AND scout_count < 5",
                       "then": {"buy_upgrade": "scout"},
                       "priority": 3, "cooldown": 500}]}
            ← triggers can fire buy_upgrade directly — no need to poll

    Dot-notation (flat keys also supported):
        {"military.rally_point": [75, 50], "military.rally_release_at": 20}

    Triggers replace the entire triggers array when provided.
    """
    return _post(f"/directive/{colony_id}", {"patches": patches})


@mcp.tool()
def set_directive(colony_id: int, directive: dict) -> dict:
    """Replace the entire directive for a colony.

    Use patch_directive for partial updates. This replaces everything.

    Args:
        colony_id: 0 for RED, 1 for BLUE
        directive: complete directive object (must include all sections)
    """
    return _post(f"/directive/{colony_id}", {"directive": directive})


# ─── Direct commands ─────────────────────────────────────────────────────────

@mcp.tool()
def buy_upgrade(colony_id: int, unit: str) -> dict:
    """Purchase the next upgrade for a unit type (costs food, improves permanently).

    Args:
        colony_id: 0 for RED, 1 for BLUE
        unit: "worker" | "scout" | "soldier"

    Upgrade trees:
    - Worker: T1(500♦)=+carry, T2(2200♦)=+carry, T3(6000♦)=fast return
    - Scout:  T1(450♦)=vision8→12, T2(2000♦)=vision12→16+speed, T3(5500♦)=vision16→22
    - Soldier:T1(600♦)=+10dmg, T2(2800♦)=+80HP+faster cd, T3(7500♦)=splash damage

    The purchase will execute on the next sim tick if you have enough food.
    """
    return _post(f"/command/{colony_id}", {"type": "buy_upgrade", "unit": unit})


@mcp.tool()
def build_structure(colony_id: int, structure_type: str, x: int, y: int) -> dict:
    """Order construction of a structure at the given map coordinates.

    Args:
        colony_id: 0 for RED, 1 for BLUE
        structure_type: "watchtower" | "barracks" | "guard_post" | "wall" | "larder"
        x: tile x coordinate (0-149)
        y: tile y coordinate (0-99)

    Costs (dirt, not food):
        guard_post: 150◆  — HP=300, DMG=18, CD=3, RANGE=10, max 3  [build: 100 work]
        watchtower: 80◆   — HP=150, fog reveal radius 12, max 3      [build: 60 work]
        barracks:   200◆  — HP=200, spawns soldiers every 20 ticks, max 2 [build: 150 work]
        wall:       25◆   — impassable tile, max 12 segments          [build: 25 work]
        larder:     150◆  — HP=150, +6♦/tick passive food income, max 2 [build: 120 work]

    CONSTRUCTION: Dirt is deducted immediately, but the structure starts INACTIVE
    (not functional, shown as scaffolding on the map). Workers within 2 tiles auto-
    contribute work each tick based on tier (T0=1/tick, T1=2, T2=3, T3=4/tick,
    max 4 workers simultaneously). Structure activates when build_progress reaches
    build_required. To speed up construction:
        command_unit(colony_id, worker_id, "build", x=N, y=M)  — manual assignment
        command_type(colony_id, "worker", "build", x=N, y=M)   — all workers to site

    Use get_intel_map to find good placement coordinates.
    Buildings cost dirt, so food economy is never blocked by construction.
    If rejected with "insufficient dirt": workers only pick up dirt opportunistically
    when passing near deposits — set economy.gather_dirt=true in the directive to
    prioritize dirt collection, and check dirt_per_s in get_state to confirm it flows.
    """
    return _post(f"/command/{colony_id}", {
        "type": "build",
        "build": {"type": structure_type, "x": x, "y": y}
    })


@mcp.tool()
def convert_unit(colony_id: int, ant_id: int, to_type: str) -> dict:
    """Convert an ant near your queen to a different type (costs food, resets HP and lifespan).

    Args:
        colony_id: 0 for RED, 1 for BLUE
        ant_id: ID of the ant to convert (must be within 8 tiles of your queen)
        to_type: "worker" | "scout" | "soldier"

    Conversion costs: worker=15♦, scout=22♦, soldier=30♦
    The ant's HP and lifespan are reset to the new type's values.

    Use get_state() to find ant IDs near your queen — the events log often lists them,
    and the CONVERT hint in the prompt shows nearby ant IDs directly.
    """
    return _post(f"/command/{colony_id}", {
        "type": "convert",
        "convert": {"id": ant_id, "to": to_type}
    })


@mcp.tool()
def command_unit(colony_id: int, ant_id: int, command: str,
                 x: int = None, y: int = None, waypoints: list = None) -> dict:
    """Issue a direct movement/behavior command to a specific ant.

    The override persists until the ant dies or you call command="clear".
    Ant dies naturally by lifespan — workers 500t, soldiers 300t, scouts 200t.
    Use get_state() to see the units list with IDs, positions, types, and current overrides.

    Args:
        colony_id: 0 for RED, 1 for BLUE
        ant_id: ID of the ant to command (from get_state()["units"])
        command: one of:
            "move_to"   — move to (x, y); engage enemies encountered en route
            "attack_xy" — advance to (x, y) prioritizing queen targets; engage on arrival
            "gather"    — (workers only) go to food node at (x, y) and keep harvesting it
            "build"     — (workers only) go to build site at (x, y) and construct until done;
                          override auto-clears when structure completes. Use to prioritize and
                          speed up construction (up to 4 workers simultaneously per site).
            "hold"      — hold current position; fight enemies within 5 tiles; don't advance
            "patrol"    — loop through waypoints list indefinitely; engage enemies encountered
            "clear"     — remove override, return ant to normal colony AI behavior
        x, y: target coordinates for move_to / attack_xy / gather
        waypoints: list of [x, y] pairs for patrol command (min 2 points)

    Examples:
        Send a squad of soldiers to contest center food:
            command_unit(0, 142, "attack_xy", x=75, y=50)
        Keep a worker on a specific high-yield food node:
            command_unit(0, 87, "gather", x=75, y=50)
        Post a soldier as a guard at a chokepoint:
            command_unit(0, 155, "hold", x=49, y=50)
        Scout a specific patrol route:
            command_unit(0, 203, "patrol", waypoints=[[14,50],[45,20],[75,50],[45,80],[14,50]])
    """
    body = {"type": "unit_command", "ant_id": ant_id, "command": command}
    if x is not None: body["x"] = x
    if y is not None: body["y"] = y
    if waypoints is not None: body["waypoints"] = waypoints
    return _post(f"/command/{colony_id}", body)


@mcp.tool()
def command_units(colony_id: int, commands: list) -> dict:
    """Issue unit commands to multiple ants at once (batch).

    Args:
        colony_id: 0 for RED, 1 for BLUE
        commands: list of command dicts, each with:
            {"ant_id": N, "command": "move_to"|"attack_xy"|"gather"|"hold"|"patrol"|"clear",
             "x": N, "y": N}   — x/y required for move_to/attack_xy/gather
            {"ant_id": N, "command": "patrol", "waypoints": [[x1,y1],[x2,y2],...]}

    Returns count of successful commands and any errors.

    Example — send 5 soldiers to attack-march to the center:
        command_units(0, [
            {"ant_id": 142, "command": "attack_xy", "x": 75, "y": 50},
            {"ant_id": 143, "command": "attack_xy", "x": 75, "y": 50},
            {"ant_id": 144, "command": "attack_xy", "x": 75, "y": 50},
        ])
    """
    return _post(f"/command/{colony_id}", {"type": "unit_command_batch", "commands": commands})


@mcp.tool()
def command_type(colony_id: int, unit_type: str, command: str,
                 x: int = None, y: int = None,
                 filter_state: str = None) -> dict:
    """Issue the same command to all ants of a given type — no IDs needed.

    Calls get_state internally to collect current IDs, then issues a batch command.
    Saves many round-trips when managing an entire army.

    Args:
        colony_id: 0 for RED, 1 for BLUE
        unit_type: "soldier", "worker", or "scout"
        command: "move_to", "attack_xy", "gather", "build", "hold", "patrol", or "clear"
        x, y: target coordinates (required for move_to / attack_xy / patrol / build)
        filter_state: optional — only command ants in this state.
            Valid values: "idle", "foraging", "returning", "exploring",
                          "fighting", "patrolling", "recruited", "building"
            Useful to avoid re-commanding soldiers already fighting at the front.

    Examples:
        Send all soldiers to the enemy nest:
            command_type(1, "soldier", "attack_xy", x=14, y=50)
        Clear all worker overrides (let colony AI take back over):
            command_type(0, "worker", "clear")
        Command only idle scouts to explore the south flank:
            command_type(1, "scout", "move_to", x=75, y=80, filter_state="idle")
    """
    type_names = {"worker": "worker", "soldier": "soldier", "scout": "scout"}
    if unit_type not in type_names:
        return {"error": f"unit_type must be one of: {list(type_names)}"}
    state = _get(f"/state/{colony_id}")
    if "error" in state:
        return state
    units = state.get("units", [])
    cmds = []
    for u in units:
        if u.get("type") != unit_type:
            continue
        if filter_state is not None and u.get("state") != filter_state:
            continue
        entry = {"ant_id": u["id"], "command": command}
        if x is not None: entry["x"] = x
        if y is not None: entry["y"] = y
        cmds.append(entry)
    if not cmds:
        return {"ok": True, "commanded": 0, "note": "no matching ants found"}
    result = _post(f"/command/{colony_id}", {"type": "unit_command_batch", "commands": cmds})
    result["commanded"] = len(cmds)
    return result


@mcp.tool()
def cancel_spawn(colony_id: int, unit_type: str = "all") -> dict:
    """Cancel pending spawn queue entries and refund their reserved food immediately.

    Use this when you want to redirect food toward an upgrade, a building, or a
    different unit type. Food is refunded at the moment the command is processed
    (next tick), not when units would have spawned.

    Args:
        colony_id: 0 for RED, 1 for BLUE
        unit_type: "worker", "soldier", "scout", or "all" (default: "all")

    Returns:
        {"ok": True, "cancelled": N, "food_refunded": M}

    Examples:
        Clear the entire queue before saving for an upgrade:
            cancel_spawn(0)
        Cancel only queued soldiers to redirect food:
            cancel_spawn(0, unit_type="soldier")
    """
    valid = {"worker", "soldier", "scout", "all"}
    if unit_type not in valid:
        return {"error": f"unit_type must be one of: {sorted(valid)}"}
    return _post(f"/command/{colony_id}", {"type": "cancel_spawn", "unit_type": unit_type})


@mcp.tool()
def submit_feedback(feedback: str, category: str = "general",
                    colony_id: int = None, agent: str = None) -> dict:
    """Submit feedback about the game, its UI, or the MCP interface.

    Use this ANY TIME you notice something that could be improved — missing data,
    confusing state, a control that doesn't exist, a balance issue, a bug, or
    anything that made your decision-making harder. This feedback is stored
    permanently and reviewed by the developers.

    Args:
        feedback: Your feedback text (be specific — include tick numbers, field names,
                  what you expected vs. what you got)
        category: one of:
            "general"      — anything that doesn't fit a specific category
            "missing_data" — information you needed but couldn't get from any tool
            "ux"           — confusing interface, unclear field names, bad defaults
            "balance"      — game mechanics that feel broken or unfair
            "bug"          — unexpected behavior or error
            "feature"      — new control or capability you wanted but was missing
        colony_id: which colony you were controlling when you noticed this (0=RED, 1=BLUE)
        agent: your agent name (for attribution)

    Examples:
        submit_feedback(
            "Workers don't show their current assigned food node. I had 40 workers and
             0 income for 30 ticks before I realized they were all targeting a depleted node.
             get_state()['units'] should show each worker's recruit_target.",
            category="missing_data", colony_id=1
        )
        submit_feedback(
            "Trigger fires every tick when true. With eco_emergency firing 30+ times,
             the trigger_log is useless — I can't see other events.",
            category="ux", colony_id=1
        )
    """
    body = {"feedback": feedback, "category": category}
    if colony_id is not None: body["colony_id"] = colony_id
    if agent is not None: body["agent"] = agent
    return _post("/feedback", body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Swarm Wars MCP Server")
    parser.add_argument("--port", type=int, default=None,
                        help="Run HTTP+SSE transport on this port (default: stdio)")
    parser.add_argument("--game-url", default="http://localhost:8083",
                        help="Game server base URL (default: http://localhost:8083)")
    args = parser.parse_args()

    BASE_URL = f"{args.game_url}/api"

    if args.port:
        # FastMCP reads port/host from constructor settings or FASTMCP_* env vars
        import os
        os.environ["FASTMCP_PORT"] = str(args.port)
        os.environ["FASTMCP_HOST"] = "0.0.0.0"
        # Recreate with correct settings
        mcp.settings.port = args.port
        mcp.settings.host = "0.0.0.0"
        print(f"🔌  Swarm Wars MCP Server — HTTP+SSE on port {args.port}", file=sys.stderr)
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
