"""Local web server for the dashboard (standard library only).

Security: every /api/ call needs the access token printed at start-up. The page moves the
token out of the address bar, never sends it to third parties (Referrer-Policy: no-referrer)
and runs under a nonce-based Content-Security-Policy. By default the server only listens on
this computer (127.0.0.1); use --host 0.0.0.0 to reach it from a phone on the same Wi-Fi.
"""
from __future__ import annotations

import csv
import hmac
import io
import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import trends
from .diagnosis import EvidenceSet, check_symptoms
from .kb.maintenance import ITEM_INDEX
from .kb.symptoms import SYMPTOMS

log = logging.getLogger(__name__)
WEB = Path(__file__).parent / "web"


def _json_default(o):
    if isinstance(o, set):
        return sorted(o)
    return str(o)


class App:
    def __init__(self, monitor, store, cfg: dict, token: str):
        self.monitor = monitor
        self.store = store
        self.cfg = cfg
        self.token = token
        self.started = time.time()

    def trends_payload(self) -> dict:
        trips = self.store.trips(limit=60)
        return {
            "findings": [f.to_dict() for f in trends.analyse(trips)],
            "charts": trends.chart_data(trips),
            "trips": [{"id": t["id"], "start_ts": t["start_ts"], "end_ts": t["end_ts"], "source": t["source"],
                       **{k: (t["summary"] or {}).get(k) for k in ("km", "duration_min", "km_per_l", "codes",
                                                                   "coolant_max", "run_v")}}
                      for t in trips],
        }

    def evidence(self) -> EvidenceSet:
        ev = EvidenceSet()
        for tag, e in (self.monitor.get_report().get("evidence") or {}).items():
            if not tag.startswith("sym:"):
                ev.add(tag, e["strength"], e["severity"], e["reasons"][0] if e["reasons"] else tag, e["kind"])
        return ev


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AllionMonitor/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quieter console
            log.debug("%s - %s", self.address_string(), fmt % args)

        # ---------------------------------------------------------- helpers
        def _security_headers(self, nonce: str | None = None):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Cache-Control", "no-store")
            if nonce:
                self.send_header("Content-Security-Policy",
                                 f"default-src 'self'; script-src 'nonce-{nonce}'; "
                                 "style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; "
                                 "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None, nonce: str | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self._security_headers(nonce)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, default=_json_default).encode(), "application/json; charset=utf-8")

        def _error(self, code: int, msg: str):
            self._json({"error": msg}, code)

        def _authorised(self, qs: dict) -> bool:
            given = self.headers.get("X-Token") or (qs.get("t") or [""])[0]
            return bool(given) and hmac.compare_digest(given.encode(), app.token.encode())

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64_000:
                raise ValueError("Request too large")
            if n and "application/json" not in (self.headers.get("Content-Type") or ""):
                raise ValueError("Send JSON")
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data

        # ---------------------------------------------------------- GET
        def do_GET(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                return self._index()
            if url.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if url.path.startswith("/fonts/"):
                name = url.path.rsplit("/", 1)[-1]
                f = WEB / "fonts" / name
                if name.endswith(".woff2") and "/" not in name and f.is_file():
                    return self._send(200, f.read_bytes(), "font/woff2", {"Cache-Control": "max-age=604800"})
                return self._error(404, "Not found")
            if not url.path.startswith("/api/"):
                return self._error(404, "Not found")
            if not self._authorised(qs):
                return self._error(401, "Missing or wrong access token - open the link printed in the terminal.")
            m = app.monitor
            try:
                if url.path == "/api/state":
                    return self._json(m.snapshot())
                if url.path == "/api/report":
                    return self._json(m.get_report())
                if url.path == "/api/history":
                    n = min(1800, int((qs.get("n") or ["600"])[0]))
                    return self._json(m.history_rows(n))
                if url.path == "/api/trends":
                    return self._json(app.trends_payload())
                if url.path == "/api/symptoms":
                    cat = [{"id": s["id"], "title": s["title"], "group": s["group"]} for s in SYMPTOMS]
                    return self._json({"symptoms": cat, "active": app.store.active_symptoms()})
                if url.path == "/api/stream":
                    return self._stream()
                if url.path == "/api/export.csv":
                    return self._export(int((qs.get("trip") or ["0"])[0]))
            except Exception as exc:
                log.exception("API error")
                return self._error(500, str(exc))
            return self._error(404, "Unknown endpoint")

        def do_HEAD(self):
            self.do_GET()

        def _index(self):
            nonce = secrets.token_urlsafe(16)
            html = (WEB / "index.html").read_text(encoding="utf-8").replace("<script>", f'<script nonce="{nonce}">')
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", nonce=nonce)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self._security_headers()
            self.end_headers()
            last_report = None
            last_hist = None
            try:
                while True:
                    snap = app.monitor.snapshot()
                    rows = app.monitor.history_rows(1)
                    row = rows[-1] if rows and rows[-1]["ts"] != last_hist else None
                    if row:
                        last_hist = row["ts"]
                    rep_ts = app.monitor.get_report().get("generated_ts")
                    msg = {"type": "tick", "state": snap, "row": row, "report_ts": rep_ts,
                           "report_changed": rep_ts != last_report}
                    last_report = rep_ts
                    self.wfile.write(f"data: {json.dumps(msg, default=_json_default)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(1.0)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def _export(self, trip_id: int):
            rows = app.store.samples(trip_id)
            buf = io.StringIO()
            keys = sorted({k for r in rows for k in r})
            if "ts" in keys:
                keys.remove("ts")
                keys = ["ts"] + keys
            w = csv.DictWriter(buf, fieldnames=keys or ["ts"])
            w.writeheader()
            for r in rows:
                w.writerow(r)
            self._send(200, buf.getvalue().encode(), "text/csv; charset=utf-8",
                       {"Content-Disposition": f'attachment; filename="trip-{trip_id}.csv"'})

        # ---------------------------------------------------------- POST
        def do_POST(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            if not self._authorised(qs):
                return self._error(401, "Missing or wrong access token.")
            try:
                body = self._body()
            except (ValueError, json.JSONDecodeError) as exc:
                return self._error(400, str(exc))
            m = app.monitor
            try:
                if url.path == "/api/scan":
                    return self._json(m.request("scan", timeout=90))
                if url.path == "/api/clear-codes":
                    if body.get("confirm") != "CLEAR":
                        return self._error(400, "Confirmation missing")
                    return self._json(m.request("clear_codes", timeout=90))
                if url.path == "/api/symptoms/check":
                    sel = [s for s in body.get("selected", []) if isinstance(s, str)][:12]
                    return self._json({"results": check_symptoms(sel, app.evidence())})
                if url.path == "/api/symptoms/save":
                    sel = [s for s in body.get("selected", []) if isinstance(s, str)][:12]
                    app.store.set_symptoms(sel)
                    m.request("refresh", timeout=30)
                    return self._json({"ok": True, "active": sel})
                if url.path == "/api/service":
                    item = body.get("item")
                    if item not in ITEM_INDEX:
                        return self._error(400, "Unknown service item")
                    km = body.get("km")
                    km = float(km) if km not in (None, "") else None
                    date = body.get("date") or None
                    if date:
                        import datetime as _dt
                        _dt.date.fromisoformat(date)
                    app.store.record_service(item, km, date, str(body.get("note", ""))[:200])
                    m.request("refresh", timeout=30)
                    return self._json({"ok": True})
                if url.path == "/api/odometer":
                    km = float(body.get("km"))
                    if not 0 <= km < 2_000_000:
                        return self._error(400, "Odometer reading looks wrong")
                    app.store.set_odometer(m.clock.now(), km, "manual")
                    m.request("refresh", timeout=30)
                    return self._json({"ok": True})
            except (TimeoutError, RuntimeError) as exc:
                return self._error(409, str(exc))
            except (TypeError, ValueError) as exc:
                return self._error(400, str(exc))
            return self._error(404, "Unknown endpoint")

    return Handler


def serve(monitor, store, cfg: dict, host: str, port: int, token: str | None = None):
    token = token or cfg["server"].get("token") or secrets.token_urlsafe(18)
    app = App(monitor, store, cfg, token)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True, name="http")
    t.start()
    return httpd, token
