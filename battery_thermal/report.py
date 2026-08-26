"""
report.py
=========
Builds a plain-English explanation of a simulation run: the design
requirements and parameters an engineer would have specified before ever
touching a solver, what was simulated, what the numbers mean physically,
and whether the design meets the safety limits the user set.

`resolve_context` and `compute_mode_metrics` are the shared number-crunching
used both by `generate_report` (the on-screen Markdown summary) and by
`pdf_report.py` (the downloadable IEEE-format PDF with embedded charts) --
kept in one place so the two presentations never disagree on the numbers.
"""

import ev_database as evdb
import simulation as sim


def fmt_time(seconds):
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _safety_note(t_max, max_cell_temp, runaway_temp):
    if t_max < max_cell_temp - 5:
        return (f"This stays comfortably below the {max_cell_temp:.0f}°C design limit set for this "
                f"pack, with headroom to spare.")
    if t_max < max_cell_temp:
        return (f"This is close to the {max_cell_temp:.0f}°C design limit. A production BMS would "
                f"likely start derating charge/discharge power well before reaching it, so a driver "
                f"might notice reduced acceleration or slower DC fast-charging under these conditions.")
    if t_max < runaway_temp * 0.6:
        return (f"This exceeds the {max_cell_temp:.0f}°C design limit specified for this pack. "
                f"Capacity fade and internal-resistance growth accelerate noticeably above this "
                f"threshold, even without an immediate failure.")
    return (f"This is approaching the {runaway_temp:.0f}°C thermal-runaway onset temperature "
            f"specified for this chemistry. A production BMS would almost certainly have cut power "
            f"well before this point -- this scenario should be treated as a design failure, not a "
            f"tolerable edge case.")


def resolve_context(vehicle_id, battery_key, design=None):
    """Resolve vehicle/battery selection + design overrides into one consistent
    dict of concrete numbers (falls back through battery preset -> chemistry
    default -> hardcoded default, same precedence app.py's callback uses)."""
    design = design or {}
    vehicle = evdb.get_vehicle(vehicle_id) if vehicle_id else None
    battery = evdb.get_battery(battery_key) if battery_key else None

    chemistry = design.get("chemistry") or (battery["chemistry"] if battery else "NMC")
    v_defaults = evdb.CHEMISTRY_CELL_VOLTAGE.get(chemistry, evdb.CHEMISTRY_CELL_VOLTAGE["NMC"])
    rated_ah = design.get("rated_ah") or (battery["cell_ah"] if battery else sim.Q_CELL_AH)
    nominal_v = design.get("nominal_v", v_defaults["nominal_v"])
    cutoff_high_v = design.get("cutoff_high_v", v_defaults["cutoff_high_v"])
    cutoff_low_v = design.get("cutoff_low_v", v_defaults["cutoff_low_v"])
    cell_format = design.get("cell_format", "Pouch")
    series = design.get("series") or (battery["series"] if battery else 96)
    parallel = design.get("parallel") or (battery["parallel"] if battery else 1)
    soc_min = design.get("soc_min_pct", 5)
    soc_max = design.get("soc_max_pct", 95)
    max_cell_temp = design.get("max_cell_temp_c", 50)
    runaway_temp = design.get("thermal_runaway_temp_c", 150)
    max_delta_t = design.get("max_delta_t_c", 5)

    return dict(
        vehicle=vehicle, battery=battery,
        chemistry=chemistry, rated_ah=rated_ah, nominal_v=nominal_v,
        cutoff_high_v=cutoff_high_v, cutoff_low_v=cutoff_low_v, cell_format=cell_format,
        series=series, parallel=parallel,
        pack_voltage=series * nominal_v,
        pack_energy_kwh=series * parallel * rated_ah * nominal_v / 1000.0,
        soc_min=soc_min, soc_max=soc_max,
        max_cell_temp=max_cell_temp, runaway_temp=runaway_temp, max_delta_t=max_delta_t,
    )


