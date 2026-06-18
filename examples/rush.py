"""
rush.py — Early aggression agent for Agants.

Strategy:
  - Prioritise soldiers from tick 0; minimal workers (just enough income to sustain)
  - Rally at the midfield ridge (x=73 for RED, x=77 for BLUE)
  - Release the wave when 10+ soldiers are gathered
  - Siege with queen priority to end the game fast
  - Fall back and rebuild if the wave is wiped out

Usage:
    python examples/rush.py --colony 0 --name "Rush RED"
    python examples/rush.py --colony 1 --name "Rush BLUE" --key YOUR_KEY
"""

import argparse
import sys
import time
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agants import AgantClient

SERVER  = "https://api.datthemaster.com/agants"
API_KEY = os.environ.get("AGANTS_API_KEY", "")

# Rally points: RED pushes right toward centre; BLUE pushes left
RALLY = {0: [73, 50], 1: [77, 50]}
ENEMY_NEST = {0: [136, 50], 1: [14, 50]}
WAVE_SIZE = 10   # soldiers to accumulate before releasing
REBUILD   = 6    # fall back and rally again if soldiers drop below this

PHASE_RUSH = {
    "spawn": {
        "worker":  {"target_ratio": 0.30, "min": 4, "max": 20},
        "soldier": {"target_ratio": 0.55, "min_ratio": 0.30, "min": 3, "max": 40},
        "scout":   {"target_ratio": 0.15, "min": 2, "max": 6},
        "reserve_food": 30,
    },
    "economy": {
        "auto_upgrade": True,
        "upgrade_priority": ["soldier", "scout", "worker"],
    },
    "military": {
        "stance": "aggressive",
        "auto_attack": False,  # we control the push manually
    },
    "triggers": [
        {
            "label": "wave_release",
            "if": f"soldier_count >= {WAVE_SIZE} AND soldiers_in_siege == 0",
            "then": {
                "military.auto_attack": True,
                "military.siege_priority": "queen",
                "military.rally_point": None,
            },
        },
        {
            "label": "wave_wiped",
            "if": f"soldier_count < {REBUILD} AND elapsed_ticks > 60",
            "then": {
                "military.auto_attack": False,
                "military.siege_priority": None,
                # rally_point patched per-colony in code below
            },
        },
        {
            "label": "eco_emergency",
            "if": "food < 40 AND income_per_s < 1 AND elapsed_ticks > 90",
            "then": {"military.retreat": True},
            "else": {"military.retreat": False},
            "cooldown": 90,
        },
    ],
}


def run(colony_id: int, name: str, api_key: str = ""):
    rally = RALLY[colony_id]
    enemy = ENEMY_NEST[colony_id]

    print(f"[rush] connecting to {SERVER} as {name!r} (colony {colony_id})")

    with AgantClient(SERVER, api_key) as client:
        seat = client.join_seat(colony_id, name)
        print(f"[rush] seat joined — match {seat.get('match_id', 'default')}")

        # Set rally point in directive then apply
        directive = dict(PHASE_RUSH)
        directive["military"] = {**PHASE_RUSH["military"], "rally_point": rally}
        client.patch_directive(directive)
        print(f"[rush] directive applied — rallying at {rally}")

        # Wait for game to start
        print("[rush] waiting for game start …")
        while True:
            state = client.get_state()
            if state.get("phase") == "running":
                break
            time.sleep(2)

        prev_wave_wiped = False

        while True:
            state  = client.get_state()
            tick   = state["tick"]
            phase  = state.get("phase", "running")

            if phase != "running":
                print(f"[rush] game ended (phase={phase})")
                break

            soldier_count = state["counts"]["soldiers"]

            # If the wave was wiped, re-establish the rally point
            if soldier_count < REBUILD and tick > 60:
                if not prev_wave_wiped:
                    client.patch_directive({"military": {
                        "auto_attack": False,
                        "rally_point": rally,
                        "siege_priority": None,
                    }})
                    print(f"[rush] tick={tick} — wave wiped, rebuilding at rally {rally}")
                    prev_wave_wiped = True
            else:
                prev_wave_wiped = False

            # Drain notifications
            for n in client.get_notifications():
                if n.get("type") == "alert":
                    label = n["data"].get("label", "")
                    if label == "wave_release":
                        print(f"[rush] tick={tick} soldiers={soldier_count} — WAVE RELEASED → {enemy}")
                    elif label == "wave_wiped":
                        pass  # handled above
                    else:
                        print(f"[rush] tick={tick} ALERT: {label}")

            time.sleep(1.2)


def main():
    global SERVER
    parser = argparse.ArgumentParser(description="Rush early-aggression Agants agent")
    parser.add_argument("--colony", type=int, default=0, choices=[0, 1],
                        help="0=RED 1=BLUE (default: 0)")
    parser.add_argument("--name", default="Rush", help="Agent display name")
    parser.add_argument("--key", default=API_KEY,
                        help="API key (or set AGANTS_API_KEY env var)")
    parser.add_argument("--server", default=SERVER, help="Server base URL")
    args = parser.parse_args()

    SERVER = args.server
    run(args.colony, args.name, args.key)


if __name__ == "__main__":
    main()
