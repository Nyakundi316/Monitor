"""Standard OBD-II Mode 01 parameters (SAE J1979) and how to decode them.

Each decoder takes the data bytes that follow the PID number and returns a dict of
named values, so one PID can yield several readings (e.g. lambda and current).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PID:
    pid: int
    name: str
    length: int
    decode: Callable[[bytes], dict]
    units: dict  # key -> unit, for display


def _pct(key):
    return lambda d: {key: round(d[0] * 100 / 255, 1)}


def _temp(key):
    return lambda d: {key: d[0] - 40}


def _trim(key):
    return lambda d: {key: round((d[0] - 128) * 100 / 128, 1)}


def _word(key, scale=1.0, offset=0.0, nd=2):
    return lambda d: {key: round((d[0] * 256 + d[1]) * scale + offset, nd)}


def _o2_narrow(n):
    def dec(d):
        out = {f"o2s{n}_v": round(d[0] / 200, 3)}
        if d[1] != 0xFF:
            out[f"o2s{n}_trim"] = round((d[1] - 128) * 100 / 128, 1)
        return out
    return dec


def _o2_wide_v(n):
    return lambda d: {
        f"o2s{n}_lambda": round((d[0] * 256 + d[1]) * 2 / 65536, 3),
        f"o2s{n}_wv": round((d[2] * 256 + d[3]) * 8 / 65536, 3),
    }


def _o2_wide_ma(n):
    return lambda d: {
        f"o2s{n}_lambda": round((d[0] * 256 + d[1]) * 2 / 65536, 3),
        f"o2s{n}_ma": round((d[2] * 256 + d[3]) / 256 - 128, 3),
    }


MONITOR_NAMES_SPARK = ["catalyst", "heated_catalyst", "evap", "secondary_air",
                       "ac_refrigerant", "o2_sensor", "o2_heater", "egr"]
CONTINUOUS = ["misfire", "fuel_system", "components"]


def decode_monitor_status(d: bytes) -> dict:
    a, b, c, dd = d[0], d[1], d[2], d[3]
    monitors = {}
    for i, name in enumerate(CONTINUOUS):
        if b & (1 << i):
            monitors[name] = {"supported": True, "complete": not bool(b & (1 << (i + 4)))}
    compression = bool(b & 0x08)
    if not compression:
        for i, name in enumerate(MONITOR_NAMES_SPARK):
            if c & (1 << i):
                monitors[name] = {"supported": True, "complete": not bool(dd & (1 << i))}
    return {"status": {"mil": bool(a & 0x80), "dtc_count": a & 0x7F,
                       "compression_ignition": compression, "monitors": monitors}}


FUEL_STATUS = {
    1: "open loop - engine still cold",
    2: "closed loop",
    4: "open loop - high load or deceleration",
    8: "open loop - system fault",
    16: "closed loop - fault in an oxygen sensor",
}


def decode_fuel_status(d: bytes) -> dict:
    a = d[0]
    return {"fuel_status": FUEL_STATUS.get(a, f"unknown ({a})"), "closed_loop": a in (2, 16)}


OBD_STANDARDS = {
    1: "OBD-II (California ARB)", 2: "OBD (Federal EPA)", 3: "OBD and OBD-II", 4: "OBD-I",
    5: "Not OBD compliant", 6: "EOBD (Europe)", 7: "EOBD and OBD-II", 8: "EOBD and OBD",
    9: "EOBD, OBD and OBD-II", 10: "JOBD (Japan)", 11: "JOBD and OBD-II", 12: "JOBD and EOBD",
    13: "JOBD, EOBD and OBD-II",
}

FUEL_TYPES = {1: "Petrol", 2: "Methanol", 3: "Ethanol", 4: "Diesel", 5: "LPG", 6: "CNG",
              8: "Electric", 11: "Bifuel petrol", 17: "Hybrid petrol", 19: "Hybrid diesel"}


def decode_supported(d: bytes, base: int) -> set[int]:
    bits = int.from_bytes(d[:4], "big")
    return {base + i + 1 for i in range(32) if bits & (1 << (31 - i))}


_DEFS: list[PID] = [
    PID(0x01, "Monitor status since codes cleared", 4, decode_monitor_status, {}),
    PID(0x03, "Fuel system status", 2, decode_fuel_status, {}),
    PID(0x04, "Engine load", 1, _pct("load"), {"load": "%"}),
    PID(0x05, "Coolant temperature", 1, _temp("coolant"), {"coolant": "°C"}),
    PID(0x06, "Short-term fuel trim", 1, _trim("stft"), {"stft": "%"}),
    PID(0x07, "Long-term fuel trim", 1, _trim("ltft"), {"ltft": "%"}),
    PID(0x0A, "Fuel pressure", 1, lambda d: {"fuel_pressure": d[0] * 3}, {"fuel_pressure": "kPa"}),
    PID(0x0B, "Intake manifold pressure", 1, lambda d: {"map": d[0]}, {"map": "kPa"}),
    PID(0x0C, "Engine speed", 2, _word("rpm", 0.25, nd=0), {"rpm": "rpm"}),
    PID(0x0D, "Vehicle speed", 1, lambda d: {"speed": d[0]}, {"speed": "km/h"}),
    PID(0x0E, "Ignition timing advance", 1, lambda d: {"timing": d[0] / 2 - 64}, {"timing": "°"}),
    PID(0x0F, "Intake air temperature", 1, _temp("iat"), {"iat": "°C"}),
    PID(0x10, "Mass air flow", 2, _word("maf", 0.01), {"maf": "g/s"}),
    PID(0x11, "Throttle position", 1, _pct("throttle"), {"throttle": "%"}),
    PID(0x13, "Oxygen sensors present", 1, lambda d: {"o2_present": d[0]}, {}),
    PID(0x14, "O2 sensor B1S1", 2, _o2_narrow(1), {"o2s1_v": "V"}),
    PID(0x15, "O2 sensor B1S2", 2, _o2_narrow(2), {"o2s2_v": "V"}),
    PID(0x1C, "OBD standard", 1, lambda d: {"obd_standard": OBD_STANDARDS.get(d[0], f"Type {d[0]}")}, {}),
    PID(0x1F, "Run time since start", 2, _word("runtime", 1, nd=0), {"runtime": "s"}),
    PID(0x21, "Distance with check-engine light on", 2, _word("mil_distance", 1, nd=0), {"mil_distance": "km"}),
    PID(0x24, "O2 B1S1 wide-range (voltage)", 4, _o2_wide_v(1), {"o2s1_lambda": "λ"}),
    PID(0x25, "O2 B1S2 wide-range (voltage)", 4, _o2_wide_v(2), {"o2s2_lambda": "λ"}),
    PID(0x2E, "Commanded evaporative purge", 1, _pct("purge"), {"purge": "%"}),
    PID(0x2F, "Fuel level", 1, _pct("fuel_level"), {"fuel_level": "%"}),
    PID(0x30, "Warm-ups since codes cleared", 1, lambda d: {"warmups_since_clear": d[0]}, {}),
    PID(0x31, "Distance since codes cleared", 2, _word("distance_since_clear", 1, nd=0),
        {"distance_since_clear": "km"}),
    PID(0x33, "Barometric pressure", 1, lambda d: {"baro": d[0]}, {"baro": "kPa"}),
    PID(0x34, "O2 B1S1 wide-range (current)", 4, _o2_wide_ma(1), {"o2s1_lambda": "λ", "o2s1_ma": "mA"}),
    PID(0x35, "O2 B1S2 wide-range (current)", 4, _o2_wide_ma(2), {"o2s2_lambda": "λ"}),
    PID(0x3C, "Catalyst temperature B1S1", 2, _word("cat_temp", 0.1, -40, nd=0), {"cat_temp": "°C"}),
    PID(0x41, "Monitor status this drive", 4, lambda d: {}, {}),
    PID(0x42, "ECU supply voltage", 2, _word("ecu_voltage", 0.001), {"ecu_voltage": "V"}),
    PID(0x43, "Absolute load", 2, _word("abs_load", 100 / 255, nd=1), {"abs_load": "%"}),
    PID(0x44, "Commanded air-fuel ratio (λ)", 2, _word("cmd_lambda", 2 / 65536, nd=3), {"cmd_lambda": "λ"}),
    PID(0x45, "Relative throttle position", 1, _pct("rel_throttle"), {"rel_throttle": "%"}),
    PID(0x46, "Outside air temperature", 1, _temp("ambient"), {"ambient": "°C"}),
    PID(0x47, "Throttle position B", 1, _pct("throttle_b"), {"throttle_b": "%"}),
    PID(0x49, "Accelerator pedal D", 1, _pct("pedal"), {"pedal": "%"}),
    PID(0x4A, "Accelerator pedal E", 1, _pct("pedal_e"), {"pedal_e": "%"}),
    PID(0x4C, "Commanded throttle", 1, _pct("cmd_throttle"), {"cmd_throttle": "%"}),
    PID(0x4D, "Time with check-engine light on", 2, _word("mil_minutes", 1, nd=0), {"mil_minutes": "min"}),
    PID(0x4E, "Time since codes cleared", 2, _word("minutes_since_clear", 1, nd=0),
        {"minutes_since_clear": "min"}),
    PID(0x51, "Fuel type", 1, lambda d: {"fuel_type": FUEL_TYPES.get(d[0], f"Type {d[0]}")}, {}),
    PID(0x5C, "Engine oil temperature", 1, _temp("oil_temp"), {"oil_temp": "°C"}),
    PID(0x5E, "Engine fuel rate", 2, _word("fuel_rate", 0.05), {"fuel_rate": "L/h"}),
    PID(0xA6, "Odometer", 4, lambda d: {"odometer": round(int.from_bytes(d[:4], "big") / 10, 1)},
        {"odometer": "km"}),
]

PIDS: dict[int, PID] = {p.pid: p for p in _DEFS}
SUPPORT_PIDS = (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0)

# Data length of every Mode 01 PID we may see in a multi-PID reply (needed to split replies).
PID_LENGTHS: dict[int, int] = {p.pid: p.length for p in _DEFS}
PID_LENGTHS.update({b: 4 for b in SUPPORT_PIDS})
PID_LENGTHS.update({0x02: 2, 0x08: 1, 0x09: 1, 0x12: 1, 0x16: 2, 0x17: 2, 0x18: 2, 0x19: 2,
                    0x1A: 2, 0x1B: 2, 0x1D: 1, 0x1E: 1, 0x22: 2, 0x23: 2, 0x2C: 1, 0x2D: 1,
                    0x32: 2, 0x3D: 2, 0x3E: 2, 0x3F: 2, 0x48: 1, 0x4B: 1, 0x52: 1, 0x5A: 1})

UNITS: dict[str, str] = {}
for _p in _DEFS:
    UNITS.update(_p.units)
UNITS.update({"battery": "V", "ve": "%", "fuel_lph": "L/h", "kmpl": "km/L"})

LABELS = {
    "rpm": "Engine speed", "speed": "Speed", "coolant": "Coolant", "load": "Engine load",
    "stft": "Short-term fuel trim", "ltft": "Long-term fuel trim", "maf": "Air flow (MAF)",
    "throttle": "Throttle", "iat": "Intake air", "timing": "Timing advance", "battery": "Battery",
    "o2s1_lambda": "Front A/F sensor λ", "o2s1_ma": "Front A/F sensor current",
    "o2s1_v": "Front O2 sensor", "o2s2_v": "Rear O2 sensor", "baro": "Air pressure",
    "cat_temp": "Catalyst temperature", "ecu_voltage": "ECU voltage", "fuel_level": "Fuel level",
    "pedal": "Accelerator pedal", "cmd_throttle": "Commanded throttle", "map": "Manifold pressure",
    "ambient": "Outside air", "oil_temp": "Oil temperature", "fuel_rate": "Fuel rate",
}


def decode_pid(pid: int, data: bytes) -> dict:
    p = PIDS.get(pid)
    if p is None or len(data) < p.length:
        return {}
    try:
        return p.decode(data[: p.length])
    except Exception:
        return {}


def split_multi_pid(payload: bytes) -> dict[int, bytes]:
    """Split a Mode 01 reply (starting at the 0x41 byte) into {pid: data}.

    Works for single and multi-PID replies. Stops at the first unknown PID, because
    without its length we cannot know where the next one starts.
    """
    out: dict[int, bytes] = {}
    if not payload or payload[0] != 0x41:
        return out
    i = 1
    while i < len(payload):
        pid = payload[i]
        n = PID_LENGTHS.get(pid)
        if n is None or i + 1 + n > len(payload):
            break
        out[pid] = bytes(payload[i + 1 : i + 1 + n])
        i += 1 + n
    return out
