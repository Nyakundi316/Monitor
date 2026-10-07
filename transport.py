"""Ways of talking to an ELM327 adapter.

* SerialTransport  - USB adapters, and Bluetooth adapters once paired
                     (they appear as a COM port on Windows or /dev/rfcomm0 on Linux).
* TcpTransport     - Wi-Fi adapters (usually 192.168.0.10:35000).
* SimTransport     - the built-in simulator, so everything can be tried without the car.

Every transport exposes the same three calls: write(), read_until_prompt() and close().
The ELM327 ends every reply with a '>' prompt, which is how we know a reply is complete.
"""
from __future__ import annotations

import socket
import time


class TransportError(Exception):
    """The adapter could not be opened or stopped answering."""


class TransportTimeout(TransportError):
    """No '>' prompt arrived in time."""


class Transport:
    name = "transport"

    def write(self, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def read_until_prompt(self, timeout: float) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def reset_input(self) -> None:
        pass

    def close(self) -> None:
        pass

    def describe(self) -> str:
        return self.name


# --------------------------------------------------------------------------- serial
class SerialTransport(Transport):
    name = "serial"
    CANDIDATE_BAUDS = (38400, 115200, 9600, 230400, 57600, 500000)

    def __init__(self, port: str, baudrate: int | None = None):
        try:
            import serial  # pyserial
        except ImportError as exc:  # pragma: no cover - depends on install
            raise TransportError("pyserial is not installed. Run: pip install pyserial") from exc
        self._serial = serial
        self.port = port
        self.ser = None
        self.baudrate = None
        bauds = (baudrate,) if baudrate else self.CANDIDATE_BAUDS
        for baud in bauds:
            try:
                ser = serial.Serial(port, baud, timeout=0.05, write_timeout=2)
            except (serial.SerialException, OSError) as exc:
                raise TransportError(f"Cannot open {port}: {exc}") from exc
            if self._probe(ser):
                self.ser, self.baudrate = ser, baud
                return
            ser.close()
        raise TransportError(
            f"Opened {port} but no ELM327 answered (tried {', '.join(map(str, bauds))} baud). "
            "Check the port name and that the adapter is plugged in with the ignition on."
        )

    @staticmethod
    def _probe(ser) -> bool:
        try:
            ser.reset_input_buffer()
            ser.write(b"\r")
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(b"ATI\r")
            deadline = time.monotonic() + 1.5
            buf = b""
            while time.monotonic() < deadline:
                buf += ser.read(64)
                if b">" in buf:
                    break
            return b">" in buf or b"ELM" in buf.upper()
        except Exception:
            return False

    def write(self, data: bytes) -> None:
        try:
            self.ser.write(data)
            self.ser.flush()
        except Exception as exc:
            raise TransportError(f"Write to {self.port} failed: {exc}") from exc

    def read_until_prompt(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        buf = bytearray()
        try:
            while time.monotonic() < deadline:
                chunk = self.ser.read(self.ser.in_waiting or 1)
                if chunk:
                    buf += chunk
                    if b">" in chunk:
                        return buf.decode("ascii", "replace")
        except Exception as exc:
            raise TransportError(f"Read from {self.port} failed: {exc}") from exc
        raise TransportTimeout(buf.decode("ascii", "replace"))

    def reset_input(self) -> None:
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass

    def describe(self) -> str:
        return f"{self.port} @ {self.baudrate} baud"


# --------------------------------------------------------------------------- wifi
class TcpTransport(Transport):
    name = "wifi"

    def __init__(self, host: str = "192.168.0.10", port: int = 35000, connect_timeout: float = 6.0):
        self.host, self.port = host, port
        try:
            self.sock = socket.create_connection((host, port), timeout=connect_timeout)
        except OSError as exc:
            raise TransportError(
                f"Cannot reach the Wi-Fi adapter at {host}:{port} ({exc}). "
                "Join the adapter's Wi-Fi network first (often called 'WiFi_OBDII' or 'V-LINK')."
            ) from exc
        self.sock.settimeout(0.05)

    def write(self, data: bytes) -> None:
        try:
            self.sock.sendall(data)
        except OSError as exc:
            raise TransportError(f"Wi-Fi adapter write failed: {exc}") from exc

    def read_until_prompt(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            try:
                chunk = self.sock.recv(1024)
            except socket.timeout:
                continue
            except OSError as exc:
                raise TransportError(f"Wi-Fi adapter read failed: {exc}") from exc
            if not chunk:
                raise TransportError("The Wi-Fi adapter closed the connection.")
            buf += chunk
            if b">" in chunk:
                return buf.decode("ascii", "replace")
        raise TransportTimeout(buf.decode("ascii", "replace"))

    def reset_input(self) -> None:
        try:
            while self.sock.recv(1024):
                pass
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def describe(self) -> str:
        return f"Wi-Fi {self.host}:{self.port}"


# --------------------------------------------------------------------------- simulator
class SimTransport(Transport):
    """Connects the driver to the in-process ELM327 emulator (see simulator.py)."""

    name = "simulator"

    def __init__(self, emulator):
        self.emulator = emulator
        self._out = ""

    def write(self, data: bytes) -> None:
        self._out += self.emulator.handle(data.decode("ascii", "replace"))

    def read_until_prompt(self, timeout: float) -> str:
        if ">" not in self._out:
            raise TransportTimeout(self._out)
        idx = self._out.index(">") + 1
        reply, self._out = self._out[:idx], self._out[idx:]
        return reply

    def reset_input(self) -> None:
        self._out = ""

    def describe(self) -> str:
        return f"simulator ({self.emulator.scenario.name})"


def list_serial_ports() -> list[tuple[str, str]]:
    """Return (device, description) for every serial port the OS knows about."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [(p.device, p.description or "") for p in sorted(list_ports.comports())]


def open_transport(conn: dict, emulator=None) -> Transport:
    """Create a transport from the [connection] section of the config."""
    kind = (conn.get("type") or "simulator").lower()
    if kind in ("serial", "usb", "bluetooth", "bt"):
        port = conn.get("port")
        if not port:
            raise TransportError("Set connection.port in config.toml (run: python run.py ports).")
        return SerialTransport(port, conn.get("baudrate") or None)
    if kind in ("wifi", "tcp"):
        return TcpTransport(conn.get("host", "192.168.0.10"), int(conn.get("tcp_port", 35000)))
    if kind in ("sim", "simulator"):
        if emulator is None:
            raise TransportError("Simulator transport needs an emulator instance.")
        return SimTransport(emulator)
    raise TransportError(f"Unknown connection type '{kind}'. Use serial, wifi or simulator.")