def compute_mode_metrics(results, ctx):
    """Per-cooling-mode headline numbers, in the order the simulation was run.
    Returns a list of dicts: mode, label, h, t_max, t_avg_final, t_start, rise,
    peak_delta_t, delta_t_ok, t_to_target, safety_note."""
    modes = [k for k in results.keys() if k != "meta"]
    out = []
    for m in modes:
        d = results[m]
        t_max = max(d["tmax"])
        t_avg_final = d["tavg"][-1]
        t_start = d["tavg"][0]
        tmin_series = d.get("tmin", d["tavg"])
        peak_delta_t = max(hi - lo for hi, lo in zip(d["tmax"], tmin_series))
        try:
            idx0 = next(i for i, s in enumerate(d["soc"]) if s <= (ctx["soc_min"] / 100.0))
            t_discharge = d["times"][idx0]
        except StopIteration:
            t_discharge = d["times"][-1]

        out.append(dict(
            mode=m, label=sim.COOLING_LABELS[m], h=sim.COOLING_H[m], color=sim.COOLING_COLORS[m],
            t_max=t_max, t_avg_final=t_avg_final, t_start=t_start, rise=t_max - t_start,
            peak_delta_t=peak_delta_t, delta_t_ok=peak_delta_t <= ctx["max_delta_t"],
            t_to_target=t_discharge,
            safety_note=_safety_note(t_max, ctx["max_cell_temp"], ctx["runaway_temp"]),
        ))
    return out


