"""Plain-text version of the fix list, for the terminal and for saving to a file."""
from __future__ import annotations

import datetime as dt
import sys
import textwrap

COLOURS = {4: "\033[1;31m", 3: "\033[38;5;208m", 2: "\033[33m", 1: "\033[36m", 0: "\033[32m"}
RESET = "\033[0m"
WORDS = {"stop": "STOP AND CHECK NOW", "fix": "Fix this week", "attention": "Fix soon", "watch": "Keep an eye on it",
         "good": "All good"}


def _c(text: str, level: int, colour: bool) -> str:
    return f"{COLOURS.get(level, '')}{text}{RESET}" if colour else text


def render_text(report: dict, vehicle: dict, colour: bool | None = None, width: int = 88) -> str:
    if colour is None:
        colour = sys.stdout.isatty()
    lvl = {"stop": 4, "fix": 3, "attention": 2, "watch": 1, "good": 0}.get(report.get("status"), 0)
    out = []
    when = dt.datetime.fromtimestamp(report.get("generated_ts", 0)).strftime("%d %b %Y %H:%M")
    out.append(f"{vehicle.get('name', 'My Allion')} ({vehicle.get('chassis_code')}, {vehicle.get('engine')}, "
               f"{vehicle.get('transmission')}) - report {when}")
    out.append("=" * min(width, 72))
    out.append(_c(WORDS.get(report.get("status"), ""), lvl, colour))
    headline = report.get("headline", "")
    if report.get("status") != "good" and ": " in headline:
        headline = headline.split(": ", 1)[1]
    out.append(textwrap.fill(headline, width))
    out.append("")
    sys_line = "  ".join(f"{s['name']}: {s['label'] if s['worst'] else 'OK'}" for s in report.get("systems", []))
    out.append(textwrap.fill(sys_line, width))
    for n in report.get("notes", []):
        if n.get("severity", 0) > 0:
            out.append("")
            out.append(_c(f"! {n['title']}", min(4, n["severity"]), colour))
            out.append(textwrap.indent(textwrap.fill(n["detail"], width - 2), "  "))
    out.append("")
    actions = report.get("actions", [])
    if not actions:
        out.append("Nothing to fix.")
    else:
        out.append("WHAT TO FIX, IN ORDER")
        out.append("-" * 21)
    for a in actions:
        out.append("")
        head = f"{a['rank']}. {a['title']}"
        out.append(_c(head, a["urgency"], colour))
        meta = [a.get("urgency_label", ""), {"High": "likely", "Medium": "probable", "Low": "possible"}.get(a.get("confidence"), "")]
        if a.get("cost") and a["cost"] != "?":
            meta.append(a.get("cost_label", a["cost"]).lower())
        if a.get("diy") and a["diy"] != "-":
            meta.append(a["diy"])
        out.append("   " + ", ".join(m for m in meta if m))
        for e in a.get("evidence", [])[:4]:
            out.append(textwrap.fill(e, width, initial_indent="   - ", subsequent_indent="     "))
        if a.get("checks"):
            out.append("   Check:")
            for c in a["checks"]:
                out.append(textwrap.fill(c, width, initial_indent="     * ", subsequent_indent="       "))
        if a.get("fix"):
            out.append(textwrap.fill("Fix: " + a["fix"], width, initial_indent="   ", subsequent_indent="   "))
        if a.get("drive"):
            out.append(textwrap.fill("Driving: " + a["drive"], width, initial_indent="   ", subsequent_indent="   "))
    scan = report.get("scan") or {}
    if scan.get("dtcs"):
        out.append("")
        out.append("FAULT CODES")
        for d in scan["dtcs"]:
            out.append(f"  {d['code']}  {d['kind']:<9} {d.get('ecu_label', ''):<12} {d.get('description', '')}")
    out.append("")
    out.append("This is guidance from generic OBD-II data, not a replacement for a qualified mechanic.")
    return "\n".join(out)
