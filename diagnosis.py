"""Turns all the evidence into one prioritised fix list.

Evidence sources:  fault codes · live-data findings · the ECU's self-tests (Mode 06)
                   · trends across trips · service history · symptoms you reported
Each piece of evidence becomes a tag (e.g. 'live:lean_idle_only'). Issue templates in
kb/issues.py score how well the tags fit each possible root cause, so the list says
"air leak - here's why" rather than repeating raw codes.
"""
from __future__ import annotations

import statistics as st
import time
from dataclasses import dataclass, field

from .kb.dtc_advice import advice_for
from .kb.dtc_codes import describe
from .kb.issues import COST_NAMES, ISSUES, URGENCY_NAMES
from .kb.symptoms import SYMPTOM_INDEX
from .mode06 import MID_NAMES
from .rules import Finding

SYSTEMS = ["Engine", "Ignition", "Fuel & air", "Cooling", "Charging", "Emissions", "Transmission", "Service"]
PENALTY = {4: 70, 3: 45, 2: 25, 1: 10}


@dataclass
class Evidence:
    tag: str
    strength: float
    severity: int
    reasons: list = field(default_factory=list)
    kind: str = "live"


class EvidenceSet:
    def __init__(self):
        self.items: dict[str, Evidence] = {}

    def add(self, tag: str, strength: float, severity: int, reason: str, kind: str) -> None:
        e = self.items.get(tag)
        if e is None:
            self.items[tag] = Evidence(tag, strength, severity, [reason], kind)
        else:
            e.strength = max(e.strength, strength)
            e.severity = max(e.severity, severity)
            if reason not in e.reasons:
                e.reasons.append(reason)

    def __contains__(self, tag):
        return tag in self.items

    def get(self, tag):
        return self.items.get(tag)

    def strengths(self) -> dict[str, float]:
        return {t: e.strength for t, e in self.items.items()}


# --------------------------------------------------------------------------- evidence builders

def evidence_from_codes(ev: EvidenceSet, dtcs: list[dict]) -> None:
    for d in dtcs:
        code, kind = d["code"], d["kind"]
        adv = advice_for(code)
        sev = 3 if kind in ("stored", "permanent") else 2
        strength = 1.0 if kind in ("stored", "permanent") else 0.8
        reason = f"{code} ({kind}): {describe(code)}"
        tags = list(adv["tags"])
        if code.startswith("P030") and code != "P0300":
            tags.append("code:misfire_any")
        if code == "P0300":
            tags.append("code:misfire_any")
        if code[:3] in ("P07", "P08", "P09") or d.get("ecu") in ("7E9",):
            tags.append("code:trans")
        for t in tags:
            ev.add(t, strength, sev, reason, "code")


def evidence_from_mode06(ev: EvidenceSet, mode06: list[dict], misfire: dict | None) -> list[Finding]:
    """Misfire counts per cylinder and self-test margins."""
    notes: list[Finding] = []
    per = (misfire or {}).get("per_cylinder") or {}
    if per:
        counts = {int(c): max(v.get("current", 0), v.get("average", 0)) for c, v in per.items()}
        hot = []
        for c, n in counts.items():
            others = [m for k, m in counts.items() if k != c]
            base = st.median(others) if others else 0
            if n >= 8 and n >= 3 * (base + 1):
                hot.append((c, n))
        for c, n in hot:
            sev = 3 if n >= 100 else 2
            others = [str(counts[k]) for k in sorted(counts) if k != c]
            reason = (f"Engine computer counted {n} misfires on cylinder {c}, against "
                      f"{', '.join(others[:-1])} and {others[-1]} on the others" if len(others) > 1 else
                      f"Engine computer counted {n} misfires on cylinder {c}")
            ev.add(f"m06:misfire_cyl{c}", 1.0 if n >= 20 else 0.8, sev, reason, "selftest")
            ev.add("m06:misfire_any", 1.0, sev, reason, "selftest")
        busy = [c for c, n in counts.items() if n >= 8]
        if len(busy) >= 2 and len(hot) != 1:
            ev.add("m06:misfire_multi", 1.0, 2, f"Misfires counted on cylinders {', '.join(map(str, busy))}", "selftest")
            ev.add("m06:misfire_any", 1.0, 2, "Misfires on several cylinders", "selftest")
    for t in mode06:
        if t.get("misfire"):
            continue
        usage = t.get("usage")
        name = t.get("name") or MID_NAMES.get(t["mid"], f"Test {t['mid']:02X}")
        if t["mid"] in (0x21, 0x22):
            if not t["passed"]:
                ev.add("m06:cat_failed", 1.0, 2, f"{name}: self-test failed", "selftest")
            elif usage is not None and usage >= 0.8:
                ev.add("m06:cat_near_limit", 0.8, 2 if usage >= 0.9 else 1,
                       f"{name}: passing, but at {usage * 100:.0f} % of the failure limit", "selftest")
        elif t["mid"] <= 0x08 or 0x41 <= t["mid"] <= 0x48:
            if not t["passed"]:
                ev.add("m06:o2_near_limit", 1.0, 2, f"{name}: self-test failed", "selftest")
            elif usage is not None and usage >= 0.9:
                ev.add("m06:o2_near_limit", 0.7, 1, f"{name}: at {usage * 100:.0f} % of its limit", "selftest")
        elif not t["passed"]:
            notes.append(Finding(f"m06_fail_{t['mid']:02X}", "m06:failed_other", "Emissions", 1,
                                 f"Self-test failed: {name}", "The engine computer's own test is outside its limits.",
                                 0.6, "selftest"))
    return notes


