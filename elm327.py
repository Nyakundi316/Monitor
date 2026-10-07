"""ELM327 driver: talks to the adapter, which talks to the car.

Handles adapter set-up, protocol detection, CAN (ISO-TP) reassembly, older K-line
protocols, and the OBD services we use:

  01 live data          02 freeze frame        03 stored codes      04 clear codes
  06 self-test results  07 pending codes       09 vehicle info      0A permanent codes
  ATRV battery voltage measured by the adapter itself
"""
from __future__ import annotations

import functools
import logging
import re
import time

from . import pids as P
from .dtc import DTC, decode_dtc, parse_dtc_messages
from .mode06 import TestResult, parse_results, parse_supported_mids
from .safe_eval import compile_formula, evaluate
from .transport import Transport, TransportError, TransportTimeout

log = logging.getLogger(__name__)

PROTOCOLS = {
    "1": "SAE J1850 PWM", "2": "SAE J1850 VPW", "3": "ISO 9141-2 (K-line)",
    "4": "ISO 14230-4 KWP (5-baud init)", "5": "ISO 14230-4 KWP (fast init)",
    "6": "CAN 11-bit 500 kbit/s", "7": "CAN 29-bit 500 kbit/s",
    "8": "CAN 11-bit 250 kbit/s", "9": "CAN 29-bit 250 kbit/s",
    "A": "SAE J1939", "B": "User CAN 1", "C": "User CAN 2",
}
CAN_PROTOCOLS = {"6", "7", "8", "9"}
CAN_29BIT = {"7", "9"}
# Functional ("talk to everyone") header for each protocol - used to undo ATSH.
DEFAULT_HEADERS = {"1": "616AF1", "2": "686AF1", "3": "686AF1", "4": "C133F1", "5": "C133F1",
                   "6": "7DF", "8": "7DF", "7": "18DB33F1", "9": "18DB33F1"}

ERRORS = ("NO DATA", "UNABLE TO CONNECT", "CAN ERROR", "BUS ERROR", "BUS BUSY", "BUFFER FULL",
          "DATA ERROR", "FB ERROR", "RX ERROR", "STOPPED", "ACT ALERT", "LV RESET", "LP ALERT",
          "ERROR")
HEX_ONLY = re.compile(r"^[0-9A-F]+$")
ISO_TP_LEN = re.compile(r"^[0-9A-F]{3}$")
ISO_TP_SEG = re.compile(r"^([0-9A-F]):\s*([0-9A-F ]+)$")

ENGINE_ECUS = ("7E8", "18DAF110", "10")


class ELMError(Exception):
    pass


class NoVehicleError(ELMError):
    """Adapter is fine but the car's computer didn't answer (ignition off / unsupported)."""


Message = tuple[str, bytes]  # (ecu address, payload starting with the response mode byte)


@functools.lru_cache(maxsize=64)
def _formula(text: str):
    return compile_formula(text)


