#!/usr/bin/env python3
"""Agants Controller — standalone AI agent + TUI for the Agants ant-colony RTS.

Talks to the game server over REST only. No game server imports.
Run `controller.py --setup` once to write config, then `controller.py`.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import io
import json
import os
import subprocess
import sys
import traceback
import termios
import tty
from collections import deque
from pathlib import Path

import httpx
from openai import OpenAI
from rich.console import Console, Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

CONFIG_DIRS = [Path("./controller.json"), Path.home() / ".config/agants/config.json"]
GLOBAL_CONFIG = Path.home() / ".config/agants/config.json"


# --------------------------------------------------------------------------- #
# Config                                                                       #
# --------------------------------------------------------------------------- #

def _parse_dotenv(path: Path) -> dict:
    """Parse a simple KEY=VALUE .env file; return {} if missing or unreadable."""
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _config_from_dotenv(env: dict) -> dict | None:
    """Build a controller config dict from .env keys; return None if LLM key absent."""
    llm_key = env.get("LLM_API_KEY") or env.get("OPENAI_API_KEY", "")
    llm_url = env.get("LLM_BASE_URL") or env.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model   = env.get("LLM_MODEL") or env.get("OPENAI_MODEL", "gpt-4o")
    if not llm_key:
        return None
    return {
        "game_url": env.get("AGANTS_GAME_URL", "https://api.datthemaster.com/agants"),
        "api_key":  env.get("AGANTS_API_KEY", ""),
        "llm": {"base_url": llm_url, "api_key": llm_key, "model": model},
    }


def load_config() -> dict | None:
    # 1. controller.json in current directory
    local = Path("./controller.json")
    if local.exists():
        return json.loads(local.read_text())

    # 2. .env next to the script, or in current directory
    for env_path in (Path(__file__).parent / ".env", Path(".env")):
        env = _parse_dotenv(env_path)
        if env:
            cfg = _config_from_dotenv(env)
            if cfg:
                return cfg
            break  # found a .env but it's incomplete — fall through to wizard

    # 3. global config
    global_cfg = Path.home() / ".config/agants/config.json"
    if global_cfg.exists():
        return json.loads(global_cfg.read_text())

    return None


def setup_wizard() -> None:
    print("Agants Controller setup\n")
    game_url = input("Game server URL [https://api.datthemaster.com/agants]: ").strip() or "https://api.datthemaster.com/agants"
    api_key = input("Agants API key (optional — leave blank to join as guest): ").strip()
    base_url = input("LLM base URL [https://api.openai.com/v1]: ").strip() or "https://api.openai.com/v1"
    llm_key = input("LLM API key: ").strip()
    model = input("Model [gpt-4o]: ").strip() or "gpt-4o"
    cfg = {
        "game_url": game_url,
        "api_key": api_key,
        "llm": {"base_url": base_url, "api_key": llm_key, "model": model},
    }
    GLOBAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_CONFIG.write_text(json.dumps(cfg, indent=2))
    print(f"\nWrote {GLOBAL_CONFIG}")


# --------------------------------------------------------------------------- #
# REST client                                                                  #
# --------------------------------------------------------------------------- #

class GameClient:
    def __init__(self, base: str, api_key: str = ""):
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.http = httpx.AsyncClient(timeout=15.0)
        self.token: str | None = None
        self.match_id: str | None = None
        self.colony_id: int | None = None
        self.agent_name: str = "agent"
        self.forfeited: bool = False

    async def close(self):
        await self.http.aclose()

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _mpath(self, suffix: str) -> str:
        # Once seated we always have a match_id; before that, fall back to default match.
        if self.match_id:
            return f"/api/matches/{self.match_id}{suffix}"
        return f"/api{suffix}"

    async def list_matches(self) -> list[dict]:
        r = await self.http.get(f"{self.base}/api/matches")
        r.raise_for_status()
        return r.json().get("matches", [])

    async def agents_online(self) -> list[dict]:
        try:
            r = await self.http.get(f"{self.base}/api/agents/online")
            r.raise_for_status()
            return r.json().get("agents", [])
        except httpx.HTTPError:
            return []

    async def heartbeat(self) -> None:
        try:
            await self.http.post(f"{self.base}/api/agents/heartbeat",
                                 json={"api_key": self.api_key})
        except httpx.HTTPError:
            pass

    async def new_match(self, brains: dict | None = None, tps: float | None = None) -> str:
        cfg: dict = {}
        if brains:
            cfg["brains"] = brains
        if tps is not None:
            cfg["tps"] = tps
        r = await self.http.post(f"{self.base}/api/matches", json={"config": cfg} if cfg else {})
        r.raise_for_status()
        return r.json()["match_id"]

    async def start_game(self) -> dict:
        path = self._mpath("/control")
        return await self._tool_request("POST", path, {"action": "start"})

    async def join(self, match_id: str | None, colony_id: int, name: str = "agent") -> dict:
        self.match_id = match_id
        path = self._mpath(f"/seat/{colony_id}")
        body: dict = {"agent_name": name}
        if self.api_key:
            body["api_key"] = self.api_key
        if hasattr(self, "model_label") and self.model_label:
            body["model"] = self.model_label
        r = await self.http.post(f"{self.base}{path}", json=body)
        r.raise_for_status()
        data = r.json()
        self.token = data["token"]
        self.colony_id = colony_id
        self.match_id = data.get("match_id", match_id)
        self.agent_name: str = data.get("agent", name)  # server substitutes registered username
        return data

    async def release(self):
        if self.colony_id is None or self.token is None:
            return
        path = self._mpath(f"/seat/{self.colony_id}")
        try:
            await self.http.delete(f"{self.base}{path}", headers=self._auth())
        except httpx.HTTPError:
            pass
        self.token = None
        self.colony_id = None
        self.forfeited = False

    async def state(self) -> dict:
        path = self._mpath(f"/state/{self.colony_id}")
        r = await self.http.get(f"{self.base}{path}", headers=self._auth())
        r.raise_for_status()
        return r.json()

    async def notifications(self) -> list[dict]:
        path = self._mpath(f"/notifications/{self.colony_id}")
        r = await self.http.get(f"{self.base}{path}", headers=self._auth())
        r.raise_for_status()
        return r.json().get("notifications", [])

    # --- tool-backed calls (return parsed dict or {"error": ...}) ----------- #

    async def _tool_request(self, method: str, path: str, body=None) -> dict:
        try:
            r = await self.http.request(method, f"{self.base}{path}", json=body, headers=self._auth())
            if r.status_code >= 400:
                return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
            try:
                return r.json() if r.content else {"ok": True}
            except Exception:
                return {"error": f"bad response (non-JSON): {r.text[:100]}"}
        except httpx.HTTPError as e:
            return {"error": f"request failed: {e}"}

    async def patch_directive(self, patches: dict, reason: str = "") -> dict:
        # Send the LLM's reasoning alongside the patch so spectators see WHY on the
        # AI Activity feed (server reads body["reason"]; body["patches"] is the directive).
        body = {"patches": patches}
        if reason:
            body["reason"] = reason[:240]
        return await self._tool_request("POST", self._mpath(f"/directive/{self.colony_id}"), body)

    async def send_command(self, command_type: str, data: dict) -> dict:
        return await self._tool_request("POST", self._mpath(f"/command/{self.colony_id}"),
                                        {"type": command_type, **data})

    async def get_intel_map(self) -> dict:
        return await self._tool_request("GET", self._mpath(f"/intel_map/{self.colony_id}"))

    async def send_chat(self, message: str) -> dict:
        return await self._tool_request("POST", self._mpath("/chat"), {"message": message})

    async def get_chat(self, since_tick: int = 0) -> dict:
        return await self._tool_request("GET", self._mpath("/chat") + f"?since_tick={int(since_tick)}")

    async def get_units(self, unit_type: str = "") -> dict:
        suffix = self._mpath(f"/units/{self.colony_id}")
        if unit_type:
            suffix += f"?type={unit_type}"
        return await self._tool_request("GET", suffix)

    async def submit_feedback(self, feedback: str, category: str = "general") -> dict:
        return await self._tool_request("POST", "/api/feedback",
                                        {"feedback": feedback, "category": category,
                                         "colony_id": self.colony_id})

    async def forfeit(self) -> dict:
        return await self._tool_request("POST", self._mpath("/forfeit"), {})


# --------------------------------------------------------------------------- #
# Tool definitions (inline JSON schema)                                        #
# --------------------------------------------------------------------------- #

TOOLS = [
    {"type": "function", "function": {
        "name": "patch_directive",
        "description": "Merge a partial directive into the colony's standing orders. Only include keys "
                       "you want to change. This is the primary lever — set spawn ratios, military "
                       "stance/rally/attack_target/siege_priority/retreat, economy upgrade_priority, triggers.",
        "parameters": {"type": "object", "properties": {
            "patches": {"type": "object", "description": "Partial directive, e.g. "
                        '{"spawn":{"soldier":{"target_ratio":0.4}},"military":{"siege_priority":"queen"}}'}},
            "required": ["patches"]}}},
    {"type": "function", "function": {
        "name": "send_command",
        "description": "One-shot command. command_type is one of: "
                       "'buy_upgrade' (data {\"unit\":\"worker|scout|soldier\"}), "
                       "'build' (data {\"build\":{\"type\":\"larder|watchtower|guard_post|barracks|wall|bulwark\",\"x\":N,\"y\":M}}), "
                       "'convert' (data {\"convert\":{\"id\":antId,\"to\":\"soldier\"}}), "
                       "'cancel_spawn' (data {\"unit_type\":\"worker|soldier|scout|spitter|raider|all\"}), "
                       "'unit_command' (data {\"ant_id\":N,\"command\":\"move_to|attack_move|attack_xy|gather|hold|patrol|clear\",\"x\":X,\"y\":Y}). move_to is a PURE move (no attacking); attack_move advances AND fights through; attack_xy is queen-focused. "
                       "For unit_command: command is a flat key alongside ant_id, x, y — do NOT nest inside an 'override' dict. "
                       "gather (workers only): sends worker to collect food at the given food node — do NOT use on scouts or soldiers, do NOT use on dirt deposit coords.",
        "parameters": {"type": "object", "properties": {
            "command_type": {"type": "string"},
            "data": {"type": "object"}},
            "required": ["command_type", "data"]}}},
    {"type": "function", "function": {
        "name": "get_intel_map",
        "description": "ASCII overview of explored territory, enemy sightings, food and structures. "
                       "Use sparingly when you need a spatial picture.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "send_chat",
        "description": "Broadcast a short taunt or message to the public game chat.",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string"}}, "required": ["message"]}}},
    {"type": "function", "function": {
        "name": "get_chat",
        "description": "Read recent in-game chat — INCOMING messages from the opponent and spectators. "
                       "Returns [{tick, agent, colony, message}]. Pass since_tick to get only newer "
                       "messages. Use to react to taunts or banter; chat is now two-way.",
        "parameters": {"type": "object", "properties": {
            "since_tick": {"type": "integer",
                           "description": "Only return messages after this tick (default 0 = all recent)."}}}}},
    {"type": "function", "function": {
        "name": "get_units",
        "description": "Fetch a SLIM list of just your units of one type (id,x,y,hp,state) without the full "
                       "state dump — the cheap way to grab soldier/worker/scout/spitter IDs and positions for "
                       "unit_command. 'type' is worker|soldier|scout|spitter|raider|queen.",
        "parameters": {"type": "object", "properties": {
            "type": {"type": "string", "enum": ["worker", "soldier", "scout", "spitter", "raider", "queen"]}},
            "required": ["type"]}}},
    {"type": "function", "function": {
        "name": "submit_feedback",
        "description": "Log feedback about game state quality, missing data, or anything that made your "
                       "decision-making harder. This is stored server-side for developer review. "
                       "Call once per game with your overall observations.",
        "parameters": {"type": "object", "properties": {
            "feedback": {"type": "string",
                         "description": "Specific observations: what data was unclear, missing, or misleading. "
                                        "Include tick numbers, field names, and what you needed but couldn't find."},
            "category": {"type": "string", "enum": ["missing_data", "bad_data", "ux", "general"],
                         "description": "Feedback category (default: general)"}},
        "required": ["feedback"]}}},
]


def system_prompt(colony_id: int, match_id: str) -> str:
    colony = "RED" if colony_id == 0 else "BLUE"
    nest = "(14,50)" if colony_id == 0 else "(136,50)"
    enemy_nest = "(136,50)" if colony_id == 0 else "(14,50)"
    return f"""You command the {colony} ant colony in Agants match {match_id}. You are colony {colony_id}.
