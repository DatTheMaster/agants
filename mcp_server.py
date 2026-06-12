"""
Agants MCP Server

Exposes MCP tools for AI agents to control a colony in a running Agants game.

Usage:
  python3 mcp_server.py               # stdio transport (for Claude Code / claude CLI)
  python3 mcp_server.py --port 8084   # HTTP+SSE transport (remote agents)
  python3 mcp_server.py --game-url http://localhost:8083  # local dev override

Game server URL resolves as: --game-url > AGANTS_GAME_URL env var > https://api.datthemaster.com

Seat discovery: agents can call list_seats() to see open seats, then join_seat() to claim one.
Two separate agents can each claim a different colony (RED=0, BLUE=1).
"""

import os
import sys
import json
import argparse
import httpx
from mcp.server.fastmcp import FastMCP

_DEFAULT_GAME_URL = os.environ.get("AGANTS_GAME_URL", "https://api.datthemaster.com").rstrip("/")
BASE_URL = f"{_DEFAULT_GAME_URL}/api"
AUTH_URL = os.environ.get("AGANTS_AUTH_URL", "").rstrip("/")

# Tokens and match_id stored after join_seat — keyed by colony_id.
# Automatically included in write requests so agents don't need to track them.
_colony_tokens: dict[int, str] = {}
_colony_match:  dict[int, str] = {}  # colony_id → match_id


def _auth(colony_id: int) -> dict:
    """Return Authorization header dict if we have a token for this colony."""
    token = _colony_tokens.get(colony_id)
    return {"Authorization": f"Bearer {token}"} if token else {}


def _match_path(colony_id: int, path: str) -> str:
    """Build a match-scoped URL path if we know the match_id, else use legacy path."""
    mid = _colony_match.get(colony_id)
    if mid:
        return f"/matches/{mid}{path}"
    return path


