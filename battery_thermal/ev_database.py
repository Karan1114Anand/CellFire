"""
ev_database.py
===============
Curated reference data for electric vehicles sold in the Indian market and
the battery pack options fitted to them.

All figures (dimensions, mass, range, motor power, fast-charge time, pack
capacity, voltage, chemistry, series/parallel counts) are APPROXIMATE,
drawn from publicly available specification sheets and rounded for
illustration -- good enough to seed realistic design-parameter defaults,
but must not be read as certified OEM data. This is called out explicitly
in the generated report.

Each vehicle lists one or more compatible `battery_options` (by key into
BATTERY_OPTIONS) -- e.g. Standard Range / Long Range packs. Choosing a
vehicle/battery pre-fills the editable design-parameter fields in the
dashboard (a professional engineer would start from requirements like
these and then adjust them):
  - range_km, mass_kg, motor_kw, fast_charge_min, duty_cycle -> vehicle-level
    requirements that motivate a given pack design
  - chemistry               -> resistance-curve correction (NMC vs LFP)
  - cell_ah                 -> representative single-cell capacity used by
                                the FDM solver (Q_CELL_AH override, editable)
  - series (S) / parallel (P) -> pack topology, editable; used to compute
                                pack voltage/energy and report context --
                                the FDM itself always solves one
                                representative cell.
"""

# Typical per-cell voltage envelope by chemistry -- used to pre-fill the
# editable nominal/cutoff voltage fields in the design-parameters panel.
CHEMISTRY_CELL_VOLTAGE = {
    "NMC": {"nominal_v": 3.7, "cutoff_high_v": 4.2, "cutoff_low_v": 2.8},
    "LFP": {"nominal_v": 3.2, "cutoff_high_v": 3.65, "cutoff_low_v": 2.5},
}

BATTERY_OPTIONS = {
    "lfp_19": {"label": "19.2 kWh LFP (Standard Range)", "chemistry": "LFP",
               "pack_kwh": 19.2, "series": 96},
    "lfp_24": {"label": "24.0 kWh LFP (Long Range)", "chemistry": "LFP",
               "pack_kwh": 24.0, "series": 96},
    "lfp_25": {"label": "25.0 kWh LFP (Standard Range)", "chemistry": "LFP",
               "pack_kwh": 25.0, "series": 100},
    "lfp_35": {"label": "35.0 kWh LFP (Long Range)", "chemistry": "LFP",
               "pack_kwh": 35.0, "series": 108},
    "lfp_17": {"label": "17.3 kWh LFP", "chemistry": "LFP",
               "pack_kwh": 17.3, "series": 80},
    "lfp_60": {"label": "60.5 kWh LFP Blade (Long Range)", "chemistry": "LFP",
               "pack_kwh": 60.5, "series": 108},
    "lfp_72": {"label": "71.7 kWh LFP", "chemistry": "LFP",
               "pack_kwh": 71.7, "series": 108},
    "lfp_82": {"label": "82.5 kWh LFP Blade (Long Range)", "chemistry": "LFP",
               "pack_kwh": 82.5, "series": 110},
    "nmc_30": {"label": "30.0 kWh NMC (Medium Range)", "chemistry": "NMC",
               "pack_kwh": 30.0, "series": 96},
    "nmc_40": {"label": "40.5 kWh NMC (Long Range)", "chemistry": "NMC",
               "pack_kwh": 40.5, "series": 96},
    "nmc_34": {"label": "34.5 kWh NMC (Standard Range)", "chemistry": "NMC",
               "pack_kwh": 34.5, "series": 108},
    "nmc_39": {"label": "39.4 kWh NMC (Long Range)", "chemistry": "NMC",
               "pack_kwh": 39.4, "series": 108},
    "nmc_39_kona": {"label": "39.2 kWh NMC", "chemistry": "NMC",
                     "pack_kwh": 39.2, "series": 98},
    "nmc_50": {"label": "50.3 kWh NMC (Long Range)", "chemistry": "NMC",
               "pack_kwh": 50.3, "series": 104},
    "nmc_38": {"label": "38.0 kWh NMC", "chemistry": "NMC",
               "pack_kwh": 38.0, "series": 96},
    "nmc_59": {"label": "59.0 kWh NMC (Standard Range)", "chemistry": "NMC",
               "pack_kwh": 59.0, "series": 108},
    "nmc_79": {"label": "79.0 kWh NMC (Long Range)", "chemistry": "NMC",
               "pack_kwh": 79.0, "series": 108},
    "nmc_77": {"label": "77.4 kWh NMC (Long Range AWD)", "chemistry": "NMC",
               "pack_kwh": 77.4, "series": 102},
    "nmc_72": {"label": "72.6 kWh NMC (Long Range)", "chemistry": "NMC",
               "pack_kwh": 72.6, "series": 108},
}

