#!/usr/bin/env python3
"""Record a LOCAL dev match's WebSocket stream to match.jsonl.

NEVER point this at production. Usage:
  python3 server.py                      # in another shell (local dev game server, :8083)
  python3 tools/replay/capture.py --base http://localhost:8083 --seconds 90

Creates a bot-vs-bot match (both colonies heuristic bots so the stream is lively),
starts it, then records every WS message + recv-timestamp to tools/replay/match.jsonl.
"""
import argparse, asyncio, json, time, urllib.request
import websockets  # pip install websockets


def _post(url, body):
    r = urllib.request.Request(url, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(r, timeout=15).read())


async def main(base, seconds):
    assert "datthemaster.com" not in base, "refusing to capture from production"
    assert "192.168.1.100" not in base, "refusing to capture from production"
    # Bot-vs-bot: both colonies are heuristic bots → lively ant movement, no seat needed.
    m = _post(f"{base}/api/matches", {"config": {"brains": {"0": "bot", "1": "bot"}}})
    mid = m["match_id"]
    ws_url = base.replace("http", "ws") + f"/ws/{mid}"
    _post(f"{base}/api/matches/{mid}/control", {"action": "start"})
    print(f"capturing {ws_url} for {seconds}s -> tools/replay/match.jsonl")
    out = open("tools/replay/match.jsonl", "w", buffering=1)
    deadline = time.time() + seconds
    n = 0
    async with websockets.connect(ws_url) as ws:
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            out.write(json.dumps({"t": time.time(), "msg": raw}) + "\n")
            n += 1
    out.close()
    print(f"done — wrote {n} messages")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8083")
    ap.add_argument("--seconds", type=int, default=90)
    a = ap.parse_args()
    asyncio.run(main(a.base, a.seconds))
