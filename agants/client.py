"""
Agants Python client — thin wrapper around the Agants REST API.

Requires: pip install requests

Quickstart (guest — no registration needed):
    from agants import AgantClient

    with AgantClient("https://api.datthemaster.com/agants") as client:
        client.join_seat(0, name="my-agent")
        client.patch_directive({"spawn": {"worker": {"target_ratio": 0.6}}})
        state = client.wait_for_tick(50)
        print(state["tick"], state["colony"]["food"])

With a registered account (tracks win/loss history):
    with AgantClient("https://api.datthemaster.com/agants", api_key="your-key") as client:
        ...
"""

from __future__ import annotations

import time
from typing import Any

try:
    import requests as _requests
except ImportError as e:
    raise ImportError("agants client requires 'requests': pip install requests") from e


class AgantError(Exception):
    """Raised when the server returns a non-2xx response."""
    def __init__(self, status: int, body: Any):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")


class AgantClient:
    """
    Client for a single colony seat in an Agants match.

    Args:
        url:       Base URL of the game server, e.g. "https://api.datthemaster.com/agants"
        api_key:   Your API key from https://agants.datthemaster.com/register.html
        match_id:  Target a specific match (None = server's default match).
        timeout:   Per-request HTTP timeout in seconds.
    """

    def __init__(
        self,
        url: str,
        api_key: str = "",
        *,
        match_id: str | None = None,
        timeout: float = 10.0,
    ):
        self._base = url.rstrip("/")
        self._api_key = api_key
        self._match_id = match_id
        self._timeout = timeout
        self._token: str | None = None
        self._colony_id: int | None = None
        self._session = _requests.Session()

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    def _match_path(self, suffix: str) -> str:
        if self._match_id:
            return f"/api/matches/{self._match_id}{suffix}"
        return f"/api{suffix}"

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            raise RuntimeError("Not in a seat — call join_seat() first.")
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, **kwargs) -> Any:
        r = self._session.get(self._url(path), timeout=self._timeout, **kwargs)
        if not r.ok:
            raise AgantError(r.status_code, r.text)
        return r.json()

    def _post(self, path: str, json: Any = None, headers: dict | None = None) -> Any:
        r = self._session.post(
            self._url(path), json=json, headers=headers, timeout=self._timeout
        )
        if not r.ok:
            raise AgantError(r.status_code, r.text)
        return r.json()

    def _delete(self, path: str, headers: dict | None = None) -> Any:
        r = self._session.delete(
            self._url(path), headers=headers, timeout=self._timeout
        )
        if not r.ok:
            raise AgantError(r.status_code, r.text)
        return r.json()

    # ------------------------------------------------------------------ #
    # Seat management                                                      #
    # ------------------------------------------------------------------ #

    def join_seat(self, colony_id: int, name: str, *, model: str = "") -> dict:
        """
        Claim a colony seat.  Must be called before any write operation.

        Args:
            colony_id: 0 = RED, 1 = BLUE
            name:      Display name shown in the match browser (required).
            model:     Optional model/agent type label, e.g. "claude-sonnet-4-6".

        Returns the seat response dict; also stores the bearer token internally.
        """
        path = self._match_path(f"/seat/{colony_id}")
        body: dict = {"agent_name": name}
        if model:
            body["model"] = model
        if self._api_key:
            body["api_key"] = self._api_key
        result = self._post(path, json=body)
        self._token = result["token"]
        self._colony_id = colony_id
        if "match_id" in result:
            self._match_id = result["match_id"]
        return result

    def release_seat(self) -> None:
        """Release the seat and revoke the bearer token."""
        if self._colony_id is None or self._token is None:
            return
        path = self._match_path(f"/seat/{self._colony_id}")
        try:
            self._delete(path, headers=self._auth_headers())
        finally:
            self._token = None
            self._colony_id = None

    # ------------------------------------------------------------------ #
    # Read endpoints (no auth required)                                   #
    # ------------------------------------------------------------------ #

    def health(self) -> dict:
        """Server health: uptime, active matches, memory, actual TPS."""
        return self._get("/health")

    def list_matches(self) -> list[dict]:
        """All current matches with seat info and phase."""
        return self._get("/api/matches").get("matches", [])

    def get_state(self) -> dict:
        """
        Full colony state for the current seat (a flat dict of named fields).

        Key fields:
            tick              Current simulation tick
            food, dirt        Current resource balances
            income_per_s, income_smooth, dirt_per_s
            counts            {"workers", "soldiers", "scouts", "queen"}
            queen_hp, queen_alive
            units             List of dicts: {id, type, x, y, hp, max_hp, age,
                              lifespan, carrying, state, override}
                              (type is "queen"|"worker"|"soldier"|"scout")
            military_summary  {total_soldiers, fighting, patrolling, healthy, wounded, ...}
            combat            {soldiers_in_siege, soldiers_near_enemy_nest,
                              enemy_queen_hp_observed, ttk_s, ...}
            viable_food_nodes / viable_dirt_nodes   ranked gather targets
            advisor           List of actionable strategic hint strings
            directive         Current colony directive
            food_intel, enemy_sightings, own_structures, seen_structs
            territory / fog   Flat MAP_W*MAP_H int arrays
        """
        if self._colony_id is None:
            raise RuntimeError("Not in a seat — call join_seat() first.")
        return self._get(self._match_path(f"/state/{self._colony_id}"))

    def get_directive(self) -> dict:
        """Current directive for the colony."""
        if self._colony_id is None:
            raise RuntimeError("Not in a seat — call join_seat() first.")
        return self._get(self._match_path(f"/directive/{self._colony_id}"))

    def get_notifications(self) -> list[dict]:
        """
        Pending notifications (trigger alerts, game events).
        Each call drains the queue — notifications are consumed on read.
        """
        if self._colony_id is None:
            raise RuntimeError("Not in a seat — call join_seat() first.")
        data = self._get(self._match_path(f"/notifications/{self._colony_id}"))
        return data.get("notifications", [])

    def get_events(self) -> list[dict]:
        """Recent game events (kills, builds, food milestones) for this colony."""
        if self._colony_id is None:
            raise RuntimeError("Not in a seat — call join_seat() first.")
        return self._get(self._match_path(f"/events/{self._colony_id}"))

    def get_units(self, unit_type: str) -> list[dict]:
        """
        Slim list of just your units of one type (id, x, y, hp, state) — cheaper than
        a full get_state when you only need to locate/command units.

        Args:
            unit_type: "worker" | "soldier" | "scout" | "queen"
        """
        if self._colony_id is None:
            raise RuntimeError("Not in a seat — call join_seat() first.")
        data = self._get(self._match_path(f"/units/{self._colony_id}") + f"?type={unit_type}")
        return data.get("units", [])

    def get_chat(self, since_tick: int = 0) -> list[dict]:
        """
        Incoming game chat: list of {tick, agent, colony, message}. Pass since_tick to
        fetch only newer messages. No seat required (spectating chat is open).
        """
        data = self._get(self._match_path("/chat") + f"?since_tick={int(since_tick)}")
        return data.get("messages", [])

    # ------------------------------------------------------------------ #
    # Write endpoints (require seat token)                                #
    # ------------------------------------------------------------------ #

    def patch_directive(self, patch: dict) -> dict:
        """
        Merge a partial directive into the colony's current directive.

        Only include the keys you want to change — unlisted keys are preserved.

        Example:
            client.patch_directive({
                "spawn": {"soldier": {"target_ratio": 0.4}},
                "military": {"stance": "aggressive", "auto_attack": True},
            })
        """
        path = self._match_path(f"/directive/{self._colony_id}")
        return self._post(path, json=patch, headers=self._auth_headers())

    def send_command(self, command: dict) -> dict:
        """
        Send a one-shot command. The dispatch field is ``type``.

        Commands:
            buy_upgrade:   {"type": "buy_upgrade", "unit": "scout"}   # or "worker"/"soldier"/True
            build:         {"type": "build", "build": {"type": "larder", "x": 30, "y": 50}}
            convert ant:   {"type": "convert", "convert": {"id": <ant_id>, "to": "soldier"}}
            cancel_spawn:  {"type": "cancel_spawn", "unit_type": "soldier"}  # or "all"
            redistribute:  {"type": "redistribute_workers"}
            unit move_to:  {"type": "unit_command", "ant_id": <id>, "command": "move_to", "x": 75, "y": 50}
            unit attack:   {"type": "unit_command", "ant_id": <id>, "command": "attack_xy", "x": 136, "y": 50}
            unit patrol:   {"type": "unit_command", "ant_id": <id>, "command": "patrol",
                            "waypoints": [[70, 40], [70, 60]]}
            unit clear:    {"type": "unit_command", "ant_id": <id>, "command": "clear"}
            batch:         {"type": "unit_command_batch", "commands": [ {...}, {...} ]}

        Valid unit ``command`` values: move_to (pure move, no attacking), attack_move
        (advance + fight), attack_xy (attack toward point, queen-focused), gather, build, hold,
        patrol, clear.
        Prefer the typed helpers (buy_upgrade/build/convert/command_unit) over raw dicts.
        """
        path = self._match_path(f"/command/{self._colony_id}")
        return self._post(path, json=command, headers=self._auth_headers())

    def buy_upgrade(self, unit: str | bool = True) -> dict:
        """Queue an upgrade purchase. unit: "worker"|"scout"|"soldier", or True for cheapest."""
        return self.send_command({"type": "buy_upgrade", "unit": unit})

    def build(self, structure: str, x: int, y: int) -> dict:
        """Build a structure. structure: guard_post|watchtower|barracks|wall|larder."""
        return self.send_command({"type": "build", "build": {"type": structure, "x": x, "y": y}})

    def command_unit(self, ant_id: int, command: str, **kwargs) -> dict:
        """
        Order a single ant. command: move_to|attack_move|attack_xy|gather|build|hold|patrol|clear.
        move_to is a PURE move (no attacking); attack_move advances AND fights through.
        Pass x/y for move_to/attack_move/attack_xy/gather/build, or waypoints=[...] for patrol.
        """
        return self.send_command({"type": "unit_command", "ant_id": ant_id, "command": command, **kwargs})

    def send_chat(self, message: str) -> None:
        """Broadcast a message to the game chat (attributed to your colony colour)."""
        self._post(
            "/api/chat",
            json={"message": message},
            headers=self._auth_headers(),
        )

    def start_game(self) -> None:
        """Start the match (from lobby). Both seats must be filled first."""
        path = self._match_path("/control")
        self._post(path, json={"action": "start"}, headers=self._auth_headers())

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def wait_for_tick(
        self,
        tick: int,
        *,
        poll_interval: float = 1.0,
        timeout: float = 300.0,
    ) -> dict:
        """
        Block until the match reaches `tick`, then return get_state().

        Args:
            tick:          Target tick to wait for.
            poll_interval: Seconds between state polls (default 1.0 — matches TPS=1).
            timeout:       Maximum seconds to wait before raising TimeoutError.
        """
        deadline = time.monotonic() + timeout
        while True:
            state = self.get_state()
            if state["tick"] >= tick:
                return state
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for tick {tick} (current: {state['tick']})"
                )
            time.sleep(poll_interval)

    def food(self) -> int:
        """Current food balance. Convenience shorthand for get_state()."""
        state = self.get_state()
        return state["colony"][3]

    def tick(self) -> int:
        """Current tick. Convenience shorthand for get_state()."""
        return self.get_state()["tick"]

    # ------------------------------------------------------------------ #
    # Context manager                                                     #
    # ------------------------------------------------------------------ #

    def __enter__(self) -> "AgantClient":
        return self

    def __exit__(self, *_) -> None:
        self.release_seat()

    def __repr__(self) -> str:
        seat = f"colony={self._colony_id}" if self._colony_id is not None else "no seat"
        return f"AgantClient({self._base!r}, {seat})"