class ELM327:
    def __init__(self, transport: Transport, protocol: str = "auto", batch_pids: bool = True,
                 timeout: float = 2.5):
        self.t = transport
        p = str(protocol).strip().upper()
        self.requested_protocol = "0" if p in ("AUTO", "0", "") else p
        self.timeout = timeout
        self.protocol: str | None = None
        self.version = ""
        self.supported: set[int] = set()
        self.batch_ok = batch_pids
        self._batch_failures = 0
        self.ecus: set[str] = set()
        self._custom_header = False

    # ------------------------------------------------------------------ low level
    def _send(self, cmd: str, timeout: float | None = None) -> list[str]:
        """Send one command, return the reply lines (echo and blank lines removed)."""
        self.t.write((cmd + "\r").encode("ascii"))
        try:
            raw = self.t.read_until_prompt(timeout or self.timeout)
        except TransportTimeout as exc:
            # Interrupt whatever the adapter is doing so the next command starts clean.
            log.debug("timeout on %s (partial: %r)", cmd, str(exc))
            self.t.write(b"\r")
            try:
                self.t.read_until_prompt(1.0)
            except TransportError:
                self.t.reset_input()
            return []
        lines = []
        for line in re.split(r"[\r\n]+", raw.replace(">", "")):
            line = line.strip()
            if not line or line.replace(" ", "") == cmd.replace(" ", ""):
                continue
            lines.append(line)
        return lines

    def at(self, cmd: str, timeout: float | None = None) -> str:
        return " ".join(self._send(cmd, timeout))

    def initialise(self) -> dict:
        """Reset and configure the adapter, then find the car's protocol."""
        self.t.reset_input()
        self._send("ATZ", timeout=4.0)
        time.sleep(0.3)
        for cmd in ("ATE0", "ATL0", "ATS1", "ATH1", "ATAT1"):
            self._send(cmd)
        self.version = self.at("ATI") or "unknown"
        self._send(f"ATSP{self.requested_protocol}")
        return self.connect_vehicle()

    def connect_vehicle(self) -> dict:
        """Wake the car's ECU (this triggers the protocol search) and read what it supports."""
        lines = self._send("0100", timeout=15.0)
        dpn = self.at("ATDPN").upper().replace(" ", "")
        self.protocol = dpn.lstrip("A")[:1] if dpn not in ("A", "") else "?"
        self._custom_header = False
        messages = self._parse(lines)
        if not messages:
            raise NoVehicleError(self._error_in(lines) or "No reply from the car")
        self.supported = set()
        self.ecus = {ecu for ecu, _ in messages if ecu != "?"}
        self._absorb_support(messages, 0x00)
        base = 0x20
        while base <= 0xC0 and base in self.supported:
            self._absorb_support(self.query(f"01{base:02X}"), base)
            base += 0x20
        if not self.supported:
            raise NoVehicleError("The car answered but listed no supported data")
        return self.info()

    def _absorb_support(self, messages: list[Message], base: int) -> None:
        for _, data in messages:
            if len(data) >= 6 and data[0] == 0x41 and data[1] == base:
                self.supported |= P.decode_supported(data[2:6], base)

    def info(self) -> dict:
        return {
            "adapter": self.version,
            "protocol": self.protocol,
            "protocol_name": PROTOCOLS.get(self.protocol or "", "Unknown"),
            "is_can": self.is_can,
            "ecus": sorted(self.ecus),
            "supported_pids": sorted(self.supported),
        }

    @property
    def is_can(self) -> bool:
        return self.protocol in CAN_PROTOCOLS

    @staticmethod
    def _error_in(lines: list[str]) -> str | None:
        for line in lines:
            up = line.upper()
            if up == "?":
                return "Command not understood"
            for e in ERRORS:
                if e in up:
                    return e
        return None

    def set_header(self, header: str | None) -> None:
        """Talk to one ECU (e.g. 7E1 = transmission on Toyota CAN) or back to everyone (None)."""
        if header:
            self._send(f"ATSH{header}")
            self._custom_header = True
        elif self._custom_header:
            self._send(f"ATSH{DEFAULT_HEADERS.get(self.protocol or '', '7DF')}")
            self._custom_header = False

    # ------------------------------------------------------------------ parsing
    def query(self, request: str, header: str | None = None, timeout: float | None = None) -> list[Message]:
        """Send an OBD request, return [(ecu, payload)] - payload starts at the reply mode byte."""
        if header:
            self.set_header(header)
        elif self._custom_header:
            self.set_header(None)
        msgs = self._parse(self._send(request, timeout))
        self.ecus |= {ecu for ecu, _ in msgs if ecu != "?"}
        return msgs

    def _parse(self, lines: list[str]) -> list[Message]:
        """Turn raw reply lines into messages. Error/status lines are ignored."""
        clean = []
        for x in lines:
            up = x.strip().upper()
            if HEX_ONLY.match(up.replace(" ", "")) or ISO_TP_SEG.match(up):
                clean.append(up)
        if not clean:
            return []
        if any(ISO_TP_SEG.match(l) for l in clean):
            return self._parse_noheader_multiframe(clean)
        if self.is_can or self.protocol in (None, "?"):
            return self._parse_can(clean)
        return self._parse_legacy(clean)

    def _parse_can(self, lines: list[str]) -> list[Message]:
        is29 = self.protocol in CAN_29BIT
        hl = 8 if is29 else 3
        frames: dict[str, list[list[int]]] = {}
        order: list[str] = []
        out: list[Message] = []
        for line in lines:
            compact = line.replace(" ", "")
            if is29:
                headerless = len(compact) < hl + 2 or not compact.startswith("18")
            else:
                headerless = len(compact) % 2 == 0 or compact[:1] != "7"
            if headerless:
                # Adapter ignored ATH1: the ELM already removed the PCI byte for us.
                if len(compact) % 2 == 0:
                    out.append(("?", bytes.fromhex(compact)))
                continue
            if len(compact) <= hl or (len(compact) - hl) % 2:
                continue
            ecu, data = compact[:hl], list(bytes.fromhex(compact[hl:]))
            frames.setdefault(ecu, []).append(data)
            if ecu not in order:
                order.append(ecu)
        for ecu in order:
            for payload in self._reassemble(frames[ecu]):
                out.append((ecu, payload))
        return out

    @staticmethod
    def _reassemble(frames: list[list[int]]) -> list[bytes]:
        """ISO-TP: single frames, or a first frame followed by consecutive frames."""
        out: list[bytes] = []
        cur: list[int] | None = None
        total = 0
        expect = 1
        for f in frames:
            if not f:
                continue
            kind = f[0] >> 4
            if kind == 0:
                n = f[0] & 0x0F
                if n == 0 and len(f) > 1:  # CAN-FD style escape
                    n, body = f[1], f[2:]
                else:
                    body = f[1:]
                out.append(bytes(body[:n]))
            elif kind == 1 and len(f) >= 2:
                if cur:
                    out.append(bytes(cur[:total]))
                total = ((f[0] & 0x0F) << 8) | f[1]
                cur, expect = list(f[2:]), 1
            elif kind == 2 and cur is not None:
                seq = f[0] & 0x0F
                if seq != expect:
                    log.debug("ISO-TP sequence jump: got %s expected %s", seq, expect)
                cur.extend(f[1:])
                expect = (seq + 1) & 0x0F
                if len(cur) >= total:
                    out.append(bytes(cur[:total]))
                    cur = None
            # kind 3 = flow control, nothing to do
        if cur:
            out.append(bytes(cur[:total]))
        return out

    @staticmethod
    def _parse_noheader_multiframe(lines: list[str]) -> list[Message]:
        """Headers off: '00A' length line then '0: ..', '1: ..' segments."""
        total, data = None, []
        out: list[Message] = []
        for l in lines:
            if ISO_TP_LEN.match(l):
                if data:
                    out.append(("?", bytes(data[:total] if total else data)))
                total, data = int(l, 16), []
                continue
            m = ISO_TP_SEG.match(l)
            if m:
                data.extend(bytes.fromhex(m.group(2).replace(" ", "")))
            else:
                compact = l.replace(" ", "")
                if len(compact) % 2 == 0:
                    out.append(("?", bytes.fromhex(compact)))
        if data:
            out.append(("?", bytes(data[:total] if total else data)))
        return out

    @staticmethod
    def _parse_legacy(lines: list[str]) -> list[Message]:
        """K-line / J1850: 3 header bytes, data, 1 checksum byte. One message per line."""
        out: list[Message] = []
        for line in lines:
            compact = line.replace(" ", "")
            if len(compact) % 2:
                continue
            b = bytes.fromhex(compact)
            if len(b) < 5:
                out.append(("?", b))
                continue
            out.append((f"{b[2]:02X}", b[3:-1]))
        return out

    # ------------------------------------------------------------------ services
    def battery_voltage(self) -> float | None:
        reply = self.at("ATRV", timeout=1.5).upper().replace("V", "").strip()
        try:
            v = float(reply)
            return v if 3.0 < v < 30.0 else None
        except ValueError:
            return None

    def read_pids(self, pids: list[int]) -> dict:
        """Read several Mode 01 PIDs. Batches up to 6 per request on CAN cars."""
        wanted = [p for p in pids if p in self.supported]
        values: dict = {}
        if not wanted:
            return values
        if self.is_can and self.batch_ok:
            for i in range(0, len(wanted), 6):
                chunk = wanted[i : i + 6]
                parts = self._merge_pid_replies(self.query("01" + "".join(f"{p:02X}" for p in chunk)))
                missing = [p for p in chunk if p not in parts]
                if missing and len(chunk) > 1:
                    self._batch_failures += 1
                    if self._batch_failures >= 3:
                        log.info("Multi-PID requests not answered fully; using one PID per request")
                        self.batch_ok = False
                    for pid in missing:
                        parts.update(self._merge_pid_replies(self.query(f"01{pid:02X}")))
                elif not missing:
                    self._batch_failures = 0
                for pid, data in parts.items():
                    values.update(P.decode_pid(pid, data))
            return values
        for pid in wanted:
            parts = self._merge_pid_replies(self.query(f"01{pid:02X}"))
            if pid in parts:
                values.update(P.decode_pid(pid, parts[pid]))
        return values

    @staticmethod
    def _merge_pid_replies(msgs: list[Message]) -> dict[int, bytes]:
        """Prefer the engine ECU's answer when several ECUs reply."""
        ranked = sorted(msgs, key=lambda m: 0 if m[0] in ENGINE_ECUS else 1)
        merged: dict[int, bytes] = {}
        for _, data in reversed(ranked):  # engine last so it wins
            merged.update(P.split_multi_pid(data))
        return merged

    def read_status(self) -> dict:
        """Check-engine light, code count and readiness monitors (PID 01)."""
        parts = self._merge_pid_replies(self.query("0101"))
        return P.decode_pid(0x01, parts[0x01]).get("status", {}) if 0x01 in parts else {}

    def read_dtcs(self) -> list[DTC]:
        """Stored (03), pending (07) and permanent (0A) codes."""
        out: list[DTC] = []
        for mode, resp, kind in (("03", 0x43, "stored"), ("07", 0x47, "pending"), ("0A", 0x4A, "permanent")):
            out += parse_dtc_messages(self.query(mode, timeout=4.0), resp, self.is_can, kind)
        return out

    def read_freeze_frame(self) -> dict:
        """Snapshot the ECU saved when the first code was stored (Mode 02, frame 0)."""
        frame: dict = {}
        for _, data in self.query("020200"):
            if len(data) >= 5 and data[0] == 0x42 and data[1] == 0x02:
                code = decode_dtc(data[3], data[4])
                if code:
                    frame["trigger_code"] = code
        if not frame:
            return {}
        for pid in (0x03, 0x04, 0x05, 0x06, 0x07, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11):
            if pid not in self.supported:
                continue
            for _, data in self.query(f"02{pid:02X}00"):
                if len(data) >= 4 and data[0] == 0x42 and data[1] == pid:
                    frame.update(P.decode_pid(pid, data[3:]))
                    break
        return frame

    def clear_dtcs(self) -> bool:
        msgs = self.query("04", timeout=5.0)
        return any(d[:1] == b"\x44" for _, d in msgs)

    def read_mode06(self) -> list[TestResult]:
        """Self-test results. CAN cars only (older protocols use a different format)."""
        if not self.is_can:
            return []
        mids: set[int] = set()
        base = 0x00
        while base <= 0xE0:
            got: set[int] = set()
            for _, data in self.query(f"06{base:02X}"):
                got |= parse_supported_mids(data, base)
            if not got:
                break
            mids |= got
            if (base + 0x20) not in got:
                break
            base += 0x20
        results: list[TestResult] = []
        for mid in sorted(m for m in mids if m % 0x20):
            for _, data in self.query(f"06{mid:02X}", timeout=3.0):
                results += [r for r in parse_results(data) if r.mid == mid]
        return results

    def read_vin(self) -> str | None:
        """Japanese-market cars usually have a chassis number instead of a VIN, so this may be None."""
        msgs = [d for _, d in self.query("0902", timeout=3.0)
                if len(d) > 3 and d[0] == 0x49 and d[1] == 0x02]
        if not msgs:
            return None
        body = msgs[0][3:] if self.is_can else b"".join(d[3:] for d in sorted(msgs, key=lambda d: d[2]))
        text = bytes(b for b in body if 32 < b < 127).decode("ascii", "ignore")
        return text[-17:] if len(text) >= 11 else None

    def read_custom(self, request: str, header: str | None, formula: str) -> float | None:
        """Read a manufacturer-specific value, e.g. Toyota mode 21 data."""
        tree = _formula(formula)
        req = bytes.fromhex(request)
        try:
            msgs = self.query(request, header=header or None)
        finally:
            if header:
                self.set_header(None)
        want_mode = req[0] + 0x40
        for _, data in msgs:
            if data and data[0] == want_mode and data[1:len(req)] == req[1:]:
                try:
                    return round(float(evaluate(tree, data[len(req):])), 2)
                except Exception:
                    return None
        return None

    def close(self) -> None:
        try:
            self._send("ATPC", timeout=1.0)
        except TransportError:
            pass
        self.t.close()
