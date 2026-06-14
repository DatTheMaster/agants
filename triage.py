#!/usr/bin/env python3
"""
triage.py — Agants external outage monitor.

Run from your local machine (not the VPS) to watch sites as a real user would.
Automatically fires Cloudflare diagnostics when a site goes down.

Usage:
  python3 triage.py          # continuous monitor
  python3 triage.py --once   # single check and exit
"""

import os, sys, time, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

# ── Config ────────────────────────────────────────────────────────────────────

ZONE_ID = "9ba1059f87ceb5161c741362b7058559"

SITES = {
    "agants":    "https://agants.datthemaster.com/landing",
    "analytics": "https://analytics.datthemaster.com/api/heartbeat",
    "linkedai":  "https://linkedai.datthemaster.com/",
}
ORIGIN_HEALTH = "https://api.datthemaster.com/agants/health"

POLL_HEALTHY  = 20   # seconds between polls when all up
POLL_OUTAGE   = 10   # seconds between polls during outage
RETRIAGE_SECS = 120  # re-run CF diagnostics every N seconds during ongoing outage

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "sec-fetch-site": "none",
    "sec-fetch-mode": "navigate",
    "sec-fetch-dest": "document",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ── ANSI ──────────────────────────────────────────────────────────────────────

R  = "\033[91m"
G  = "\033[92m"
Y  = "\033[93m"
B  = "\033[94m"
DIM = "\033[2m"
BO  = "\033[1m"
X  = "\033[0m"

def ts():
    return datetime.now().strftime("%H:%M:%S")

# ── Env ───────────────────────────────────────────────────────────────────────

