"""Trends across trips: slow changes that are invisible on any single drive.

Each completed trip stores a summary (resting battery voltage, idle fuel trim, warm-up
time...). Comparing the car's recent trips with its own earlier 'baseline' catches a
battery that is fading, an air leak that is growing or a coil that is starting to fail.
"""
from __future__ import annotations

import statistics as st

from .rules import Finding

# metric, direction that is bad, change that matters (absolute unless rel=True), tag, system, title, explanation
TREND_RULES = [
    ("rest_v", "down", 0.20, False, "trend:battery_rest_down", "Charging", "Battery resting voltage falling",
     "Resting voltage has dropped from {base} V to {recent} V. Batteries fade like this for weeks before a no-start."),
    ("crank_v", "down", 0.45, False, "trend:crank_down", "Charging", "Starting voltage dip getting deeper",
     "Lowest voltage while cranking has gone from {base} V to {recent} V - the battery is losing capacity."),
    ("idle_trim", "up", 6.0, False, "trend:lean_idle_up", "Fuel & air", "Fuel trims at idle drifting lean",
     "Idle fuel correction has moved from {base} % to {recent} %. Something is letting in unmeasured air - usually a "
     "hose or the intake boot starting to crack."),
    ("cruise_trim", "up", 6.0, False, "trend:lean_cruise_up", "Fuel & air", "Fuel trims at cruise drifting lean",
     "Cruise fuel correction has moved from {base} % to {recent} % - the MAF sensor may be getting dirty."),
    ("idle_trim", "down", 6.0, False, "trend:rich_drift", "Fuel & air", "Fuel trims drifting rich",
     "Idle fuel correction has moved from {base} % to {recent} %."),
    ("idle_throttle", "up", 1.5, False, "trend:idle_throttle_up", "Fuel & air", "Throttle opening more at idle",
     "To hold the same idle the computer now opens the throttle to {recent} % (was {base} %). Carbon is building up "
     "in the throttle body."),
    ("warmup_min", "up", 0.40, True, "trend:warmup_slower", "Cooling", "Engine taking longer to warm up",
     "Cold-start warm-up has gone from {base} to {recent} minutes - the thermostat may be starting to stick open."),
    ("km_per_l", "down", 0.12, True, "trend:economy_down", "Engine", "Fuel economy getting worse",
     "Fuel economy has dropped from {base} to {recent} km/L for similar driving. Check tyre pressures, air filter and brakes."),
    ("cruise_timing", "down", 4.0, False, "trend:timing_down", "Engine", "Ignition timing pulled back at cruise",
     "Timing advance at cruise is down from {base}° to {recent}°. The engine is protecting itself from knock - often poor fuel."),
    ("idle_rpm_sd", "up", 12.0, False, "trend:idle_rough_up", "Engine", "Idle getting rougher",
     "Idle steadiness has gone from ±{base} to ±{recent} rpm."),
    ("wot_ve", "down", 0.08, False, "trend:ve_down", "Fuel & air", "Engine breathing less air at full throttle",
     "Full-throttle air flow is down from {base_pct} % to {recent_pct} % - check the air filter first."),
    ("cruise_o2s2_sd", "up", 0.07, False, "trend:rear_o2_up", "Emissions", "Catalytic converter losing efficiency",
     "Rear oxygen-sensor swing has grown from ±{base} to ±{recent} V."),
]

MIN_TRIPS = 6


def _series(trips: list[dict], key: str) -> list[tuple[float, float]]:
    out = []
    for t in trips:
        s = t.get("summary") or {}
        v = s.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append((t["start_ts"], float(v)))
    return out


def _fmt(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".") if abs(x) < 10 else f"{x:.1f}".rstrip("0").rstrip(".")


def analyse(trips: list[dict]) -> list[Finding]:
    out: list[Finding] = []
    for key, bad, thresh, rel, tag, system, title, text in TREND_RULES:
        ser = _series(trips, key)
        if len(ser) < MIN_TRIPS:
            continue
        vals = [v for _, v in ser]
        k = max(3, len(vals) // 3)
        base = st.median(vals[:k])
        recent = st.median(vals[-3:])
        change = recent - base
        if rel:
            if base == 0:
                continue
            change = change / abs(base)
        worse = change >= thresh if bad == "up" else change <= -thresh
        if not worse:
            continue
        days = max(1.0, (ser[-1][0] - ser[0][0]) / 86400)
        sev = 2 if abs(change) >= 2 * thresh else 1
        out.append(Finding(
            id=f"trend_{key}_{bad}", tag=tag, system=system, severity=sev, title=title,
            detail=text.format(base=_fmt(base), recent=_fmt(recent), base_pct=round(base * 100), recent_pct=round(recent * 100))
            + f" (over {days:.0f} days, {len(vals)} trips)",
            confidence=0.65 if len(vals) >= 10 else 0.5, source="trend",
            data={"metric": key, "baseline": round(base, 3), "recent": round(recent, 3), "trips": len(vals)},
        ))
    out += _misfire_trends(trips)
    return out


def _misfire_trends(trips: list[dict]) -> list[Finding]:
    """Per-cylinder misfire counts (from the ECU's self-tests) creeping up across trips."""
    per: dict[int, list[float]] = {}
    for t in trips:
        mf = (t.get("summary") or {}).get("misfire") or {}
        for cyl, n in mf.items():
            per.setdefault(int(cyl), []).append(float(n))
    out = []
    for cyl, vals in sorted(per.items()):
        if len(vals) < MIN_TRIPS:
            continue
        k = max(3, len(vals) // 3)
        base, recent = st.median(vals[:k]), st.median(vals[-3:])
        if recent >= 10 and recent >= 3 * (base + 2):
            out.append(Finding(
                id=f"trend_misfire_{cyl}", tag=f"trend:misfire_up_cyl{cyl}", system="Ignition",
                severity=2 if recent >= 40 else 1, title=f"Misfires increasing on cylinder {cyl}",
                detail=f"The engine computer counted about {recent:.0f} misfires per trip on cylinder {cyl} recently, "
                       f"up from {base:.0f}. Other cylinders are clean - a coil or plug on cylinder {cyl} is failing.",
                confidence=0.75, source="trend", data={"baseline": base, "recent": recent, "cylinder": cyl}))
    return out


CHART_METRICS = [
    ("rest_v", "Battery at rest", "V"), ("crank_v", "Lowest cranking voltage", "V"),
    ("run_v", "Charging voltage", "V"), ("idle_trim", "Fuel trim at idle", "%"),
    ("cruise_trim", "Fuel trim at cruise", "%"), ("idle_throttle", "Throttle at idle", "%"),
    ("idle_rpm_sd", "Idle roughness", "± rpm"), ("warmup_min", "Warm-up time", "min"),
    ("coolant_max", "Hottest coolant", "°C"), ("km_per_l", "Fuel economy", "km/L"),
    ("wot_ve", "Full-throttle air flow", "ratio"), ("cruise_o2s2_sd", "Rear O2 swing", "V"),
]


def chart_data(trips: list[dict]) -> dict:
    data = {}
    for key, label, unit in CHART_METRICS:
        ser = _series(trips, key)
        if len(ser) >= 2:
            data[key] = {"label": label, "unit": unit, "points": [[ts, v] for ts, v in ser]}
    per: dict[str, list] = {}
    for t in trips:
        mf = (t.get("summary") or {}).get("misfire") or {}
        for cyl, n in mf.items():
            per.setdefault(str(cyl), []).append([t["start_ts"], n])
    if per:
        data["misfire"] = {"label": "Misfires per trip by cylinder", "unit": "count", "series": per}
    return data
