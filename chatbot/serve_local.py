"""
Local preview: serves the dashboard, its data and the chatbot API on one port.

    chatbot/.venv/bin/python chatbot/serve_local.py   ->   http://localhost:8787

Loads ../.env into this process only (keys are never printed or served).
"""
import json, mimetypes, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import Assistant
from tools import Facts

DASH = ROOT / "dashboard"
DATA = Path(os.environ.get("SITE_DATA", "/tmp/lk/site_data"))
bot = Assistant(Facts(os.environ.get("CHAT_DATA", "/tmp/lk/chat_data")))
lock = threading.Lock()
last = {}  # tiny per-IP rate limit


class H(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/config.js":
            return self._send(200, b'window.SP_API = "/api";', "application/javascript")
        base, rel = (DATA, p[len("/data/"):]) if p.startswith("/data/") else (DASH, p.lstrip("/") or "index.html")
        f = (base / rel).resolve()
        if base.resolve() not in f.parents and f != base.resolve() or not f.is_file():
            return self._send(404, b'{"error":"not found"}')
        self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")

    def do_POST(self):
        if urlparse(self.path).path != "/api/ask":
            return self._send(404, b"{}")
        ip = self.client_address[0]
        now = time.time()
        with lock:
            if now - last.get(ip, 0) < 1.0:
                return self._send(429, b'{"error":"slow down"}')
            last[ip] = now
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(min(n, 20000)) or b"{}")
            q = str(body.get("question", ""))
            hist = [h for h in body.get("history", []) if isinstance(h, dict)][-6:]
            r = bot.ask(q, hist)
            print(f"Q: {q[:80]!r} -> {r['ms']}ms tools={[t['name'] for t in r['tools']]} model={r['model']}", flush=True)
            self._send(200, json.dumps(r, default=str).encode())
        except Exception as e:  # never leak internals to the browser
            print("ERR", type(e).__name__, e, flush=True)
            self._send(500, b'{"error":"internal"}')

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("StreamPulse preview on http://localhost:8787", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8787), H).serve_forever()
