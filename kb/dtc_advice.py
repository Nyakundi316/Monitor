"""What a fault code means for an Allion owner, and what to do about it.

Grouped by fault family. `tags` feed the diagnosis engine; {cyl} is filled in for
cylinder-specific codes. Urgency: 1 watch, 2 fix soon, 3 fix this week, 4 stop driving.
Cost: low (cleaning or a small part), medium (a sensor, coil set or battery),
high (catalytic converter, CVT or engine work).
"""

GROUPS = {
    "lean": {
        "title": "Engine running lean - too much air or too little fuel",
        "tags": ["code:lean"], "urgency": 2, "cost": "low", "diy": "Often DIY",
        "causes": [
            "Air leak after the air-flow sensor: cracked intake boot, PCV hose or a vacuum hose",
            "Dirty air-flow (MAF) sensor - very common with Nairobi dust",
            "Weak fuel pump, blocked fuel filter or poor-quality fuel",
            "Exhaust leak in front of the front A/F sensor",
        ],
        "checks": [
            "With the engine idling, listen around the intake for a hiss",
            "Squeeze the rubber intake boot between the air box and throttle body - look for cracks underneath",
            "Compare fuel trims at idle and at 2,500 rpm (the Live tab does this for you)",
        ],
        "fix": "Fix the leak or clean the MAF sensor with MAF-sensor cleaner spray, then clear the code and re-check trims after two drives.",
        "drive": "Safe to drive gently. Fix within a few weeks - running lean causes misfires and can damage the catalytic converter.",
    },
    "rich": {
        "title": "Engine running rich - too much fuel",
        "tags": ["code:rich"], "urgency": 2, "cost": "medium", "diy": "Mechanic",
        "causes": [
            "Leaking or dirty fuel injector",
            "Front A/F sensor reading wrongly",
            "Coolant temperature sensor telling the computer the engine is cold",
            "Oil-contaminated air filter or MAF sensor over-reading",
        ],
        "checks": ["Look for black smoke or a strong fuel smell", "Check the coolant temperature reading once warm (should be 85-95 °C)"],
        "fix": "Have injectors and the A/F sensor tested; replace whichever is faulty.",
        "drive": "Drive gently; a rich engine wastes fuel and overheats the catalytic converter.",
    },
    "misfire_random": {
        "title": "Engine misfiring on several cylinders",
        "tags": ["code:misfire_random"], "urgency": 3, "cost": "low", "diy": "Often DIY",
        "causes": [
            "Worn spark plugs (iridium plugs last about 100,000 km)",
            "Air leak making the mixture lean",
            "Poor or contaminated fuel, or low fuel pressure",
            "Dirty injectors",
        ],
        "checks": ["Check the age of the spark plugs", "Check fuel trims for a lean condition",
                   "Fill up at a reputable station and see if it improves"],
        "fix": "Replace spark plugs if old; fix any lean condition; consider an injector clean.",
        "drive": "If the check-engine light is FLASHING, stop driving hard - unburnt fuel is overheating the catalytic converter.",
    },
    "misfire_cyl": {
        "title": "Cylinder {cyl} misfiring",
        "tags": ["code:misfire_cyl{cyl}"], "urgency": 3, "cost": "low", "diy": "DIY-friendly",
        "causes": [
            "Failing ignition coil on cylinder {cyl} (very common on the 1NZ and 2ZR engines)",
            "Worn or oil-fouled spark plug on cylinder {cyl}",
            "Injector on cylinder {cyl} blocked or not firing",
        ],
        "checks": [
            "Swap the cylinder {cyl} coil with a neighbouring cylinder, clear the code, drive - if the misfire moves, the coil is bad",
            "Remove the cylinder {cyl} plug: look for wear, oil or a cracked insulator",
        ],
        "fix": "Replace the faulty coil (Denso or genuine). If plugs are over ~100,000 km, replace all four.",
        "drive": "Fix this week. If the check-engine light flashes, avoid hard acceleration until fixed.",
    },
    "coil_circuit": {
        "title": "Ignition coil {cyl} circuit fault",
        "tags": ["code:coil_cyl{cyl}"], "urgency": 3, "cost": "low", "diy": "DIY-friendly",
        "causes": ["Ignition coil {cyl} failed", "Loose or corroded connector on coil {cyl}", "Damaged wiring to the coil"],
        "checks": ["Check the coil connector is clipped in and clean", "Swap the coil with another cylinder to confirm"],
        "fix": "Replace the coil; repair the connector if corroded.",
        "drive": "Fix this week - the cylinder may be misfiring.",
    },
    "igniter": {
        "title": "Ignition (igniter) signal fault",
        "tags": ["code:coil_any"], "urgency": 3, "cost": "low", "diy": "Mechanic",
        "causes": ["A failing ignition coil (the coils contain the igniter)", "Wiring or connector fault"],
        "checks": ["Check for misfires on a particular cylinder", "Check coil connectors"],
        "fix": "Find and replace the failing coil.",
        "drive": "Fix this week.",
    },
    "injector": {
        "title": "Fuel injector {cyl} electrical fault",
        "tags": ["code:injector_cyl{cyl}"], "urgency": 3, "cost": "medium", "diy": "Mechanic",
        "causes": ["Injector {cyl} coil open or shorted", "Connector or wiring fault"],
        "checks": ["Check the injector connector", "Measure injector resistance (about 12 Ω on most Toyota injectors)"],
        "fix": "Repair wiring or replace the injector.",
        "drive": "Fix this week - that cylinder may not be getting fuel.",
    },
    "cat": {
        "title": "Catalytic converter not cleaning the exhaust well enough",
        "tags": ["code:cat"], "urgency": 2, "cost": "high", "diy": "Mechanic",
        "causes": [
            "Catalytic converter worn out or damaged by misfires / rich running",
            "Rear oxygen sensor lazy or faulty (cheaper - check first)",
            "Exhaust leak near the rear oxygen sensor",
        ],
        "checks": ["Fix any misfire or fuel-mixture problem first", "Check for exhaust leaks",
                   "Watch the rear O2 sensor: it should be steady around 0.6-0.8 V, not swinging"],
        "fix": "Replace the rear O2 sensor if faulty; otherwise the converter (genuine or good-quality aftermarket).",
        "drive": "Safe to drive. A badly blocked converter causes power loss - then fix urgently.",
    },
    "thermostat": {
        "title": "Engine not reaching normal temperature (thermostat)",
        "tags": ["code:thermostat"], "urgency": 2, "cost": "low", "diy": "Moderate DIY",
        "causes": ["Thermostat stuck open", "Coolant temperature sensor reading low", "Low coolant level"],
        "checks": ["After 15 minutes of driving the temperature should be 85-95 °C",
                   "Check coolant level when cold"],
        "fix": "Replace the thermostat and gasket; refill with Toyota Super Long Life Coolant (pink) and bleed the air out.",
        "drive": "Safe to drive, but fuel use goes up and the heater is weak. Fix within a month.",
    },
    "ect_sensor": {
        "title": "Coolant temperature sensor fault",
        "tags": ["code:ect"], "urgency": 2, "cost": "low", "diy": "Moderate DIY",
        "causes": ["Coolant temperature sensor failed", "Connector or wiring fault", "Low coolant around the sensor"],
        "checks": ["Compare the coolant reading with the dashboard gauge", "Check the connector"],
        "fix": "Replace the sensor (small, inexpensive part).",
        "drive": "Drive carefully: the radiator fan and fuelling rely on this sensor.",
    },
    "overheat": {
        "title": "Engine overheated",
        "tags": ["code:overheat"], "urgency": 4, "cost": "medium", "diy": "Mechanic",
        "causes": ["Low coolant from a leak (water pump and radiator hoses are common)", "Radiator fan not running",
                   "Thermostat stuck closed", "Blocked radiator", "Head gasket failure"],
        "checks": ["When cold: check coolant level and look for pink crust around the water pump and hoses",
                   "With the engine hot and AC on, the radiator fan must run"],
        "fix": "Find the cause before driving far - overheating can warp the cylinder head.",
        "drive": "Do not drive until checked. Never open the radiator cap while hot.",
    },
    "maf": {
        "title": "Air-flow (MAF) sensor fault",
        "tags": ["code:maf"], "urgency": 2, "cost": "low", "diy": "DIY-friendly",
        "causes": ["Dirty MAF sensor (dust past a poorly fitted air filter)", "Air leak near the sensor",
                   "Sensor failed or connector corroded"],
        "checks": ["Check the air filter fits properly and the box clips are closed", "Inspect the connector"],
        "fix": "Clean with MAF-sensor cleaner spray (never touch the wires); replace if the code returns.",
        "drive": "Safe to drive gently; power and fuel economy suffer.",
    },
    "iat": {
        "title": "Intake air temperature sensor fault",
        "tags": ["code:iat"], "urgency": 1, "cost": "low", "diy": "DIY-friendly",
        "causes": ["The sensor is built into the MAF sensor on these engines - sensor or connector fault"],
        "checks": ["Check the MAF connector", "The reading should be close to outside temperature on a cold start"],
        "fix": "Clean or replace the MAF sensor assembly.",
        "drive": "Safe to drive.",
    },
    "map": {
        "title": "Pressure sensor fault",
        "tags": ["code:map"], "urgency": 2, "cost": "low", "diy": "Mechanic",
        "causes": ["Sensor or its vacuum hose", "Wiring"], "checks": ["Check the vacuum hose to the sensor"],
        "fix": "Replace the sensor or hose.", "drive": "Drive gently.",
    },
    "throttle": {
        "title": "Electronic throttle fault",
        "tags": ["code:throttle"], "urgency": 3, "cost": "medium", "diy": "Mechanic",
        "causes": ["Carbon build-up making the throttle stick", "Throttle body motor or sensor failing",
                   "Accelerator pedal sensor or wiring fault"],
        "checks": ["Look inside the throttle body for black carbon", "Check connectors on the throttle body and pedal"],
        "fix": "Clean the throttle body first; replace the throttle body or pedal sensor if the fault returns.",
        "drive": "The car may go into 'limp mode' with very little power. Get it checked this week.",
    },
    "o2": {
        "title": "Oxygen / air-fuel sensor fault",
        "tags": ["code:o2"], "urgency": 2, "cost": "medium", "diy": "Moderate DIY",
        "causes": ["Sensor aged or contaminated (oil or coolant burning, poor fuel)", "Exhaust leak near the sensor",
                   "Wiring damaged by exhaust heat"],
        "checks": ["Inspect the sensor wiring near the exhaust", "Check for exhaust leaks"],
        "fix": "Replace the sensor (Denso is the original supplier) with an O2-sensor socket.",
        "drive": "Safe to drive; fuel use rises and the catalytic converter can suffer over time.",
    },
    "o2_heater": {
        "title": "Oxygen sensor heater fault",
        "tags": ["code:o2_heater"], "urgency": 2, "cost": "medium", "diy": "Moderate DIY",
        "causes": ["The heater inside the sensor has burnt out (most common)", "Blown EFI / A/F fuse", "Wiring"],
        "checks": ["Check the relevant fuse", "Measure heater resistance at the sensor plug"],
        "fix": "Replace the sensor - the heater can't be repaired separately.",
        "drive": "Safe to drive; mixture control is poorer until the sensor warms up.",
    },
    "evap_leak": {
        "title": "Fuel vapour (EVAP) leak",
        "tags": ["code:evap_leak"], "urgency": 1, "cost": "low", "diy": "DIY-friendly",
        "causes": ["Fuel cap loose, worn or not original (most common)", "Cracked EVAP hose",
                   "Charcoal canister or valve leaking"],
        "checks": ["Tighten the fuel cap until it clicks; check its rubber seal"],
        "fix": "Replace the fuel cap if the seal is worn; otherwise smoke-test the EVAP system.",
        "drive": "Safe to drive. A fuel smell around the car needs checking sooner.",
    },
    "evap": {
        "title": "Fuel vapour (EVAP) system fault",
        "tags": ["code:evap"], "urgency": 1, "cost": "low", "diy": "Mechanic",
        "causes": ["Purge valve (VSV) sticking", "Vent / switching valve or pump fault", "Pressure sensor"],
        "checks": ["Check hoses and connectors around the charcoal canister"],
        "fix": "Replace the faulty valve.", "drive": "Safe to drive.",
    },
    "vvt": {
        "title": "Variable valve timing (VVT-i) fault",
        "tags": ["code:vvt"], "urgency": 2, "cost": "low", "diy": "Moderate DIY",
        "causes": ["Old or low engine oil (VVT-i runs on oil pressure)", "Sticking oil control valve (OCV) or its blocked filter screen",
                   "OCV connector", "Worn timing chain (if P0016/P0017 too)"],
        "checks": ["Check oil level and how old the oil is", "Remove and clean the OCV and its small filter screen"],
        "fix": "Change the oil first, then clean or replace the OCV.",
        "drive": "Drive gently; power and economy suffer. Low oil can damage the engine - check it today.",
    },
    "cam_crank": {
        "title": "Camshaft and crankshaft out of step",
        "tags": ["code:cam_crank", "code:vvt"], "urgency": 3, "cost": "high", "diy": "Mechanic",
        "causes": ["Stretched timing chain or worn chain tensioner", "VVT-i gear (cam phaser) sticking", "Sensor fault"],
        "checks": ["Listen for a rattle from the right side of the engine for a few seconds after a cold start",
                   "Check oil level and quality"],
        "fix": "A mechanic should check chain stretch; replace chain, tensioner and VVT gear if worn.",
        "drive": "Avoid long trips and high revs until checked - a jumped chain can wreck the engine.",
    },
    "knock": {
        "title": "Knock sensor circuit fault",
        "tags": ["code:knock"], "urgency": 2, "cost": "medium", "diy": "Mechanic",
        "causes": ["Knock sensor or its wiring", "Connector corroded"],
        "checks": ["Check the connector", "Use quality fuel - knocking itself is often fuel-related"],
        "fix": "Replace the sensor or repair wiring.", "drive": "Drive gently; the engine runs with retarded timing.",
    },
    "crank_cam_sensor": {
        "title": "Crankshaft / camshaft position sensor fault",
        "tags": ["code:crank_cam_sensor"], "urgency": 3, "cost": "low", "diy": "Mechanic",
        "causes": ["Sensor failing (often cuts out when hot)", "Wiring or connector", "Oil contamination"],
        "checks": ["Does the car stall when hot and restart after cooling? That points to this sensor"],
        "fix": "Replace the sensor.", "drive": "The engine can cut out without warning - fix this week.",
    },
    "speed_sensor": {
        "title": "Vehicle speed signal fault",
        "tags": ["code:vss"], "urgency": 2, "cost": "low", "diy": "Mechanic",
        "causes": ["Speed sensor or ABS wheel sensor signal", "Wiring"],
        "checks": ["Does the speedometer work?"], "fix": "Repair the sensor or wiring.",
        "drive": "Gear-change quality and ABS can be affected.",
    },
    "idle": {
        "title": "Idle speed not controlled properly",
        "tags": ["code:idle"], "urgency": 2, "cost": "low", "diy": "DIY-friendly",
        "causes": ["Carbon in the throttle body (very common on Toyotas)", "Air leak", "PCV valve stuck"],
        "checks": ["Look for carbon on the throttle plate", "Listen for vacuum leaks"],
        "fix": "Clean the throttle body, then let it idle 10 minutes so the computer relearns idle.",
        "drive": "Safe to drive; the car may stall at junctions.",
    },
    "voltage": {
        "title": "Electrical supply voltage fault",
        "tags": ["code:voltage"], "urgency": 2, "cost": "medium", "diy": "Mechanic",
        "causes": ["Weak battery", "Alternator not charging (or overcharging)", "Loose or corroded battery terminals or earth strap"],
        "checks": ["Check the Battery card: engine running should read 13.8-14.7 V",
                   "Clean and tighten battery terminals"],
        "fix": "Test battery and alternator at an auto-electrician; replace the faulty one.",
        "drive": "If the battery light is on, the car may stop when the battery runs flat.",
    },
    "ecu": {
        "title": "Engine computer internal fault",
        "tags": ["code:ecu"], "urgency": 2, "cost": "high", "diy": "Mechanic",
        "causes": ["Low or unstable supply voltage (check this first)", "Water damage", "Computer fault"],
        "checks": ["Check battery voltage and earth connections"], "fix": "Rule out power problems before replacing anything.",
        "drive": "Get it checked if the engine behaves oddly.",
    },
    "starter": {
        "title": "Starting system fault",
        "tags": ["code:start"], "urgency": 2, "cost": "medium", "diy": "Mechanic",
        "causes": ["Weak battery", "Starter relay or starter motor", "Neutral / park switch"],
        "checks": ["Does it start in N as well as P?"], "fix": "Test battery first, then starter circuit.",
        "drive": "Safe once running, but it may not restart.",
    },
    "trans_general": {
        "title": "Transmission computer has a fault",
        "tags": ["code:trans"], "urgency": 2, "cost": "medium", "diy": "Mechanic",
        "causes": ["The transmission computer stored its own code - read it with a Toyota scan tool (Techstream)"],
        "checks": ["Have the transmission codes read", "Check CVT fluid condition"],
        "fix": "Depends on the transmission code.", "drive": "Drive gently until checked.",
    },
    "trans_sensor": {
        "title": "Transmission sensor or switch fault",
        "tags": ["code:trans"], "urgency": 2, "cost": "medium", "diy": "Mechanic",
        "causes": ["Sensor or wiring at the transmission", "Gear selector switch out of adjustment",
                   "Fluid-temperature sensor (inside the transmission)"],
        "checks": ["Check connectors on the transmission", "Does the gear indicator match the lever?"],
        "fix": "Repair wiring or replace the sensor.", "drive": "Drive gently; shifting may be harsh.",
    },
    "trans_lockup": {
        "title": "Torque converter lock-up fault",
        "tags": ["code:trans", "code:trans_lockup"], "urgency": 2, "cost": "high", "diy": "Mechanic",
        "causes": ["Old or degraded CVT fluid (check first)", "Lock-up solenoid or valve body", "Torque converter wear"],
        "checks": ["Check fluid level and condition at a Toyota-experienced garage",
                   "Feel for shudder at 40-70 km/h with light throttle"],
        "fix": "Fresh Toyota CVT Fluid TC (drain and fill, not a pressure flush); solenoid / valve body if it persists.",
        "drive": "Drive gently; avoid hard acceleration and steep loaded climbs.",
    },
    "trans_pressure": {
        "title": "Transmission hydraulic pressure / solenoid fault",
        "tags": ["code:trans"], "urgency": 3, "cost": "high", "diy": "Mechanic",
        "causes": ["Low or old CVT fluid", "Solenoid or valve body", "Internal wear"],
        "checks": ["Check fluid level/condition straight away"],
        "fix": "Specialist transmission diagnosis.",
        "drive": "Drive as little as possible - continuing can turn a solenoid job into a gearbox replacement.",
    },
    "brake_switch": {
        "title": "Brake-light switch signal",
        "tags": ["code:brake_switch"], "urgency": 2, "cost": "low", "diy": "DIY-friendly",
        "causes": ["Brake-light switch worn or out of adjustment", "Blown brake-light fuse"],
        "checks": ["Ask someone to check the brake lights come on"], "fix": "Replace or adjust the switch.",
        "drive": "Make sure your brake lights work - it's a safety issue.",
    },
    "network": {
        "title": "Computers not talking to each other",
        "tags": ["code:network"], "urgency": 2, "cost": "medium", "diy": "Mechanic",
        "causes": ["Low battery voltage during starting", "Corroded connector or damaged wiring", "A module has failed"],
        "checks": ["Check battery health first", "Clear and see if it returns"],
        "fix": "Auto-electrician to trace the network fault.", "drive": "Usually safe; watch for other warning lights.",
    },
}


