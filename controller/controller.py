#!/usr/bin/env python3
"""Agants Controller — standalone AI agent + TUI for the Agants ant-colony RTS.

Talks to the game server over REST only. No game server imports.
Run `controller.py --setup` once to write config, then `controller.py`.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
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

def load_config() -> dict | None:
    for p in CONFIG_DIRS:
        if p.exists():
            return json.loads(p.read_text())
    return None


def setup_wizard() -> None:
    print("Agants Controller setup\n")
    game_url = input("Game server URL [https://api.datthemaster.com]: ").strip() or "https://api.datthemaster.com"
    api_key = input("Agants API key (from agants.datthemaster.com/register.html): ").strip()
    name = input("Agent name (shown in match seats, e.g. your username): ").strip() or "agent"
    base_url = input("LLM base URL [https://api.openai.com/v1]: ").strip() or "https://api.openai.com/v1"
    llm_key = input("LLM API key: ").strip()
    model = input("Model [gpt-4o]: ").strip() or "gpt-4o"
    cfg = {
        "game_url": game_url,
        "api_key": api_key,
        "name": name,
        "llm": {"base_url": base_url, "api_key": llm_key, "model": model},
    }
    GLOBAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_CONFIG.write_text(json.dumps(cfg, indent=2))
    print(f"\nWrote {GLOBAL_CONFIG}")


# --------------------------------------------------------------------------- #
# REST client                                                                  #
# --------------------------------------------------------------------------- #

class GameClient:
    def __init__(self, base: str, api_key: str):
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.http = httpx.AsyncClient(timeout=15.0)
        self.token: str | None = None
        self.match_id: str | None = None
        self.colony_id: int | None = None

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

    async def new_match(self, brains: dict | None = None) -> str:
        body: dict = {}
        if brains:
            body["config"] = {"brains": brains}
        r = await self.http.post(f"{self.base}/api/matches", json=body)
        r.raise_for_status()
        return r.json()["match_id"]

    async def start_game(self) -> dict:
        path = self._mpath("/control")
        return await self._tool_request("POST", path, {"action": "start"})

    async def join(self, match_id: str | None, colony_id: int, name: str) -> dict:
        self.match_id = match_id
        path = self._mpath(f"/seat/{colony_id}")
        r = await self.http.post(f"{self.base}{path}", json={"agent_name": name, "api_key": self.api_key})
        r.raise_for_status()
        data = r.json()
        self.token = data["token"]
        self.colony_id = colony_id
        self.match_id = data.get("match_id", match_id)
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
            return r.json() if r.content else {"ok": True}
        except httpx.HTTPError as e:
            return {"error": f"request failed: {e}"}

    async def patch_directive(self, patches: dict) -> dict:
        return await self._tool_request("POST", self._mpath(f"/directive/{self.colony_id}"), patches)

    async def send_command(self, command_type: str, data: dict) -> dict:
        return await self._tool_request("POST", self._mpath(f"/command/{self.colony_id}"),
                                        {"type": command_type, **data})

    async def get_intel_map(self) -> dict:
        return await self._tool_request("GET", self._mpath(f"/intel_map/{self.colony_id}"))

    async def send_chat(self, message: str) -> dict:
        return await self._tool_request("POST", "/api/chat", {"message": message})


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
                       "'build' (data {\"build\":{\"type\":\"larder|watchtower|guard_post|barracks|wall\",\"x\":N,\"y\":M}}), "
                       "'convert' (data {\"convert\":{\"id\":antId,\"to\":\"soldier\"}}), "
                       "'cancel_spawn' (data {\"unit_type\":\"worker|soldier|scout|all\"}), "
                       "'unit_command' (data {\"ant_id\":N,\"command\":\"move_to|attack_xy|gather|hold|patrol|clear\",\"x\":X,\"y\":Y}). "
                       "For unit_command: command is a flat key alongside ant_id, x, y — do NOT nest inside an 'override' dict.",
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
]


def system_prompt(colony_id: int, match_id: str) -> str:
    colony = "RED" if colony_id == 0 else "BLUE"
    nest = "(14,50)" if colony_id == 0 else "(136,50)"
    enemy_nest = "(136,50)" if colony_id == 0 else "(14,50)"
    return f"""You command the {colony} ant colony in Agants match {match_id}. You are colony {colony_id}.
Win by killing the enemy queen; lose if yours dies. Act every tick via tools.

MAP "The Crossing" 150x100. Your nest {nest}. Enemy nest {enemy_nest}.
Ridges (rock, choke through gaps) at x=48-50 and x=100-102. Midfield x=75.

