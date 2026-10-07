"""Running the simulator through the full system: demo history, headless scenario runs."""
from __future__ import annotations

import datetime as dt
import logging
import time

from .monitor import Monitor
from .simulator import ELMEmulator, SCENARIOS, SimCar, SimClock, aged_scenario
from .transport import SimTransport

log = logging.getLogger(__name__)


def _sim_cfg(cfg: dict, poll: float = 1.0) -> dict:
    c = {**cfg, "monitor": {**cfg["monitor"], "poll_interval_s": poll, "log_interval_s": 1.0}}
    c["connection"] = {**cfg["connection"], "type": "simulator", "protocol": "auto"}
    return c


def drive_trips(monitor: Monitor, car: SimCar, trips: int, max_steps: int = 20000) -> None:
    """Step the monitor until the simulated car has finished `trips` more drives."""
    target = car.trip_index + trips
    steps = 0
    while steps < max_steps:
        monitor.step()
        steps += 1
        if car.trip_index >= target and car.state == "off" and monitor.trip_id is None:
            # let the monitor notice the ignition is off
            for _ in range(3):
                monitor.step()
            return
    raise RuntimeError("Simulated trips did not finish")


def seed_service(store, odo_km: float, today: dt.date) -> None:
    """Plausible service history so the Service tab has something to show."""
    store.set_odometer(time.mktime(today.timetuple()) - 30 * 86400, odo_km, "demo")

    def ago(days):
        return (today - dt.timedelta(days=days)).isoformat()

    store.record_service("engine_oil", odo_km - 5150, ago(190), "Mineral 5W-30")
    store.record_service("air_filter", odo_km - 16800, ago(400))
    store.record_service("cvt_fluid", odo_km - 26000, ago(560), "Toyota CVT Fluid TC")
    store.record_service("spark_plugs", odo_km - 44000, ago(900))
    store.record_service("brake_pads", odo_km - 3500, ago(70))
    store.record_service("tyres", odo_km - 6200, ago(120))


def seed_history(store, cfg: dict, vehicle: dict, trips: int = 14, every_days: float = 2.0, seed: int = 11):
    """Simulate weeks of driving an ageing Allion. Returns (car, emulator, end_time)."""
    start = time.time() - (trips * every_days + 1) * 86400
    clock = SimClock(manual=True, start=start)
    car = SimCar(aged_scenario(0, trips), clock, vehicle, seed=seed)
    emu = ELMEmulator(car)
    seed_service(store, 86150, dt.date.fromtimestamp(time.time()))
    mon = Monitor(_sim_cfg(cfg), store, lambda: SimTransport(emu), clock, vehicle, source="sim")
    for i in range(trips):
        car.set_scenario(aged_scenario(i, trips))
        emu.scenario = car.sc
        drive_trips(mon, car, 1)
        clock.jump(every_days * 86400 - car.script.duration)
    return car, emu, clock.now()


def run_scenario(name: str, cfg: dict, vehicle: dict, store, trips: int = 1, seed: int = 7,
                 protocol: str = "6") -> dict:
    """Drive a scenario headless and return the final report (used by tests and `simulate`)."""
    sc = SCENARIOS[name]
    if sc.demo_ageing:
        sc = aged_scenario(trips - 1, trips)
    clock = SimClock(manual=True, start=time.time() - trips * 86400)
    car = SimCar(sc, clock, vehicle, seed=seed)
    emu = ELMEmulator(car, protocol=protocol)
    mon = Monitor(_sim_cfg(cfg), store, lambda: SimTransport(emu), clock, vehicle, source="sim")
    for i in range(trips):
        if SCENARIOS[name].demo_ageing:
            car.set_scenario(aged_scenario(i, trips))
        drive_trips(mon, car, 1)
        clock.jump(6 * 3600)
    # come back to the car: ignition on so a final scan can run
    for _ in range(40):
        mon.step()
        if mon.vehicle_ok:
            break
    mon._refresh_report()
    return mon.get_report()