def evidence_from_findings(ev: EvidenceSet, findings: list[Finding]) -> None:
    for f in findings:
        ev.add(f.tag, f.confidence, f.severity, f"{f.title}: {f.detail}", f.source)


# --------------------------------------------------------------------------- issues

def _expand_templates(cylinders: int) -> list[dict]:
    out = []
    for tpl in ISSUES:
        if tpl.get("per_cylinder"):
            for n in range(1, cylinders + 1):
                sub = lambda x, n=n: x.replace("{n}", str(n)) if isinstance(x, str) else x  # noqa: E731
                out.append({
                    **tpl, "id": sub(tpl["id"]), "title": sub(tpl["title"]),
                    "signals": {sub(k): v for k, v in tpl["signals"].items()},
                    "requires": [sub(r) for r in tpl["requires"]],
                    "checks": [sub(c) for c in tpl["checks"]], "fix": sub(tpl["fix"]),
                })
        else:
            out.append(tpl)
    return out


def score_issues(ev: EvidenceSet, cylinders: int = 4) -> list[dict]:
    issues = []
    for tpl in _expand_templates(cylinders):
        if not any(r in ev for r in tpl["requires"]):
            continue
        score, urgency, reasons, kinds, used = 0.0, 0, [], set(), []
        for tag, w in tpl["signals"].items():
            e = ev.get(tag)
            if e is None:
                continue
            score += w * e.strength
            if w > 0:
                urgency = max(urgency, e.severity)
                kinds.add(e.kind)
                used.append(tag)
                for r in e.reasons:
                    if r not in reasons:
                        reasons.append(r)
        if score < 2.0:
            continue
        conf = "High" if score >= 6 else "Medium" if score >= 3.5 else "Low"
        issues.append({
            "id": tpl["id"], "title": tpl["title"], "system": tpl["system"], "score": round(score, 2),
            "confidence": conf, "urgency": max(1, urgency), "evidence": reasons, "evidence_tags": used,
            "evidence_kinds": sorted(kinds), "checks": tpl["checks"], "fix": tpl["fix"],
            "diy": tpl.get("diy", ""), "cost": tpl.get("cost", "?"), "why_early": tpl.get("why_early"),
            "kind": "issue",
        })
    # one issue per system id-family: keep separate, but drop weaker duplicates sharing all evidence
    issues.sort(key=lambda i: (-i["urgency"], -i["score"]))
    return issues


# --------------------------------------------------------------------------- symptom checker

def check_symptoms(selected: list[str], ev: EvidenceSet) -> list[dict]:
    strengths = ev.strengths()
    for s in selected:
        strengths[f"sym:{s}"] = 1.0
    out = []
    for sid in selected:
        sym = SYMPTOM_INDEX.get(sid)
        if not sym:
            continue
        causes = []
        for c in sym["causes"]:
            score = c["base"]
            support, against = [], []
            for tag, w in c.get("boost", {}).items():
                if tag in strengths:
                    score += w * strengths[tag]
                    e = ev.get(tag)
                    why = e.reasons[0] if e else tag
                    (support if w > 0 else against).append(why)
            causes.append({**{k: v for k, v in c.items() if k != "boost"}, "score": round(max(score, 0.05), 2),
                           "supported_by": support, "argued_against_by": against})
        total = sum(c["score"] for c in causes) or 1
        for c in causes:
            c["likelihood"] = round(100 * c["score"] / total)
        causes.sort(key=lambda c: -c["score"])
        out.append({"id": sid, "title": sym["title"], "group": sym["group"], "causes": causes})
    return out


# --------------------------------------------------------------------------- report

