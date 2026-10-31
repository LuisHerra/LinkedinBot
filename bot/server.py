"""API de métricas de solo lectura (biblioteca estándar, sin dependencias extra).

  GET /api/health    -> {"ok": true}
  GET /api/summary   -> métricas sin datos de personas (pública salvo METRICS_PUBLIC=0)
  GET /api/metrics   -> métricas + invitaciones y detalle de operaciones (requiere token)

Token: cabecera "Authorization: Bearer <METRICS_TOKEN>" o parámetro ?token=.
Variables: METRICS_HOST (127.0.0.1), METRICS_PORT (8787), METRICS_TOKEN, METRICS_PUBLIC (1), METRICS_CORS (*).
"""
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import metrics
from .config import DEFAULT_DB
from .db import DB


def _db():
    path = os.environ.get("BOT_DB")
    return DB(type(DEFAULT_DB)(path) if path else DEFAULT_DB)


class Handler(BaseHTTPRequestHandler):
    cfg = None
    server_version = "linkedin-bot-metrics"

    def _send(self, status, body):
        data = json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", os.environ.get("METRICS_CORS", "*"))
        self.send_header("Access-Control-Allow-Headers", "Authorization")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, query):
        token = os.environ.get("METRICS_TOKEN", "")
        if not token:
            return False
        given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        given = given or (query.get("token") or [""])[0]
        return hmac.compare_digest(given.encode(), token.encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", os.environ.get("METRICS_CORS", "*"))
        self.send_header("Access-Control-Allow-Headers", "Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        if parts.path == "/api/health":
            return self._send(200, {"ok": True})
        if parts.path not in ("/api/summary", "/api/metrics"):
            return self._send(404, {"error": "not found"})
        detailed = parts.path == "/api/metrics"
        public = os.environ.get("METRICS_PUBLIC", "1") == "1"
        if (detailed or not public) and not self._authorized(query):
            return self._send(401, {"error": "token requerido"})
        db = _db()
        try:
            return self._send(200, metrics.build(db, self.cfg, detailed=detailed))
        finally:
            db.close()

    def log_message(self, fmt, *args):
        # Sin la query: puede llevar el token.
        print(f"{self.address_string()} {self.command} {urlsplit(self.path).path} -> {args[1] if len(args) > 1 else ''}")


def serve(cfg):
    host = os.environ.get("METRICS_HOST", "127.0.0.1")
    port = int(os.environ.get("METRICS_PORT", "8787"))
    if not os.environ.get("METRICS_TOKEN"):
        print("Aviso: sin METRICS_TOKEN, /api/metrics queda deshabilitado (siempre 401).")
    Handler.cfg = cfg
    print(f"API de métricas en http://{host}:{port}/api/summary")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