UNITS  worker (gather food/dirt, build), soldier (HP200 dmg22), scout (vision/intel), queen (HP900, never command).
SPAWN COST/TIME  worker 25food/20t, soldier 50food/35t, scout 35food/25t. Queue max 10. Food reserved at queue time.
BUILDINGS (cost dirt)  larder 150 (+6food/t passive income), watchtower 80 (vision), guard_post 150 (turret), barracks 200, wall 25.
LIFESPAN  worker 500t, soldier 300t, scout 200t.

DIRECTIVE LEVERS (patch_directive):
 spawn.<worker|soldier|scout>.target_ratio (sum ~1.0), .min, .max
 economy.upgrade_priority [list], economy.auto_upgrade, economy.priority_food [x,y]
 military.stance, military.rally_point [x,y], military.rally_release_at <count>,
   military.attack_target [x,y], military.auto_attack, military.retreat,
   military.siege_priority "queen"

HARD RULES — violating these loses games:

1. RETREAT CANCELS ATTACK. Never set retreat=True while attack_target is set — soldiers will
   just circle your own nest forever. To attack: patch {{\"military\":{{\"retreat\":false,
   \"attack_target\":[ex,ey]}}}} in a single call. To defend: clear attack_target first.

2. RALLY BEFORE ATTACKING. Set rally_point near midfield (x=75,y=50) and rally_release_at=12
   BEFORE you set attack_target. Soldiers that advance without rallying die one-by-one at the
   chokepoint. The sequence is: (a) set rally_point+rally_release_at → wait for count → (b) set
   attack_target and clear rally_point in the same patch.

3. BUILD A LARDER BY TICK 200. Home/approach food nodes deplete ~tick 300. After that, income=0
   means you can never spawn again. Larder costs 150 dirt (+6 food/t passive forever). Use
   send_command("build", {{"build":{{"type":"larder","x":<near nest>,"y":<near nest>}}}}).
   Check dirt value in state — workers passively accumulate it.

4. SIEGE REQUIRES siege_priority="queen". Without it soldiers fight bodyguards and deal 0
   damage to the queen. Set it the moment your soldiers enter enemy territory.

STRATEGY FLOW:
 - Early (0–150): 60% workers, auto_upgrade=true, scout the flanks, queue 1-2 soldiers.
 - Mid (150–300): shift to 50% soldiers, set rally_point=[75,50], rally_release_at=12.
   Build larder NOW before food depletes.
 - Attack: once rally count hit, patch retreat=false + attack_target=[{enemy_nest}] +
   rally_point=null + siege_priority="queen" in one call.
 - Defend: if enemy_soldiers_near_nest>3, patch retreat=true + attack_target=null.
   Clear retreat once threat passes.
 - Read 'advisor' hints — they name exactly what's being neglected.

TOOLS: patch_directive(patches) sets standing orders. send_command(command_type,data) for
buy_upgrade/build/convert/cancel_spawn/unit_command. get_intel_map() for a spatial picture.
send_chat(message) to taunt. Be decisive: usually 1-2 tool calls per tick.
For unit_command: data is flat — {{\"ant_id\":N,\"command\":\"move_to\",\"x\":X,\"y\":Y}}.
Do NOT nest command inside an \"override\" dict."""


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
        f"workers={c['workers']} soldiers={c['soldiers']} scouts={c['scouts']} queen_hp={state.get('queen_hp')}",
        f"tiers W{state['tiers']['worker']}/Sc{state['tiers']['scout']}/So{state['tiers']['soldier']} "
        f"aging(w/s/sc)={state['aging_soon']['workers']}/{state['aging_soon']['soldiers']}/{state['aging_soon']['scouts']}",
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
    units = state.get("units", [])
    if units:
        idle_workers = [u for u in units if u["type"] == "worker" and u.get("state") == "idle" and not u.get("override")][:6]
        if idle_workers:
            lines.append("idle_workers: " + ", ".join(f"id={u['id']}@({u['x']},{u['y']})" for u in idle_workers))
    if state.get("advisor"):
        lines.append("ADVISOR: " + " | ".join(state["advisor"]))
    if state.get("events"):
        lines.append("events: " + " | ".join(str(e) for e in state["events"][:5]))
    if notifs:
        lines.append("NOTIFICATIONS: " + " | ".join(
            n.get("data", {}).get("label", n.get("type", str(n))) if isinstance(n, dict) else str(n)
            for n in notifs))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Shared UI state                                                              #
# --------------------------------------------------------------------------- #

class UI:
    def __init__(self):
        self.matches: list[dict] = []
        self.online: list[dict] = []
        self.state: dict = {}
        self.log: deque[str] = deque(maxlen=200)
        self.selected = 0
        self.prompt: str | None = None       # active input label, or None
        self.prompt_buf = ""
        self.prompt_result: asyncio.Future | None = None
        self.status = "no seat"
        self.running = True

    def add_log(self, line: str):
        self.log.append(line)


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
    tool_map = {
        "patch_directive": lambda a: client.patch_directive(a.get("patches", {})),
        "send_command": lambda a: client.send_command(a.get("command_type", ""), a.get("data", {})),
        "get_intel_map": lambda a: client.get_intel_map(),
        "send_chat": lambda a: client.send_chat(a.get("message", "")),
    }
    while ui.running and client.colony_id is not None:
        try:
            state = await client.state()
            notifs = await client.notifications()
        except httpx.HTTPError as e:
            ui.add_log(f"[err] state fetch: {e}")
            await asyncio.sleep(1.0)
            continue
        ui.state = state
        tick = state.get("tick", -1)
        if "error" in state or tick == last_tick:
            await asyncio.sleep(0.2)
            continue
        last_tick = tick

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

        for call in (msg.tool_calls or []):
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = tool_map.get(call.function.name)
            result = await fn(args) if fn else {"error": "unknown tool"}
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
    while ui.running:
        try:
            all_matches = await client.list_matches()
            ui.matches = [m for m in all_matches
                          if m.get("phase") == "lobby" and m.get("winner") is None]
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

    if ui.prompt is not None:
        footer = Text(f"{ui.prompt}: {ui.prompt_buf}_", style="bold yellow")
    else:
        footer = Text("[j] join   [n] new vs bot   [s] start   [w] watch   [↑/↓] select   [q] quit   "
                      f"— {ui.status}", style="dim")
    layout["footer"].update(footer)
    return layout


def render_matches(ui: UI, client: GameClient) -> Panel:
    t = Table.grid(padding=(0, 1))
    for i, m in enumerate(ui.matches):
        phase = m.get("phase", "?")
        if m.get("winner"):
            dot, style = "○", "dim"
        elif phase == "running":
            dot, style = "●", "green"
        else:
            dot, style = "◌", "yellow"
        seats = m.get("seats", {})
        red = seats.get("0", {}).get("agent") or "open"
        blue = seats.get("1", {}).get("agent") or "open"
        mid = m["match_id"][:8]
        marker = "▶ " if i == ui.selected else "  "
        line = Text(marker, style="bold cyan" if i == ui.selected else "")
        line.append(f"{dot} ", style=style)
        line.append(f"{mid}  ", style="bold" if i == ui.selected else "")
        line.append(f"t{m.get('tick',0)} R:{red} B:{blue}", style="dim")
        t.add_row(line)
    if not ui.matches:
        t.add_row(Text("(no open lobbies — press 'n' to create one)", style="dim"))

    online = Table.grid(padding=(0, 1))
    online.add_row(Text(f"Online agents ({len(ui.online)}):", style="bold"))
    for a in ui.online[:8]:
        online.add_row(Text(f" {a['agent']:<12} {a['colony']:<5} {a['seconds_ago']}s ago", style="dim"))

    return Panel(Group(t, Text(""), online), title="Open Lobbies", border_style="grey50")


def render_colony(ui: UI, client: GameClient) -> Panel:
    s = ui.state
    if not s or "error" in s:
        body = Text(s.get("error", "not in a seat — press 'j' to join") if s else
                    "not in a seat — press 'j' to join", style="dim")
        return Panel(body, title="Colony State", border_style="grey50")
    colony = "RED" if client.colony_id == 0 else "BLUE"
    cstyle = "red" if client.colony_id == 0 else "blue"
    c = s["counts"]
    cb = s.get("combat", {})
    head = Text()
    head.append(f"Tick {s['tick']}  ", style="bold")
    head.append(f"{colony}  ", style=f"bold {cstyle}")
    head.append(f"food:{s['food']} dirt:{s['dirt']} income:{s['income_per_s']}/s\n")
    head.append(f"Workers:{c['workers']} Soldiers:{c['soldiers']} Scouts:{c['scouts']} "
                f"QueenHP:{s.get('queen_hp')}\n")
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

async def ask(ui: UI, label: str, default: str = "") -> str | None:
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    ui.prompt = label
    ui.prompt_buf = default
    ui.prompt_result = fut
    result = await fut
    ui.prompt = None
    ui.prompt_buf = ""
    ui.prompt_result = None
    return result


async def run_tui(cfg: dict):
    console = Console(force_terminal=True, highlight=False)
    client = GameClient(cfg["game_url"], cfg["api_key"])
    llm = OpenAI(base_url=cfg["llm"]["base_url"],
                 api_key=cfg["llm"].get("api_key") or "no-llm-key-set")
    model = cfg["llm"]["model"]
    ui = UI()
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

    def submit_prompt(value: str | None):
        if ui.prompt_result and not ui.prompt_result.done():
            ui.prompt_result.set_result(value)

    def on_key(ch: str):
        if ui.prompt is not None:
            if ch == "\r":
                submit_prompt(ui.prompt_buf)
            elif ch == "ESC":
                submit_prompt(None)
            elif ch == "\x7f":
                ui.prompt_buf = ui.prompt_buf[:-1]
            elif ch.isprintable() and len(ch) == 1:
                ui.prompt_buf += ch
            return
        if ch in ("q", "\x03"):
            ui.running = False
        elif ch == "UP":
            ui.selected = max(0, ui.selected - 1)
        elif ch == "DOWN":
            ui.selected = min(len(ui.matches) - 1, ui.selected + 1)
        elif ch == "j":
            asyncio.create_task(do_join())
        elif ch == "n":
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

    agent_name = cfg.get("name", "agent")

    async def do_join():
        if client.colony_id is not None:
            await client.release()
        default_mid = ui.matches[ui.selected]["match_id"] if ui.matches else ""
        mid = await ask(ui, "Match id (Enter=highlighted)", default_mid)
        if mid is None:
            return
        col = await ask(ui, "Colony (0=RED  1=BLUE)", "0")
        if col is None or col not in ("0", "1"):
            return
        try:
            await client.join(mid.strip(), int(col), agent_name)
            ui.status = f"seated {'RED' if int(col) == 0 else 'BLUE'} @ {client.match_id[:8]}"
            ui.add_log(f"joined {client.match_id[:8]} as colony {col}")
            await start_agent()
        except httpx.HTTPError as e:
            ui.add_log(f"[err] join failed: {e}")

    async def do_new():
        try:
            mid = await client.new_match(brains={"0": "mcp", "1": "bot"})
            await client.join(mid, 0, agent_name)
            ui.status = f"seated RED @ {mid[:8]} (new)"
            ui.add_log(f"created + joined {mid[:8]} as RED")
            r = await client.start_game()
            if "error" in r:
                ui.add_log(f"[warn] start: {r['error']}")
            else:
                ui.add_log("game starting — placement phase in progress")
            await start_agent()
        except httpx.HTTPError as e:
            ui.add_log(f"[err] new match: {e}")

    async def do_start():
        if client.match_id is None:
            ui.add_log("[warn] not in a match — press 'j' to join one first")
            return
        r = await client.start_game()
        if "error" in r:
            ui.add_log(f"[warn] start: {r['error']}")
        else:
            ui.add_log("game starting")

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

    try:
        while ui.running:
            while not key_queue.empty():
                on_key(key_queue.get_nowait())
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
        await client.release()
        await client.close()


# --------------------------------------------------------------------------- #
# Headless entry                                                               #
# --------------------------------------------------------------------------- #

async def run_headless(cfg: dict, spec: str):
    mid_str, _, col_str = spec.partition(":")
    if not col_str:
        sys.exit("--headless expects MATCH_ID:COLONY (e.g. a1b2c3d4:0)")
    client = GameClient(cfg["game_url"], cfg["api_key"])
    llm = OpenAI(base_url=cfg["llm"]["base_url"],
                 api_key=cfg["llm"].get("api_key") or "no-llm-key-set")
    ui = UI()
    loop = asyncio.get_event_loop()

    class StdoutLog:
        def append(self, line):
            print(line, flush=True)
    ui.log = StdoutLog()  # type: ignore

    await client.join(mid_str, int(col_str), cfg.get("name", "agent"))
    print(f"joined {client.match_id} as colony {col_str}", flush=True)
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
        print("Note: no Agants API key in config — you can browse matches but joining requires one.")
        print("Register at https://agants.datthemaster.com/register.html then run --setup.\n")

    if args.headless:
        asyncio.run(run_headless(cfg, args.headless))
    else:
        if not sys.stdin.isatty():
            sys.exit("No TTY. Use --headless MATCH_ID:COLONY for non-interactive runs.")
        asyncio.run(run_tui(cfg))


if __name__ == "__main__":
    main()