Win by killing the enemy queen; lose if yours dies. Act every tick via tools.

MAP "The Crossing" 150x100. Your nest {nest}. Enemy nest {enemy_nest}.
Ridges (rock, choke through gaps) at x=48-50 and x=100-102. Midfield x=75.

UNITS  worker (gather food/dirt, build), soldier (HP200 dmg22), scout (vision/intel),
 spitter (ranged anti-mass: HP70, range5, ~16dmg+~8 splash to adjacent, slow, fragile vs focused
 soldier fire — counter to massed soldiers/workers; hold-and-fire, never chases. Reposition it with
 send_command unit_command move_to/hold; it auto-fires at anything within range 5),
 raider (RANGED SIEGE: HP65, 45 food, range7 — OUT-RANGES spitters5. Hold-and-fire, no chase. ~95 vs
 STRUCTURES + ~16 vs spitters from safety, but only ~6 vs soldiers (armor). THE counter to a spitter+
 bulwark turtle; hard-countered by soldiers. Auto-seeks structures; move_to/hold to aim it. RPS:
 spitters>soldiers>raiders>turtle), queen (HP900, never command).
SPAWN COST/TIME  worker 25food/20t, soldier 50food/35t, scout 35food/25t, spitter 45food/30t. Queue max 10. Food reserved at queue time.
BUILDINGS (cost dirt)  larder 150 (+6food/t passive income), watchtower 80 (vision), guard_post 150 (turret),
 barracks 200, wall 25, bulwark 50 (cheap spiked barricade: HP250, blocks + chips adjacent enemies ~4/t).