def generate_report(vehicle_id, battery_key, c_rate, t_amb_c, lx_cm, ly_cm, results, design=None):
    """
    results: dict returned by simulation.run_simulation (per-mode data + 'meta')
    design: dict of engineering design parameters (see app.py's run_simulation_callback
            for the exact keys) -- vehicle requirements, cell spec, pack topology,
            and BMS/safety limits. Falls back to sensible defaults if omitted.
    """
    ctx = resolve_context(vehicle_id, battery_key, design)
    vehicle, chemistry = ctx["vehicle"], ctx["chemistry"]
    modes_metrics = compute_mode_metrics(results, ctx)
    modes = [mm["mode"] for mm in modes_metrics]

    lines = []
    lines.append("## Simulation Report")
    lines.append("")

    if vehicle:
        lines.append(
            f"Target application: **{vehicle['name']}** ({vehicle['segment']}, {vehicle['drive']}). "
            f"*(Vehicle figures are approximate, sourced from public specification sheets and rounded "
            f"for this teaching simulation.)*"
        )
        lines.append("")

    lines.append("### Design Requirements & Parameters")
    lines.append(
        "This is the kind of specification sheet a pack design engineer would settle on "
        "*before* running any thermal analysis:\n"
    )
    if vehicle:
        lines.append(
            f"**Vehicle requirements:** {vehicle.get('range_km', '--')} km target range, "
            f"{vehicle.get('mass_kg', '--')} kg kerb mass, {vehicle.get('motor_kw', '--')} kW peak "
            f"motor power, {vehicle.get('fast_charge_min', '--')} min target 10-80% DC fast-charge "
            f"time, {vehicle.get('duty_cycle', 'Mixed')} duty cycle.\n"
        )
    lines.append(
        f"**Cell specification:** {chemistry} chemistry, {ctx['cell_format'].lower()} format, "
        f"{ctx['rated_ah']:.1f} Ah rated capacity, {ctx['nominal_v']:.2f} V nominal / "
        f"{ctx['cutoff_low_v']:.2f}-{ctx['cutoff_high_v']:.2f} V cutoff window.\n"
    )
    lines.append(
        f"**Pack topology:** {ctx['series']} cells in series x {ctx['parallel']} in parallel "
        f"({ctx['series'] * ctx['parallel']} cells total) -> **{ctx['pack_voltage']:.0f} V** nominal "
        f"pack voltage, **{ctx['pack_energy_kwh']:.1f} kWh** nominal pack energy.\n"
    )
    lines.append(
        f"**BMS / safety limits:** operate between {ctx['soc_min']:.0f}-{ctx['soc_max']:.0f}% SoC, "
        f"max cell temperature **{ctx['max_cell_temp']:.0f}°C**, max cell-to-cell ΔT "
        f"**{ctx['max_delta_t']:.0f}°C**, thermal-runaway onset assumed at "
        f"**{ctx['runaway_temp']:.0f}°C** for this chemistry."
    )
    lines.append("")

    lines.append("### What was simulated")
    lines.append(
        f"- **Discharge rate:** {c_rate:g}C — the higher this number, the faster the pack "
        f"is drained and the more heat is generated per second (heating scales with the "
        f"*square* of current, so 2C generates roughly 4x the heat of 1C).\n"
        f"- **Ambient temperature:** {t_amb_c:g}°C — the temperature of the air or coolant "
        f"the cell is losing heat into.\n"
        f"- **SoC window simulated:** {ctx['soc_max']:.0f}% down to {ctx['soc_min']:.0f}%, matching "
        f"the BMS operating limits above rather than a full 100%-0% discharge.\n"
        f"- **Cell footprint modeled:** {lx_cm:g} cm x {ly_cm:g} cm (6 mm thick), solved on "
        f"a full 3D grid so temperature can vary across the cell's length, width, *and* "
        f"thickness, not just as a single average number.\n"
        f"- **Cooling strategies compared:** " + ", ".join(sim.COOLING_LABELS[m] for m in modes) + "."
    )
    lines.append("")

    lines.append("### What happened, mode by mode")
    for mm in modes_metrics:
        delta_t_verdict = (
            f"within the {ctx['max_delta_t']:.0f}°C ΔT limit" if mm["delta_t_ok"]
            else f"**exceeds** the {ctx['max_delta_t']:.0f}°C ΔT limit -- uneven heating like this "
                 f"accelerates imbalanced aging between cells"
        )
        lines.append(f"**{mm['label']}** (heat transfer coefficient h = {mm['h']:g} W/m²K)")
        lines.append(
            f"- Peak cell temperature reached: **{mm['t_max']:.1f}°C** (a rise of {mm['rise']:.1f}°C "
            f"above the {mm['t_start']:.0f}°C starting point).\n"
            f"- Average cell temperature at end of window: {mm['t_avg_final']:.1f}°C.\n"
            f"- Peak within-cell temperature spread (hot spot vs. cold spot): "
            f"**{mm['peak_delta_t']:.1f}°C** -- {delta_t_verdict}.\n"
            f"- Time to reach {ctx['soc_min']:.0f}% SoC: {fmt_time(mm['t_to_target'])}.\n"
            f"- {mm['safety_note']}"
        )
        lines.append("")

    if len(modes_metrics) > 1:
        best = min(modes_metrics, key=lambda mm: mm["t_max"])
        worst = max(modes_metrics, key=lambda mm: mm["t_max"])
        if best is not worst:
            spread = worst["t_max"] - best["t_max"]
            lines.append(
                f"**Takeaway:** switching from {worst['label']} to {best['label']} lowered the peak "
                f"cell temperature by about **{spread:.1f}°C** in this scenario. Liquid cooling's much "
                f"higher heat transfer coefficient (h) pulls heat out of the cell surface far faster "
                f"than air ever can, which is why high-performance EVs and fast-charging-capable packs "
                f"almost always use it despite the added cost, weight, and complexity of pumps and "
                f"coolant loops."
            )
            lines.append("")

    lines.append("### Why this happens (in plain terms)")
    lines.append(
        "As current flows through the cell, its internal resistance turns some of that "
        "electrical energy directly into heat (this is the same I²R heating that warms a "
        "toaster wire). That heat has to escape through the cell's outer surface into "
        "whatever is cooling it — still air, moving air, or a liquid coolant loop — and it "
        "escapes faster when that cooling medium can carry heat away more effectively "
        "(a higher 'h' value). If heat is generated faster than it can escape, the cell's "
        "temperature climbs — and hotter cells also have *slightly higher* resistance in "
        "this model, so the heating gets marginally worse as the cell warms up. Because "
        "resistance also depends on how depleted the cell is (State of Charge), heating "
        "isn't perfectly steady through the discharge — it typically peaks near the "
        "beginning and end of the discharge window, where the cell's internal resistance is "
        "highest according to the published Li-Ion resistance curve used here. The pack "
        "topology (series/parallel count) and cell voltage window mainly determine *how much "
        "energy and power* the pack can deliver -- they don't change this cell-level thermal "
        "picture directly, since every cell in the pack is assumed to share the load equally."
    )
    lines.append("")

    lines.append(
        "---\n*Generated automatically from the simulation parameters and results above. "
        "This is an educational thermal model (BMEE402L, VIT Vellore) — it does not "
        "replace validated OEM battery-management or safety engineering.*"
    )

    return "\n".join(lines)
