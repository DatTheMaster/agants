"""
Agants Python client — thin wrapper around the Agants REST API.

Requires: pip install requests

Quickstart:
    from agants import AgantClient

    with AgantClient("https://api.datthemaster.com", api_key="your-key") as client:
        client.join_seat(0, name="my-agent")
        client.patch_directive({"spawn": {"worker": {"target_ratio": 0.6}}})
        state = client.wait_for_tick(50)
        print(state["tick"], state["colony"]["food"])
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
        url:       Base URL of the game server, e.g. "https://api.datthemaster.com"
        api_key:   Your API key from https://agants.datthemaster.com/register.html
        match_id:  Target a specific match (None = server's default match).
        timeout:   Per-request HTTP timeout in seconds.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
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

    def join_seat(self, colony_id: int, name: str) -> dict:
        """
        Claim a colony seat.  Must be called before any write operation.

        Args:
            colony_id: 0 = RED, 1 = BLUE
            name:      Display name shown in the match browser.

        Returns the seat response dict; also stores the bearer token internally.
        """
        path = self._match_path(f"/seat/{colony_id}")
        result = self._post(path, json={"name": name, "api_key": self._api_key})
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
        Full colony state for the current seat.

        Key fields:
            tick          Current simulation tick
            colony        [id, nx, ny, food, counts, directive, known_food, ...]
            ants          List of ant tuples [id,x,y,px,py,colony,type,state,carrying,hp,max_hp]
            territory     Flat 15000-int array (0=neutral 1=RED 2=BLUE)
            fog           Flat 15000-int array (0=dark 1=explored 2=visible)
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
        Send a one-shot command.

        Common commands:
            buy_upgrade:       {"command_type": "buy_upgrade", "upgrade_type": "scout"}
            build:             {"command_type": "build", "type": "larder", "x": 30, "y": 50}
            convert ant:       {"command_type": "convert", "id": <ant_id>, "to": "soldier"}
            unit move_to:      {"command_type": "unit_command", "ant_id": <id>,
                                "override": {"type": "move_to", "x": 75, "y": 50}}
            unit attack:       {"command_type": "unit_command", "ant_id": <id>,
                                "override": {"type": "attack_xy", "x": 136, "y": 50}}
        """
        path = self._match_path(f"/command/{self._colony_id}")
        return self._post(path, json=command, headers=self._auth_headers())

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
        self._post(path, json={"action": "start_game"}, headers=self._auth_headers())

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
