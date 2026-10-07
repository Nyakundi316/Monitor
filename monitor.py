"""The monitoring loop: keeps the adapter connected, reads the car, logs trips,
runs the checks and keeps an up-to-date fix list.

Car states:  no adapter -> ignition off -> engine off (ignition on) -> running
A trip starts when the engine starts and ends when it stops. While the engine is off
with the ignition on, the battery voltage is sampled quickly to catch the cranking dip.
"""
from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import logging
import queue
import threading

from . import maintenance as svc_mod
from . import trends
from .diagnosis import build_report
from .elm327 import ELM327, NoVehicleError
from .kb.dtc_codes import describe
from .mode06 import misfire_summary
from .rules import Finding, TripAnalyser
from .transport import TransportError

log = logging.getLogger(__name__)

FAST = [0x0C, 0x0D, 0x05, 0x04, 0x06, 0x07, 0x10, 0x11, 0x49]
MEDIUM = [0x0E, 0x0F, 0x03, 0x14, 0x15, 0x24, 0x34, 0x4C, 0x0B, 0x44]
SLOW = [0x33, 0x3C, 0x42, 0x46, 0x2F, 0x1F, 0x5C, 0x5E, 0xA6]
INFO_PIDS = [0x1C, 0x21, 0x30, 0x31, 0x4D, 0x4E, 0x51, 0x33]
LOG_KEYS = ["rpm", "speed", "coolant", "load", "stft", "ltft", "maf", "throttle", "pedal", "timing", "iat",
            "o2s1_lambda", "o2s1_v", "o2s2_v", "battery", "cat_temp", "cvt_temp", "closed_loop", "baro"]


class Command:
    def __init__(self, name: str, **kw):
        self.name, self.kw = name, kw
        self.done = threading.Event()
        self.result = None
        self.error = None


