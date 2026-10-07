"""Service schedule status: what's due, what's overdue."""
from __future__ import annotations

import datetime as dt

from .kb.maintenance import ITEMS
from .rules import Finding


def status(store, vehicle: dict, overrides: dict | None = None, today: dt.date | None = None) -> dict:
    overrides = overrides or {}
    today = today or dt.date.today()
    odo, odo_ts = store.odometer()
    records = store.maintenance()
    items = []
    for item in ITEMS:
        applies = item.get("applies")
        if applies and applies != vehicle.get("transmission"):
            continue
        ov = overrides.get(item["id"], {})
        km_int = ov.get("km", item["km"])
        mo_int = ov.get("months", item["months"])
        rec = records.get(item["id"])
        entry = {"id": item["id"], "title": item["title"], "interval_km": km_int, "interval_months": mo_int,
                 "why": item["why"], "how": item["how"], "last_km": None, "last_date": None,
                 "km_left": None, "days_left": None, "status": "unknown"}
        if rec:
            entry["last_km"], entry["last_date"] = rec["last_km"], rec["last_date"]
            states = []
            if rec["last_km"] is not None and odo is not None and km_int:
                km_left = rec["last_km"] + km_int - odo
                entry["km_left"] = round(km_left)
                states.append("overdue" if km_left <= 0 else "due_soon" if km_left <= max(500, 0.1 * km_int) else "ok")
            if rec["last_date"] and mo_int:
                try:
                    last = dt.date.fromisoformat(rec["last_date"])
                    days_left = round(mo_int * 30.44 - (today - last).days)
                    entry["days_left"] = days_left
                    states.append("overdue" if days_left <= 0 else "due_soon" if days_left <= 30 else "ok")
                except ValueError:
                    pass
            for s in ("overdue", "due_soon", "ok"):
                if s in states:
                    entry["status"] = s
                    break
        items.append(entry)
    return {"odometer_km": odo, "odometer_set_ts": odo_ts, "items": items}


def findings(svc: dict) -> list[Finding]:
    out = []
    for it in svc["items"]:
        if it["status"] not in ("overdue", "due_soon"):
            continue
        overdue = it["status"] == "overdue"
        parts = []
        if it["km_left"] is not None:
            parts.append(f"{abs(it['km_left']):,} km {'overdue' if it['km_left'] <= 0 else 'left'}")
        if it["days_left"] is not None:
            d = it["days_left"]
            parts.append(f"{abs(d)} days {'overdue' if d <= 0 else 'left'}")
        sev = 1
        if overdue:
            sev = 2
            if it["id"] in ("engine_oil", "brake_fluid", "brake_pads") and it["km_left"] is not None \
                    and it["km_left"] < -0.4 * it["interval_km"]:
                sev = 3
        out.append(Finding(
            id=f"svc_{it['id']}", tag=f"svc:{it['id']}_{'overdue' if overdue else 'due'}", system="Service",
            severity=sev, title=f"{it['title']} {'overdue' if overdue else 'due soon'}",
            detail=(f"{' and '.join(parts).capitalize()}. " if parts else "") + f"{it['why']} {it['how']}",
            confidence=0.9, source="service",
            data={"item": it["id"]}))
    return out