def build_report(*, vehicle: dict, scan: dict | None, live_findings: list[Finding], trend_findings: list[Finding],
                 service: dict, service_findings: list[Finding], symptoms: list[str], alerts: list[Finding],
                 now: float | None = None) -> dict:
    now = now or time.time()
    ev = EvidenceSet()
    dtcs = (scan or {}).get("dtcs", [])
    evidence_from_codes(ev, dtcs)
    m06_notes = evidence_from_mode06(ev, (scan or {}).get("mode06", []), (scan or {}).get("misfire"))
    evidence_from_findings(ev, live_findings)
    evidence_from_findings(ev, trend_findings)
    evidence_from_findings(ev, service_findings)
    sym_results = check_symptoms(symptoms, ev)
    for res in sym_results:
        top_urgency = max(1, res["causes"][0]["urgency"]) if res["causes"] else 1
        ev.add(f"sym:{res['id']}", 1.0, top_urgency, f"You reported: {res['title'].lower()}", "symptom")

    issues = score_issues(ev, int(vehicle.get("cylinders", 4)))
    actions: list[dict] = []

    # 1. anything happening right now that needs the driver to stop
    for a in alerts:
        if a.severity >= 4:
            actions.append({"id": a.id, "title": a.title, "system": a.system, "urgency": 4, "confidence": "High",
                            "evidence": [a.detail], "checks": [], "fix": a.detail, "kind": "alert",
                            "evidence_kinds": ["live"], "diy": "", "cost": "?"})

    # 2. root-cause issues
    claimed_tags = set()
    for i in issues:
        actions.append(i)
        claimed_tags.update(i["evidence_tags"])

    # 3. fault codes that no issue explains
    seen_codes = set()
    for d in dtcs:
        code = d["code"]
        if code in seen_codes:
            continue
        adv = advice_for(code)
        tags = set(adv["tags"])
        if tags & claimed_tags:
            continue
        seen_codes.add(code)
        kind = d["kind"]
        actions.append({
            "id": f"code_{code}", "title": f"{code}: {adv.get('title') or describe(code)}", "system": _system_for_code(code),
            "urgency": adv["urgency"] if kind != "pending" else max(1, adv["urgency"] - 1),
            "confidence": "High" if kind != "pending" else "Medium",
            "evidence": [f"{code} ({kind}, {d.get('ecu_label', '')}): {describe(code)}"],
            "checks": adv["checks"], "fix": adv["fix"], "causes": adv["causes"], "drive": adv.get("drive"),
            "diy": adv["diy"], "cost": adv["cost"], "kind": "code", "evidence_kinds": ["code"],
        })

    # 4. strong live / trend findings not explained by an issue
    for f in live_findings + trend_findings:
        if f.tag in claimed_tags or f.tag == "live:overheat_critical":
            continue
        actions.append({"id": f.id, "title": f.title, "system": f.system, "urgency": f.severity,
                        "confidence": "Medium" if f.confidence >= 0.6 else "Low", "evidence": [f.detail],
                        "checks": [], "fix": "", "kind": "finding", "evidence_kinds": [f.source],
                        "diy": "", "cost": "?"})

    # 5. reported symptoms with no matching issue -> symptom checker's best guess
    for res in sym_results:
        if f"sym:{res['id']}" in claimed_tags or not res["causes"]:
            continue
        top = res["causes"][0]
        actions.append({
            "id": f"sym_{res['id']}", "title": f"{top['title']}", "system": res["group"] if res["group"] in SYSTEMS else "Engine",
            "urgency": max(1, top["urgency"]), "confidence": "Medium" if top["likelihood"] >= 45 else "Low",
            "evidence": [f"You reported: {res['title'].lower()} ({top['likelihood']} % likely cause)"] + top["supported_by"],
            "checks": [top["check"]], "fix": top["fix"], "diy": top["diy"], "cost": top["cost"],
            "kind": "symptom", "evidence_kinds": ["symptom"],
        })

    # 6. service items
    for f in service_findings:
        if f.tag in claimed_tags:
            continue
        actions.append({"id": f.id, "title": f.title, "system": "Service", "urgency": f.severity, "confidence": "High",
                        "evidence": [f.detail], "checks": [], "fix": "", "kind": "service",
                        "evidence_kinds": ["service"], "diy": "", "cost": "low"})

    if vehicle.get("transmission") == "4AT":
        for a in actions:
            for k in ("title", "fix"):
                if isinstance(a.get(k), str):
                    a[k] = _for_4at(a[k])
            a["checks"] = [_for_4at(c) for c in a.get("checks", [])]

    cost_rank = {"low": 0, "medium": 1, "high": 2, "?": 1}
    conf_rank = {"High": 0, "Medium": 1, "Low": 2}
    actions.sort(key=lambda a: (-a["urgency"], conf_rank.get(a["confidence"], 1), cost_rank.get(a.get("cost", "?"), 1)))
    for i, a in enumerate(actions, 1):
        a["rank"] = i
        a["urgency_label"] = URGENCY_NAMES.get(a["urgency"], "")
        a["cost_label"] = COST_NAMES.get(a.get("cost", "?"), "")

    # health per system
    factor = {"High": 1.0, "Medium": 0.8, "Low": 0.55}
    systems = []
    for name in SYSTEMS:
        pen = sum(PENALTY.get(a["urgency"], 0) * factor.get(a["confidence"], 0.8)
                  for a in actions if a["system"] == name)
        score = max(0, round(100 - pen))
        worst = max([a["urgency"] for a in actions if a["system"] == name], default=0)
        systems.append({"name": name, "score": score, "worst": worst, "label": URGENCY_NAMES[worst]})
    worst = max([a["urgency"] for a in actions], default=0)
    status = {4: "stop", 3: "fix", 2: "attention", 1: "watch", 0: "good"}[worst]

    notes = _notes(scan, vehicle) + [n.to_dict() for n in m06_notes]
    headline = _headline(status, actions)
    return {
        "generated_ts": now, "status": status, "headline": headline, "systems": systems, "actions": actions,
        "notes": notes, "symptom_results": sym_results,
        "evidence": {t: {"strength": e.strength, "severity": e.severity, "kind": e.kind, "reasons": e.reasons}
                     for t, e in ev.items.items()},
    }


