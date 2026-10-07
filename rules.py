"""Live-data checks that catch problems early - usually before a fault code exists.

A TripAnalyser watches one drive. It sorts samples into driving situations (warm idle,
steady cruise, full throttle...) because the same reading can be normal in one and a
warning sign in another. For example, fuel trims that are lean at idle but normal at
cruise point to an air leak, while lean everywhere points to the air-flow sensor.
"""
from __future__ import annotations

import statistics as st
from dataclasses import asdict, dataclass, field

from .vehicle import baro_from_altitude, fuel_lph_from_maf, volumetric_efficiency

DEFAULT_THRESHOLDS = {
    "coolant_hot_c": 103.0,
    "coolant_critical_c": 110.0,
    "charging_low_v": 13.0,
    "charging_very_low_v": 12.6,
    "charging_high_v": 14.9,
    "charging_very_high_v": 15.2,
    "rest_v_watch": 12.4,
    "rest_v_low": 12.2,
    "rest_v_very_low": 11.9,
    "crank_v_watch": 9.8,
    "crank_v_low": 9.6,
    "crank_v_very_low": 8.8,
    "trim_watch_pct": 8.0,
    "trim_soon_pct": 12.0,
    "trim_urgent_pct": 20.0,
    "idle_rough_watch_rpm": 20.0,
    "idle_rough_soon_rpm": 35.0,
    "ve_watch": 0.76,
    "ve_low": 0.70,
    "rear_o2_sd_watch": 0.13,
    "rear_o2_sd_soon": 0.20,
    "cat_temp_hot_c": 900.0,
    "cvt_temp_hot_c": 110.0,
}


@dataclass
class Finding:
    id: str
    tag: str
    system: str
    severity: int          # 1 watch, 2 fix soon, 3 fix this week, 4 stop driving
    title: str
    detail: str
    confidence: float = 0.7
    source: str = "live"
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _median(xs):
    return st.median(xs) if xs else None


def _robust_sd(xs):
    if len(xs) < 5:
        return None
    m = st.median(xs)
    mad = st.median([abs(x - m) for x in xs])
    return 1.4826 * mad


def _sev(value, watch, soon, urgent=None):
    if urgent is not None and value >= urgent:
        return 3
    if value >= soon:
        return 2
    if value >= watch:
        return 1
    return 0