def _map() -> dict[str, tuple[str, int | None]]:
    m: dict[str, tuple[str, int | None]] = {}

    def add(group, codes, cyl=None):
        for c in codes:
            m[c] = (group, cyl)

    add("lean", ["P0171", "P0174", "P0170"])
    add("rich", ["P0172", "P0175"])
    add("misfire_random", ["P0300"])
    for c in range(1, 5):
        add("misfire_cyl", [f"P030{c}"], c)
        add("coil_circuit", [f"P035{c}"], c)
        add("injector", [f"P020{c}"], c)
    add("igniter", ["P1300", "P1310"])
    add("cat", ["P0420", "P0421"])
    add("thermostat", ["P0128", "P0125"])
    add("ect_sensor", ["P0115", "P0116", "P0117", "P0118"])
    add("overheat", ["P0217"])
    add("maf", ["P0100", "P0101", "P0102", "P0103", "P0104"])
    add("iat", ["P0110", "P0111", "P0112", "P0113"])
    add("map", ["P0105", "P0106", "P0107", "P0108"])
    add("throttle", ["P0120", "P0121", "P0122", "P0123", "P0220", "P0222", "P0223", "P2102", "P2103",
                     "P2111", "P2112", "P2118", "P2119", "P2120", "P2122", "P2123", "P2125", "P2127",
                     "P2128", "P2135", "P2138", "P1120", "P1121", "P1125", "P1126", "P1127", "P1128",
                     "P1129", "P1633", "P0657"])
    add("o2", ["P0130", "P0131", "P0132", "P0133", "P0134", "P0136", "P0137", "P0138", "P0139", "P0140",
               "P2195", "P2196", "P2237", "P2238", "P2239", "P2251", "P2252", "P2253", "P2A00", "P1130", "P1133"])
    add("o2_heater", ["P0030", "P0031", "P0032", "P0036", "P0037", "P0038", "P0135", "P0141", "P1135"])
    add("evap_leak", ["P0440", "P0442", "P0455", "P0456"])
    add("evap", ["P0441", "P0443", "P0446", "P0450", "P0451", "P0452", "P0453", "P2401", "P2402", "P2419", "P2420"])
    add("vvt", ["P0010", "P0011", "P0012", "P0013", "P0014", "P0015", "P1346", "P1349", "P1656"])
    add("cam_crank", ["P0016", "P0017"])
    add("knock", ["P0325", "P0327", "P0328"])
    add("crank_cam_sensor", ["P0335", "P0336", "P0339", "P0340", "P0341", "P1335"])
    add("speed_sensor", ["P0500", "P0503"])
    add("idle", ["P0505", "P0506", "P0507"])
    add("voltage", ["P0560", "P0562", "P0563", "P1600"])
    add("ecu", ["P0600", "P0601", "P0604", "P0606", "P0607", "P0630", "P2610"])
    add("starter", ["P0617", "P1604"])
    add("trans_general", ["P0700"])
    add("trans_sensor", ["P0705", "P0710", "P0711", "P0712", "P0713", "P0715", "P0717", "P0720", "P0722",
                         "P0725", "P0841", "P1780"])
    add("trans_lockup", ["P0741", "P2757", "P2759", "P2769", "P2770"])
    add("trans_pressure", ["P0746", "P0750", "P0755", "P0776", "P0778", "P0868", "P0962", "P0963", "P0966",
                           "P0967", "P0970", "P0971", "P0973", "P0974", "P0976", "P0977", "P2714"])
    add("brake_switch", ["P1520"])
    add("network", ["U0001", "U0100", "U0101", "U0121", "U0129", "U0140", "U0155"])
    return m


CODE_GROUP = _map()


def advice_for(code: str) -> dict:
    """Return advice for a code with {cyl} filled in. Unknown codes get family-level advice."""
    code = code.upper()
    group, cyl = CODE_GROUP.get(code, (None, None))
    if group is None:
        fam = "trans_general" if code[:3] in ("P07", "P08", "P09") else None
        if fam:
            group = fam
        else:
            return {
                "group": "unknown", "title": None, "tags": ["code:other"], "urgency": 2, "cost": "?",
                "diy": "Mechanic",
                "causes": ["This code isn't in the built-in library. Look it up with the exact code plus 'Toyota Allion'."],
                "checks": ["Note when it happens (cold, hot, at idle, at speed)"],
                "fix": "Get a second opinion from a Toyota-experienced mechanic.",
                "drive": "Drive gently until you know what it is.",
            }
    a = GROUPS[group]

    def fill(x):
        if isinstance(x, str):
            return x.replace("{cyl}", str(cyl) if cyl else "")
        if isinstance(x, list):
            return [fill(i) for i in x]
        return x

    out = {k: fill(v) for k, v in a.items()}
    out["group"] = group
    out["cylinder"] = cyl
    return out
