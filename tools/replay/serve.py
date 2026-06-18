#!/usr/bin/env python3
"""Static server for frontend/ that disables caching, so reloading always pulls fresh
JS modules/atlases (Python's default http.server lets the browser cache them, which
hid renderer changes). Run from repo root:  python3 tools/replay/serve.py [port]"""
import sys, os, http.server, socketserver
os.chdir(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()
print(f"no-cache static server on http://localhost:{PORT}  (serving frontend/)")
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("", PORT), H) as httpd:
    httpd.serve_forever()