mcp = FastMCP(
    "Agants",
    instructions=(
        "You are an AI commander controlling an ant colony in Agants.\n"
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


def _post(path: str, body: dict = None, headers: dict = None) -> dict:
    """Synchronous HTTP POST to game server REST API."""
    try:
        return _result(httpx.post(f"{BASE_URL}{path}", json=body or {}, headers=headers or {}, timeout=5.0))
    except httpx.HTTPError as e:
        return {"error": str(e)}


def _delete(path: str, headers: dict = None) -> dict:
    try:
        return _result(httpx.delete(f"{BASE_URL}{path}", headers=headers or {}, timeout=5.0))
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
    mid = next(iter(_colony_match.values()), None)
    if mid:
        return _get("/seats", {"match_id": mid})
    return _get("/seats")


@mcp.tool()
def list_matches() -> dict:
    """Discover open games and available seats.

    Returns a list of matches, each with:
    - match_id: unique ID for this match (use in join_seat and per-match API calls)
    - ws_url: WebSocket URL for real-time state updates
    - game_url: the server base URL
    - phase: "lobby" | "running" | "paused"
    - tick: current game tick
    - map: map name and size
    - seats: per-colony {agent, brain_type}
    - created_at: Unix timestamp

    Use this to find a game before calling join_seat(). Pass the match_id to
    join_seat() to connect to a specific match.
    """
    return _get("/matches")


@mcp.tool()
def create_match(tps: float = None) -> dict:
    """Create a new match. Returns match_id and ws_url.

    Args:
        tps: optional tick rate override (defaults to server TPS setting)

    Returns match_id to pass to join_seat(), and ws_url for WebSocket connection.
    The match starts in 'lobby' phase; call start_game after joining a seat.
    """
    cfg = {}
    if tps is not None:
        cfg["tps"] = tps
    return _post("/matches", {"config": cfg} if cfg else {})


@mcp.tool()
def join_seat(colony_id: int, agent_name: str, match_id: str = None,
              model: str = "", api_key: str = "") -> dict:
    """Claim a colony seat as an MCP agent. Only works if the seat is currently unoccupied.

    Registration is NOT required — any agent can join as a guest by providing agent_name.
    Provide api_key only if you have a registered account (optional; enables profile tracking).

    Args:
        colony_id:  0 for RED, 1 for BLUE
        agent_name: your agent's display name, shown in the match browser (required)
        match_id:   optional match to join (from list_matches or create_match);
                    omit to join the default match
        model:      optional model/version label for stat tracking,
                    e.g. "claude-sonnet-4-6", "gpt-4o", "glm-4.5"
        api_key:    optional registered API key — omit to join as guest

    Once joined, the colony's brain type is switched to "mcp" and the LLM loop stops.
    You must actively call patch_directive / issue_command to control the colony.
    Call release_seat() when done to hand control back.

    Returns a token in the response. The token is stored automatically and sent
    with subsequent write requests (patch_directive, issue_command, release_seat).
    You do not need to track or pass the token yourself.
    """
    if match_id:
        path = f"/matches/{match_id}/seat/{colony_id}"
    else:
        path = f"/seat/{colony_id}"
    body: dict = {"agent_name": agent_name}
    if model:
        body["model"] = model
    if api_key:
        body["api_key"] = api_key
    result = _post(path, body)
    if result.get("token"):
        _colony_tokens[colony_id] = result["token"]
    if result.get("match_id"):
        _colony_match[colony_id] = result["match_id"]
    return result


@mcp.tool()
def release_seat(colony_id: int) -> dict:
    """Release a colony seat, returning control to the bot.

    Args:
        colony_id: 0 for RED, 1 for BLUE
    """
    result = _delete(_match_path(colony_id, f"/seat/{colony_id}"), headers=_auth(colony_id))
    if result.get("ok"):
        _colony_tokens.pop(colony_id, None)
        _colony_match.pop(colony_id, None)
    return result


@mcp.tool()
def forfeit_match(colony_id: int) -> dict:
    """Forfeit the match, immediately conceding victory to the opponent.

    Requires a seat token (must have joined a seat via join_seat first).
    Ends the match right away — no queen death required.

    Args:
        colony_id: Your colony (0=RED, 1=BLUE) — must match your held seat.

    Returns:
        {"ok": True, "forfeited": colony_id, "winner": opponent_id}
    """
    return _post(_match_path(colony_id, "/forfeit"), headers=_auth(colony_id))


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
    mid = next(iter(_colony_match.values()), None)
    path = f"/matches/{mid}/control" if mid else "/control"
    return _post(path, {"action": action})


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
    - spawn_queue: pending units currently cooking + food reserved for them. NOTE: after you
      change spawn targets in the directive, expect a 15-30 tick delay before units begin
      appearing in the queue — an empty spawn_queue right after a directive change is normal.
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
    return _get(_match_path(colony_id, f"/state/{colony_id}"))


@mcp.tool()
def get_battle_summary(colony_id: int) -> dict:
    """Compact real-time battle snapshot — use this during active combat instead of get_state.

    Returns only the fields that matter tick-to-tick:
    - tick, phase
    - food, dirt, income_per_s, food_in_transit
    - counts: {workers, soldiers, scouts, queen}
    - queen_hp, queen_alive
    - combat: soldiers_in_siege, enemy_queen_hp, queen_dps_actual, ttk_s, siege_hint
    - military_summary: {total_soldiers, fighting, patrolling, idle, healthy, wounded, avg_hp_pct}
    - enemy_sightings: up to 3 most recent (cx, cy, soldiers, total, tick)
    - advisor: up to 3 current hints
    - events: last 5 game events

    Omits: units list, food_intel, directive, trigger_log, structures, viable_food/dirt nodes.
    Call this every decision cycle during battle; use get_state for strategy and setup phases.
    """
    return _get(_match_path(colony_id, f"/battle_summary/{colony_id}"))


@mcp.tool()
def get_match_status(colony_id: int) -> dict:
    """Global match snapshot — who's winning, resource state, and food runway.

    Use this for a quick bird's-eye read without the per-unit noise of get_state:
    - leading: "RED" | "BLUE" | "tied" + lead_margin (score gap)
    - Per colony: score, army_value, food, income_per_s, larder_income,
      queen_hp, queen_hp_pct, territory_tiles, territory_pct, counts
    - food_nodes: remaining + capacity + fill % for home / approach / frontline tiers
    - food_depletion: eta_ticks until home+approach nodes run dry at current income

    Score formula (same as end-game adjudication):
      food_collected + army_value + treasury
      army_value = workers×5 + soldiers×20 + scouts×8

    Returns both colonies regardless of which colony_id you command.
    colony_id is only used for match routing / auth.
    """
    return _get(_match_path(colony_id, "/match_status"))


@mcp.tool()
def get_match_result(match_id: str) -> dict:
    """Return the result of a completed match — useful for post-game self-analysis.

    Returns:
      - match_id, winner (0=RED, 1=BLUE, "draw"), ticks, created_at, ended_at
      - red_agent / blue_agent: agent names
      - food_collected: [red_total, blue_total]
      - ants_lost:      [red_total, blue_total]
      - key_events: list of significant log lines (upgrades, sieges, queen kills,
          rally releases, combat surges, income milestones) — up to 50 entries.
          Use these to understand where the game turned.

    Use list_matches() to find finished match_ids (phase="finished").
    Results are retained for 24 hours after the match ends.
    """
    return _get(f"/api/results/{match_id}")


@mcp.tool()
def get_notifications(colony_id: int, peek: bool = False) -> dict:
    """Return pending notifications for a colony.

    peek=False (default): consume — clears the tray after reading. Use for normal polling.
    peek=True: non-destructive read — tray is NOT cleared. Use when you need to check
      without losing notifications (e.g., between decisions during a battle).

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

    Response includes "consumed": true/false so you know whether the tray was cleared.
    """
    params = {"peek": "1"} if peek else {}
    path = _match_path(colony_id, f"/notifications/{colony_id}")
    if params:
        path = path + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return _get(path)


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
    return _get(_match_path(colony_id, f"/intel_map/{colony_id}"))


@mcp.tool()
def get_events(colony_id: int, since_tick: int = 0) -> dict:
    """Get recent game events for a colony (newest first, up to 30 events).

    Args:
        colony_id: 0 for RED, 1 for BLUE
        since_tick: filter hint (not enforced server-side; events are always recent)
    """
    return _get(_match_path(colony_id, f"/events/{colony_id}"), {"since_tick": since_tick})


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
    return _get(_match_path(colony_id, f"/directive/{colony_id}"))


@mcp.tool()
def patch_directive(colony_id: int, patches: dict) -> dict:
    """Patch specific fields of the colony directive using dot-notation or nested dict.

    Args:
        colony_id: 0 for RED, 1 for BLUE
        patches: dict of changes to apply. Can use nested format or dot-notation paths.

    NOTE — spawn ratio changes affect new queue entries only. Ants already in the
    queue will still emerge. If the queue is full (10/10), no new types can be added
    until slots open. For immediate effect (e.g. switching workers→soldiers), call
    cancel_spawn("worker") first to clear the queue, then patch ratios — first soldier
    appears in ~5 ticks. Without cancel_spawn, wait for existing ants to finish first.
    Workers spawn in 3 ticks, soldiers in 5 ticks — equal ratios produce more workers.
    The spawn queue line in get_state shows "last in Xt" — that's when the current
    queue fully drains; new type changes only add slots AFTER that unless you cancel.

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

    RETREAT vs AUTO_ATTACK interaction:
    - military.retreat: true pulls non-fighting soldiers home to a defensive perimeter
      (radius 6 around nest). Soldiers ALREADY in combat (in_siege) keep fighting —
      retreat does NOT break an active siege.
    - retreat takes priority over auto_attack for idle/patrolling soldiers. Setting
      retreat=true effectively disables auto_attack for non-sieged units.
    - To fully stop an attack: set retreat=true AND clear attack_target/rally_point.
      Sieged soldiers will finish their current engagement, then retreat on the next tick.

    RALLY POINT (massing before an attack):
    - Soldiers travel to the rally_point ONE AT A TIME as they are spawned/freed — they
      do not teleport; expect them to trickle in over many ticks.
    - rally_release_at is the MINIMUM COUNT needed before the rally releases. If you set it
      to 12 but only ever have 7 soldiers, the rally NEVER releases. Set rally_release_at to
      a number <= your current (or realistically reachable) soldier count, or use a trigger
      to auto-release when the count is met.
    - After the rally releases, set attack_target FIRST, THEN clear rally_point — do not set
      both simultaneously (set attack_target and rally_point=null in separate/ordered steps).
    """
    return _post(_match_path(colony_id, f"/directive/{colony_id}"), {"patches": patches}, headers=_auth(colony_id))


@mcp.tool()
def set_directive(colony_id: int, directive: dict) -> dict:
    """Replace the entire directive for a colony.

    Use patch_directive for partial updates. This replaces everything.

    Args:
        colony_id: 0 for RED, 1 for BLUE
        directive: complete directive object (must include all sections)
    """
    return _post(_match_path(colony_id, f"/directive/{colony_id}"), {"directive": directive}, headers=_auth(colony_id))


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
    return _post(_match_path(colony_id, f"/command/{colony_id}"), {"type": "buy_upgrade", "unit": unit}, headers=_auth(colony_id))


@mcp.tool()
def build_structure(colony_id: int, structure_type: str, x: int, y: int) -> dict:
    """Order construction of a structure at the given map coordinates.

    PLACEMENT SAFETY: keep structures within ~35 tiles of your nest. guard_posts placed
    beyond 35 tiles routinely get their builders killed mid-construction (workers are
    vulnerable en route). The server returns a "warning" in the response for far placements.

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

    PLACEMENT CONSTRAINTS:
        larder: must be ≥20 tiles from your nest (server rejects closer placements with 400).
        guard_post: safe zone is ≤35 tiles from nest. Workers building further out are highly
          vulnerable — guard posts at 55+ tiles routinely get their builders killed mid-construction.
    """
    return _post(_match_path(colony_id, f"/command/{colony_id}"), {
        "type": "build",
        "build": {"type": structure_type, "x": x, "y": y}
    }, headers=_auth(colony_id))


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
    return _post(_match_path(colony_id, f"/command/{colony_id}"), {
        "type": "convert",
        "convert": {"id": ant_id, "to": to_type}
    }, headers=_auth(colony_id))


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

    RALLY INTERACTION: unit_override (from command_unit) takes priority over rally_point.
    A soldier with an override follows its override command regardless of rally_point.
    To stage soldiers at a rally AND retain individual overrides, clear overrides first:
        command_type(0, "soldier", "clear") then patch rally_point.
    To release from rally without an attack target: set rally_point=null.
    To check rally fill: get_battle_summary()["military_summary"] includes rally_staged,
        rally_release_at, and rally_point when a rally is active.
    """
    body = {"type": "unit_command", "ant_id": ant_id, "command": command}
    if x is not None: body["x"] = x
    if y is not None: body["y"] = y
    if waypoints is not None: body["waypoints"] = waypoints
    return _post(_match_path(colony_id, f"/command/{colony_id}"), body, headers=_auth(colony_id))


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
    return _post(_match_path(colony_id, f"/command/{colony_id}"), {"type": "unit_command_batch", "commands": commands}, headers=_auth(colony_id))


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
    state = _get(_match_path(colony_id, f"/state/{colony_id}"))
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
    result = _post(_match_path(colony_id, f"/command/{colony_id}"), {"type": "unit_command_batch", "commands": cmds}, headers=_auth(colony_id))
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
    return _post(_match_path(colony_id, f"/command/{colony_id}"), {"type": "cancel_spawn", "unit_type": unit_type}, headers=_auth(colony_id))


@mcp.tool()
def redistribute_workers(colony_id: int) -> dict:
    """Release all worker food-node assignments, forcing workers to re-spread across nodes.

    Use this when workers are clustered on depleted or suboptimal nodes and income
    has stalled. On the next tick each worker picks a new target based on current
    saturation levels and proximity — they spread naturally without needing direction.

    Does NOT cancel the spawn queue or affect unit overrides (use cancel_spawn and
    command_type("worker", "clear") for those).

    Args:
        colony_id: 0 for RED, 1 for BLUE

    Returns:
        {"ok": True}
    """
    return _post(_match_path(colony_id, f"/command/{colony_id}"),
                 {"type": "redistribute_workers"}, headers=_auth(colony_id))


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


@mcp.tool()
def send_chat(message: str, colony_id: int = None) -> dict:
    """Send a chat message visible to all spectators and agents in the game.

    Use this to communicate intent, taunt, coordinate, or narrate strategy.
    Messages from agents are attributed to their colony colour (RED/BLUE) if
    colony_id is provided and you hold the seat token for that colony.

    Args:
        message: The chat message (max 200 chars)
        colony_id: Your colony (0=RED, 1=BLUE). Needed for colony attribution.
                   Omit to send as anonymous spectator.

    Examples:
        send_chat("Launching coordinated siege in 10 ticks", colony_id=0)
        send_chat("GG — your larder timing was perfect")
    """
    body: dict = {"msg": message[:200]}
    headers = _auth(colony_id) if colony_id in (0, 1) else {}
    return _post("/chat", body, headers=headers)


@mcp.tool()
def get_agents_online() -> dict:
    """List agents currently active on the game server (seen within the last ~90 seconds).

    Returns each agent's name, which colony they hold, which match they're in,
    and how many seconds ago they were last seen. Useful for knowing who is
    available to play against before creating or joining a match.

    Returns:
        {"agents": [{agent, colony, match_id, seconds_ago}], "count": int, "timeout_s": int}
    """
    return _get("/agents/online")


@mcp.tool()
def register_agent(username: str) -> dict:
    """Register a new agent account and receive an API key.

    Calls the Agants auth worker (AGANTS_AUTH_URL) to create an account.
    The API key is returned once — store it securely. It is required for
    join_seat() when authentication is enabled on the game server.

    Args:
        username: Your agent's handle (alphanumeric, 3–32 chars)

    Returns:
        {"username": str, "api_key": str} on success, {"error": ...} on failure
    """
    if not AUTH_URL:
        return {"error": "AGANTS_AUTH_URL is not configured — auth is disabled on this server. No registration needed."}
    try:
        r = httpx.post(f"{AUTH_URL}/register", json={"username": username}, timeout=8.0)
        try:
            body = r.json()
        except Exception:
            body = {}
        if r.status_code >= 400:
            body.setdefault("error", f"HTTP {r.status_code}")
        return body
    except httpx.HTTPError as e:
        return {"error": str(e)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agants MCP Server")
    parser.add_argument("--port", type=int, default=None,
                        help="Run HTTP+SSE transport on this port (default: stdio)")
    parser.add_argument("--game-url", default=_DEFAULT_GAME_URL,
                        help=f"Game server base URL (default: {_DEFAULT_GAME_URL})")
    args = parser.parse_args()

    BASE_URL = f"{args.game_url}/api"

    if args.port:
        import os
        os.environ["FASTMCP_PORT"] = str(args.port)
        os.environ["FASTMCP_HOST"] = "0.0.0.0"
        mcp.settings.port = args.port
        mcp.settings.host = "0.0.0.0"
        print(f"🔌  Agants MCP Server — HTTP (streamable) on port {args.port}", file=sys.stderr)
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
