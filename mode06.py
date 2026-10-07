"""Mode 06: the ECU's own self-test results (CAN cars only).

These are the numbers behind the check-engine light. Each test reports a value plus the
lower and upper limits it must stay within. A test that passes but sits close to its limit
is an early warning - e.g. a catalytic converter that is still passing, but only just.

Misfire counts per cylinder (MIDs A2-AD) are the best early sign of a failing ignition
coil or spark plug, often weeks before a P030x code appears.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

MID_NAMES = {
    0x01: "Front oxygen sensor (B1S1)", 0x02: "Rear oxygen sensor (B1S2)",
    0x03: "Oxygen sensor B1S3", 0x05: "Oxygen sensor B2S1", 0x06: "Oxygen sensor B2S2",
    0x21: "Catalytic converter (bank 1)", 0x22: "Catalytic converter (bank 2)",
    0x31: "EGR system", 0x35: "Variable valve timing (VVT-i)", 0x36: "Variable valve timing bank 2",
    0x39: "EVAP leak test (large / cap off)", 0x3A: "EVAP leak test (0.090\")",
    0x3B: "EVAP leak test (0.040\")", 0x3C: "EVAP leak test (0.020\")", 0x3D: "EVAP purge flow",
    0x41: "Front O2 sensor heater", 0x42: "Rear O2 sensor heater",
    0x61: "Heated catalyst", 0x71: "Secondary air", 0x81: "Fuel system (bank 1)",
    0xA1: "Misfire (all cylinders)",
}
for _c in range(1, 13):
    MID_NAMES[0xA1 + _c] = f"Misfire - cylinder {_c}"

# Unit and scaling identifiers (J1979 Appendix E) - the common ones.
UASID = {
    0x01: ("", 1), 0x02: ("", 0.1), 0x03: ("", 0.01), 0x04: ("", 0.001), 0x07: ("rpm", 0.25),
    0x0A: ("mV", 0.122), 0x0B: ("V", 0.001), 0x0C: ("V", 0.01), 0x10: ("ms", 1), 0x12: ("s", 1),
    0x16: ("°C", 0.1), 0x1A: ("kPa", 1), 0x1C: ("°", 0.01), 0x20: ("ratio", 0.0039062),
    0x24: ("counts", 1), 0x27: ("g/s", 0.01), 0x2B: ("switches", 1),
    0x81: ("", 1), 0x82: ("", 0.1), 0x83: ("", 0.01), 0x84: ("", 0.001), 0x8A: ("mV", 0.122),
    0x8B: ("V", 0.001), 0x8C: ("V", 0.01), 0x96: ("°C", 0.1), 0x9C: ("°", 0.01),
}

TID_MISFIRE_EWMA = 0x0B     # average misfire count over the last 10 drive cycles
TID_MISFIRE_CURRENT = 0x0C  # misfire count in the last / current drive cycle


@dataclass
class TestResult:
    mid: int
    tid: int
    uasid: int
    value: int
    min: int
    max: int

    @property
    def signed(self) -> bool:
        return self.uasid >= 0x80

    def _num(self, raw: int) -> int:
        if self.signed and raw >= 0x8000:
            return raw - 0x10000
        return raw

    @property
    def v(self) -> int:
        return self._num(self.value)

    @property
    def lo(self) -> int:
        return self._num(self.min)

    @property
    def hi(self) -> int:
        return self._num(self.max)

    @property
    def passed(self) -> bool:
        return self.lo <= self.v <= self.hi

    @property
    def is_misfire_count(self) -> bool:
        return 0xA1 <= self.mid <= 0xAD and self.tid in (TID_MISFIRE_EWMA, TID_MISFIRE_CURRENT)

    def _limit_active(self, which: str) -> bool:
        if which == "min":
            return self.lo not in (0, -32768)
        return self.hi not in (0xFFFF, 32767)

    @property
    def usage(self) -> float | None:
        """How close the result is to failing: 0 = far from limit, 1 = at the limit."""
        lo_on, hi_on = self._limit_active("min"), self._limit_active("max")
        v, lo, hi = self.v, self.lo, self.hi
        if hi_on and not lo_on:
            if hi <= 0:
                return None
            return max(0.0, v / hi) if v >= 0 else 0.0
        if lo_on and not hi_on:
            if v <= 0:
                return 1.0 if lo > 0 else None
            return max(0.0, lo / v)
        if lo_on and hi_on and hi > lo:
            mid = (lo + hi) / 2
            half = (hi - lo) / 2
            return min(1.5, abs(v - mid) / half)
        return None

    def scaled(self, raw: int) -> float:
        unit, scale = UASID.get(self.uasid, ("", 1))
        val = raw * scale
        if self.uasid in (0x16, 0x96):
            val -= 40
        return round(val, 4)

    @property
    def unit(self) -> str:
        return UASID.get(self.uasid, ("", 1))[0]

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update({
            "name": MID_NAMES.get(self.mid, f"Test group {self.mid:02X}"),
            "value_scaled": self.scaled(self.v), "min_scaled": self.scaled(self.lo),
            "max_scaled": self.scaled(self.hi), "unit": self.unit, "passed": self.passed,
            "usage": None if self.usage is None else round(self.usage, 3),
            "misfire": self.is_misfire_count,
        })
        return d


def parse_supported_mids(payload: bytes, base: int) -> set[int]:
    """Reply to 06 00 / 06 20 ...: 46 <base> <4-byte bitmask>."""
    if len(payload) < 6 or payload[0] != 0x46 or payload[1] != base:
        return set()
    bits = int.from_bytes(payload[2:6], "big")
    return {base + i + 1 for i in range(32) if bits & (1 << (31 - i))}


def parse_results(payload: bytes) -> list[TestResult]:
    """Reply to 06 <MID>: 46 then 9-byte records: MID TID UASID value(2) min(2) max(2)."""
    out: list[TestResult] = []
    if not payload or payload[0] != 0x46:
        return out
    body = payload[1:]
    for i in range(0, len(body) - 8, 9):
        r = body[i : i + 9]
        out.append(TestResult(
            mid=r[0], tid=r[1], uasid=r[2],
            value=r[3] << 8 | r[4], min=r[5] << 8 | r[6], max=r[7] << 8 | r[8],
        ))
    return out


def misfire_summary(results: list[TestResult]) -> dict:
    """{'per_cylinder': {1: {'current': n, 'average': n}, ...}, 'total_current': n}."""
    per: dict[int, dict] = {}
    for r in results:
        if not r.is_misfire_count or r.mid == 0xA1:
            continue
        cyl = r.mid - 0xA1
        slot = per.setdefault(cyl, {"current": 0, "average": 0})
        if r.tid == TID_MISFIRE_CURRENT:
            slot["current"] = r.v
        else:
            slot["average"] = r.v
    return {"per_cylinder": per, "total_current": sum(s["current"] for s in per.values()),
            "total_average": sum(s["average"] for s in per.values())}
