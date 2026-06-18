#!/usr/bin/env python3
"""Replay tools/replay/match.jsonl over ws://localhost:8765 at the recorded cadence.

  python3 tools/replay/server.py

Then load the client with ?replay=true (Connection points at ws://localhost:8765).
Add ?delay=<ms> to the WS URL to inject one extra sleep before a mid-stream tick
(tests late-tick handling). Loops the recording at EOF.
"""
import asyncio, json
import websockets  # pip install websockets


def load():
    rows = []
    with open("tools/replay/match.jsonl") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _delay_ms(ws):
    try:
        path = ws.request.path if getattr(ws, "request", None) else ""
        if "delay=" in path:
            return int(path.split("delay=")[1].split("&")[0])
    except Exception:
        pass
    return 0


async def handler(ws):
    rows = load()
    if not rows:
        await ws.close()
        return
    delay_ms = _delay_ms(ws)
    while True:  # loop forever
        prev_t = rows[0]["t"]
        for i, row in enumerate(rows):
            gap = max(0.0, row["t"] - prev_t)
            prev_t = row["t"]
            await asyncio.sleep(gap if gap < 5 else 1.0)  # cap pathological gaps
            if delay_ms and i == len(rows) // 2:
                await asyncio.sleep(delay_ms / 1000.0)     # inject one late tick
            try:
                await ws.send(row["msg"])
            except websockets.ConnectionClosed:
                return


async def main():
    print("replay server on ws://localhost:8765 (loops match.jsonl)")
    async with websockets.serve(handler, "localhost", 8765):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
