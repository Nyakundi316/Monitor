"""Service schedule for a Toyota Allion used in Kenyan conditions.

Nairobi traffic, dust and short trips count as 'severe' use in Toyota's terms, so these
intervals are shorter than the 'normal' schedule. They are sensible defaults - adjust
them in config.toml to match your owner's manual or your mechanic's advice.
"""

ITEMS = [
    {"id": "engine_oil", "title": "Engine oil and filter", "km": 5000, "months": 6,
     "why": "Old oil wears the engine and upsets the VVT-i system.",
     "how": "Toyota 0W-20 or 5W-30 (check the oil cap/manual) + new filter. Check the level every two weeks."},
    {"id": "air_filter", "title": "Air filter", "km": 15000, "months": 12,
     "why": "A blocked filter costs power and fuel; a badly fitted one lets dust ruin the MAF sensor.",
     "how": "Inspect at every oil change; replace when you can't see light through it."},
    {"id": "cabin_filter", "title": "Cabin (AC) filter", "km": 15000, "months": 12,
     "why": "Weak AC airflow and musty smells.", "how": "Behind the glovebox - a five-minute DIY job."},
    {"id": "spark_plugs", "title": "Spark plugs (iridium)", "km": 100000, "months": 96,
     "why": "Worn plugs strain the coils and cause misfires.", "how": "Denso/NGK iridium, all four at once."},
    {"id": "cvt_fluid", "title": "CVT fluid", "km": 40000, "months": 36, "applies": "CVT",
     "why": "Old fluid is the main cause of CVT judder, whine and failure.",
     "how": "Genuine Toyota CVT Fluid TC only (never universal ATF). Drain and fill, no pressure flush."},
    {"id": "atf", "title": "Automatic gearbox fluid", "km": 40000, "months": 36, "applies": "4AT",
     "why": "Keeps shifts smooth and the gearbox cool.", "how": "Use the fluid type marked on the dipstick (T-IV or WS)."},
    {"id": "coolant", "title": "Coolant", "km": 80000, "months": 60,
     "why": "Protects against overheating and corrosion.",
     "how": "Toyota Super Long Life Coolant (pink). If you don't know what's in it, flush and refill."},
    {"id": "brake_fluid", "title": "Brake fluid", "km": 40000, "months": 24,
     "why": "Brake fluid absorbs water, which lowers its boiling point.", "how": "DOT 3 / DOT 4 flush and bleed."},
    {"id": "brake_pads", "title": "Brake pads and discs - inspect", "km": 10000, "months": 6,
     "why": "Stop-go traffic wears pads quickly.", "how": "Replace pads under 3 mm."},
    {"id": "tyres", "title": "Tyre rotation and pressures", "km": 10000, "months": 6,
     "why": "Even wear and lower fuel use.", "how": "Pressures cold, to the label on the driver's door pillar."},
    {"id": "alignment", "title": "Wheel alignment and balancing", "km": 10000, "months": 12,
     "why": "Potholes knock alignment out, wearing tyres and pulling the car.", "how": "Also after any hard pothole hit."},
    {"id": "suspension", "title": "Suspension check (links, bushes, shocks)", "km": 20000, "months": 12,
     "why": "Stabiliser links and bushes wear fast on rough roads.", "how": "On a lift: check for play and leaking shocks."},
    {"id": "drive_belt", "title": "Drive belt - inspect", "km": 40000, "months": 36,
     "why": "A snapped belt stops the alternator, water pump and AC.", "how": "Replace if cracked, glazed or squealing."},
    {"id": "throttle_body", "title": "Throttle body clean", "km": 40000, "months": 36,
     "why": "Carbon causes idle hunting and stalling.", "how": "Throttle-body cleaner, then let it idle 10 minutes."},
    {"id": "battery_check", "title": "Battery test and terminal clean", "km": 20000, "months": 12,
     "why": "Batteries last about 2-3 years here; the monitor also tracks voltage.", "how": "Free load test at most battery shops."},
]

ITEM_INDEX = {i["id"]: i for i in ITEMS}