# Pack figures above are the quoted, trustworthy ones: pack_kwh and series.
# Everything else is derived so the numbers cannot contradict each other.
#
# The thermal solver models ONE pouch cell of ~200 x 140 x 6 mm (168 cm^3).
# At a realistic pouch-cell energy density that footprint holds ~20 Ah of NMC
# (~440 Wh/L) or ~16 Ah of LFP (~330 Wh/L). The earlier hand-typed 3-6 Ah
# values were 18650-sized capacities, which forced absurd 27-51 parallel
# strings and made the cell generate ~15x too little heat.
#
#   string capacity  = pack_kwh / (series * nominal_v)
#   parallel         = round(string capacity / footprint cell capacity)
#   cell_ah          = string capacity / parallel      (re-derived, so that
#                      series * parallel * cell_ah * nominal_v == pack_kwh)
FOOTPRINT_CELL_AH = {"NMC": 20.0, "LFP": 16.0}

for _key, _b in BATTERY_OPTIONS.items():
    _nominal_v = CHEMISTRY_CELL_VOLTAGE[_b["chemistry"]]["nominal_v"]
    _string_ah = _b["pack_kwh"] * 1000 / (_b["series"] * _nominal_v)
    _b["pack_voltage"] = round(_b["series"] * _nominal_v)
    _b["parallel"] = max(1, round(_string_ah / FOOTPRINT_CELL_AH[_b["chemistry"]]))
    _b["cell_ah"] = round(_string_ah / _b["parallel"], 1)