def load_cf_token():
    for env_file in [Path(__file__).parent / ".env", Path.home() / ".env"]:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("CLOUDFLARE_API_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("CLOUDFLARE_API_TOKEN", "")

# ── Health checks ─────────────────────────────────────────────────────────────

def check(url):
    """GET with browser headers + cache-busting query param. Returns (status_code, ms)."""
    sep = "&" if "?" in url else "?"
    busted = f"{url}{sep}_t={int(time.time())}"
    try:
        r = requests.get(busted, headers=BROWSER_HEADERS, timeout=10, allow_redirects=True)
        return r.status_code, r.elapsed.total_seconds() * 1000
    except requests.exceptions.ConnectionError:
        return 0, 0
    except requests.exceptions.Timeout:
        return -1, 10000
    except Exception:
        return -2, 0

def check_origin():
    try:
        r = requests.get(ORIGIN_HEALTH, timeout=10)
        return r.status_code, r.elapsed.total_seconds() * 1000
    except Exception:
        return 0, 0

# ── Cloudflare GraphQL ────────────────────────────────────────────────────────

CF_GQL = "https://api.cloudflare.com/client/v4/graphql"

def cf_post(token, query):
    if not token:
        return None
    try:
        r = requests.post(
            CF_GQL,
            json={"query": query},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15,
        )
        d = r.json()
        if d.get("errors"):
            return {"_errors": d["errors"]}
        return d
    except Exception as e:
        return {"_errors": [str(e)]}

def firewall_events(token, minutes=15):
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    q = f"""{{
      viewer {{
        zones(filter: {{zoneTag: "{ZONE_ID}"}}) {{
          firewallEventsAdaptive(
            filter: {{datetime_gt: "{since}"}}
            limit: 50 orderBy: [datetime_DESC]
          ) {{
            action clientRequestHTTPHost clientRequestPath
            clientIP clientCountryName source datetime rayName
          }}
        }}
      }}
    }}"""
    return cf_post(token, q)

def cf_403s(token):
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    q = f"""{{
      viewer {{
        zones(filter: {{zoneTag: "{ZONE_ID}"}}) {{
          httpRequestsAdaptiveGroups(
            limit: 30 orderBy: [count_DESC]
            filter: {{date_geq: "{date}", edgeResponseStatus: 403}}
          ) {{
            count
            dimensions {{clientRequestHTTPHost clientRequestPath datetimeHour}}
          }}
        }}
      }}
    }}"""
    return cf_post(token, q)

# ── Triage ────────────────────────────────────────────────────────────────────

def run_triage(down, token):
    print(f"\n{BO}{R}╔══ TRIAGE {ts()} ══════════════════════════════════{X}")

    # Origin check
    o_code, o_ms = check_origin()
    origin_ok = o_code == 200
    o_str = f"{G}UP {o_ms:.0f}ms{X}" if origin_ok else f"{R}DOWN ({o_code}){X}"
    print(f"{R}║{X}  Origin (game server):  {o_str}")

    # CF firewall events
    fw = firewall_events(token)
    events = []
    if fw and "_errors" not in fw:
        try:
            events = fw["result"]["viewer"]["zones"][0]["firewallEventsAdaptive"]
        except (KeyError, IndexError):
            pass

    # CF 403 breakdown
    bd = cf_403s(token)
    host_403s = {}
    if bd and "_errors" not in bd:
        try:
            for g in bd["result"]["viewer"]["zones"][0]["httpRequestsAdaptiveGroups"]:
                h = g["dimensions"]["clientRequestHTTPHost"]
                p = g["dimensions"]["clientRequestPath"]
                host_403s.setdefault(h, []).append((p, g["count"]))
        except (KeyError, IndexError):
            pass

    if not token:
        print(f"{R}║{X}  {DIM}CF diagnostics: no token — set CLOUDFLARE_API_TOKEN in .env{X}")
    elif fw and "_errors" in fw:
        print(f"{R}║{X}  {Y}CF query failed: {fw['_errors']}{X}")

    # Firewall events
    if events:
        print(f"{R}║{X}  {Y}Firewall events (last 15m): {len(events)}{X}")
        for e in events[:8]:
            t = e.get("datetime", "")[:19]
            print(f"{R}║{X}    {DIM}{t}{X}  {e['action']:8s}  {e['clientRequestHTTPHost']}{e['clientRequestPath']}  {e['clientIP']} ({e['source']})")
    else:
        print(f"{R}║{X}  {DIM}Firewall events (last 15m): none{X}")

    # 403 breakdown
    if host_403s:
        print(f"{R}║{X}  {Y}403s by host today:{X}")
        for host, paths in host_403s.items():
            total = sum(c for _, c in paths)
            note = f"{DIM}← WAF block (expected){X}" if "linkedai" in host else f"{R}← UNEXPECTED{X}"
            print(f"{R}║{X}    {host}: {total}  {note}")
            for path, count in sorted(paths, key=lambda x: -x[1])[:3]:
                print(f"{R}║{X}      {count}× {path}")
    elif token and "_errors" not in (bd or {}):
        print(f"{R}║{X}  {DIM}No 403s logged for today{X}")

    # Conclusion
    print(f"{R}║{X}")
    print(f"{R}║{X}  {BO}Conclusion:{X}")

    agants_down   = "agants"    in down
    analytics_down = "analytics" in down
    linkedai_down  = "linkedai"  in down

    if not origin_ok:
        print(f"{R}║  ● Game server / tunnel is DOWN.{X}")
        print(f"{R}║{X}    SSH to VPS → check process + tunnel. `bash deploy.sh` to restart.")
    elif linkedai_down:
        print(f"{R}║  ● All/multiple domains down including linkedai.{X}")
        print(f"{R}║{X}    Likely zone-wide CF event. Check CF dashboard.")
    elif agants_down and analytics_down and not linkedai_down:
        if events:
            print(f"{R}║{X}  {Y}● WAF/firewall events detected — see above.{X}")
            print(f"{R}║{X}    Scanner traffic on linkedai likely triggered zone-wide mitigation.")
        else:
            print(f"{R}║{X}  {Y}● agants + analytics down, linkedai up, no CF events logged.{X}")
            print(f"{R}║{X}    Likely CF adaptive DDoS or managed challenge (not visible on free plan).")
            print(f"{R}║{X}    → Try incognito: if it loads, clear cookies for datthemaster.com.")
            print(f"{R}║{X}    → If incognito also fails: grab Ray ID from CF error page (bottom of page)")
            print(f"{R}║{X}      or DevTools → Network → first request → cf-ray response header.")
    elif agants_down and not analytics_down:
        print(f"{R}║{X}  {Y}● Only agants down. Likely CF Worker or ASSETS issue.{X}")
        print(f"{R}║{X}    Check recent Worker deployment in CF dashboard.")
    elif analytics_down and not agants_down:
        print(f"{R}║{X}  {Y}● Only analytics down. Umami service or tunnel ingress issue.{X}")
        print(f"{R}║{X}    SSH to VPS → check Umami container.")
    else:
        print(f"{R}║{X}  {DIM}Pattern unclear. Collect Ray ID and monitor.{X}")

    print(f"{R}╚{'═' * 50}{X}\n")

# ── Display ───────────────────────────────────────────────────────────────────

def site_row(name, code, ms):
    if code == 200:
        dot = f"{G}●{X}"
        stat = f"{G}UP  {X}"
        lat  = f"{DIM}{ms:.0f}ms{X}"
    elif code == 0:
        dot = f"{R}●{X}"
        stat = f"{R}DOWN{X}"
        lat  = f"{R}connection refused{X}"
    elif code == -1:
        dot = f"{R}●{X}"
        stat = f"{R}DOWN{X}"
        lat  = f"{R}timeout{X}"
    else:
        dot = f"{R}●{X}"
        stat = f"{R}{code} {X}"
        lat  = f"{DIM}{ms:.0f}ms{X}"
    return f"  {dot} {stat}  {name:<12} {lat}"

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agants outage triage monitor")
    parser.add_argument("--once", action="store_true", help="Single check then exit")
    args = parser.parse_args()

    token = load_cf_token()

    print(f"{BO}AGANTS TRIAGE{X}  {DIM}external monitor — ctrl+c to stop{X}")
    print(f"{DIM}CF diagnostics: {'enabled' if token else 'DISABLED (no CLOUDFLARE_API_TOKEN in .env)'}{X}")
    print(f"{DIM}polling: {POLL_HEALTHY}s healthy / {POLL_OUTAGE}s outage{X}\n")

    outage_active = False
    outage_start  = None
    last_triage   = 0
    event_log     = []

    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"triage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    def log(msg):
        entry = f"[{ts()}] {msg}"
        event_log.append(entry)
        with log_file.open("a") as f:
            f.write(entry + "\n")

    while True:
        now = ts()
        statuses = {name: check(url) for name, url in SITES.items()}
        down = {name for name, (code, _) in statuses.items() if code != 200}

        # Clear screen and redraw
        print("\033[2J\033[H", end="")
        headline = f"{R}■ OUTAGE — {', '.join(down).upper()}{X}" if down else f"{G}■ ALL UP{X}"
        print(f"{BO}AGANTS TRIAGE{X}  {DIM}{now}{X}  {headline}\n")

        for name, (code, ms) in statuses.items():
            print(site_row(name, code, ms))

        # Recent events
        if event_log:
            print(f"\n{DIM}{'─'*52}{X}")
            for entry in event_log[-5:]:
                print(f"  {DIM}{entry}{X}")

        print(f"\n{DIM}next poll in {POLL_OUTAGE if down else POLL_HEALTHY}s  ·  log → {log_file.name}{X}")

        # Outage state machine
        now_ts = time.time()
        if down and not outage_active:
            outage_active = True
            outage_start  = now_ts
            last_triage   = now_ts
            msg = f"OUTAGE: {', '.join(down)}"
            log(msg)
            run_triage(down, token)

        elif down and outage_active:
            duration = int(now_ts - outage_start)
            if now_ts - last_triage >= RETRIAGE_SECS:
                last_triage = now_ts
                log(f"re-triage at {duration}s")
                run_triage(down, token)

        elif not down and outage_active:
            duration = int(now_ts - outage_start)
            outage_active = False
            msg = f"RESOLVED after {duration}s"
            log(msg)
            print(f"\n  {G}{BO}{msg}{X}")
            time.sleep(2)

        if args.once:
            if down:
                run_triage(down, token)
            break

        time.sleep(POLL_OUTAGE if down else POLL_HEALTHY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{DIM}stopped{X}")