def _for_4at(text: str) -> str:
    """The knowledge base talks about the CVT (260 series); adapt wording for 4-speed automatics."""
    return (text.replace("genuine Toyota CVT Fluid TC", "the ATF type marked on the dipstick (T-IV or WS)")
                .replace("Toyota CVT Fluid TC", "the ATF type marked on the dipstick (T-IV or WS)")
                .replace("CVT fluid", "gearbox fluid").replace("CVT", "gearbox"))


def _system_for_code(code: str) -> str:
    if code[:3] in ("P07", "P08", "P09"):
        return "Transmission"
    if code.startswith("P03"):
        return "Ignition"
    if code.startswith(("P04", "P2195", "P2196", "P013", "P014")):
        return "Emissions"
    if code.startswith(("P01", "P02", "P2")):
        return "Fuel & air"
    if code.startswith(("P056", "P0617")):
        return "Charging"
    return "Engine"


def _notes(scan: dict | None, vehicle: dict) -> list[dict]:
    notes = []
    if not scan:
        return notes
    info = scan.get("info", {})
    status = scan.get("status", {})
    dist = info.get("distance_since_clear")
    mins = info.get("minutes_since_clear")
    warm = info.get("warmups_since_clear")
    monitors = status.get("monitors", {})
    incomplete = [k for k, m in monitors.items() if m.get("supported") and not m.get("complete")]
    recently = (dist is not None and dist < 100) or (mins is not None and mins < 300) or (warm is not None and warm < 5)
    if recently:
        notes.append({"id": "codes_cleared", "severity": 2, "title": "Fault codes were cleared recently",
                      "detail": f"Codes were cleared {dist if dist is not None else '?'} km ago "
                                f"({warm if warm is not None else '?'} warm-ups). Self-tests not finished yet: "
                                f"{', '.join(i.replace('_', ' ') for i in incomplete) or 'none'}. Problems can hide until the "
                                "car has done a few days of mixed driving - re-scan then. If you're buying this car, ask why "
                                "codes were cleared."})
    elif incomplete:
        notes.append({"id": "readiness", "severity": 1, "title": "Some self-tests haven't completed",
                      "detail": f"Not yet complete: {', '.join(i.replace('_', ' ') for i in incomplete)}. Normal after a "
                                "battery disconnect; if it stays like this for weeks, something is stopping the test running."})
    if status.get("mil"):
        notes.append({"id": "mil", "severity": 3, "title": "Check-engine light is ON",
                      "detail": (f"Driven {info['mil_distance']:.0f} km with the light on. "
                                 if isinstance(info.get("mil_distance"), (int, float)) else "")
                                + "See the fix list for what's causing it."})
    std = info.get("obd_standard")
    if std:
        notes.append({"id": "standard", "severity": 0, "title": f"Car reports: {std}",
                      "detail": "Japanese-market cars often report JOBD. Generic codes work; Toyota-only codes may need Techstream."})
    return notes


def _headline(status: str, actions: list[dict]) -> str:
    if not actions:
        return "No problems found. Keep driving with the monitor connected to build up its baseline."
    top = actions[0]
    n = len([a for a in actions if a["urgency"] >= 2])
    lead = {
        "stop": "Stop and check now:", "fix": "Fix this week:", "attention": "Fix soon:", "watch": "Keep an eye on:",
        "good": "",
    }[status]
    more = f" (+{n - 1} more to fix)" if n > 1 else ""
    return f"{lead} {top['title']}{more}"