class Monitor(threading.Thread):
    def __init__(self, cfg: dict, store, transport_factory, clock, vehicle: dict, source: str = "car"):
        super().__init__(daemon=True, name="monitor")
        self.cfg = cfg
        self.store = store
        self.transport_factory = transport_factory
        self.clock = clock
        self.vehicle = vehicle
        self.source = source
        mcfg = cfg["monitor"]
        self.poll_interval = float(mcfg.get("poll_interval_s", 0.05))
        self.log_interval = float(mcfg.get("log_interval_s", 1.0))
        self.scan_interval = float(mcfg.get("scan_interval_s", 300))
        self.thresholds = cfg.get("thresholds", {})
        self.custom = [c for c in cfg.get("custom_pids", []) if c.get("enabled")]
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._cmds: queue.Queue[Command] = queue.Queue()
        # connection
        self.elm: ELM327 | None = None
        self.vehicle_ok = False
        self.info: dict = {}
        self.state = "starting"
        self.status_text = "Starting"
        self.no_resp = 0
        self.cycle = 0
        # live data
        self.latest: dict = {}
        self.latest_ts = None
        self.history = collections.deque(maxlen=900)
        self.alerts: list[Finding] = []
        # trip
        self.trip_id = None
        self.analyser: TripAnalyser | None = None
        self._engine_off_since = None
        self._pending_rows: list = []
        self._last_log_ts = 0.0
        self._last_scan_ts = 0.0
        self._last_report_ts = 0.0
        self._rest_off = None          # (ts, volts) with ignition off
        self._rest_ign = []            # readings with ignition on, engine off
        self._crank_min = None
        # results
        self.last_scan = store.last_scan()
        self.last_trip_findings = self._load_last_findings()
        self.trend_findings = trends.analyse(store.trips(limit=60))
        self.report: dict = {}
        self.events = collections.deque(maxlen=40)
        self._refresh_report()

    # ================================================================= public API
    def stop(self) -> None:
        self._stop.set()

    def request(self, name: str, timeout: float = 60.0, **kw):
        cmd = Command(name, **kw)
        self._cmds.put(cmd)
        if not cmd.done.wait(timeout):
            raise TimeoutError("The car didn't answer in time")
        if cmd.error:
            raise RuntimeError(cmd.error)
        return cmd.result

    def snapshot(self) -> dict:
        with self._lock:
            trip = None
            if self.analyser is not None:
                a = self.analyser
                trip = {"id": self.trip_id, "km": round(a.km, 2), "minutes": round(a.run_s / 60, 1),
                        "fuel_l": round(a.fuel_l, 2),
                        "km_per_l": round(a.km / a.fuel_l, 1) if a.fuel_l > 0.05 and a.km > 0.5 else None}
            live = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.latest.items()
                    if not isinstance(v, (dict, list))}
            return {
                "now": self.clock.now(), "state": self.state, "status_text": self.status_text,
                "source": self.source, "info": self.info, "vehicle": self.vehicle, "live": live,
                "live_ts": self.latest_ts, "alerts": [a.to_dict() for a in self.alerts], "trip": trip,
                "events": list(self.events)[-12:],
            }

    def history_rows(self, n: int = 600) -> list[dict]:
        with self._lock:
            return list(self.history)[-n:]

    def get_report(self) -> dict:
        with self._lock:
            return self.report

    # ================================================================= loop
    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as exc:  # keep the monitor alive whatever happens
                log.exception("monitor error")
                self._drop_connection(f"Error: {exc}")
                self.clock.sleep(2.0)
        self._shutdown()

    def step(self) -> None:
        self._handle_commands()
        if self.elm is None:
            self._open_adapter()
        elif not self.vehicle_ok:
            self._wait_for_vehicle()
        else:
            self._poll()

    def _shutdown(self) -> None:
        if self.trip_id is not None:
            self._end_trip(self.clock.now(), scan=False)
        self._flush_rows()
        if self.elm:
            try:
                self.elm.close()
            except Exception:
                pass

    # ----------------------------------------------------------------- connection
    def _set_state(self, state: str, text: str) -> None:
        with self._lock:
            self.state, self.status_text = state, text

    def _open_adapter(self) -> None:
        self._set_state("connecting", "Connecting to the adapter...")
        try:
            transport = self.transport_factory()
        except TransportError as exc:
            self._set_state("no_adapter", str(exc))
            self.clock.sleep(5.0)
            return
        elm = ELM327(transport, protocol=self.cfg["connection"].get("protocol", "auto"),
                     batch_pids=self.cfg["connection"].get("batch_pids", True))
        self.elm = elm
        try:
            info = elm.initialise()
            self._vehicle_connected(info)
        except NoVehicleError as exc:
            self.vehicle_ok = False
            self._set_state("ignition_off", f"Adapter ready ({elm.version}). Waiting for ignition ({exc}).")
        except TransportError as exc:
            self._drop_connection(str(exc))
            self.clock.sleep(3.0)

    def _drop_connection(self, why: str) -> None:
        if self.trip_id is not None:
            self._end_trip(self.clock.now(), scan=False)
        if self.elm is not None:
            try:
                self.elm.t.close()
            except Exception:
                pass
        self.elm = None
        self.vehicle_ok = False
        self._set_state("no_adapter", why)

    def _wait_for_vehicle(self) -> None:
        """Ignition off: retry the ECU every few seconds and measure true resting voltage."""
        try:
            info = self.elm.connect_vehicle()
            self._vehicle_connected(info)
            return
        except NoVehicleError:
            pass
        v = self.elm.battery_voltage()
        now = self.clock.now()
        last_end = self.store.meta_get("last_trip_end", 0) or 0
        if v is not None:
            with self._lock:
                self.latest["battery"] = v
                self.latest_ts = now
            if now - last_end > 1800:  # 30 min after a drive: surface charge has gone
                self._rest_off = (now, v)
        self._set_state("ignition_off", "Ignition off - waiting" + (f" (battery {v:.2f} V)" if v else ""))
        self.clock.sleep(3.0)

    def _vehicle_connected(self, info: dict) -> None:
        self.vehicle_ok = True
        self.no_resp = 0
        with self._lock:
            self.info = {**info, "transport": self.elm.t.describe()}
        self._set_state("engine_off", f"Connected: {info['protocol_name']}")
        self._event("connected", f"Connected to the car ({info['protocol_name']})")
        try:
            self._scan()
        except TransportError:
            raise
        except Exception:
            log.exception("initial scan failed")

    def _lost_vehicle(self) -> None:
        if self.trip_id is not None:
            self._end_trip(self.clock.now(), scan=False)
        self.vehicle_ok = False
        self._rest_ign.clear()
        self._crank_min = None
        self._set_state("ignition_off", "Ignition off")

    # ----------------------------------------------------------------- polling
    def _poll(self) -> None:
        self.cycle += 1
        pids = list(FAST)
        if self.cycle % 3 == 0:
            pids += MEDIUM
        if self.cycle % 15 == 1:
            pids += SLOW
        try:
            vals = self.elm.read_pids(pids)
        except TransportError as exc:
            self._drop_connection(str(exc))
            return
        if not vals:
            self.no_resp += 1
            if self.no_resp >= 3:
                self._lost_vehicle()
            self.clock.sleep(0.5)
            return
        self.no_resp = 0
        batt = self.elm.battery_voltage()
        if batt is not None:
            vals["battery"] = batt
        if self.custom and self.cycle % 10 == 0:
            for c in self.custom:
                try:
                    val = self.elm.read_custom(c["request"], c.get("header") or None, c["formula"])
                    if val is not None:
                        vals[c.get("key") or c["name"]] = val
                except Exception as exc:
                    log.debug("custom PID %s failed: %s", c.get("name"), exc)
        ts = self.clock.now()
        with self._lock:
            self.latest.update(vals)
            self.latest_ts = ts
        rpm = vals.get("rpm", 0) or 0

        if rpm > 400:
            if self.trip_id is None:
                self._start_trip(ts)
            self._engine_off_since = None
            sample = dict(self.latest)
            self.analyser.add(ts, sample)
            alerts = self.analyser.live_alerts(sample)
            self._update_alerts(alerts)
            if ts - self._last_log_ts >= self.log_interval:
                self._last_log_ts = ts
                row = {k: sample[k] for k in LOG_KEYS if k in sample}
                with self._lock:
                    self.history.append({"ts": ts, **row})
                self._pending_rows.append((ts, row))
                if len(self._pending_rows) >= 30:
                    self._flush_rows()
            if ts - self._last_scan_ts >= self.scan_interval:
                self._scan()
            elif ts - self._last_report_ts >= 20:
                self._refresh_report()
            self._set_state("running", "Engine running")
        else:
            if self.trip_id is not None:
                self._engine_off_since = self._engine_off_since or ts
                if ts - self._engine_off_since >= 4:
                    self._end_trip(ts)
            else:
                self._set_state("engine_off", "Ignition on, engine off")
                self._watch_cranking()
        self.clock.sleep(self.poll_interval)

    def _watch_cranking(self) -> None:
        """Sample battery voltage fast for ~2 s to catch the dip when the starter turns."""
        end = self.clock.now() + 2.0
        cranking = False
        while self.clock.now() < end:
            v = self.elm.battery_voltage()
            if v is None:
                return
            if v < 11.3:
                cranking = True
                self._crank_min = v if self._crank_min is None else min(self._crank_min, v)
            elif not cranking:
                self._rest_ign.append(v)
                self._rest_ign = self._rest_ign[-20:]
            elif v > 12.9:
                return
            self.clock.sleep(0.08)

    def _update_alerts(self, alerts: list[Finding]) -> None:
        with self._lock:
            old = {a.id for a in self.alerts}
            self.alerts = alerts
        for a in alerts:
            if a.id not in old:
                self._event("alert", a.title, severity=a.severity)

    def _flush_rows(self) -> None:
        if self.trip_id is not None and self._pending_rows:
            self.store.add_samples(self.trip_id, self._pending_rows)
        self._pending_rows = []

    # ----------------------------------------------------------------- trips
    def _start_trip(self, ts: float) -> None:
        self.trip_id = self.store.start_trip(ts, self.source)
        a = TripAnalyser(self.vehicle, self.thresholds)
        if self._rest_off and ts - self._rest_off[0] < 12 * 3600:
            a.set_rest_voltage(self._rest_off[1], ignition_on=False)
        elif self._rest_ign:
            ign = sorted(self._rest_ign)
            a.set_rest_voltage(ign[len(ign) // 2], ignition_on=True)
        if self._crank_min is not None:
            a.set_crank_voltage(self._crank_min)
        self._rest_off, self._rest_ign, self._crank_min = None, [], None
        with self._lock:
            self.analyser = a
        self._last_scan_ts = ts - self.scan_interval + 60  # first in-trip scan after a minute
        self._last_log_ts = 0.0
        self._event("trip_start", "Engine started - trip logging")

    def _end_trip(self, ts: float, scan: bool = True) -> None:
        if scan and self.vehicle_ok:
            try:
                self._scan()
            except Exception:
                log.exception("end-of-trip scan failed")
        a = self.analyser
        summary = a.summary()
        trip_scans = self.store.scans_for_trip(self.trip_id)
        if trip_scans:
            last = trip_scans[-1]
            per = (last.get("misfire") or {}).get("per_cylinder") or {}
            if per:
                summary["misfire"] = {str(c): v.get("current", 0) for c, v in per.items()}
            cat = [t for t in last.get("mode06", []) if t["mid"] == 0x21 and t.get("usage") is not None]
            if cat:
                summary["cat_usage"] = cat[0]["usage"]
            summary["codes"] = sorted({d["code"] for d in last.get("dtcs", [])})
        findings = a.findings()
        summary["findings"] = [f.to_dict() for f in findings]
        self._flush_rows()
        self.store.end_trip(self.trip_id, ts, summary)
        self.store.meta_set("last_trip_end", ts)
        self._event("trip_end", f"Trip ended: {summary['km']:.1f} km"
                    + (f", {summary['km_per_l']} km/L" if summary.get("km_per_l") else ""))
        with self._lock:
            self.trip_id = None
            self.analyser = None
            self.alerts = []
        self.last_trip_findings = findings
        self.trend_findings = trends.analyse(self.store.trips(limit=60))
        self._refresh_report()

    def _load_last_findings(self) -> list[Finding]:
        trips = self.store.trips(limit=1)
        if not trips or not trips[-1]["summary"]:
            return []
        return [Finding(**f) for f in trips[-1]["summary"].get("findings", [])]

    # ----------------------------------------------------------------- scans & report
    def _scan(self) -> dict:
        elm = self.elm
        ts = self.clock.now()
        status = elm.read_status()
        dtcs = [{**d.to_dict(), "description": describe(d.code)} for d in elm.read_dtcs()]
        freeze = elm.read_freeze_frame() if any(d["kind"] == "stored" for d in dtcs) else {}
        m06 = elm.read_mode06()
        info = elm.read_pids(INFO_PIDS)
        scan = {"ts": ts, "status": status, "dtcs": dtcs, "freeze": freeze,
                "mode06": [r.to_dict() for r in m06], "misfire": misfire_summary(m06) if m06 else None,
                "info": info}
        before = {(d["code"], d["kind"]) for d in (self.last_scan or {}).get("dtcs", [])}
        for d in dtcs:
            if (d["code"], d["kind"]) not in before:
                self._event("code", f"New {d['kind']} code {d['code']}", severity=3 if d["kind"] == "stored" else 2)
        self.store.add_scan(ts, self.trip_id, scan)
        with self._lock:
            self.last_scan = scan
        self._last_scan_ts = ts
        self._refresh_report()
        return scan

    def _live_findings(self) -> list[Finding]:
        """This trip's findings; early in a drive, fill gaps with the previous drive's so the
        fix list doesn't flicker while the new trip gathers enough data."""
        if self.analyser is None:
            return list(self.last_trip_findings)
        current = self.analyser.findings()
        seen = {f.tag for f in current}
        carried = [dataclasses.replace(f, confidence=round(f.confidence * 0.85, 2),
                                       detail=f"{f.detail} (seen on your last drive)")
                   for f in self.last_trip_findings if f.tag not in seen]
        return current + carried

    def _refresh_report(self) -> None:
        now = self.clock.now()
        live = self._live_findings()
        today = dt.date.fromtimestamp(now)
        service = svc_mod.status(self.store, self.vehicle, self.cfg.get("maintenance", {}), today=today)
        report = build_report(
            vehicle=self.vehicle, scan=self.last_scan, live_findings=live, trend_findings=self.trend_findings,
            service=service, service_findings=svc_mod.findings(service), symptoms=self.store.active_symptoms(),
            alerts=self.alerts, now=now)
        report["service"] = service
        report["scan"] = self.last_scan
        with self._lock:
            self.report = report
        self._last_report_ts = now

    def _event(self, kind: str, text: str, severity: int = 0) -> None:
        ts = self.clock.now()
        with self._lock:
            self.events.append({"ts": ts, "kind": kind, "text": text, "severity": severity})
        try:
            self.store.add_event(ts, self.trip_id, kind, {"text": text, "severity": severity})
        except Exception:
            pass

    # ----------------------------------------------------------------- commands from the dashboard
    def _handle_commands(self) -> None:
        while True:
            try:
                cmd = self._cmds.get_nowait()
            except queue.Empty:
                return
            try:
                cmd.result = self._run_command(cmd)
            except Exception as exc:
                cmd.error = str(exc)
            finally:
                cmd.done.set()

    def _run_command(self, cmd: Command):
        if cmd.name == "refresh":
            self.trend_findings = trends.analyse(self.store.trips(limit=60))
            self._refresh_report()
            return self.report
        if not (self.elm and self.vehicle_ok):
            raise RuntimeError("The car isn't connected (is the ignition on?)")
        if cmd.name == "scan":
            return self._scan()
        if cmd.name == "clear_codes":
            if (self.latest.get("rpm") or 0) > 0:
                raise RuntimeError("Switch the engine off (leave the ignition on) before clearing codes.")
            ok = self.elm.clear_dtcs()
            self._event("cleared", "Fault codes cleared" if ok else "Clear-codes request not confirmed", severity=1)
            self._scan()
            return {"ok": ok}
        raise RuntimeError(f"Unknown command {cmd.name}")
