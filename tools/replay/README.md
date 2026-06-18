# Tick-replay harness (LOCAL ONLY)

Capture a local dev match's WebSocket stream to JSONL, then re-emit it at the recorded
cadence so the graphics client can be built and verified offline — with **zero production
contact**.

> **Never point `capture.py` at production.** It refuses `datthemaster.com` / the LAN IP.
> Capture only from a local `python3 server.py` (`:8083`).

## Capture (produces `tools/replay/match.jsonl`)

```bash
# shell A — local dev game server
python3 server.py

# shell B — record ~60s of a bot-vs-bot match
pip install websockets
python3 tools/replay/capture.py --base http://localhost:8083 --seconds 60
```

Output: one JSON object per line — `{"t": <recv_epoch_float>, "msg": <raw message string>}`
in arrival order (the initial `init`/`map` message, the `game_start`, then every tick).

`match.jsonl` is a local artifact and is **gitignored** (not source).

## Replay (serves `ws://localhost:8765`)

```bash
python3 tools/replay/server.py
```

Per client connection it re-emits each message at the recorded inter-arrival cadence and
loops at EOF. Add `?delay=<ms>` to the WS URL to inject one late mid-stream tick (tests
the client's late-tick handling).

Point the Pixi client at it with `?replay=true`:

```bash
cd frontend && python3 -m http.server 8090
# then open http://localhost:8090/game/?pixi=1&replay=true
```
