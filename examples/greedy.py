"""
greedy.py — Economy-first agent for Agants.

Strategy:
  - Flood workers early to maximise food income from home + approach nodes
  - Build a larder at tick ~120 for passive +6/t income before approach nodes deplete
  - Slowly grow a military reserve (soldiers gather but don't push)
  - At tick 400, switch to aggressive push once the economy is secured

Usage:
    python examples/greedy.py --colony 0 --name "Greedy RED"
    python examples/greedy.py --colony 1 --name "Greedy BLUE" --key YOUR_KEY
"""

import argparse
import sys
import time
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agants import AgantClient

SERVER  = "https://api.datthemaster.com/agants"
API_KEY = os.environ.get("AGANTS_API_KEY", "")

# ── Directive phases ─────────────────────────────────────────────────────────

PHASE_ECO = {
    "spawn": {
        "worker":   {"target_ratio": 0.65, "min": 6, "max": 40},
        "soldier":  {"target_ratio": 0.20, "min": 2, "max": 16},
        "scout":    {"target_ratio": 0.15, "min": 2, "max": 8},
        "reserve_food": 40,
    },
    "economy": {
        "auto_upgrade": True,
        "upgrade_priority": ["worker", "scout", "soldier"],
    },
    "military": {
        "stance": "defensive",
        "auto_attack": False,
    },
}

PHASE_PUSH = {
    "spawn": {
        "worker":  {"target_ratio": 0.40, "min": 6, "max": 30},
        "soldier": {"target_ratio": 0.45, "min": 8, "max": 40},
        "scout":   {"target_ratio": 0.15, "min": 2, "max": 8},
        "reserve_food": 80,
    },
    "economy": {
        "auto_upgrade": True,
        "upgrade_priority": ["soldier", "worker", "scout"],
    },
    "military": {
        "stance": "aggressive",
        "auto_attack": True,
        "rally_release_at": 12,
    },
}

# ── Triggers applied once at game start ─────────────────────────────────────

TRIGGERS = [
    {
        "label": "eco_emergency_retreat",
        "if": "food < 60 AND income_per_s < 2 AND elapsed_ticks > 120 AND soldiers_in_siege == 0",
        "then": {"military.retreat": True, "military.auto_attack": False},
        "else": {"military.retreat": False},
        "cooldown": 60,
    },
    {
        "label": "larder_built_notify",
        "if": "elapsed_ticks > 130",
        "then": {},
        "priority": 0,
        "duration": 1,
    },
]


def run(colony_id: int, name: str, api_key: str = ""):
    print(f"[greedy] connecting to {SERVER} as {name!r} (colony {colony_id})")

    with AgantClient(SERVER, api_key) as client:
        seat = client.join_seat(colony_id, name)
        print(f"[greedy] seat joined — match {seat.get('match_id', 'default')}")

        # Apply eco phase + triggers
        client.patch_directive({**PHASE_ECO, "triggers": TRIGGERS})
        print("[greedy] phase=ECO directive applied")

        # Wait for game to start (lobby → running)
        print("[greedy] waiting for game start …")
        while True:
            state = client.get_state()
            if state.get("phase") == "running":
                break
            time.sleep(2)

        larder_built = False
        push_started = False

        while True:
            state = client.get_state()
            tick   = state["tick"]
            colony = state["colony"]
            food   = colony[3]
            phase  = state.get("phase", "running")

            if phase != "running":
                print(f"[greedy] game ended (phase={phase})")
                break

            # Build a larder around tick 120 if we have the dirt
            if not larder_built and tick >= 120:
                nx, ny = colony[1], colony[2]
                # Place larder ~12 tiles in front of nest toward the centre
                lx = nx + (12 if colony_id == 0 else -12)
                try:
                    client.send_command({
                        "command_type": "build",
                        "type": "larder",
                        "x": lx,
                        "y": ny,
                    })
                    print(f"[greedy] tick={tick} — larder build ordered at ({lx},{ny})")
                    larder_built = True
                except Exception as e:
                    print(f"[greedy] tick={tick} — larder build failed: {e}")
                    larder_built = True  # don't retry every tick

            # Switch to aggressive push at tick 400
            if not push_started and tick >= 400:
                client.patch_directive(PHASE_PUSH)
                print(f"[greedy] tick={tick} food={food} — switching to PUSH phase")
                push_started = True

            # Drain notifications so they don't pile up
            notifications = client.get_notifications()
            for n in notifications:
                if n.get("type") == "alert":
                    print(f"[greedy] tick={tick} ALERT: {n['data'].get('label')}")

            time.sleep(1.2)


def main():
    global SERVER
    parser = argparse.ArgumentParser(description="Greedy economy-first Agants agent")
    parser.add_argument("--colony", type=int, default=0, choices=[0, 1],
                        help="0=RED 1=BLUE (default: 0)")
    parser.add_argument("--name", default="Greedy", help="Agent display name")
    parser.add_argument("--key", default=API_KEY,
                        help="API key (or set AGANTS_API_KEY env var)")
    parser.add_argument("--server", default=SERVER, help="Server base URL")
    args = parser.parse_args()

    SERVER = args.server
    run(args.colony, args.name, args.key)


if __name__ == "__main__":
    main()
