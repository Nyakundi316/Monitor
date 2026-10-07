"""SQLite storage for trips, samples, scans, service history and reported symptoms."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS trips(
    id INTEGER PRIMARY KEY, start_ts REAL, end_ts REAL, source TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS samples(trip_id INTEGER, ts REAL, data TEXT);
CREATE INDEX IF NOT EXISTS idx_samples_trip ON samples(trip_id, ts);
CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY, ts REAL, trip_id INTEGER, data TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts REAL, trip_id INTEGER, kind TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS maintenance(item_id TEXT PRIMARY KEY, last_km REAL, last_date TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS odometer(ts REAL, km REAL, source TEXT);
CREATE TABLE IF NOT EXISTS symptoms(id INTEGER PRIMARY KEY, ts REAL, symptom_id TEXT, active INTEGER DEFAULT 1);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.commit()

    # ------------------------------------------------------------- helpers
    def _exec(self, sql: str, args=(), commit=True):
        with self._lock:
            cur = self.db.execute(sql, args)
            if commit:
                self.db.commit()
            return cur

    def _all(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def meta_get(self, key: str, default=None):
        rows = self._all("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def meta_set(self, key: str, value) -> None:
        self._exec("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # ------------------------------------------------------------- trips
    def start_trip(self, ts: float, source: str) -> int:
        return self._exec("INSERT INTO trips(start_ts, source) VALUES (?, ?)", (ts, source)).lastrowid

    def end_trip(self, trip_id: int, ts: float, summary: dict) -> None:
        self._exec("UPDATE trips SET end_ts=?, summary=? WHERE id=?", (ts, json.dumps(summary), trip_id))

    def add_samples(self, trip_id: int, rows: list[tuple[float, dict]]) -> None:
        if not rows:
            return
        with self._lock:
            self.db.executemany("INSERT INTO samples(trip_id, ts, data) VALUES (?, ?, ?)",
                                [(trip_id, ts, json.dumps(d, separators=(",", ":"))) for ts, d in rows])
            self.db.commit()

    def trips(self, limit: int = 60, completed_only: bool = True) -> list[dict]:
        where = "WHERE summary IS NOT NULL" if completed_only else ""
        rows = self._all(f"SELECT * FROM trips {where} ORDER BY start_ts DESC LIMIT ?", (limit,))
        out = []
        for r in reversed(rows):
            out.append({"id": r["id"], "start_ts": r["start_ts"], "end_ts": r["end_ts"], "source": r["source"],
                        "summary": json.loads(r["summary"]) if r["summary"] else None})
        return out

    def samples(self, trip_id: int, every: int = 1) -> list[dict]:
        rows = self._all("SELECT ts, data FROM samples WHERE trip_id=? ORDER BY ts", (trip_id,))
        out = []
        for i, r in enumerate(rows):
            if i % every == 0:
                d = json.loads(r["data"])
                d["ts"] = r["ts"]
                out.append(d)
        return out

    def km_since(self, ts: float) -> float:
        total = 0.0
        for r in self._all("SELECT summary FROM trips WHERE start_ts>=? AND summary IS NOT NULL", (ts,)):
            total += float(json.loads(r["summary"]).get("km", 0) or 0)
        return total

    # ------------------------------------------------------------- scans & events
    def add_scan(self, ts: float, trip_id: int | None, data: dict) -> None:
        self._exec("INSERT INTO scans(ts, trip_id, data) VALUES (?, ?, ?)", (ts, trip_id, json.dumps(data)))

    def last_scan(self) -> dict | None:
        rows = self._all("SELECT ts, data FROM scans ORDER BY ts DESC LIMIT 1")
        if not rows:
            return None
        d = json.loads(rows[0]["data"])
        d["ts"] = rows[0]["ts"]
        return d

    def scans_for_trip(self, trip_id: int) -> list[dict]:
        return [json.loads(r["data"]) for r in self._all("SELECT data FROM scans WHERE trip_id=? ORDER BY ts", (trip_id,))]

    def add_event(self, ts: float, trip_id: int | None, kind: str, data: dict) -> None:
        self._exec("INSERT INTO events(ts, trip_id, kind, data) VALUES (?, ?, ?, ?)",
                   (ts, trip_id, kind, json.dumps(data)))

    def events(self, limit: int = 50) -> list[dict]:
        rows = self._all("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        return [{"ts": r["ts"], "trip_id": r["trip_id"], "kind": r["kind"], **json.loads(r["data"])} for r in rows]

    # ------------------------------------------------------------- service history
    def maintenance(self) -> dict[str, dict]:
        return {r["item_id"]: {"last_km": r["last_km"], "last_date": r["last_date"], "note": r["note"]}
                for r in self._all("SELECT * FROM maintenance")}

    def record_service(self, item_id: str, km: float | None, date: str | None, note: str = "") -> None:
        self._exec("INSERT OR REPLACE INTO maintenance(item_id, last_km, last_date, note) VALUES (?, ?, ?, ?)",
                   (item_id, km, date, note))

    def set_odometer(self, ts: float, km: float, source: str = "manual") -> None:
        self._exec("INSERT INTO odometer(ts, km, source) VALUES (?, ?, ?)", (ts, km, source))

    def odometer(self) -> tuple[float | None, float | None]:
        """Latest manual reading plus distance logged since -> current estimate."""
        rows = self._all("SELECT ts, km FROM odometer ORDER BY ts DESC LIMIT 1")
        if not rows:
            return None, None
        ts, km = rows[0]["ts"], rows[0]["km"]
        return round(km + self.km_since(ts), 1), ts

    # ------------------------------------------------------------- symptoms
    def active_symptoms(self) -> list[str]:
        return [r["symptom_id"] for r in self._all("SELECT symptom_id FROM symptoms WHERE active=1 ORDER BY ts")]

    def set_symptoms(self, ids: list[str]) -> None:
        with self._lock:
            self.db.execute("UPDATE symptoms SET active=0")
            for i in ids:
                self.db.execute("INSERT INTO symptoms(ts, symptom_id, active) VALUES (?, ?, 1)", (time.time(), i))
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()
