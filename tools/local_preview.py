"""Local frontend preview server.

The game page (frontend/game/index.html) is normally served by the Cloudflare
Worker in production; the game *server* (server.py) only exposes the API/WebSocket
under the /agants prefix. This tiny static server lets you preview the LOCAL
frontend against the LOCAL game server: it serves frontend/ and intercepts
/config.js to point AGANTS_BACKEND at http://localhost:8083/agants instead of
production — so the new AI Activity feed actually has data.

Run:  python3 tools/local_preview.py        # serves on :8090
Open: http://localhost:8090/game/index.html
"""
import http.server, socketserver, os, sys

PORT = int(os.environ.get("PREVIEW_PORT", "8090"))
BACKEND = os.environ.get("LOCAL_BACKEND", "http://localhost:8083/agants")
ROOT = os.path.join(os.path.dirname(__file__), "..", "frontend")
ROOT = os.path.abspath(ROOT)

_CONFIG_JS = f"""// LOCAL PREVIEW config (served by tools/local_preview.py)
window.AGANTS_BACKEND  = "{BACKEND}";
window.AGANTS_AUTH_URL = window.AGANTS_AUTH_URL || "https://agants-auth.hermesagent424.workers.dev";
window.AGANTS_ADMIN    = true;
""".encode()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def end_headers(self):
        # allow the page (this origin) to call the game server (other origin)
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?")[0] in ("/config.js", "/game/config.js"):
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(_CONFIG_JS)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(_CONFIG_JS)
            return
        return super().do_GET()

    def log_message(self, *a):
        pass  # quiet


if __name__ == "__main__":
    os.chdir(ROOT)
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"local preview serving {ROOT} on :{PORT} -> backend {BACKEND}")
        print(f"open http://localhost:{PORT}/game/index.html")
        httpd.serve_forever()
