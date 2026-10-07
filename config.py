"""Loads config.toml on top of sensible defaults."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

if sys.version_info < (3, 11):  # pragma: no cover
    raise SystemExit("Python 3.11 or newer is needed (for tomllib). Install it from python.org.")
import tomllib

DEFAULTS = {
    "vehicle": {
        "name": "My Allion", "chassis_code": "NZT260", "engine": "", "displacement_l": 0,
        "transmission": "", "year": 0, "altitude_m": 1795, "cylinders": 4,
    },
    "connection": {
        "type": "serial", "port": "", "baudrate": 0, "host": "192.168.0.10", "tcp_port": 35000,
        "protocol": "auto", "batch_pids": True,
    },
    "monitor": {
        "poll_interval_s": 0.05, "log_interval_s": 1.0, "scan_interval_s": 300, "database": "data/allion.db",
    },
    "server": {"host": "127.0.0.1", "port": 8765, "token": ""},
    "thresholds": {},
    "maintenance": {},
    "custom_pids": [],
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None) -> dict:
    data = {}
    if path:
        p = Path(path)
        if p.exists():
            with p.open("rb") as fh:
                data = tomllib.load(fh)
        elif str(path) != "config.toml":
            raise SystemExit(f"Config file not found: {p}")
    return _merge(DEFAULTS, data)
