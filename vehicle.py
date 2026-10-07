"""Toyota Allion variants. The chassis code is on the plate in the engine bay and in the
logbook (e.g. NZT260-3012345). It tells us the engine, size and gearbox."""
from __future__ import annotations

CHASSIS = {
    # 260 series (2007-2021) - Super CVT-i gearbox
    "NZT260": {"generation": "T260", "engine": "1NZ-FE", "displacement_l": 1.5, "transmission": "CVT"},
    "ZRT260": {"generation": "T260", "engine": "2ZR-FE / 2ZR-FAE", "displacement_l": 1.8, "transmission": "CVT"},
    "ZRT261": {"generation": "T260", "engine": "3ZR-FAE", "displacement_l": 2.0, "transmission": "CVT"},
    "ZRT265": {"generation": "T260", "engine": "2ZR-FE (4WD)", "displacement_l": 1.8, "transmission": ""},
    # 240 series (2001-2007) - 4-speed automatic
    "NZT240": {"generation": "T240", "engine": "1NZ-FE", "displacement_l": 1.5, "transmission": "4AT"},
    "ZZT240": {"generation": "T240", "engine": "1ZZ-FE", "displacement_l": 1.8, "transmission": "4AT"},
    "ZZT245": {"generation": "T240", "engine": "1ZZ-FE (4WD)", "displacement_l": 1.8, "transmission": "4AT"},
    "AZT240": {"generation": "T240", "engine": "1AZ-FSE (D-4)", "displacement_l": 2.0, "transmission": "4AT"},
}


def resolve_vehicle(cfg: dict) -> dict:
    """Fill engine/size/gearbox from the chassis code unless the user set them."""
    v = dict(cfg)
    code = (v.get("chassis_code") or "").upper().split("-")[0].strip()
    base = CHASSIS.get(code, {})
    v["chassis_code"] = code or "unknown"
    v["generation"] = v.get("generation") or base.get("generation", "T260")
    v["engine"] = v.get("engine") or base.get("engine", "1NZ-FE")
    v["displacement_l"] = float(v.get("displacement_l") or base.get("displacement_l", 1.5))
    trans = (v.get("transmission") or base.get("transmission") or "CVT").upper()
    v["transmission"] = "CVT" if trans.startswith("CVT") else "4AT"
    v["cylinders"] = int(v.get("cylinders") or 4)
    v["altitude_m"] = float(v.get("altitude_m", 1795))
    v["known_chassis"] = bool(base)
    v["name"] = v.get("name") or "My Allion"
    return v


def baro_from_altitude(alt_m: float) -> float:
    """Standard-atmosphere air pressure in kPa (Nairobi ~1,795 m -> ~81.6 kPa)."""
    return round(101.325 * (1 - 2.25577e-5 * alt_m) ** 5.25588, 1)


def air_density(baro_kpa: float, iat_c: float) -> float:
    """kg/m^3 from pressure and intake air temperature."""
    return baro_kpa * 1000 / (287.05 * (iat_c + 273.15))


def volumetric_efficiency(maf_gs: float, rpm: float, disp_l: float, baro_kpa: float, iat_c: float) -> float | None:
    """How full the cylinders are filling (0-1+). Low at full throttle = restricted intake,
    clogged air filter, dirty MAF sensor or blocked exhaust/catalyst."""
    if rpm < 500 or maf_gs <= 0 or disp_l <= 0:
        return None
    rho = air_density(baro_kpa, iat_c)
    theoretical = rpm * disp_l * rho / 120  # g/s at 100% VE for a 4-stroke
    return maf_gs / theoretical if theoretical > 0 else None


def fuel_lph_from_maf(maf_gs: float, afr: float = 14.7, density_kg_l: float = 0.745) -> float:
    """Petrol used per hour estimated from air flow (assumes the stoichiometric mixture)."""
    return maf_gs * 3600 / afr / (density_kg_l * 1000)
