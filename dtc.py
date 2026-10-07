"""Fault codes (DTCs): turning raw bytes into codes like P0303."""
from __future__ import annotations

from dataclasses import dataclass, asdict

LETTERS = "PCBU"

# Which ECU answered. Toyota CAN cars: 7E8 = engine, 7E9 = transmission (CVT).
ECU_NAMES = {
    "7E8": "Engine", "7E9": "Transmission", "7EA": "ECU 3", "7EB": "ECU 4",
    "10": "Engine", "18": "Transmission", "1A": "Transmission",
    "18DAF110": "Engine", "18DAF118": "Transmission",
}


def ecu_name(ecu: str) -> str:
    return ECU_NAMES.get(ecu.upper(), f"ECU {ecu}") if ecu else "Unknown ECU"


def decode_dtc(a: int, b: int) -> str | None:
    """Two bytes -> 'P0303'. Returns None for the 0x0000 padding value."""
    if a == 0 and b == 0:
        return None
    letter = LETTERS[(a >> 6) & 0x03]
    return f"{letter}{(a >> 4) & 0x03}{a & 0x0F:X}{b >> 4:X}{b & 0x0F:X}"


def encode_dtc(code: str) -> tuple[int, int]:
    """'P0303' -> (0x03, 0x03). Used by the simulator."""
    code = code.strip().upper()
    first = LETTERS.index(code[0]) << 6 | int(code[1]) << 4 | int(code[2], 16)
    second = int(code[3], 16) << 4 | int(code[4], 16)
    return first, second


@dataclass
class DTC:
    code: str
    kind: str          # stored | pending | permanent
    ecu: str = ""

    @property
    def ecu_label(self) -> str:
        return ecu_name(self.ecu)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ecu_label"] = self.ecu_label
        return d


def parse_dtc_messages(messages: list[tuple[str, bytes]], response_mode: int, is_can: bool,
                       kind: str) -> list[DTC]:
    """Decode Mode 03/07/0A replies.

    CAN replies carry a count byte after the mode byte: 43 02 01 33 01 71
    Older K-line replies don't and repeat the mode byte on every line: 43 01 33 01 71 00 00
    """
    seen: set[tuple[str, str]] = set()
    out: list[DTC] = []
    for ecu, data in messages:
        if not data or data[0] != response_mode:
            continue
        body = data[2:] if is_can else data[1:]
        for i in range(0, len(body) - 1, 2):
            code = decode_dtc(body[i], body[i + 1])
            if code and (code, ecu) not in seen:
                seen.add((code, ecu))
                out.append(DTC(code, kind, ecu))
    return out