EV_DATABASE = [
    {"id": "tata_tiago_ev", "name": "Tata Tiago EV", "segment": "Hatchback", "drive": "FWD",
     "range_km": 315, "mass_kg": 1235, "motor_kw": 55, "fast_charge_min": 57, "duty_cycle": "Urban",
     "battery_options": ["lfp_19", "lfp_24"]},

    {"id": "tata_punch_ev", "name": "Tata Punch EV", "segment": "Compact SUV", "drive": "FWD",
     "range_km": 315, "mass_kg": 1358, "motor_kw": 90, "fast_charge_min": 56, "duty_cycle": "Mixed",
     "battery_options": ["lfp_25", "lfp_35"]},

    {"id": "tata_nexon_ev", "name": "Tata Nexon EV", "segment": "Compact SUV", "drive": "FWD",
     "range_km": 325, "mass_kg": 1400, "motor_kw": 106, "fast_charge_min": 56, "duty_cycle": "Mixed",
     "battery_options": ["nmc_30", "nmc_40"]},

    {"id": "mg_comet_ev", "name": "MG Comet EV", "segment": "Micro Hatchback", "drive": "RWD",
     "range_km": 230, "mass_kg": 800, "motor_kw": 17, "fast_charge_min": 60, "duty_cycle": "Urban",
     "battery_options": ["lfp_17"]},

    {"id": "mg_zs_ev", "name": "MG ZS EV", "segment": "SUV", "drive": "FWD",
     "range_km": 461, "mass_kg": 1620, "motor_kw": 130, "fast_charge_min": 50, "duty_cycle": "Mixed",
     "battery_options": ["nmc_50"]},

    {"id": "mg_windsor_ev", "name": "MG Windsor EV", "segment": "Crossover", "drive": "FWD",
     "range_km": 332, "mass_kg": 1600, "motor_kw": 100, "fast_charge_min": 45, "duty_cycle": "Mixed",
     "battery_options": ["nmc_38"]},

    {"id": "citroen_ec3", "name": "Citroen eC3", "segment": "Hatchback", "drive": "FWD",
     "range_km": 320, "mass_kg": 1358, "motor_kw": 57, "fast_charge_min": 57, "duty_cycle": "Urban",
     "battery_options": ["lfp_25"]},

    {"id": "hyundai_kona_ev", "name": "Hyundai Kona Electric", "segment": "SUV", "drive": "FWD",
     "range_km": 452, "mass_kg": 1685, "motor_kw": 100, "fast_charge_min": 47, "duty_cycle": "Mixed",
     "battery_options": ["nmc_39_kona"]},

    {"id": "mahindra_xuv400_ev", "name": "Mahindra XUV400 EV", "segment": "SUV", "drive": "FWD",
     "range_km": 456, "mass_kg": 1502, "motor_kw": 110, "fast_charge_min": 50, "duty_cycle": "Mixed",
     "battery_options": ["nmc_34", "nmc_39"]},

    {"id": "mahindra_be6", "name": "Mahindra BE 6", "segment": "SUV Coupe", "drive": "RWD",
     "range_km": 682, "mass_kg": 2000, "motor_kw": 210, "fast_charge_min": 20, "duty_cycle": "Highway",
     "battery_options": ["nmc_59", "nmc_79"]},

    {"id": "byd_atto3", "name": "BYD Atto 3", "segment": "SUV", "drive": "FWD",
     "range_km": 521, "mass_kg": 1750, "motor_kw": 150, "fast_charge_min": 50, "duty_cycle": "Mixed",
     "battery_options": ["lfp_60"]},

    {"id": "byd_e6", "name": "BYD e6", "segment": "MPV", "drive": "FWD",
     "range_km": 520, "mass_kg": 2020, "motor_kw": 130, "fast_charge_min": 50, "duty_cycle": "Urban",
     "battery_options": ["lfp_72"]},

    {"id": "byd_seal", "name": "BYD Seal", "segment": "Sedan", "drive": "AWD",
     "range_km": 650, "mass_kg": 1985, "motor_kw": 390, "fast_charge_min": 26, "duty_cycle": "Highway",
     "battery_options": ["lfp_82"]},

    {"id": "kia_ev6", "name": "Kia EV6", "segment": "Crossover", "drive": "AWD",
     "range_km": 663, "mass_kg": 1985, "motor_kw": 239, "fast_charge_min": 18, "duty_cycle": "Highway",
     "battery_options": ["nmc_77"]},

    {"id": "hyundai_ioniq5", "name": "Hyundai Ioniq 5", "segment": "Crossover", "drive": "AWD",
     "range_km": 631, "mass_kg": 1900, "motor_kw": 239, "fast_charge_min": 18, "duty_cycle": "Highway",
     "battery_options": ["nmc_72"]},
]

_EV_BY_ID = {v["id"]: v for v in EV_DATABASE}


def vehicle_options():
    """Dropdown-ready list of {label, value} for every vehicle."""
    return [{"label": f'{v["name"]} ({v["segment"]})', "value": v["id"]} for v in EV_DATABASE]


def get_vehicle(vehicle_id):
    return _EV_BY_ID.get(vehicle_id)


def battery_options_for(vehicle_id):
    """Dropdown-ready list of {label, value} for the batteries fitted to a vehicle."""
    v = get_vehicle(vehicle_id)
    if not v:
        return []
    return [{"label": BATTERY_OPTIONS[k]["label"], "value": k} for k in v["battery_options"]]


def get_battery(battery_key):
    return BATTERY_OPTIONS.get(battery_key)