LIFESPAN  worker 500t, soldier 300t, scout 200t, spitter 300t.

DIRECTIVE LEVERS (patch_directive):
 spawn.<worker|soldier|scout|spitter|raider>.target_ratio (sum ~1.0), .min, .max
 economy.upgrade_priority [list], economy.auto_upgrade, economy.priority_food [x,y], economy.gather_dirt true/false
 military.stance, military.rally_point [x,y], military.rally_release_at <count>,
   military.attack_target [x,y], military.auto_attack, military.retreat,
   military.siege_priority "queen"

HARD RULES — violating these loses games:

1. OFFENSE OR DEFENSE, NOT BOTH. retreat=True reliably pulls soldiers home and overrides
   attacking, so never set retreat=True and attack_target at the same time. To attack, patch in
   ONE call: {{\"military\":{{\"retreat\":false,\"attack_target\":[ex,ey],\"siege_priority\":\"queen\"}}}}.
   To defend: {{\"military\":{{\"retreat\":true,\"attack_target\":null}}}}; clear retreat once safe.

2. JUST ATTACK — RALLY IS OPTIONAL. Armies now advance as a body and push THROUGH the
   chokepoints together (they no longer die one-by-one), so once you have a soldier mass (~10+)
   you can set attack_target=[{enemy_nest}] directly. Rally is only a TIMING tool: to launch one
   big coordinated wave, set rally_point=[75,50]+rally_release_at=12, wait for the count, then set
   attack_target. Setting attack_target now AUTO-OVERRIDES any leftover rally_point (a stale rally can
   never pin your army again — you don't need to clear it). Don't stall your whole army rallying every fight.

3. BUILD A LARDER (~tick 150-200). Home/approach food depletes ~tick 300; a larder (150 dirt,
   +6 food/t forever) keeps you spawning. Dirt now accumulates on its own (workers gather it by
   default + a small passive trickle), so you'll have ~150 dirt by mid-game without doing anything
   — just build when you can afford it: send_command("build", {{"build":{{"type":"larder",
   "x":<≥20 tiles from your nest>,"y":<y>}}}}). NOTE: larders must be ≥20 tiles from your nest.

4. SIEGE: keep siege_priority="queen" so soldiers focus the enemy queen over her bodyguards.
   Enemy guard posts are auto-targeted by your soldiers — no need to micro them.

STRATEGY FLOW:
 - Early (0-150): ~60% workers, auto_upgrade=true, scout the flanks, queue a few soldiers.
 - Mid (150-350): shift to ~55% soldiers. Once you have ~10+ soldiers, push:
   attack_target=[{enemy_nest}] + siege_priority="queen". Build a larder when you have 150 dirt.
 - Defend: if enemy_soldiers_near_nest>3, patch retreat=true + attack_target=null; clear once safe.
 - READ YOUR SITREP every tick (shown as SITREP lines / the state's 'sitrep' object) — pure facts, never advice:
   * standing: your strength vs the enemy on military/economy/territory/queen. Enemy figures are
     SCOUTED-ONLY — "unknown"/"not_observable" means you haven't scouted it (scout to learn it), NOT zero.
   * orders: whether each standing order is actually WORKING — attack_target "no_effect" = your army is
     NOT advancing (fix it); "engaged" = fighting at the objective; rally "holding"/"filling" = staged count.
   * field: scouted front-line per lane, last-seen enemy army, known enemy structures.
   Also read 'advisor' facts and get_chat (react to the opponent). The sitrep describes reality — YOU pick the move.

TOOLS: patch_directive(patches) sets standing orders. send_command(command_type,data) for
buy_upgrade/build/convert/cancel_spawn/unit_command. get_units(type) grabs unit IDs/positions
cheaply. get_chat(since_tick) reads incoming messages. get_intel_map() for a spatial picture.
send_chat(message) to taunt. Be decisive: usually 1-2 tool calls per tick.
For unit_command: data is flat — {{\"ant_id\":N,\"command\":\"attack_move\",\"x\":X,\"y\":Y}}.
Use attack_move to send a unit somewhere AND fight through; move_to is a pure reposition
(no attacking — for retreating/regrouping). Do NOT nest command inside an \"override\" dict.
Use ant IDs from the state's unit lists or get_units(type) — never guess or increment IDs.
Ants die frequently; IDs become stale within 1-2 ticks. If you see 400 errors, stop retrying and use directives instead.

FEEDBACK: When the game ends (phase=ended, or your colony alive=False), call submit_feedback()
with what worked, what data was unclear or missing, and what you'd do differently. Be specific —
include tick numbers and field names. This is stored for developer review."""


# --------------------------------------------------------------------------- #
# State formatting                                                             #
# --------------------------------------------------------------------------- #

def format_state_message(state: dict, notifs: list[dict]) -> str:
    if "error" in state:
        return f"State unavailable: {state['error']}"
    c = state["counts"]
    cb = state.get("combat", {})
    lines = [
        f"TICK {state['tick']} phase={state['phase']}",
        f"food={state['food']} dirt={state['dirt']} income={state['income_per_s']}/s dirt_income={state.get('dirt_per_s',0)}/s",
        f"workers={c['workers']} soldiers={c['soldiers']} scouts={c['scouts']}"
        + (f" spitters={c['spitters']}" if c.get('spitters') else "")
        + (f" raiders={c['raiders']}" if c.get('raiders') else "")
        + f" queen_hp={state.get('queen_hp')}",
        f"tiers W{state['tiers']['worker']}/Sc{state['tiers']['scout']}/So{state['tiers']['soldier']} "
        f"aging(w/s/sc)={state['aging_soon']['workers']}/{state['aging_soon']['soldiers']}/{state['aging_soon']['scouts']}"
        + (f"/sp{state['aging_soon']['spitters']}" if state['aging_soon'].get('spitters') else ""),
        f"siege={cb.get('soldiers_in_siege')} near_enemy={cb.get('soldiers_near_enemy_nest')} "
        f"enemy_near_us={cb.get('enemy_soldiers_near_nest')} enemy_queen_hp={cb.get('enemy_queen_hp')} "
        f"queen_dps={cb.get('queen_dps_actual')} dps_potential={cb.get('siege_dps_potential')}",
    ]
    structs = state.get("own_structures", [])
    if structs:
        lines.append("structures: " + ", ".join(f"{s['type']}@({s['x']},{s['y']})" for s in structs))
    nodes = state.get("viable_food_nodes", [])
    if nodes:
        lines.append("food_nodes: " + ", ".join(
            f"({n['pos'][0]},{n['pos'][1]}){n['tier'][:1]}={n['amt']}({n['pct']}%)w{n['workers_here']}/{n['cap']}"
            for n in nodes[:6]))
    dirt_nodes = state.get("viable_dirt_nodes", [])
    if dirt_nodes:
        lines.append("dirt_nodes: " + ", ".join(
            f"({n['pos'][0]},{n['pos'][1]}){n['tier'][:1]}={n['amt']}({n['pct']}%)w{n['workers_here']}"
            + ("" if n["known"] else "[unscouted]")
            for n in dirt_nodes[:4]))
    # income sanity check: if income=0 but food was collected, the meter is unreliable
    if state.get("income_per_s", 0) == 0 and state.get("food_collected", 0) > 50 and state["tick"] > 80:
        lines.append("NOTE: income_per_s reads 0 but food_collected shows workers ARE delivering — "
                     "the income sensor may be delayed or unreliable; do NOT over-react to 0-income.")
    if state.get("advisor"):
        lines.append("ADVISOR: " + " | ".join(state["advisor"]))
    if state.get("events"):
        lines.append("events: " + " | ".join(str(e) for e in state["events"][:5]))
    sr = state.get("sitrep")
    if sr:
        st = sr.get("standing", {})
        m = st.get("military", {}); e = st.get("economy", {}).get("you", {}); q = st.get("queen", {})
        lines.append(f"SITREP standing: military you {m.get('you')} vs enemy {m.get('enemy')} "
                     f"({m.get('verdict')}); food {e.get('food')} income {e.get('income_per_s')}/s; "
                     f"queen {q.get('your_hp_pct')}% threat {q.get('threat_soldiers_near_nest')}")
        for o in sr.get("orders", []):
            lines.append(f"SITREP order [{o['intent']}]: {o['status']} — {o['detail']}")
        fld = sr.get("field", {})
        ea = fld.get("enemy_army", {})
        if ea.get("seen"):
            lines.append(f"SITREP field: enemy army ~{ea['size']} @ {ea['pos']} (age {ea['age_ticks']}t); "
                         f"front_line {fld.get('front_line')}")
        else:
            lines.append(f"SITREP field: enemy army not currently in sight; front_line {fld.get('front_line')}")
    if notifs:
        lines.append("NOTIFICATIONS: " + " | ".join(
            n.get("data", {}).get("label", n.get("type", str(n))) if isinstance(n, dict) else str(n)
            for n in notifs))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Shared UI state                                                              #
# --------------------------------------------------------------------------- #

class UI:
    def __init__(self, log_file=None):
        self.matches: list[dict] = []
        self.online: list[dict] = []
        self.state: dict = {}
        self.log: deque[str] = deque(maxlen=200)
        self.selected = 0
        self.status = "no seat"
        self.running = True
        self.auto_challenge = False
        self._log_file = log_file

    def add_log(self, line: str):
        self.log.append(line)
        if self._log_file:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            try:
                self._log_file.write(f"{ts}  {line}\n")
                self._log_file.flush()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Agent loop                                                                    #
# --------------------------------------------------------------------------- #

def trim_context(messages: list[dict], keep: int = 12) -> list[dict]:
    if len(messages) <= keep + 1:
        return messages
    return [messages[0]] + messages[-keep:]


async def agent_loop(client: GameClient, llm: OpenAI, model: str, ui: UI, loop: asyncio.AbstractEventLoop):
    messages = [{"role": "system", "content": system_prompt(client.colony_id, client.match_id)}]
    last_tick = -1
    _starter_sent = False
    _ctx = {"reason": ""}   # carries the LLM's current rationale into patch_directive
    tool_map = {
        "patch_directive": lambda a: client.patch_directive(a.get("patches", {}), _ctx["reason"]),
        "send_command": lambda a: client.send_command(a.get("command_type", ""), a.get("data", {})),
        "get_intel_map": lambda a: client.get_intel_map(),
        "send_chat": lambda a: client.send_chat(a.get("message", "")),
        "get_chat": lambda a: client.get_chat(a.get("since_tick", 0)),
        "get_units": lambda a: client.get_units(a.get("type", "")),
        "submit_feedback": lambda a: client.submit_feedback(a.get("feedback", ""), a.get("category", "general")),
    }
    _state_errors = 0
    while ui.running and client.colony_id is not None:
        try:
            state = await client.state()
            notifs = await client.notifications()
            _state_errors = 0
        except httpx.HTTPError as e:
            _state_errors += 1
            if _state_errors >= 8:
                ui.add_log(f"[err] too many fetch errors — agent loop exiting")
                break
            ui.add_log(f"[err] state fetch: {e}")
            await asyncio.sleep(1.0)
            continue
        ui.state = state
        tick = state.get("tick", -1)
        if "error" in state or tick == last_tick:
            await asyncio.sleep(0.2)
            continue
        last_tick = tick

        # Apply a safe starter directive on the very first tick so the LLM never
        # starts from blank defaults with dangerously low max caps.
        if not _starter_sent:
            _starter_sent = True
            starter = {
                "spawn": {
                    "worker":  {"target_ratio": 0.60, "max": 40},
                    "soldier": {"target_ratio": 0.25, "max": 30},
                    "scout":   {"target_ratio": 0.15, "max": 12},
                },
                "economy": {"gather_dirt": True},
            }
            try:
                await client.patch_directive(starter)
                ui.add_log(f"[t{tick}] starter directive applied")
            except Exception as e:
                ui.add_log(f"[t{tick}] starter directive failed: {e}")

        # Detect game over: prompt for feedback then exit
        game_over = (state.get("phase") not in ("lobby", "running") or
                     state.get("queen_alive") is False or
                     getattr(client, "forfeited", False))
        if game_over:
            ui.add_log(f"[t{tick}] game over — requesting feedback")
            messages.append({"role": "user", "content":
                f"GAME OVER at tick {tick}. phase={state.get('phase')} alive={state.get('alive')} "
                f"Your final state: {format_state_message(state, notifs)}\n"
                "Call submit_feedback() now with your post-game observations."})
            try:
                resp = await loop.run_in_executor(None, lambda: llm.chat.completions.create(
                    model=model, messages=messages, tools=TOOLS, temperature=0.4))
                msg = resp.choices[0].message
                for call in (msg.tool_calls or []):
                    try:
                        args = json.loads(call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    fn = tool_map.get(call.function.name)
                    if fn:
                        result = await fn(args)
                        ui.add_log(f"[t{tick}] {call.function.name}({_brief(args)}) -> {'ok' if 'error' not in result else result['error'][:60]}")
            except Exception as e:
                ui.add_log(f"[t{tick}] feedback LLM error: {e}")
            break

        messages[:] = trim_context(messages)
        messages.append({"role": "user", "content": format_state_message(state, notifs)})

        try:
            resp = await loop.run_in_executor(None, lambda: llm.chat.completions.create(
                model=model, messages=messages, tools=TOOLS, temperature=0.4))
        except Exception as e:
            ui.add_log(f"[t{tick}] LLM error: {e}")
            await asyncio.sleep(1.0)
            continue

        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if msg.content:
            ui.add_log(f"[t{tick}] {msg.content.strip()[:160]}")
        # Make this turn's rationale available to patch_directive so it reaches the
        # spectator AI Activity feed as the agent's "thinking".
        _ctx["reason"] = (msg.content or "").strip()

        for call in (msg.tool_calls or []):
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = tool_map.get(call.function.name)
            try:
                result = await fn(args) if fn else {"error": "unknown tool"}
            except Exception as e:
                result = {"error": f"tool exception: {e}"}
            tag = "ok" if "error" not in result else result["error"][:60]
            ui.add_log(f"[t{tick}] {call.function.name}({_brief(args)}) -> {tag}")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)[:1500]})


def _brief(args: dict) -> str:
    s = json.dumps(args)
    return s[:70] + ("..." if len(s) > 70 else "")


# --------------------------------------------------------------------------- #
# Background poller (keeps match/agent lists fresh even with no seat)          #
# --------------------------------------------------------------------------- #

async def poller(client: GameClient, ui: UI):
    last_heartbeat = 0.0
    while ui.running:
        now = asyncio.get_event_loop().time()
        if now - last_heartbeat >= 30:
            await client.heartbeat()
            last_heartbeat = now
        try:
            all_matches = await client.list_matches()
            ui.matches = [m for m in all_matches if not m.get("winner")]
        except Exception as e:
            ui.add_log(f"[poll] matches: {type(e).__name__}: {str(e)[:80]}")
        try:
            ui.online = await client.agents_online()
        except Exception:
            pass
        if ui.selected >= len(ui.matches):
            ui.selected = max(0, len(ui.matches) - 1)
        await asyncio.sleep(3.0)


# --------------------------------------------------------------------------- #
# Rendering                                                                     #
# --------------------------------------------------------------------------- #

def render(ui: UI, client: GameClient, total_height: int = 32) -> Layout:
    log_h = max(6, min(10, (total_height - 3) // 3))

    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=1),
        Layout(name="log", size=log_h),
        Layout(name="footer", size=1),
    )
    layout["top"].split_row(Layout(name="left"), Layout(name="right"))

    layout["left"].update(render_matches(ui, client))
    layout["right"].update(render_colony(ui, client))
    log_lines = list(ui.log)[-(log_h - 2):]
    layout["log"].update(Panel(
        Group(*[Text(l, no_wrap=True, overflow="ellipsis") for l in log_lines]) if log_lines
        else Text("", style="dim"),
        title="Agent log", border_style="grey50"))

    if ui.auto_challenge:
        footer_keys = "[a] stop auto   [l] leave   [w] watch   [q] quit"
    elif client.colony_id is not None:
        s = ui.state or {}
        phase = s.get("phase", "")
        if phase == "running":
            footer_keys = "[l] leave   [f] forfeit   [w] watch   [q] quit"
        elif phase == "finished":
            footer_keys = "[l] leave   [w] watch   [q] quit"
        else:
            footer_keys = "[s] start   [l] leave   [w] watch   [q] quit"
    else:
        footer_keys = "[r] RED   [b] BLUE   [n] new vs bot   [a] auto-challenge   [w] watch   [↑/↓] select   [q] quit"
    footer = Text(f"{footer_keys}   — {ui.status}", style="dim")
    layout["footer"].update(footer)
    return layout


def render_matches(ui: UI, client: GameClient) -> Panel:
    online = Table.grid(padding=(0, 1))
    online.add_row(Text(f"Online ({len(ui.online)}):", style="bold"))
    for a in ui.online[:5]:
        where = a["colony"] or "lobby"
        style = "dim" if a["colony"] else "yellow"
        online.add_row(Text(f" {a['agent']:<14} {where:<6} {a['seconds_ago']}s ago", style=style))

    if client.colony_id is not None:
        # --- live match panel ---
        s = ui.state or {}
        cur = next((m for m in ui.matches if m.get("match_id") == client.match_id), None)
        my_col = client.colony_id
        opp_col = 1 - my_col
        opp_name = "?"
        if cur:
            seats = cur.get("seats", {})
            opp_name = seats.get(str(opp_col), {}).get("agent") or "open"

        colony_label = "RED" if my_col == 0 else "BLUE"
        opp_label    = "BLUE" if my_col == 0 else "RED"
        colony_style = "red" if my_col == 0 else "blue"
        opp_style    = "blue" if my_col == 0 else "red"

        info = Text()
        info.append(f"You: ", style="bold")
        info.append(f"{client.agent_name}", style=f"bold {colony_style}")
        info.append(f" ({colony_label})\n", style=colony_style)
        info.append(f"Opp: ", style="bold")
        info.append(f"{opp_name}", style=f"bold {opp_style}")
        info.append(f" ({opp_label})\n", style=opp_style)
        if client.match_id:
            info.append(f"Match {client.match_id[:8]}\n", style="dim")

        body = Table.grid()
        body.add_row(info)

        sightings = s.get("enemy_sightings") or []
        if sightings:
            body.add_row(Text("── Enemy sightings ──", style="dim"))
            for sx, sy, sol, tot, stk, *_ in sightings[:4]:
                age = s.get("tick", 0) - stk
                body.add_row(Text(f" ({sx},{sy})  {sol}sol/{tot}tot  {age}t ago",
                                  style="dim" if age > 30 else ""))
        else:
            body.add_row(Text("  no sightings (fog of war)", style="dim"))

        advisor = s.get("advisor") or []
        if advisor:
            body.add_row(Text("── Advisor ──", style="dim"))
            for hint in advisor[:3]:
                body.add_row(Text(f" {hint}", no_wrap=True, overflow="ellipsis", style="yellow"))

        body.add_row(Text(""))
        body.add_row(online)
        phase = (cur or {}).get("phase", "?")
        return Panel(body, title=f"Match  [{phase}]", border_style=colony_style)

    # --- lobby / running list ---
    t = Table.grid(padding=(0, 1))
    open_matches = [m for m in ui.matches if m.get("phase") in ("lobby", "running")]
    for i, m in enumerate(open_matches):
        phase = m.get("phase", "?")
        if phase == "running":
            dot, style = "●", "green"
        else:
            dot, style = "◌", "yellow"
        seats = m.get("seats", {})
        def _seat(s): return s.get("agent") or ("bot" if s.get("brain_type") == "bot" else "open")
        red  = _seat(seats.get("0", {}))
        blue = _seat(seats.get("1", {}))
        mid  = m["match_id"][:8]
        marker = "▶ " if i == ui.selected else "  "
        line = Text(marker, style="bold cyan" if i == ui.selected else "")
        line.append(f"{dot} ", style=style)
        line.append(f"{mid}  ", style="bold" if i == ui.selected else "")
        line.append(f"t{m.get('tick',0)} R:{red} B:{blue}", style="dim")
        if phase == "running":
            line.append("  [running]", style="green dim")
        t.add_row(line)
    if not open_matches:
        t.add_row(Text("(no open matches — press 'n' to create one)", style="dim"))
    return Panel(Group(t, Text(""), online), title="Matches", border_style="grey50")


def render_colony(ui: UI, client: GameClient) -> Panel:
    s = ui.state
    if not s or "error" in s:
        # "game not started" = we're seated but in lobby — show waiting screen
        if client.colony_id is not None and s and "not started" in s.get("error", ""):
            s = {"phase": "lobby"}  # synthetic; fall through to lobby render below
        else:
            body = Text(s.get("error", "not in a seat — press 'j' to join") if s else
                        "not in a seat — press 'j' to join", style="dim")
            return Panel(body, title="Colony State", border_style="grey50")

    if s.get("phase") == "lobby":
        cur = next((m for m in ui.matches if m.get("match_id") == client.match_id), None)
        seats = (cur or {}).get("seats", {})
        opp_col = str(1 - (client.colony_id or 0))
        opp_agent = seats.get(opp_col, {}).get("agent")
        body = Text()
        if opp_agent:
            body.append("Both seated — waiting to start\n", style="bold yellow")
            body.append(f"Opponent: {opp_agent}\n", style="dim")
            body.append("Press [s] to start or wait for auto-start", style="dim")
        else:
            body.append("Waiting for opponent…\n", style="dim")
            mid = client.match_id or "?"
            body.append(f"Match {mid[:8]}\n", style="dim")
            body.append("Share the match ID or wait for auto-challenge to pair", style="dim")
        return Panel(body, title="Waiting", border_style="grey50")

    colony = "RED" if client.colony_id == 0 else "BLUE"
    cstyle = "red" if client.colony_id == 0 else "blue"
    c = s["counts"]
    cb = s.get("combat", {})
    head = Text()
    head.append(f"Tick {s['tick']}  ", style="bold")
    head.append(f"{colony}  ", style=f"bold {cstyle}")
    head.append(f"food:{s['food']} dirt:{s['dirt']} income:{s['income_per_s']}/s\n")
    head.append(f"Workers:{c['workers']} Soldiers:{c['soldiers']} Scouts:{c['scouts']} "
                + (f"Spitters:{c['spitters']} " if c.get('spitters') else "")
                + (f"Raiders:{c['raiders']} " if c.get('raiders') else "")
                + f"QueenHP:{s.get('queen_hp')}\n")
    head.append(f"Siege:{cb.get('soldiers_in_siege',0)} EnemyQueenHP:{cb.get('enemy_queen_hp')} "
                f"QueenDPS:{cb.get('queen_dps_actual',0)}\n", style="dim")
    ev = Table.grid()
    ev.add_row(Text("── Recent events ──", style="dim"))
    for e in (s.get("events") or [])[:8]:
        ev.add_row(Text(f" {e}", no_wrap=True, overflow="ellipsis"))
    return Panel(Group(head, ev), title="Colony State", border_style=cstyle)


# --------------------------------------------------------------------------- #
# Keyboard (asyncio reader on raw stdin fd — no thread, no buffering issues)   #
# --------------------------------------------------------------------------- #

def _parse_key(data: bytes) -> list[str]:
    keys: list[str] = []
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x1B:  # ESC or CSI sequence
            if i + 2 < len(data) and data[i + 1] == 0x5B:  # ESC [
                c = data[i + 2]
                if c == 0x41:
                    keys.append("UP"); i += 3; continue
                if c == 0x42:
                    keys.append("DOWN"); i += 3; continue
            keys.append("ESC")
        elif b == 0x03:
            keys.append("\x03")
        elif b in (0x0D, 0x0A):
            keys.append("\r")
        elif b == 0x7F:
            keys.append("\x7f")
        else:
            try:
                keys.append(chr(b))
            except ValueError:
                pass
        i += 1
    return keys


# --------------------------------------------------------------------------- #
# Interactive (TUI) entry                                                       #
# --------------------------------------------------------------------------- #

async def run_tui(cfg: dict, log_file=None):
    console = Console(force_terminal=True, highlight=False)
    client = GameClient(cfg["game_url"], cfg["api_key"])
    llm = OpenAI(base_url=cfg["llm"]["base_url"],
                 api_key=cfg["llm"].get("api_key") or "no-llm-key-set")
    model = cfg["llm"]["model"]
    client.model_label = model  # sent on join_seat for stat tracking
    ui = UI(log_file=log_file)
    loop = asyncio.get_event_loop()
    agent_task: asyncio.Task | None = None

    # --- keyboard: asyncio reader on raw stdin, terminal restored in finally --
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    tty.setraw(fd)
    # setraw disables OPOST which breaks \n→\r\n for stdout; re-enable output processing
    _mode = termios.tcgetattr(fd)
    _mode[1] |= termios.OPOST | termios.ONLCR
    termios.tcsetattr(fd, termios.TCSADRAIN, _mode)
    key_queue: asyncio.Queue[str] = asyncio.Queue()

    def _stdin_ready():
        try:
            data = os.read(fd, 32)
        except OSError:
            return
        for key in _parse_key(data):
            key_queue.put_nowait(key)

    loop.add_reader(fd, _stdin_ready)

    def on_key(ch: str):
        if ch in ("q", "\x03"):
            ui.running = False
        elif ch == "UP":
            ui.selected = max(0, ui.selected - 1)
        elif ch == "DOWN":
            open_matches = [m for m in ui.matches if m.get("phase") in ("lobby", "running")]
            ui.selected = min(max(0, len(open_matches) - 1), ui.selected + 1)
        elif ch == "r" and client.colony_id is None:
            asyncio.create_task(do_join(0))
        elif ch == "b" and client.colony_id is None:
            asyncio.create_task(do_join(1))
        elif ch == "l" and client.colony_id is not None:
            asyncio.create_task(do_leave())
        elif ch == "a":
            asyncio.create_task(do_toggle_auto())
        elif ch == "f" and client.colony_id is not None:
            asyncio.create_task(do_forfeit())
        elif ch == "n" and not ui.auto_challenge:
            asyncio.create_task(do_new())
        elif ch == "s":
            asyncio.create_task(do_start())
        elif ch == "w":
            do_watch()

    async def start_agent():
        nonlocal agent_task
        if agent_task and not agent_task.done():
            agent_task.cancel()
        agent_task = asyncio.create_task(agent_loop(client, llm, model, ui, loop))

    async def do_leave():
        nonlocal agent_task
        if agent_task and not agent_task.done():
            agent_task.cancel()
            agent_task = None
        try:
            await client.release()
            ui.status = f"{client.agent_name} (lobby)"
            ui.add_log("left seat — back in lobby")
        except Exception as e:
            ui.add_log(f"[err] leave: {e}")

    async def do_forfeit():
        try:
            r = await client.forfeit()
            if "error" in r:
                ui.add_log(f"[warn] forfeit: {r['error']}")
            else:
                ui.add_log("forfeited — opponent wins")
                client.forfeited = True
        except Exception as e:
            ui.add_log(f"[err] forfeit: {e}")

    async def do_toggle_auto():
        nonlocal auto_challenge_task
        ui.auto_challenge = not ui.auto_challenge
        if ui.auto_challenge:
            if auto_challenge_task is None or auto_challenge_task.done():
                auto_challenge_task = asyncio.create_task(auto_challenge_loop())
        else:
            if auto_challenge_task and not auto_challenge_task.done():
                auto_challenge_task.cancel()
                auto_challenge_task = None
            ui.add_log("[auto] challenger mode off")

    async def do_join(col: int):
        open_matches = [m for m in ui.matches if m.get("phase") in ("lobby", "running")]
        if not open_matches:
            ui.add_log("[warn] no open matches — press 'n' to create one")
            return
        idx = min(ui.selected, len(open_matches) - 1)
        mid = open_matches[idx]["match_id"]
        if client.colony_id is not None:
            await client.release()
        try:
            await client.join(mid, col)
            color = "RED" if col == 0 else "BLUE"
            ui.status = f"{client.agent_name} {color} @ {mid[:8]}"
            ui.add_log(f"joined {mid[:8]} as {client.agent_name} ({color})")
            await start_agent()
        except httpx.HTTPError as e:
            ui.add_log(f"[err] join failed: {e.response.text[:120] if hasattr(e, 'response') else e}")

    async def do_new():
        try:
            tps = cfg.get("tps")
            mid = await client.new_match(brains={"0": "mcp", "1": "bot"}, tps=tps)
            await client.join(mid, 0)
            tps_str = f" @ {tps}TPS" if tps else ""
            ui.status = f"{client.agent_name} RED @ {mid[:8]}"
            ui.add_log(f"created + joined {mid[:8]}{tps_str} — press [s] to start vs bot")
        except httpx.HTTPError as e:
            ui.add_log(f"[err] new match: {e}")

    async def do_start():
        if client.match_id is None:
            ui.add_log("[warn] not in a match — press 'n' to create one first")
            return
        if client.colony_id is None:
            ui.add_log("[warn] not seated — press 'r' or 'b' to join a seat first")
            return
        r = await client.start_game()
        if "error" in r:
            ui.add_log(f"[warn] start: {r['error']}")
        else:
            ui.add_log("game started")
            await start_agent()

    def do_watch():
        mid = client.match_id or (ui.matches[ui.selected]["match_id"] if ui.matches else None)
        if not mid:
            return
        url = f"{cfg['game_url'].replace('api.', 'agants.')}/game?match={mid}"
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ui.add_log(f"opening {url}")
        except OSError:
            ui.add_log(f"watch: {url}")

    async def auto_challenge_loop():
        """Permanent challenger: find/create a match, wait for a human opponent,
        play when the game starts, then repeat until auto_challenge is turned off."""
        nonlocal agent_task
        ui.add_log("[auto] challenger mode active — searching for a match")
        _auto_errs = 0
        while ui.running and ui.auto_challenge:
            # ── Phase 1: Get a seat ───────────────────────────────────────────
            if client.colony_id is None:
                try:
                    all_matches = await client.list_matches()
                    joined = False
                    # Prefer matches where one seat is already taken (someone waiting)
                    lobby = [m for m in all_matches
                             if not m.get("winner") and m.get("phase") == "lobby"]
                    lobby.sort(key=lambda m: 0 if sum(
                        1 for i in [0, 1]
                        if m.get("seats", {}).get(str(i), {}).get("agent")
                    ) == 1 else 1)
                    for m in lobby:
                        seats = m.get("seats", {})
                        for col_id in [0, 1]:
                            if not seats.get(str(col_id), {}).get("agent"):
                                try:
                                    await client.join(m["match_id"], col_id)
                                    color = "RED" if col_id == 0 else "BLUE"
                                    ui.status = f"{client.agent_name} {color} @ {m['match_id'][:8]} (auto)"
                                    ui.add_log(f"[auto] joined {m['match_id'][:8]} as {color} — waiting for opponent")
                                    joined = True
                                    break
                                except Exception:
                                    pass
                        if joined:
                            break
                    if not joined:
                        mid = await client.new_match(brains={"0": "mcp", "1": "mcp"}, tps=cfg.get("tps"))
                        await client.join(mid, 0)
                        ui.status = f"{client.agent_name} RED @ {mid[:8]} (waiting...)"
                        ui.add_log(f"[auto] created {mid[:8]} — waiting for opponent")
                except Exception as e:
                    ui.add_log(f"[auto] seat error: {e}")
                    await asyncio.sleep(5)
                    continue

            await asyncio.sleep(3)

            # ── Phase 2: Check game state ──────────────────────────────────────
            if client.colony_id is None:
                continue
            try:
                state = await client.state()
                ui.state = state  # keep right pane live during lobby/waiting
                _auto_errs = 0
            except Exception:
                _auto_errs += 1
                if _auto_errs >= 5:
                    ui.add_log("[auto] repeated errors — abandoning stale match")
                    _auto_errs = 0
                    client.token = None
                    client.colony_id = None
                    client.match_id = None
                await asyncio.sleep(3)
                continue

            phase = state.get("phase", "lobby")

            if phase == "lobby":
                # Auto-start when both seats are filled
                try:
                    r = await client.http.get(
                        f"{client.base}/api/matches/{client.match_id}",
                        headers=client._auth())
                    mdata = r.json()
                    seats = mdata.get("seats", {})
                    if all(seats.get(str(i), {}).get("agent") for i in [0, 1]):
                        await client.start_game()
                        ui.add_log("[auto] both seated — game started!")
                except Exception:
                    pass

            elif phase == "running":
                if agent_task is None or agent_task.done():
                    await start_agent()

            else:
                # Ended or unknown — wait for agent to finish feedback, then release
                ui.add_log(f"[auto] game over — back to matchmaking")
                if agent_task and not agent_task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(agent_task), timeout=15)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        agent_task.cancel()
                agent_task = None
                await client.release()
                ui.status = f"{client.agent_name} (auto-challenge)"
                await asyncio.sleep(2)

        ui.add_log("[auto] challenger mode stopped")

    auto_challenge_task: asyncio.Task | None = None

    poll_task = asyncio.create_task(poller(client, ui))

    def _redraw():
        h, w = console.size
        with console.capture() as cap:
            console.print(render(ui, client, h))
        # Move to home and overwrite; hide cursor during paint to avoid flicker
        sys.stdout.write("\033[?25l\033[H" + cap.get().rstrip("\n") + "\033[?25h")
        sys.stdout.flush()

    # Clear screen once before entering the loop
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

    _last_agent_restart = 0.0

    try:
        while ui.running:
            while not key_queue.empty():
                on_key(key_queue.get_nowait())
            # Auto-start agent loop when opponent launches the game remotely,
            # or restart after a crash — 5s cooldown to prevent rapid crash loops.
            if (client.colony_id is not None and not ui.auto_challenge and
                    (agent_task is None or agent_task.done())):
                cur = next((m for m in ui.matches if m.get("match_id") == client.match_id), None)
                now = asyncio.get_event_loop().time()
                if cur and cur.get("phase") == "running" and now - _last_agent_restart >= 5.0:
                    _last_agent_restart = now
                    if agent_task is not None and agent_task.done():
                        exc = agent_task.exception() if not agent_task.cancelled() else None
                        if exc:
                            ui.add_log(f"[agent] crashed: {type(exc).__name__}: {exc} — restarting")
                    asyncio.create_task(start_agent())
                    ui.add_log("opponent started the game — agent loop launched")
            try:
                _redraw()
            except Exception as e:
                ui.add_log(f"[render] {type(e).__name__}: {e}")
            await asyncio.sleep(0.25)
    finally:
        # Show cursor and clear on exit so the shell prompt appears cleanly
        sys.stdout.write("\033[?25h\033[2J\033[H")
        sys.stdout.flush()
        loop.remove_reader(fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        ui.running = False
        poll_task.cancel()
        if agent_task:
            agent_task.cancel()
        if auto_challenge_task and not auto_challenge_task.done():
            auto_challenge_task.cancel()
        await client.release()
        await client.close()


# --------------------------------------------------------------------------- #
# Headless entry                                                               #
# --------------------------------------------------------------------------- #

async def run_headless(cfg: dict, spec: str, log_file=None):
    mid_str, _, col_str = spec.partition(":")
    if not col_str:
        sys.exit("--headless expects MATCH_ID:COLONY (e.g. a1b2c3d4:0)")
    client = GameClient(cfg["game_url"], cfg["api_key"])
    llm = OpenAI(base_url=cfg["llm"]["base_url"],
                 api_key=cfg["llm"].get("api_key") or "no-llm-key-set")
    ui = UI(log_file=log_file)
    loop = asyncio.get_event_loop()

    class StdoutLog:
        def append(self, line):
            print(line, flush=True)
    ui.log = StdoutLog()  # type: ignore

    await client.join(mid_str, int(col_str))
    print(f"joined {client.match_id} as {client.agent_name} (colony {col_str})", flush=True)
    try:
        await agent_loop(client, llm, cfg["llm"]["model"], ui, loop)
    finally:
        await client.release()
        await client.close()


# --------------------------------------------------------------------------- #
# Main                                                                          #
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Agants Controller — AI agent + TUI")
    ap.add_argument("--setup", action="store_true", help="interactive config wizard")
    ap.add_argument("--headless", metavar="MATCH_ID:COLONY", help="run agent without TUI")
    ap.add_argument("--log", metavar="FILE", nargs="?", const="",
                    help="write agent log to FILE (omit path for ~/.config/agants/logs/)")
    args = ap.parse_args()

    if args.setup:
        setup_wizard()
        return

    cfg = load_config()
    if cfg is None:
        print("No config found.\n")
        print("Agants is an ant-colony RTS for AI agents. To get started:")
        print("  1. Register at https://agants.datthemaster.com/register.html to get an API key")
        print("  2. Get an LLM key (OpenAI, or any OpenAI-compatible provider)")
        print()
        ans = input("Run setup wizard now? [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            setup_wizard()
            cfg = load_config()
        if cfg is None:
            sys.exit("Run: controller.py --setup")

    if not cfg.get("api_key"):
        print("Note: no Agants API key set — joining as guest. Register at")
        print("https://agants.datthemaster.com/register.html to track match history.\n")

    log_file = None
    if args.log is not None:
        if args.log:
            log_path = Path(args.log)
        else:
            log_dir = Path.home() / ".config/agants/logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / f"controller_{ts}.log"
        log_file = open(log_path, "w", buffering=1)
        header = (f"# Agants controller log — {datetime.datetime.now().isoformat()}\n"
                  f"# pid={os.getpid()}  log={log_path}\n")
        log_file.write(header)
        log_file.flush()
        print(f"Logging to {log_path}", file=sys.stderr)

    try:
        if args.headless:
            asyncio.run(run_headless(cfg, args.headless, log_file=log_file))
        else:
            if not sys.stdin.isatty():
                sys.exit("No TTY. Use --headless MATCH_ID:COLONY for non-interactive runs.")
            asyncio.run(run_tui(cfg, log_file=log_file))
    except BaseException as e:
        if log_file:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            log_file.write(f"\n{ts}  FATAL: {type(e).__name__}: {e}\n")
            log_file.write(traceback.format_exc())
            log_file.flush()
        if not isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        if log_file:
            log_file.write(f"# exit {datetime.datetime.now().isoformat()}\n")
            log_file.close()


if __name__ == "__main__":
    main()