class TripAnalyser:
    def __init__(self, vehicle: dict, thresholds: dict | None = None):
        self.v = vehicle
        self.th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.disp = float(vehicle.get("displacement_l", 1.5))
        self.default_baro = baro_from_altitude(float(vehicle.get("altitude_m", 1795)))
        self.start_ts = None
        self.last_ts = None
        self.run_start = None
        self.last_moving_ts = None
        self.prev = None
        self.km = 0.0
        self.fuel_l = 0.0
        self.run_s = 0.0
        self.idle_s = 0.0
        self.drive_s = 0.0
        self.pedal_rest = None
        self.throttle_min = None
        self.closed_loop_known = False
        # situation buckets
        self.idle_rpm, self.idle_trim, self.idle_ltft, self.idle_throttle, self.idle_timing = [], [], [], [], []
        self.cruise_trim, self.cruise_ltft, self.cruise_timing, self.cruise_o2s2, self.cruise_o2s1 = [], [], [], [], []
        self.cruise_lambda = []
        self.load_trim = []
        self.wot_ve = []
        self.run_v = []
        self.coolant_max = None
        self.coolant_start = None
        self.warmup_s = None
        self.hot_idle_s = 0.0
        self.hot_moving_s = 0.0
        self.recent_coolant = []  # (t_run, coolant, speed)
        self.cold_cruise_s = 0.0
        self.cold_cruise_min = None
        self.flares = 0
        self._last_flare = -999.0
        self.cat_temp_max = None
        self.cvt_temp_max = None
        self.rest_v = None
        self.rest_v_ign = None
        self.crank_v = None
        self.samples = 0

    # ------------------------------------------------------------ input
    def set_rest_voltage(self, v: float, ignition_on: bool) -> None:
        if v is None:
            return
        if ignition_on:
            self.rest_v_ign = v
        else:
            self.rest_v = v

    def set_crank_voltage(self, v: float) -> None:
        if v is not None:
            self.crank_v = v if self.crank_v is None else min(self.crank_v, v)

    def add(self, ts: float, s: dict) -> None:
        rpm = s.get("rpm")
        if rpm is None:
            return
        self.samples += 1
        if self.start_ts is None:
            self.start_ts = ts
        dt = 0.0 if self.last_ts is None else max(0.0, min(5.0, ts - self.last_ts))
        self.last_ts = ts
        running = rpm > 400
        if not running:
            self.prev = s
            return
        if self.run_start is None:
            self.run_start = ts
        t_run = ts - self.run_start
        self.run_s += dt
        speed = s.get("speed", 0) or 0
        coolant = s.get("coolant")
        stft, ltft = s.get("stft"), s.get("ltft")
        trim = (stft + ltft) if stft is not None and ltft is not None else None
        pedal, throttle = s.get("pedal"), s.get("throttle")
        if pedal is not None:
            self.pedal_rest = pedal if self.pedal_rest is None else min(self.pedal_rest, pedal)
        if throttle is not None:
            self.throttle_min = throttle if self.throttle_min is None else min(self.throttle_min, throttle)
        foot_off = (pedal <= self.pedal_rest + 2) if pedal is not None else \
            (throttle is not None and throttle <= (self.throttle_min or 0) + 2.5)
        if "closed_loop" in s:
            self.closed_loop_known = True
        closed = s.get("closed_loop", t_run > 120 and (coolant or 0) > 60)

        # distance & fuel
        self.km += speed * dt / 3600
        if s.get("maf"):
            self.fuel_l += fuel_lph_from_maf(s["maf"]) * dt / 3600
        if speed > 0:
            self.last_moving_ts = ts
            self.drive_s += dt
        else:
            self.idle_s += dt
        stopped_for = ts - (self.last_moving_ts or self.run_start)

        # coolant tracking
        if coolant is not None:
            if self.coolant_start is None:
                self.coolant_start = coolant
            self.coolant_max = coolant if self.coolant_max is None else max(self.coolant_max, coolant)
            if self.warmup_s is None and self.coolant_start < 45 and coolant >= 75:
                self.warmup_s = t_run
            if coolant >= self.th["coolant_hot_c"]:
                if speed < 15:
                    self.hot_idle_s += dt
                elif speed >= 40:
                    self.hot_moving_s += dt
            if t_run > 900 and speed > 40:
                if coolant < 75:
                    self.cold_cruise_s += dt
                    self.cold_cruise_min = coolant if self.cold_cruise_min is None else min(self.cold_cruise_min, coolant)
            self.recent_coolant.append((t_run, coolant, speed))
            cutoff = t_run - 300
            while self.recent_coolant and self.recent_coolant[0][0] < cutoff:
                self.recent_coolant.pop(0)

        warm = coolant is not None and coolant >= 80
        # warm idle: stopped for 5 s, foot off the pedal
        if warm and speed == 0 and stopped_for >= 5 and foot_off and rpm < 1300:
            self.idle_rpm.append(rpm)
            if trim is not None and closed:
                self.idle_trim.append(trim)
                self.idle_ltft.append(ltft)
            if throttle is not None:
                self.idle_throttle.append(throttle)
            if s.get("timing") is not None:
                self.idle_timing.append(s["timing"])
        # steady cruise
        steady = self.prev is not None and abs(speed - (self.prev.get("speed") or 0)) <= 3
        load = s.get("load")
        if warm and closed and steady and speed >= 40 and 1300 <= rpm <= 3600 and (load is None or 12 <= load <= 70):
            if trim is not None:
                self.cruise_trim.append(trim)
                self.cruise_ltft.append(ltft)
            if s.get("timing") is not None:
                self.cruise_timing.append(s["timing"])
            if s.get("o2s2_v") is not None:
                self.cruise_o2s2.append(s["o2s2_v"])
            if s.get("o2s1_v") is not None:
                self.cruise_o2s1.append(s["o2s1_v"])
            if s.get("o2s1_lambda") is not None:
                self.cruise_lambda.append(s["o2s1_lambda"])
        # higher load, still closed loop
        if warm and closed and trim is not None and load is not None and load >= 50 and (pedal is None or pedal < 80):
            self.load_trim.append(trim)
        # full throttle: volumetric efficiency from the MAF
        wide_open = (pedal is not None and pedal >= 60) or (pedal is None and throttle is not None and throttle >= 60)
        if wide_open and rpm >= 2500 and s.get("maf"):
            ve = volumetric_efficiency(s["maf"], rpm, self.disp, s.get("baro") or self.default_baro,
                                       s.get("iat") if s.get("iat") is not None else 25)
            if ve:
                self.wot_ve.append(ve)
        # charging voltage once the engine has run a minute
        v = s.get("battery")
        if v is not None and t_run > 60:
            self.run_v.append(v)
        # rev flare at steady throttle = possible transmission slip
        if self.prev is not None and dt <= 1.6 and speed >= 15:
            prpm = self.prev.get("rpm") or 0
            ctrl = pedal if pedal is not None else throttle
            pctrl = self.prev.get("pedal") if pedal is not None else self.prev.get("throttle")
            dp = abs((ctrl or 0) - (pctrl or 0))
            if prpm > 800 and rpm - prpm >= 400 and rpm >= prpm * 1.2 and \
                    abs(speed - (self.prev.get("speed") or 0)) <= 2 and dp <= 3 and t_run - self._last_flare > 4:
                self.flares += 1
                self._last_flare = t_run
        if s.get("cat_temp") is not None:
            self.cat_temp_max = max(self.cat_temp_max or 0, s["cat_temp"])
        if s.get("cvt_temp") is not None:
            self.cvt_temp_max = max(self.cvt_temp_max or 0, s["cvt_temp"])
        self.prev = s

    # ------------------------------------------------------------ real-time alerts
    def live_alerts(self, s: dict) -> list[Finding]:
        out = []
        c = s.get("coolant")
        if c is not None and (s.get("rpm") or 0) > 400:
            if c >= self.th["coolant_critical_c"]:
                out.append(Finding("overheat_now", "live:overheat_critical", "Cooling", 4,
                                   f"Engine overheating ({c:.0f} °C)",
                                   "Pull over safely and switch off. Let it cool for 30 minutes. "
                                   "Never open the radiator cap while hot.", 0.95))
            elif c >= self.th["coolant_hot_c"]:
                out.append(Finding("hot_now", "live:hot_now", "Cooling", 3, f"Engine running hot ({c:.0f} °C)",
                                   "Avoid idling in traffic, switch the AC off and watch the gauge.", 0.9))
        if len(self.run_v) >= 15:
            recent = _median(self.run_v[-15:])
            if recent < self.th["charging_very_low_v"]:
                out.append(Finding("not_charging_now", "live:charging_low", "Charging", 3,
                                   f"Battery not charging ({recent:.1f} V)",
                                   "The alternator or its belt isn't keeping up. Turn off AC and lights you don't need "
                                   "and drive to a garage - the car will stop when the battery runs flat.", 0.85))
            elif recent > self.th["charging_very_high_v"]:
                out.append(Finding("overcharging_now", "live:charging_high", "Charging", 3,
                                   f"Charging voltage too high ({recent:.1f} V)",
                                   "The regulator may be overcharging - this damages the battery.", 0.8))
        ct = s.get("cvt_temp")
        if ct is not None and ct >= self.th["cvt_temp_hot_c"] + 8:
            out.append(Finding("cvt_hot_now", "live:cvt_hot", "Transmission", 3, f"CVT fluid hot ({ct:.0f} °C)",
                               "Ease off - avoid hard acceleration and steep climbs until it cools.", 0.6))
        return out

    # ------------------------------------------------------------ findings
    def findings(self) -> list[Finding]:
        th = self.th
        out: list[Finding] = []
        it = _median(self.idle_trim) if len(self.idle_trim) >= 20 else None
        ct = _median(self.cruise_trim) if len(self.cruise_trim) >= 20 else None
        lt = _median(self.load_trim) if len(self.load_trim) >= 10 else None
        w, so, ur = th["trim_watch_pct"], th["trim_soon_pct"], th["trim_urgent_pct"]

        def pct(x):
            return f"{x:+.0f} %"

        if it is not None and it >= w and (ct is None or ct <= it - 6):
            sev = _sev(it, w, so, ur)
            detail = (f"Fuel trims at warm idle are {pct(it)}"
                      + (f" but only {pct(ct)} at steady cruise. " if ct is not None else ". ")
                      + "The computer is adding fuel to make up for extra air that only matters at low air flow "
                        "- the classic sign of an air (vacuum) leak.")
            out.append(Finding("lean_idle_only", "live:lean_idle_only", "Fuel & air", sev,
                               "Lean at idle, normal at speed", detail, 0.8 if ct is not None else 0.55,
                               data={"idle_trim": round(it, 1), "cruise_trim": None if ct is None else round(ct, 1)}))
        elif (it is not None and it >= w and ct is not None and ct >= w) or (it is None and ct is not None and ct >= so):
            worst = max(x for x in (it, ct) if x is not None)
            sev = _sev(worst, w, so, ur)
            out.append(Finding("lean_all", "live:lean_all", "Fuel & air", sev, "Lean at idle and at speed",
                               f"Fuel trims are {pct(it) if it is not None else 'n/a'} at idle and {pct(ct)} at cruise. "
                               "Lean everywhere usually means the air-flow (MAF) sensor is under-reading "
                               "(dust) or fuel pressure is low.", 0.75,
                               data={"idle_trim": None if it is None else round(it, 1), "cruise_trim": round(ct, 1)}))
        if lt is not None and lt >= 10 and lt >= (ct or 0) + 6 and (it is None or it < w):
            out.append(Finding("lean_load", "live:lean_load", "Fuel & air", 2, "Lean only under load",
                               f"Fuel trims rise to {pct(lt)} when the engine works hard but are normal at idle. "
                               "That points to fuel delivery: pump, filter or fuel quality.", 0.65,
                               data={"load_trim": round(lt, 1)}))
        rich = min([x for x in (it, ct) if x is not None], default=None)
        if rich is not None and rich <= -w:
            out.append(Finding("rich", "live:rich", "Fuel & air", _sev(-rich, w, so, ur), "Running rich",
                               f"Fuel trims are {pct(rich)}: the computer is taking fuel away. Possible leaking injector, "
                               "A/F sensor or temperature sensor fault.", 0.7, data={"trim": round(rich, 1)}))

        # idle quality
        if len(self.idle_rpm) >= 20:
            sd = _robust_sd(self.idle_rpm)
            med = _median(self.idle_rpm)
            dips = sum(1 for r in self.idle_rpm if r < med - 80) / len(self.idle_rpm)
            sev = _sev(sd, th["idle_rough_watch_rpm"], th["idle_rough_soon_rpm"])
            if sev == 0 and dips > 0.06:
                sev = 1
            if sev:
                out.append(Finding("rough_idle", "live:rough_idle", "Engine", sev, "Idle is unsteady",
                                   f"Warm idle wanders by about ±{sd:.0f} rpm with {dips * 100:.0f} % sudden dips "
                                   "(a healthy engine holds about ±10). Misfires, an air leak or a dirty throttle body are the usual causes.",
                                   0.7, data={"idle_sd": round(sd, 1), "idle_rpm": round(med)}))
            if med > 950:
                out.append(Finding("idle_high", "live:idle_high", "Engine", 1, "Idle speed high",
                                   f"Warm idle is {med:.0f} rpm (normal is about 650-800; a bit higher with AC on).", 0.5,
                                   data={"idle_rpm": round(med)}))
            elif med < 550:
                out.append(Finding("idle_low", "live:idle_low", "Engine", 1, "Idle speed low",
                                   f"Warm idle is {med:.0f} rpm - the engine may stall at junctions.", 0.5))

        # cooling
        if self.coolant_max is not None and self.coolant_max >= th["coolant_critical_c"]:
            where = "idle" if self.hot_idle_s >= self.hot_moving_s else "speed"
            out.append(Finding("overheat", f"live:overheat_{where}", "Cooling", 4, "Engine overheated this trip",
                               f"Coolant reached {self.coolant_max:.0f} °C, mostly when "
                               f"{'stopped or crawling in traffic' if where == 'idle' else 'driving at speed'}.", 0.9,
                               data={"max": self.coolant_max}))
        elif self.hot_idle_s + self.hot_moving_s >= 30:
            where = "idle" if self.hot_idle_s >= self.hot_moving_s else "speed"
            out.append(Finding("running_hot", f"live:overheat_{where}", "Cooling", 3, "Engine running hot",
                               f"Coolant spent {(self.hot_idle_s + self.hot_moving_s) / 60:.1f} min above "
                               f"{th['coolant_hot_c']:.0f} °C (peak {self.coolant_max:.0f} °C), mostly "
                               f"{'in traffic - suspect the radiator fan' if where == 'idle' else 'at speed - suspect coolant flow or level'}.",
                               0.8, data={"max": self.coolant_max}))
        if self.cold_cruise_s >= 180:
            out.append(Finding("runs_cold", "live:runs_cold", "Cooling", 2, "Engine running too cool",
                               f"After 15 minutes of driving, the coolant stayed below 75 °C for {self.cold_cruise_s / 60:.0f} min "
                               f"at speed, dropping to {self.cold_cruise_min:.0f} °C (normal is 85-95 °C). The thermostat is "
                               "probably stuck open - fuel use goes up and the engine wears faster.",
                               0.8, data={"cold_minutes": round(self.cold_cruise_s / 60, 1)}))
        if self.warmup_s is not None and self.warmup_s > 15 * 60:
            out.append(Finding("slow_warmup", "live:slow_warmup", "Cooling", 1, "Slow to warm up",
                               f"Took {self.warmup_s / 60:.0f} min to reach 75 °C from a cold start (usually under 10).",
                               0.55, data={"minutes": round(self.warmup_s / 60, 1)}))

        # charging and battery
        if len(self.run_v) >= 20:
            med = _median(self.run_v)
            first = _median(self.run_v[: len(self.run_v) // 3])
            last = _median(self.run_v[-len(self.run_v) // 3:])
            falling = f" and falling ({first:.2f} → {last:.2f} V)" if first - last > 0.25 else ""
            if med < th["charging_low_v"]:
                sev = 3 if med < th["charging_very_low_v"] else 2
                out.append(Finding("charging_low", "live:charging_low", "Charging", sev, "Alternator not charging enough",
                                   f"With the engine running the battery reads {med:.2f} V{falling}. It should be 13.8-14.7 V.",
                                   0.85, data={"run_v": round(med, 2)}))
            elif med > th["charging_high_v"]:
                sev = 3 if med > th["charging_very_high_v"] else 2
                out.append(Finding("charging_high", "live:charging_high", "Charging", sev, "Charging voltage too high",
                                   f"Running voltage is {med:.2f} V (should be under 14.8 V). The regulator may be failing.",
                                   0.8, data={"run_v": round(med, 2)}))
        if self.rest_v is not None or self.rest_v_ign is not None:
            if self.rest_v is not None:
                v, off = self.rest_v, 0.0
                how = "with the ignition off"
            else:
                v, off = self.rest_v_ign, 0.3
                how = "with the ignition on (reads a little lower than true resting voltage)"
            sev = 3 if v < th["rest_v_very_low"] - off else 2 if v < th["rest_v_low"] - off else 1 if v < th["rest_v_watch"] - off else 0
            if sev:
                out.append(Finding("battery_low", "live:battery_low", "Charging", sev, "Battery voltage low before starting",
                                   f"Battery read {v:.2f} V {how}. A healthy, charged battery rests at 12.5-12.8 V.",
                                   0.7, data={"rest_v": v}))
        if self.crank_v is not None:
            v = self.crank_v
            sev = 3 if v < th["crank_v_very_low"] else 2 if v < th["crank_v_low"] else 1 if v < th["crank_v_watch"] else 0
            if sev:
                out.append(Finding("crank_weak", "live:crank_weak", "Charging", sev, "Voltage dips hard when starting",
                                   f"Battery dropped to {v:.1f} V while cranking (healthy stays above about 10 V). "
                                   "The battery is losing capacity or a terminal is poor.", 0.75, data={"crank_v": v}))

        # air flow at full throttle
        if len(self.wot_ve) >= 3:
            ve = _median(self.wot_ve)
            if ve < th["ve_watch"]:
                sev = 2 if ve < th["ve_low"] else 1
                out.append(Finding("low_airflow", "live:low_airflow", "Fuel & air", sev, "Less air than expected at full throttle",
                                   f"At full throttle the cylinders filled to about {ve * 100:.0f} % (a healthy engine "
                                   "manages about 80-95 %). Blocked air filter, dirty MAF sensor or a restricted exhaust.",
                                   0.6, data={"ve": round(ve, 3)}))
            elif ve > 1.12:
                out.append(Finding("high_airflow", "live:high_airflow", "Fuel & air", 1, "Air-flow sensor reading high",
                                   f"Calculated cylinder filling is {ve * 100:.0f} % - more than physically likely. "
                                   "The MAF sensor may be over-reading.", 0.5, data={"ve": round(ve, 3)}))

        # catalytic converter via rear oxygen sensor
        if len(self.cruise_o2s2) >= 40:
            sd = st.pstdev(self.cruise_o2s2)
            sev = 2 if sd >= th["rear_o2_sd_soon"] else 1 if sd >= th["rear_o2_sd_watch"] else 0
            if sev:
                out.append(Finding("rear_o2_active", "live:rear_o2_active", "Emissions", sev,
                                   "Rear oxygen sensor copying the front one",
                                   f"At steady cruise the rear O2 sensor swings by ±{sd:.2f} V. Behind a healthy catalytic "
                                   "converter it stays steady (±0.05 V). The converter is storing less oxygen - it's wearing out.",
                                   0.7, data={"sd": round(sd, 3)}))
        if len(self.cruise_o2s1) >= 40 and st.pstdev(self.cruise_o2s1) < 0.05:
            out.append(Finding("o2_stuck", "live:o2_stuck", "Emissions", 1, "Front oxygen sensor not switching",
                               "The front O2 sensor barely moves at cruise - it may be lazy or dead.", 0.55))
        if len(self.cruise_lambda) >= 60:
            ml = _median(self.cruise_lambda)
            if abs(ml - 1) > 0.05:
                out.append(Finding("o2_stuck", "live:o2_stuck", "Emissions", 1, "Front A/F sensor reading off",
                                   f"At cruise the A/F sensor reads λ {ml:.2f} instead of about 1.00.", 0.5))
        if self.cat_temp_max and self.cat_temp_max >= th["cat_temp_hot_c"]:
            out.append(Finding("cat_hot", "live:cat_hot", "Emissions", 2, "Catalytic converter very hot",
                               f"Estimated converter temperature reached {self.cat_temp_max:.0f} °C - misfires or rich "
                               "running can melt it.", 0.6))

        # transmission
        if self.flares:
            sev = 2 if self.flares >= 3 else 1
            out.append(Finding("cvt_flare", "live:cvt_flare", "Transmission", sev, "Engine revs flared without speeding up",
                               f"{self.flares} time(s) this trip the revs jumped 400+ rpm at a steady pedal while road speed "
                               "stayed the same - the gearbox may be slipping.", 0.55 if self.flares < 3 else 0.7,
                               data={"count": self.flares}))
        if self.cvt_temp_max and self.cvt_temp_max >= th["cvt_temp_hot_c"]:
            out.append(Finding("cvt_hot", "live:cvt_hot", "Transmission", 2, "CVT fluid ran hot",
                               f"CVT fluid reached {self.cvt_temp_max:.0f} °C. Heat ages CVT fluid fast.", 0.5))
        return out

    # ------------------------------------------------------------ summary for trends
    def summary(self) -> dict:
        def r(x, n=2):
            return None if x is None else round(x, n)

        dur = (self.last_ts - self.start_ts) / 60 if self.start_ts and self.last_ts else 0
        return {
            "km": round(self.km, 2), "fuel_l": round(self.fuel_l, 3),
            "km_per_l": round(self.km / self.fuel_l, 1) if self.fuel_l > 0.05 and self.km > 1 else None,
            "duration_min": round(dur, 1),
            "avg_speed": round(self.km / (self.run_s / 3600), 1) if self.run_s > 60 else None,
            "idle_share": round(self.idle_s / self.run_s, 2) if self.run_s > 0 else None,
            "idle_rpm": r(_median(self.idle_rpm), 0) if len(self.idle_rpm) >= 10 else None,
            "idle_rpm_sd": r(_robust_sd(self.idle_rpm), 1) if len(self.idle_rpm) >= 20 else None,
            "idle_trim": r(_median(self.idle_trim), 1) if len(self.idle_trim) >= 20 else None,
            "idle_ltft": r(_median(self.idle_ltft), 1) if len(self.idle_ltft) >= 20 else None,
            "idle_throttle": r(_median(self.idle_throttle), 1) if len(self.idle_throttle) >= 20 else None,
            "cruise_trim": r(_median(self.cruise_trim), 1) if len(self.cruise_trim) >= 20 else None,
            "cruise_ltft": r(_median(self.cruise_ltft), 1) if len(self.cruise_ltft) >= 20 else None,
            "load_trim": r(_median(self.load_trim), 1) if len(self.load_trim) >= 10 else None,
            "cruise_timing": r(_median(self.cruise_timing), 1) if len(self.cruise_timing) >= 20 else None,
            "cruise_o2s2_sd": r(st.pstdev(self.cruise_o2s2), 3) if len(self.cruise_o2s2) >= 40 else None,
            "wot_ve": r(_median(self.wot_ve), 3) if len(self.wot_ve) >= 3 else None,
            "coolant_max": self.coolant_max,
            "cold_start": self.coolant_start is not None and self.coolant_start < 45,
            "warmup_min": r(self.warmup_s / 60, 1) if self.warmup_s else None,
            "run_v": r(_median(self.run_v), 2) if len(self.run_v) >= 20 else None,
            "rest_v": self.rest_v, "rest_v_ign": self.rest_v_ign, "crank_v": self.crank_v,
            "flares": self.flares,
            "cat_temp_max": self.cat_temp_max, "cvt_temp_max": self.cvt_temp_max,
        }
