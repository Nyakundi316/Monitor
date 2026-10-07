"""Built-in Toyota Allion simulator.

A small physics model of a 260-series Allion (1NZ-FE, Super CVT-i) at Nairobi altitude,
wrapped in an ELM327 emulator that answers exactly like a real adapter. The monitor talks
to it through the same driver code it uses for the real car, so a demo exercises the
whole system end to end.

Faults can be switched on per scenario (vacuum leak, dirty MAF sensor, failing coil...)
and the 'demo' scenario ages the car gradually over several simulated weeks.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field, replace

from .dtc import encode_dtc
from .vehicle import air_density, baro_from_altitude

# --------------------------------------------------------------------------- clocks


class RealClock:
    manual = False

    def now(self) -> float:
        return time.time()

    def sleep(self, dt: float) -> None:
        time.sleep(dt)


class SimClock:
    """Simulated time. speed=20 runs a 30-minute drive in 90 seconds.
    manual=True never sleeps - time only moves when sleep() is called (for tests/seeding)."""

    def __init__(self, speed: float = 1.0, start: float | None = None, manual: bool = False):
        self.speed = max(0.01, speed)
        self.manual = manual
        self._base = start if start is not None else time.time()
        self._wall0 = time.monotonic()
        self._t = self._base

    def now(self) -> float:
        if self.manual:
            return self._t
        return self._base + (time.monotonic() - self._wall0) * self.speed

    def sleep(self, dt: float) -> None:
        if self.manual:
            self._t += dt
        else:
            time.sleep(dt / self.speed)

    def jump(self, seconds: float) -> None:
        """Skip ahead (e.g. the car parked overnight)."""
        if self.manual:
            self._t += seconds
        else:
            self._base += seconds


# --------------------------------------------------------------------------- scenarios


@dataclass
class Scenario:
    name: str
    title: str
    description: str
    vacuum_leak_gs: float = 0.0          # unmetered air entering after the MAF sensor (g/s)
    maf_scale: float = 1.0               # < 1: dirty MAF sensor under-reads air flow
    thermostat_min_open: float = 0.0     # > 0: thermostat stuck partly open
    fan_failed: bool = False
    battery_health: float = 1.0          # 1 = new, 0.4 = nearly dead
    alternator_v: float = 14.25
    alternator_fade_v_per_min: float = 0.0
    misfire_cyl: int = 0                 # 0 = none
    misfire_per_1000: float = 0.0        # misfires per 1000 engine revolutions on that cylinder
    cat_efficiency: float = 1.0          # 1 = healthy converter
    cvt_flares_per_10min: float = 0.0
    codes_cleared: bool = False
    air_filter_restriction: float = 0.0  # fraction of full-throttle air flow lost
    idle_throttle_extra: float = 0.0     # carbon in the throttle body (% extra opening)
    ambient_c: float = 22.0
    ac_on: bool = False
    initial_codes: list = field(default_factory=list)   # [(code, kind, ecu)]
    demo_ageing: bool = False


SCENARIOS: dict[str, Scenario] = {s.name: s for s in [
    Scenario("healthy", "Healthy car", "Everything working as it should."),
    Scenario("vacuum_leak", "Vacuum (air) leak", "A cracked hose lets unmeasured air in. Lean at idle, normal at speed.",
             vacuum_leak_gs=0.48),
    Scenario("dirty_maf", "Dirty MAF sensor", "Dust on the air-flow sensor makes it under-read. Lean everywhere, weak at full throttle.",
             maf_scale=0.78),
    Scenario("thermostat", "Thermostat stuck open", "Engine warms slowly and runs cold on the highway.",
             thermostat_min_open=0.7),
    Scenario("fan_failure", "Radiator fan not working", "Fine on the highway, overheats in traffic.",
             fan_failed=True, ambient_c=28.0, ac_on=True),
    Scenario("weak_battery", "Weak battery", "Low resting voltage and a deep dip when cranking.",
             battery_health=0.45),
    Scenario("alternator", "Alternator failing", "Charging voltage too low and dropping while driving.",
             alternator_v=13.05, alternator_fade_v_per_min=0.025, battery_health=0.85),
    Scenario("misfire", "Ignition coil failing (cylinder 3)", "Cylinder 3 misfires; rough idle; P0303 appears.",
             misfire_cyl=3, misfire_per_1000=30.0),
    Scenario("catalyst", "Catalytic converter wearing out", "Rear oxygen sensor copies the front one; P0420 on the way.",
             cat_efficiency=0.32),
    Scenario("cvt_slip", "CVT slipping", "Engine revs flare without the car speeding up.",
             cvt_flares_per_10min=4.0),
    Scenario("codes_cleared", "Codes recently cleared", "Someone cleared the codes (e.g. before selling). A vacuum leak is hiding.",
             codes_cleared=True, vacuum_leak_gs=0.35),
    Scenario("demo", "Ageing Allion (demo)", "Weeks of history: battery ageing, an air leak developing and a coil starting to fail.",
             demo_ageing=True),
]}


def aged_scenario(trip_index: int, total: int) -> Scenario:
    """'demo' scenario for trip k of n: the car slowly develops faults."""
    k = trip_index / max(1, total - 1)  # 0 .. 1
    return replace(
        SCENARIOS["demo"],
        battery_health=round(1.0 - 0.52 * k, 3),
        vacuum_leak_gs=round(max(0.0, (k - 0.35) * 0.62), 3),
        misfire_cyl=3 if k > 0.55 else 0,
        misfire_per_1000=round(max(0.0, (k - 0.55) * 6.5), 2),
        air_filter_restriction=round(0.05 + 0.10 * k, 3),
        idle_throttle_extra=round(1.6 * k, 2),
    )


# --------------------------------------------------------------------------- drive script

ROUTE = [  # (kind, seconds, target km/h) - a Nairobi commute
    ("off", 4, 0), ("ign", 6, 0), ("crank", 1.2, 0), ("idle", 70, 0),
    ("traffic", 330, 24), ("cruise", 200, 50), ("cruise", 110, 80), ("pull", 9, 108),
    ("cruise", 280, 95), ("cruise", 110, 60), ("traffic", 320, 14), ("cruise", 160, 45),
    ("idle", 35, 0), ("ign", 14, 0), ("off", 30, 0),
]


class DriveScript:
    def __init__(self, route=ROUTE, seed: int = 1):
        self.route = route
        self.rng = random.Random(seed)
        self.starts = []
        t = 0.0
        for seg in route:
            self.starts.append(t)
            t += seg[1]
        self.duration = t
        # Pre-generate stop/go pattern for traffic segments.
        self.traffic = {}
        for i, (kind, dur, v) in enumerate(route):
            if kind == "traffic":
                pts, tt = [], 0.0
                while tt < dur:
                    go = self.rng.uniform(14, 34)
                    stop = self.rng.uniform(8, 26)
                    pts.append((tt, tt + go, self.rng.uniform(0.55, 1.0) * v))
                    tt += go + stop
                self.traffic[i] = pts

    def at(self, t: float) -> tuple[str, float, bool]:
        """(state, target speed, full throttle?) at t seconds into the trip."""
        t = t % self.duration
        idx = max(i for i, s in enumerate(self.starts) if s <= t)
        kind, dur, v = self.route[idx]
        local = t - self.starts[idx]
        if kind in ("off", "ign", "crank"):
            return kind, 0.0, False
        if kind == "idle":
            return "run", 0.0, False
        if kind == "traffic":
            for a, b, vv in self.traffic[idx]:
                if a <= local < b:
                    return "run", vv, False
            return "run", 0.0, False
        if kind == "pull":
            return "run", v, True
        # cruise with gentle variation
        return "run", v + 4 * math.sin(local / 23.0), False


# --------------------------------------------------------------------------- car model

SIM_SUPPORTED = {
    0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x13, 0x15, 0x1C,
    0x1F, 0x20, 0x21, 0x2E, 0x30, 0x31, 0x33, 0x34, 0x3C, 0x40, 0x41, 0x42, 0x43, 0x44, 0x45,
    0x47, 0x49, 0x4A, 0x4C, 0x4D, 0x4E,
}
MODE06_SUPPORT = {0x00: {0x01, 0x02, 0x20}, 0x20: {0x21, 0x40}, 0x40: {0x41, 0x42, 0x60},
                  0x60: {0x80}, 0x80: {0xA0}, 0xA0: {0xA1, 0xA2, 0xA3, 0xA4, 0xA5}}


def _bitmask(pids: set[int], base: int) -> bytes:
    bits = 0
    for p in pids:
        if base < p <= base + 32:
            bits |= 1 << (32 - (p - base))
    return bits.to_bytes(4, "big")


class SimCar:
    STEP = 0.2  # integration step, seconds

    def __init__(self, scenario: Scenario, clock, vehicle: dict | None = None, seed: int = 7,
                 route=ROUTE):
        self.sc = scenario
        self.clock = clock
        self.vehicle = vehicle or {}
        self.disp = float(self.vehicle.get("displacement_l", 1.5))
        self.baro = baro_from_altitude(float(self.vehicle.get("altitude_m", 1795)))
        self.rng = random.Random(seed)
        self.script = DriveScript(route, seed)
        self.t0 = clock.now()
        self.last = self.t0
        # engine / body state
        self.state = "off"
        self.speed = 0.0
        self.rpm = 0.0
        self.rpm_reported = 0.0
        self.maf = self.maf_actual = 0.0
        self.ve = 0.25
        self.ltft = 0.0
        self.demand = 0.0
        self.opening = 0.0
        self.coolant = scenario.ambient_c
        self.iat = scenario.ambient_c + 4
        self.ltft_idle = 0.0
        self.ltft_part = 0.0
        self.stft = 0.0
        self.lam = 1.0
        self.closed_loop = False
        self.fan = False
        self.running_s = 0.0
        self.trip_s = 0.0
        self.crank_min_v = 99.0
        self.battery_v = self._rest_v()
        self.flare_left = 0.0
        self.o2s2 = 0.6
        self.cat_temp = scenario.ambient_c
        self.cvt_temp = scenario.ambient_c
        self.fuel_level = 64.0
        self.distance_since_clear = 6.0 if scenario.codes_cleared else 3480.0
        self.minutes_since_clear = 25.0 if scenario.codes_cleared else 9800.0
        self.warmups_since_clear = 1 if scenario.codes_cleared else 180
        self.mil_distance = 0.0
        self.mil_minutes = 0.0
        self.trip_index = 0
        # diagnostics
        self.codes = {"7E8": {"stored": [], "pending": []}, "7E9": {"stored": [], "pending": []}}
        self.freeze: dict[int, bytes] = {}
        self.freeze_code: str | None = None
        self.prev_pending: set[str] = set()
        self.trip_pending: set[str] = set()
        self.lean_s = 0.0
        self.cold_s = 0.0
        self.flares = 0
        self.misfires = {c: 0.0 for c in range(1, 5)}
        self.misfire_ewma = {c: 0.0 for c in range(1, 5)}
        self.monitors = {"catalyst": True, "evap": True, "o2_sensor": True, "o2_heater": True}
        self.cruise_s = 0.0
        for code, kind, ecu in scenario.initial_codes:
            self.codes[ecu][kind].append(code)
        if scenario.codes_cleared:
            self.monitors = {k: False for k in self.monitors}
        self._phase_t = 0.0

    # ---------------------------------------------------------------- helpers
    @property
    def mil(self) -> bool:
        return any(self.codes[e]["stored"] for e in self.codes)

    def _rest_v(self) -> float:
        return 12.72 - (1 - self.sc.battery_health) * 1.05

    def set_scenario(self, scenario: Scenario) -> None:
        self.sc = scenario

    def ignition_on(self) -> bool:
        self.update()
        return self.state != "off"

    # ---------------------------------------------------------------- physics
    def update(self) -> None:
        now = self.clock.now()
        gap = now - self.last
        if gap > 900:  # parked for a while: the engine cools and the battery settles
            self._soak(gap)
            self.last = now - self.STEP
            gap = self.STEP
        steps = int(gap / self.STEP)
        for _ in range(min(steps, 20000)):
            self._step(self.STEP)
        self.last += steps * self.STEP

    def _soak(self, seconds: float) -> None:
        k = 1 - math.exp(-seconds / 3600)
        self.coolant += (self.sc.ambient_c - self.coolant) * k
        self.cvt_temp += (self.sc.ambient_c - self.cvt_temp) * k
        self.cat_temp = self.sc.ambient_c
        self.battery_v = self._rest_v()

    def _phase(self) -> tuple[str, float, bool]:
        return self.script.at(self._phase_t)

    def _step(self, dt: float) -> None:
        sc, rng = self.sc, self.rng
        prev_state = self.state
        kind, target, wot = self._phase()
        self._phase_t += dt
        state = {"off": "off", "ign": "ign", "crank": "crank"}.get(kind, "run")
        if prev_state != "off" and state == "off":
            self._end_trip()
        if prev_state == "off" and state != "off":
            self._start_trip()
        if prev_state != "run" and state == "run":
            self.running_s = 0.0
        self.state = state

        amb = sc.ambient_c
        rho = air_density(self.baro, self.iat)

        if state in ("off", "ign"):
            self.rpm = 0.0
            self.speed = 0.0
            self.demand = 0.0
            self.closed_loop = False
            self.stft = 0.0
            rest = self._rest_v()
            target_v = rest if state == "off" else rest - 0.32
            self.battery_v += (target_v - self.battery_v) * min(1.0, dt / (2 if state == 'ign' else 20))
            self.coolant += (amb - self.coolant) * dt / 3600
            self.cat_temp += (amb - self.cat_temp) * dt / 900
            self.opening = 0.0
            return

        if state == "crank":
            self.rpm = 150 + 90 * sc.battery_health + rng.uniform(-15, 15)
            dip = 10.35 - (1 - sc.battery_health) * 2.6 + rng.uniform(-0.1, 0.1)
            self.battery_v = dip
            self.crank_min_v = min(self.crank_min_v, dip)
            return

        # ---------------- running
        self.running_s += dt
        self.trip_s += dt
        # driver: speed controller
        cruise_dem = 6 + 0.17 * self.speed
        if wot:
            dem = 100.0
        elif target < self.speed - 1.5:
            dem = 0.0
        else:
            dem = cruise_dem + max(-cruise_dem, min(42.0, 2.2 * (target - self.speed)))
        self.demand += (dem - self.demand) * min(1.0, dt / 0.5)
        if target < self.speed - 1.5 and not wot:
            self.speed = max(0.0, self.speed - 2.6 * dt * (1 + self.speed / 80))
        else:
            self.speed = max(0.0, self.speed + (self.demand - cruise_dem) * 0.12 * dt)
        if target == 0 and self.speed < 3:
            self.speed = max(0.0, self.speed - 4 * dt)
            self.demand = 0.0

        # throttle opening: ETCS keeps a small idle opening; carbon needs more
        idle_open = 2.4 + sc.idle_throttle_extra + max(0, 60 - self.coolant) * 0.03 + (0.6 if sc.ac_on else 0)
        self.opening = max(idle_open, self.demand)

        # engine speed: CVT holds revs according to demand
        warm_idle = 700 + (60 if sc.vacuum_leak_gs > 0.3 else 0)
        idle_rpm = warm_idle + max(0.0, 70 - self.coolant) / 50 * 430
        if self.speed < 2 and self.demand < 2:
            target_rpm = idle_rpm
        else:
            target_rpm = min(5800.0, max(1050.0, 1000 + 8.0 * self.speed + 28 * self.demand))
        if self.running_s < 2.0:
            target_rpm = 1450
        # CVT flare (slip)
        if sc.cvt_flares_per_10min and self.flare_left <= 0 and 30 < self.speed < 95 and not wot:
            if rng.random() < sc.cvt_flares_per_10min / 600 * dt:
                self.flare_left = 1.6
                self.flares += 1
        flare = 0.0
        if self.flare_left > 0:
            flare = 750
            self.flare_left -= dt
        self.rpm += (target_rpm + flare - self.rpm) * min(1.0, dt / (0.25 if flare else 0.6))
        # idle roughness, worse with a misfire or air leak
        rough = 6 + sc.misfire_per_1000 * (2.2 if self.speed < 2 else 0.5) + sc.vacuum_leak_gs * 18
        rpm_out = self.rpm + rng.gauss(0, rough)
        if sc.misfire_cyl and self.speed < 2 and rng.random() < sc.misfire_per_1000 / 400:
            rpm_out -= rng.uniform(60, 140)
        self.rpm_reported = max(0.0, rpm_out)

        # air: volumetric efficiency from throttle opening
        ve = 0.18 + 0.72 * (self.opening / 100) ** 0.7
        if self.opening > 80:
            ve *= 1 - sc.air_filter_restriction
        ve *= 1 - 0.04 * max(0, (self.rpm - 4500) / 1300)
        metered = self.rpm * self.disp * rho * ve / 120
        actual_air = metered + sc.vacuum_leak_gs
        self.maf_actual = metered
        self.maf = metered * sc.maf_scale
        self.ve = ve

        # fuel control
        warm = self.coolant >= 40 and self.running_s > 25
        decel = self.speed > 15 and self.demand < 0.5
        power = self.demand > 88
        self.closed_loop = warm and not decel and not power
        needed = actual_air / max(0.01, self.maf)  # (1 + total trim) the ECU needs
        cell_idle = self.maf < 4.5
        ltft = self.ltft_idle if cell_idle else self.ltft_part
        if self.closed_loop:
            req = (needed - 1) * 100
            if self.coolant > 70:
                rate = dt / 45
                if cell_idle:
                    self.ltft_idle += (req - self.ltft_idle) * rate
                else:
                    self.ltft_part += (req - self.ltft_part) * rate
                ltft = self.ltft_idle if cell_idle else self.ltft_part
            osc = 1.8 * math.sin(self.trip_s * 2.7) + rng.gauss(0, 0.8)
            self.stft = max(-25.0, min(25.0, req - ltft + osc))
            self.lam = 1.0 + rng.gauss(0, 0.006)
        else:
            self.stft = 0.0
            self.lam = 0.86 if power else (2.0 if decel else 0.97)
        self.ltft = max(-25.0, min(25.0, ltft))

        # lean-for-long -> P0171 (Toyota triggers on total trim around +25-35 %)
        total = self.stft + self.ltft
        self.lean_s = self.lean_s + dt if (self.closed_loop and total > 24) else max(0.0, self.lean_s - dt * 0.2)
        if self.lean_s > 120:
            self._pending("P0171")

        # coolant temperature
        load = self.load()
        q_in = 0.05 + 0.003 * load + (0.03 if sc.ac_on else 0)
        valve = max(sc.thermostat_min_open, min(1.0, (self.coolant - 84) / 10))
        if self.coolant >= 97 and not sc.fan_failed:
            self.fan = True
        if self.coolant <= 92.5:
            self.fan = False
        airflow = 0.05 + self.speed / 70 + (1.0 if self.fan else 0.0)
        q_out = (self.coolant - amb) * (0.0006 + 0.004 * valve * airflow)
        self.coolant = min(121.0, self.coolant + (q_in - q_out) * dt)
        if self.running_s > 900 and self.coolant < 75:
            self.cold_s += dt
            if self.cold_s > 120:
                self._pending("P0128")

        # intake air: heat soak at idle, cooled when moving
        target_iat = amb + 5 + (9 if self.speed < 5 else 0) - min(4, self.speed / 25)
        self.iat += (target_iat - self.iat) * dt / 90

        # charging system
        alt = sc.alternator_v - sc.alternator_fade_v_per_min * self.running_s / 60
        if self.running_s < 60:
            alt += 0.25 * (1 - self.running_s / 60)  # recharging after the start
        floor = self._rest_v() - 0.25 - 0.0004 * self.running_s
        target_v = max(alt, floor) if alt < self._rest_v() else alt
        self.battery_v += (target_v - self.battery_v) * min(1.0, dt / 4) + rng.gauss(0, 0.004)

        # catalyst and oxygen sensors
        self.cat_temp += (420 + load * 5.6 + sc.misfire_per_1000 * 3 - self.cat_temp) * dt / 40
        eff = sc.cat_efficiency
        if not self.closed_loop:
            o2_target = 0.06 if decel else 0.82
        else:
            swing = (1 - eff) * 0.42 * math.sin(self.trip_s * 2 * math.pi / 2.1)
            o2_target = 0.64 - (1 - eff) * 0.18 + swing + 0.03 * math.sin(self.trip_s / 37)
        self.o2s2 += (o2_target - self.o2s2) * min(1.0, dt / (0.25 if eff < 0.6 else 3.0))
        self.o2s2 = max(0.02, min(0.95, self.o2s2 + rng.gauss(0, 0.006)))

        # misfire counting (Mode 06) and codes
        if sc.misfire_cyl and sc.misfire_per_1000 > 0:
            revs = self.rpm / 60 * dt
            factor = 1.4 if self.speed < 2 else (0.7 + load / 100)
            self.misfires[sc.misfire_cyl] += revs / 1000 * sc.misfire_per_1000 * factor
            m = self.misfires[sc.misfire_cyl]
            if m > 120:
                self._pending(f"P030{sc.misfire_cyl}")
            if m > 600:
                self._store(f"P030{sc.misfire_cyl}", "7E8")
        for c in self.misfires:
            if c != sc.misfire_cyl and rng.random() < 0.0004 * dt:
                self.misfires[c] += 1

        # catalyst monitor result + code
        if self.speed > 50 and self.closed_loop and self.coolant > 75:
            self.cruise_s += dt
            if self.cruise_s > 180:
                self.monitors["catalyst"] = True
                if eff < 0.4:
                    self._pending("P0420")
        if self.running_s > 120 and self.coolant > 60:
            self.monitors["o2_heater"] = True
        if self.running_s > 400 and self.closed_loop:
            self.monitors["o2_sensor"] = True

        # CVT: torque-converter lock-up complaint after repeated flares
        if self.flares >= 4:
            self._pending("P0741", ecu="7E9")
        self.cvt_temp += (amb + 45 + load * 0.35 + (12 if self.speed < 10 else 0) - self.cvt_temp) * dt / 300

        # odometer-type counters
        km = self.speed * dt / 3600
        self.distance_since_clear += km
        self.minutes_since_clear += dt / 60
        if self.mil:
            self.mil_distance += km
            self.mil_minutes += dt / 60
        self.fuel_level = max(5.0, self.fuel_level - self.maf * dt / 14.7 / 745 / 40 * 100)

    def load(self) -> float:
        if self.state != "run":
            return 0.0
        return max(0.0, min(100.0, getattr(self, "ve", 0.25) * 100 * self.baro / 101.3 * 1.04))

    def _start_trip(self) -> None:
        self.trip_index += 1
        self.running_s = 0.0
        self.trip_s = 0.0
        self.crank_min_v = 99.0
        self.lean_s = self.cold_s = self.cruise_s = 0.0
        self.flares = 0
        self.trip_pending = set()
        self.misfires = {c: 0.0 for c in self.misfires}
        self._phase_t = self._phase_t % self.script.duration

    def _end_trip(self) -> None:
        # Two-trip logic: a code pending on two drives in a row becomes stored (MIL on).
        for code in list(self.trip_pending):
            if code in self.prev_pending:
                ecu = "7E9" if code.startswith("P07") else "7E8"
                self._store(code, ecu)
        self.prev_pending = set(self.trip_pending)
        for c in self.misfire_ewma:
            self.misfire_ewma[c] = 0.9 * self.misfire_ewma[c] + 0.1 * self.misfires[c]
        self.warmups_since_clear = min(255, self.warmups_since_clear + 1)

    def _pending(self, code: str, ecu: str = "7E8") -> None:
        self.trip_pending.add(code)
        lst = self.codes[ecu]["pending"]
        if code not in lst:
            lst.append(code)

    def _store(self, code: str, ecu: str) -> None:
        lst = self.codes[ecu]["stored"]
        if code not in lst:
            if not self.freeze:
                self.freeze_code = code
                self.freeze = {pid: self.pid_bytes(pid) for pid in (0x03, 0x04, 0x05, 0x06, 0x07, 0x0C,
                                                                      0x0D, 0x0E, 0x0F, 0x10, 0x11)}
            lst.append(code)

    def clear_codes(self) -> None:
        for e in self.codes.values():
            e["stored"].clear()
            e["pending"].clear()
        self.freeze, self.freeze_code = {}, None
        self.prev_pending, self.trip_pending = set(), set()
        self.monitors = {k: False for k in self.monitors}
        self.distance_since_clear = 0.0
        self.minutes_since_clear = 0.0
        self.warmups_since_clear = 0
        self.mil_distance = self.mil_minutes = 0.0
        self.misfire_ewma = {c: 0.0 for c in self.misfire_ewma}

    # ---------------------------------------------------------------- OBD data
    def pid_bytes(self, pid: int) -> bytes | None:
        s = self
        run = self.state == "run"
        rpm = self.rpm_reported if run else (self.rpm if self.state == "crank" else 0.0)
        tps = 14.5 + 0.62 * s.opening if run else 14.5

        def b1(x):
            return bytes([max(0, min(255, int(round(x))))])

        def b2(x):
            x = max(0, min(65535, int(round(x))))
            return bytes([x >> 8, x & 0xFF])

        if pid in (0x00, 0x20, 0x40):
            return _bitmask(SIM_SUPPORTED, pid)
        if pid == 0x01:
            a = (0x80 if s.mil else 0) | min(127, sum(len(e["stored"]) for e in s.codes.values()))
            b = 0x07  # misfire, fuel, components supported and complete
            c = 0x01 | 0x04 | 0x20 | 0x40  # catalyst, evap, O2 sensor, O2 heater
            d = (0 if s.monitors["catalyst"] else 0x01) | (0 if s.monitors["evap"] else 0x04) \
                | (0 if s.monitors["o2_sensor"] else 0x20) | (0 if s.monitors["o2_heater"] else 0x40)
            return bytes([a, b, c, d])
        if pid == 0x03:
            if not run:
                return bytes([0, 0])
            if s.closed_loop:
                return bytes([2, 0])
            return bytes([1 if s.coolant < 40 else 4, 0])
        if pid == 0x04:
            return b1(s.load() * 255 / 100)
        if pid == 0x05:
            return b1(s.coolant + 40)
        if pid == 0x06:
            return b1(s.stft * 128 / 100 + 128)
        if pid == 0x07:
            return b1((s.ltft if run else s.ltft_idle) * 128 / 100 + 128)
        if pid == 0x0C:
            return b2(rpm * 4)
        if pid == 0x0D:
            return b1(s.speed)
        if pid == 0x0E:
            if not run:
                return b1(128 + 10 * 2)
            adv = 9 + self.rng.uniform(-1.5, 1.5) if s.speed < 2 and s.demand < 2 else \
                (18 if s.demand > 85 else 26 + 8 * (1 - s.load() / 100))
            return b1((adv + 64) * 2)
        if pid == 0x0F:
            return b1(s.iat + 40)
        if pid == 0x10:
            return b2((s.maf if run else 0) * 100)
        if pid == 0x11:
            return b1(tps * 255 / 100)
        if pid == 0x13:
            return bytes([0x03])
        if pid == 0x15:
            return bytes([int(round((s.o2s2 if run else 0.45) * 200)), 0xFF])
        if pid == 0x1C:
            return bytes([11])
        if pid == 0x1F:
            return b2(s.running_s if run else 0)
        if pid == 0x21:
            return b2(s.mil_distance)
        if pid == 0x2E:
            return b1(20 * 255 / 100 if run and s.coolant > 70 else 0)
        if pid == 0x30:
            return b1(s.warmups_since_clear)
        if pid == 0x31:
            return b2(s.distance_since_clear)
        if pid == 0x33:
            return b1(s.baro)
        if pid == 0x34:
            lam = s.lam if run else 1.0
            ma = max(-8, min(8, (lam - 1) * 4))
            r = int(round(lam * 65536 / 2))
            m = int(round((ma + 128) * 256))
            return bytes([min(255, r >> 8), r & 0xFF, m >> 8, m & 0xFF])
        if pid == 0x3C:
            return b2((s.cat_temp + 40) * 10)
        if pid == 0x41:
            return bytes([0, 0x07, 0x65, 0x00])
        if pid == 0x42:
            return b2((s.battery_v - 0.12) * 1000)
        if pid == 0x43:
            return b2(s.load() * 255 / 100 * 1.02)
        if pid == 0x44:
            return b2((0.86 if s.demand > 88 else 1.0) * 65536 / 2)
        if pid == 0x45:
            return b1(s.opening * 255 / 100 if run else 0)
        if pid == 0x47:
            return b1((tps + 16) * 255 / 100)
        if pid == 0x49:
            return b1((15 + 0.65 * s.demand) * 255 / 100)
        if pid == 0x4A:
            return b1((31 + 0.65 * s.demand) * 255 / 100)
        if pid == 0x4C:
            return b1(s.opening * 255 / 100 if run else 0)
        if pid == 0x4D:
            return b2(s.mil_minutes)
        if pid == 0x4E:
            return b2(s.minutes_since_clear)
        return None

    def mode06(self, mid: int) -> list[tuple[int, int, int, int, int]]:
        """[(tid, uasid, value, min, max)] for one test group."""
        s = self
        if mid == 0x01:
            return [(0x91, 0x0B, 410, 240, 700), (0x92, 0x0B, 365, 240, 700)]
        if mid == 0x02:
            return [(0x07, 0x0A, int(s.o2s2 * 1000 / 0.122), 0, int(980 / 0.122))]
        if mid == 0x21:
            val = max(1, int(980 * s.sc.cat_efficiency + self.rng.uniform(-15, 15)))
            return [(0xA9, 0x01, val, 330, 0xFFFF)]
        if mid in (0x41, 0x42):
            return [(0x91, 0x14, 3 if mid == 0x41 else 6, 0, 12)]
        if mid == 0xA1:
            return [(0x0B, 0x24, int(sum(s.misfire_ewma.values())), 0, 0xFFFF),
                    (0x0C, 0x24, int(sum(s.misfires.values())), 0, 0xFFFF)]
        if 0xA2 <= mid <= 0xA5:
            c = mid - 0xA1
            return [(0x0B, 0x24, int(s.misfire_ewma[c]), 0, 0xFFFF),
                    (0x0C, 0x24, int(s.misfires[c]), 0, 0xFFFF)]
        return []


# --------------------------------------------------------------------------- ELM327 emulator


class ELMEmulator:
    """Answers AT and OBD commands the way an ELM327 v1.5 does."""

    def __init__(self, car: SimCar, protocol: str = "6"):
        self.car = car
        self.scenario = car.sc
        self.sim_protocol = protocol
        self.echo = True
        self.headers = False
        self.spaces = True
        self.linefeeds = False
        self.protocol_set = "0"
        self.connected = False
        self.header = None
        self.pad = True

    def handle(self, text: str) -> str:
        out = []
        for raw in text.replace("\n", "\r").split("\r"):
            cmd = raw.strip().upper().replace(" ", "")
            if not cmd:
                continue
            reply = self._command(cmd)
            nl = "\r\n" if self.linefeeds else "\r"
            echo = cmd + nl if self.echo else ""
            out.append(echo + reply.replace("\r", nl) + nl + nl + ">")
        return "".join(out)

    # ------------------------------------------------------------ AT commands
    def _command(self, cmd: str) -> str:
        if cmd.startswith("AT"):
            return self._at(cmd[2:])
        if not all(c in "0123456789ABCDEF" for c in cmd) or len(cmd) < 2:
            return "?"
        return self._obd(cmd)

    def _at(self, a: str) -> str:
        if a == "Z":
            self.__init__(self.car, self.sim_protocol)
            return "\r\rELM327 v1.5"
        if a in ("I",):
            return "ELM327 v1.5"
        if a == "@1":
            return "OBDII to RS232 Interpreter"
        if a in ("E0", "E1"):
            self.echo = a == "E1"
            return "OK"
        if a in ("H0", "H1"):
            self.headers = a == "H1"
            return "OK"
        if a in ("S0", "S1"):
            self.spaces = a == "S1"
            return "OK"
        if a in ("L0", "L1"):
            self.linefeeds = a == "L1"
            return "OK"
        if a.startswith("SP") or a.startswith("TP"):
            self.protocol_set = a[2:] or "0"
            self.connected = False
            return "OK"
        if a == "DPN":
            if not self.connected:
                return "A0" if self.protocol_set in ("0", "A0") else self.protocol_set
            return ("A" if self.protocol_set == "0" else "") + self.sim_protocol
        if a == "DP":
            return "AUTO, ISO 15765-4 (CAN 11/500)" if self.sim_protocol == "6" else "AUTO, ISO 9141-2"
        if a == "RV":
            self.car.update()
            return f"{self.car.battery_v:.1f}V"
        if a.startswith("SH"):
            self.header = a[2:]
            return "OK"
        if a == "PC":
            self.connected = False
            return "OK"
        if a.startswith(("AT", "ST", "CAF", "CFC", "D", "M", "AL", "R", "CRA")):
            return "OK"
        return "?"

    # ------------------------------------------------------------ OBD
    def _obd(self, cmd: str) -> str:
        car = self.car
        car.update()
        prefix = ""
        if not self.connected:
            if not car.ignition_on():
                return "SEARCHING...\rUNABLE TO CONNECT" if self.protocol_set == "0" else "UNABLE TO CONNECT"
            if self.protocol_set == "0":
                prefix = "SEARCHING...\r" if self.sim_protocol == "6" else "BUS INIT: ...OK\r"
            self.connected = True
        elif not car.ignition_on():
            return "NO DATA" if self.sim_protocol != "6" else "CAN ERROR"

        data = bytes.fromhex(cmd if len(cmd) % 2 == 0 else cmd[:-1])  # drop response-count digit
        replies = self._answer(data)
        if not replies:
            return prefix + "NO DATA"
        return prefix + "\r".join(self._format(ecu, payload) for ecu, payload in replies)

    def _answer(self, req: bytes) -> list[tuple[str, bytes]]:
        car = self.car
        mode = req[0]
        is_can = self.sim_protocol == "6"
        out: list[tuple[str, bytes]] = []
        target = (self.header or "7DF").upper()
        engine_ok = target in ("7DF", "7E0", "686AF1", "C133F1")
        trans_ok = target in ("7DF", "7E1")

        if mode == 0x01:
            pids = list(req[1:7] if is_can else req[1:2])
            if engine_ok:
                body = bytearray([0x41])
                for pid in pids:
                    if pid in SIM_SUPPORTED or pid in (0x00, 0x20, 0x40):
                        val = car.pid_bytes(pid)
                        if val is not None:
                            body += bytes([pid]) + val
                if len(body) > 1:
                    out.append(("7E8", bytes(body)))
            if trans_ok and is_can and pids == [0x00]:
                out.append(("7E9", bytes([0x41, 0x00, 0x80, 0x00, 0x00, 0x00])))
            return out
        if mode == 0x02 and len(req) >= 3 and engine_ok:
            pid = req[1]
            if pid == 0x02:
                code = car.freeze_code
                a, b = encode_dtc(code) if code else (0, 0)
                return [("7E8", bytes([0x42, 0x02, 0x00, a, b]))]
            if car.freeze and pid in car.freeze and car.freeze[pid] is not None:
                return [("7E8", bytes([0x42, pid, 0x00]) + car.freeze[pid])]
            return []
        if mode in (0x03, 0x07):
            kind = "stored" if mode == 0x03 else "pending"
            for ecu in ("7E8", "7E9"):
                if (ecu == "7E8" and not engine_ok) or (ecu == "7E9" and not trans_ok):
                    continue
                codes = car.codes[ecu][kind]
                if ecu == "7E9" and not codes:
                    continue
                payload = bytearray([mode + 0x40])
                if is_can:
                    payload.append(len(codes))
                for c in codes:
                    payload += bytes(encode_dtc(c))
                out.append((ecu, bytes(payload)))
            return out
        if mode == 0x04:
            car.clear_codes()
            return [("7E8", b"\x44"), ("7E9", b"\x44")] if is_can else [("7E8", b"\x44")]
        if mode == 0x06 and is_can and engine_ok and len(req) >= 2:
            mid = req[1]
            if mid in MODE06_SUPPORT:
                return [("7E8", bytes([0x46, mid]) + _bitmask(MODE06_SUPPORT[mid], mid))]
            recs = car.mode06(mid)
            if not recs:
                return []
            body = bytearray([0x46])
            for tid, uas, val, lo, hi in recs:
                body += bytes([mid, tid, uas]) + val.to_bytes(2, "big") + lo.to_bytes(2, "big") + hi.to_bytes(2, "big")
            return [("7E8", bytes(body))]
        if mode == 0x21 and len(req) >= 2 and req[1] == 0x82 and target == "7E1":
            # Experimental Toyota transmission data: CVT fluid temperature (see config example)
            f = car.cvt_temp * 1.8 + 32
            raw = int(round((f * 10 + 400) / 0.07))
            return [("7E9", bytes([0x61, 0x82, raw >> 8, raw & 0xFF, 0, 0]))]
        return []

    def _format(self, ecu: str, payload: bytes) -> str:
        if self.sim_protocol != "6":
            return self._format_kline(payload)
        sp = " " if self.spaces else ""
        hx = lambda bs: sp.join(f"{b:02X}" for b in bs)  # noqa: E731
        lines = []
        if len(payload) <= 7:
            frame = bytes([len(payload)]) + payload
            if self.pad:
                frame += b"\x00" * (8 - len(frame))
            if self.headers:
                lines.append(ecu + sp + hx(frame))
            else:
                lines.append(hx(payload))
            return "\r".join(lines)
        n = len(payload)
        frames = [bytes([0x10 | (n >> 8), n & 0xFF]) + payload[:6]]
        rest, seq = payload[6:], 1
        while rest:
            chunk, rest = rest[:7], rest[7:]
            frames.append(bytes([0x20 | (seq & 0x0F)]) + chunk + b"\x00" * (7 - len(chunk)))
            seq += 1
        if self.headers:
            return "\r".join(ecu + sp + hx(f) for f in frames)
        out = [f"{n:03X}"]
        for i, f in enumerate(frames):
            body = f[2:] if i == 0 else f[1:]
            out.append(f"{i:X}:{sp}{hx(body)}")
        return "\r".join(out)

    def _format_kline(self, payload: bytes) -> str:
        sp = " " if self.spaces else ""
        frames = []
        mode = payload[0]
        if mode in (0x43, 0x47):
            codes = payload[1:]
            if not codes:
                codes = b"\x00" * 6
            for i in range(0, len(codes), 6):
                chunk = codes[i : i + 6]
                frames.append(bytes([mode]) + chunk + b"\x00" * (6 - len(chunk)))
        else:
            frames.append(payload)
        lines = []
        for f in frames:
            msg = bytes([0x48, 0x6B, 0x10]) + f
            msg += bytes([sum(msg) & 0xFF])
            lines.append(sp.join(f"{b:02X}" for b in (msg if self.headers else f)))
        return "\r".join(lines)


def make_simulation(scenario_name: str = "demo", speed: float = 20.0, vehicle: dict | None = None,
                    manual: bool = False, protocol: str = "6", seed: int = 7, start: float | None = None):
    """Returns (clock, car, emulator) ready to plug into SimTransport."""
    sc = SCENARIOS[scenario_name]
    if sc.demo_ageing:
        sc = aged_scenario(13, 14)
    clock = SimClock(speed=speed, manual=manual, start=start)
    car = SimCar(sc, clock, vehicle, seed=seed)
    emu = ELMEmulator(car, protocol=protocol)
    emu.scenario = sc
    return clock, car, emu
