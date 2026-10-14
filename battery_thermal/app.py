"""
app.py
======
Dash by Plotly frontend for the EV Battery Thermal Management dashboard.

Layout
------
Inputs on the left, results on the right, the way a simulation tool usually is.

Setup panel (left, sticky):
  - Vehicle / battery selection (pre-fills the fields below)
  - Run controls: C-rate, ambient temperature, cell footprint, cooling modes,
    and the Run button -- what you change most often
  - Collapsible design parameters: vehicle requirements, cell and pack, and
    the safety / BMS limits results are judged against

Results (right):
  - Temperature field: the 3D Plotly volume, with play/pause and the timeline
  - Summary cards: peak temperature / peak within-cell dT / time to target
    SoC per cooling mode, colour-coded against the safety limits
  - Transient plots: T_max, T_avg and SoC vs time, cursor-synced to the
    timeline and click-to-seek
  - Generated report (downloadable PDF)

Data flow
---------
"Run Simulation" solves every requested cooling mode once and caches the full
4D result in a dcc.Store. Everything after that -- scrubbing, playback,
click-to-seek, switching which cooling mode the 3D view shows -- is rendered
by clientside callbacks (assets/cellfire.js) straight from that cache, so no
timeline interaction ever reaches the server. A second, light store holds only
the time series (no 3D field) for the summary cards and the PDF.
"""

import dash
from dash import dcc, html, Input, Output, State, ClientsideFunction
import dash_bootstrap_components as dbc

import simulation as sim
import ev_database as evdb
import report as reportgen
import pdf_report

# --------------------------------------------------------------------------
# App / server
# --------------------------------------------------------------------------
GOOGLE_FONTS = ("https://fonts.googleapis.com/css2?"
                "family=Barlow+Semi+Condensed:wght@400;500;600;700&display=swap")

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP, GOOGLE_FONTS],
                title="CellFire",
                meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}])
server = app.server

MODES = ("natural", "forced_air", "liquid")


def _cooling_option(mode):
    return {
        "label": html.Span([
            html.Span(className="mode-dot", style={"backgroundColor": sim.COOLING_COLORS[mode]}),
            html.Span(sim.COOLING_LABELS[mode]),
        ], className="chip-label"),
        "value": mode,
    }


COOLING_OPTIONS = [_cooling_option(m) for m in MODES]

# --------------------------------------------------------------------------
# Defaults -- derived from the database so the first paint is self-consistent
# (the autofill callback then only has to keep them in sync on change)
# --------------------------------------------------------------------------
DEFAULT_VEHICLE = "tata_nexon_ev"
_veh0 = evdb.get_vehicle(DEFAULT_VEHICLE)
DEFAULT_BATTERY = _veh0["battery_options"][0]
_bat0 = evdb.get_battery(DEFAULT_BATTERY)
_volt0 = evdb.CHEMISTRY_CELL_VOLTAGE[_bat0["chemistry"]]


# --------------------------------------------------------------------------
# Small layout helpers
# --------------------------------------------------------------------------
def _num_field(field_id, label, value, step=1, unit=""):
    return dbc.Col([
        dbc.Label(f"{label} ({unit})" if unit else label, html_for=field_id, className="param-label"),
        dbc.Input(id=field_id, type="number", value=value, step=step, size="sm", className="field-input"),
    ], xs=6, className="mb-2")


def _mini_dropdown(field_id, label, options, value):
    return dbc.Col([
        dbc.Label(label, className="param-label"),
        dcc.Dropdown(id=field_id, options=[{"label": o, "value": o} for o in options],
                     value=value, clearable=False, searchable=False, className="field-dropdown"),
    ], xs=6, className="mb-2")


def _slider(slider_id, label, readout_id, lo, hi, step, value):
    return dbc.Col([
        html.Div([
            html.Label(label, htmlFor=slider_id, className="param-label mb-0"),
            html.Span(id=readout_id, className="slider-value"),
        ], className="d-flex justify-content-between align-items-baseline"),
        dcc.Slider(id=slider_id, min=lo, max=hi, step=step, value=value, marks=None,
                   tooltip={"placement": "bottom", "always_visible": False}),
    ], xs=6, className="mb-2")


def _group(title, children, open_=False):
    return html.Details([html.Summary(title), html.Div(children, className="group-body")],
                        open=open_, className="param-group")


# --------------------------------------------------------------------------
# Setup panel
# --------------------------------------------------------------------------
setup_panel = html.Aside([
    html.Div([
        html.Div("Vehicle", className="section-title"),
        dcc.Dropdown(id="vehicle-dropdown", options=evdb.vehicle_options(), value=DEFAULT_VEHICLE,
                     clearable=False, searchable=False, className="field-dropdown"),
        html.Div("Battery pack", className="section-title mt-3"),
        dcc.Dropdown(id="battery-dropdown", options=evdb.battery_options_for(DEFAULT_VEHICLE),
                     value=DEFAULT_BATTERY, clearable=False, searchable=False,
                     className="field-dropdown"),
    ], className="setup-block"),

    html.Div([
        html.Div("Discharge scenario", className="section-title"),
        dbc.Row([
            _slider("crate-slider", "C-rate", "crate-val", 0.5, 3.0, 0.1, 1.0),
            _slider("tamb-slider", "Ambient", "tamb-val", 15, 45, 1, 25),
        ], className="g-2"),
        dbc.Row([
            _slider("lx-slider", "Cell length", "lx-val", 15, 25, 1, 20),
            _slider("ly-slider", "Cell width", "ly-val", 10, 18, 1, 14),
        ], className="g-2"),
        html.Div("Cooling", className="param-label mt-1"),
        dcc.Checklist(id="cooling-checklist", options=COOLING_OPTIONS, value=list(MODES),
                      className="chip-list", inputClassName="chip-input", labelClassName="chip"),
        dbc.Button("Run simulation", id="run-button", n_clicks=0, className="run-button w-100"),
        html.Div(id="run-status", className="run-status", role="status"),
    ], className="setup-block"),

    html.Div([
        _group("Safety and BMS limits", [
            dbc.Row([
                _num_field("maxcelltemp-input", "Max cell temp", 50, unit="°C"),
                _num_field("maxdeltat-input", "Max cell ΔT", 5, unit="°C"),
            ], className="g-2"),
            dbc.Row([
                _num_field("socmin-input", "Min SoC", 5, unit="%"),
                _num_field("socmax-input", "Max SoC", 95, unit="%"),
            ], className="g-2"),
            dbc.Row([_num_field("runawaytemp-input", "Runaway onset", 150, step=5, unit="°C")], className="g-2"),
        ], open_=True),
        _group("Cell and pack", [
            dbc.Row([
                _mini_dropdown("chemistry-dropdown", "Chemistry", ("NMC", "LFP"), _bat0["chemistry"]),
                _mini_dropdown("cellformat-dropdown", "Format", ("Pouch", "Prismatic", "Cylindrical"), "Pouch"),
            ], className="g-2"),
            dbc.Row([
                _num_field("ratedah-input", "Capacity", _bat0["cell_ah"], step=0.1, unit="Ah"),
                _num_field("nominalv-input", "Nominal", _volt0["nominal_v"], step=0.05, unit="V"),
            ], className="g-2"),
            dbc.Row([
                _num_field("cutofflow-input", "Lower cutoff", _volt0["cutoff_low_v"], step=0.05, unit="V"),
                _num_field("cutoffhigh-input", "Upper cutoff", _volt0["cutoff_high_v"], step=0.05, unit="V"),
            ], className="g-2"),
            dbc.Row([
                _num_field("series-input", "Series", _bat0["series"], unit="S"),
                _num_field("parallel-input", "Parallel", _bat0["parallel"], unit="P"),
            ], className="g-2"),
            dbc.Row([
                dbc.Col([html.Div("Pack voltage", className="param-label"),
                         html.Div(id="packv-display", className="computed-value")], xs=6),
                dbc.Col([html.Div("Pack energy", className="param-label"),
                         html.Div(id="packenergy-display", className="computed-value")], xs=6),
            ], className="g-2"),
        ]),
        _group("Vehicle requirements", [
            dbc.Row([
                _num_field("range-input", "Range", _veh0["range_km"], step=5, unit="km"),
                _num_field("mass-input", "Mass", _veh0["mass_kg"], step=10, unit="kg"),
            ], className="g-2"),
            dbc.Row([
                _num_field("motor-power-input", "Motor power", _veh0["motor_kw"], unit="kW"),
                _num_field("fastcharge-input", "Fast charge", _veh0["fast_charge_min"], unit="min"),
            ], className="g-2"),
            dbc.Row([_mini_dropdown("dutycycle-dropdown", "Duty cycle",
                                    ("Urban", "Highway", "Mixed"), _veh0["duty_cycle"])], className="g-2"),
        ]),
    ], className="setup-block setup-groups"),
], className="setup-panel")

# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
stage = html.Section([
    html.Div([
        html.H2("Temperature field", className="panel-title"),
        html.Div([
            html.Span("Showing (one colour scale for all modes)", className="stage-hint"),
            dcc.RadioItems(id="vol-mode", options=[{"label": sim.COOLING_LABELS[m], "value": m} for m in MODES],
                           value=MODES[0], className="seg", inputClassName="seg-input",
                           labelClassName="seg-label"),
        ], className="d-flex align-items-center gap-2 flex-wrap"),
    ], className="panel-head"),
    dcc.Graph(id="volume-graph", className="volume-graph", config={"displaylogo": False},
              style={"height": "470px"}),
    html.Div([
        dbc.Button("Play", id="play-button", n_clicks=0, className="play-button", size="sm"),
        dcc.Slider(id="timestep-slider", min=0, max=1, step=1, value=0, marks=None,
                   tooltip={"placement": "bottom", "always_visible": False}, className="timeline"),
        html.Span(id="time-readout", className="time-readout"),
    ], className="playback"),
], className="panel stage")

summary_strip = html.Div(id="summary-metrics", className="summary-strip")


def _chart_panel(title, graph_id, height, hint=None):
    return html.Section([
        html.Div([html.H2(title, className="panel-title"),
                  html.Span(hint, className="panel-hint") if hint else None], className="panel-head"),
        dcc.Graph(id=graph_id, config={"displaylogo": False, "displayModeBar": False},
                  style={"height": height}),
    ], className="panel")


report_panel = html.Section([
    html.Div([
        html.H2("Simulation report", className="panel-title"),
        dbc.Button("Download PDF", id="download-report-btn", n_clicks=0, size="sm", className="ghost-button"),
    ], className="panel-head"),
    dcc.Download(id="report-download"),
    dcc.Markdown(id="report-markdown", children="Run a simulation to generate the report.",
                 className="report-markdown"),
], className="panel")

results = html.Main([
    stage,
    summary_strip,
    dbc.Row([
        dbc.Col(_chart_panel("Peak temperature", "tmax-graph", "280px", "click a point to jump there"), xs=12, xl=6),
        dbc.Col(_chart_panel("Average temperature", "tavg-graph", "280px"), xs=12, xl=6),
    ], className="g-3"),
    _chart_panel("State of charge", "soc-graph", "200px"),
    report_panel,
], className="results")

app.layout = html.Div([
    dcc.Store(id="sim-store"),
    dcc.Store(id="series-store"),
    dcc.Store(id="report-context-store"),
    dcc.Interval(id="play-interval", interval=int(1000 / 30), disabled=True),

    html.Header([
        html.Div([html.Span("CellFire", className="brand"),
                  html.Span("EV battery thermal management", className="tagline")],
                 className="brand-row"),
        html.Div(id="pack-chip", className="pack-chip"),
    ], className="app-header"),

    html.Div([
        setup_panel,
        dcc.Loading(results, target_components={"sim-store": "data"}, type="circle",
                    delay_show=250, color="#dfe5ea", className="results-loading"),
    ], className="app-body"),
], className="app-shell", **{"data-bs-theme": "dark"})


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _fmt_time_short(seconds):
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {s:02d} s"


def _active_modes(store, modes):
    keys = [k for k in store if k != "meta"]
    picked = [m for m in (modes or []) if m in store and m != "meta"]
    return picked or keys


# --------------------------------------------------------------------------
# Server callbacks (run once per change of setup -- never per timeline tick)
# --------------------------------------------------------------------------
@app.callback(
    Output("battery-dropdown", "options"),
    Output("battery-dropdown", "value"),
    Input("vehicle-dropdown", "value"),
)
def update_battery_options(vehicle_id):
    vehicle = evdb.get_vehicle(vehicle_id)
    if not vehicle:
        return [], None
    return evdb.battery_options_for(vehicle_id), vehicle["battery_options"][0]


@app.callback(
    Output("range-input", "value"),
    Output("mass-input", "value"),
    Output("motor-power-input", "value"),
    Output("fastcharge-input", "value"),
    Output("dutycycle-dropdown", "value"),
    Output("chemistry-dropdown", "value"),
    Output("ratedah-input", "value"),
    Output("nominalv-input", "value"),
    Output("cutofflow-input", "value"),
    Output("cutoffhigh-input", "value"),
    Output("series-input", "value"),
    Output("parallel-input", "value"),
    Input("vehicle-dropdown", "value"),
    Input("battery-dropdown", "value"),
)
def autofill_design_params(vehicle_id, battery_key):
    """Pre-fill the design-parameter fields from the selected vehicle/battery.
    Every field stays editable afterward -- this is a starting point, not a lock."""
    vehicle = evdb.get_vehicle(vehicle_id) or {}
    battery = evdb.get_battery(battery_key) or {}
    chemistry = battery.get("chemistry", "NMC")
    v_defaults = evdb.CHEMISTRY_CELL_VOLTAGE.get(chemistry, evdb.CHEMISTRY_CELL_VOLTAGE["NMC"])
    return (
        vehicle.get("range_km", 300),
        vehicle.get("mass_kg", 1400),
        vehicle.get("motor_kw", 100),
        vehicle.get("fast_charge_min", 55),
        vehicle.get("duty_cycle", "Mixed"),
        chemistry,
        battery.get("cell_ah", sim.Q_CELL_AH),
        v_defaults["nominal_v"],
        v_defaults["cutoff_low_v"],
        v_defaults["cutoff_high_v"],
        battery.get("series", 96),
        battery.get("parallel", 1),
    )


@app.callback(
    Output("packv-display", "children"),
    Output("packenergy-display", "children"),
    Output("pack-chip", "children"),
    Input("series-input", "value"),
    Input("parallel-input", "value"),
    Input("nominalv-input", "value"),
    Input("ratedah-input", "value"),
)
def compute_pack_totals(series, parallel, nominal_v, rated_ah):
    series, parallel = series or 0, parallel or 0
    nominal_v, rated_ah = nominal_v or 0, rated_ah or 0
    pack_v = series * nominal_v
    pack_kwh = series * parallel * rated_ah * nominal_v / 1000.0
    chip = f"{series}S{parallel}P  ·  {pack_v:.0f} V  ·  {pack_kwh:.1f} kWh"
    return f"{pack_v:.0f} V", f"{pack_kwh:.1f} kWh", chip


@app.callback(
    Output("sim-store", "data"),
    Output("series-store", "data"),
    Output("run-status", "children"),
    Output("timestep-slider", "max"),
    Output("timestep-slider", "value"),
    Output("report-markdown", "children"),
    Output("report-context-store", "data"),
    Output("vol-mode", "options"),
    Output("vol-mode", "value"),
    Input("run-button", "n_clicks"),
    State("crate-slider", "value"),
    State("tamb-slider", "value"),
    State("lx-slider", "value"),
    State("ly-slider", "value"),
    State("cooling-checklist", "value"),
    State("vehicle-dropdown", "value"),
    State("battery-dropdown", "value"),
    State("range-input", "value"),
    State("mass-input", "value"),
    State("motor-power-input", "value"),
    State("fastcharge-input", "value"),
    State("dutycycle-dropdown", "value"),
    State("chemistry-dropdown", "value"),
    State("cellformat-dropdown", "value"),
    State("ratedah-input", "value"),
    State("nominalv-input", "value"),
    State("cutofflow-input", "value"),
    State("cutoffhigh-input", "value"),
    State("series-input", "value"),
    State("parallel-input", "value"),
    State("socmin-input", "value"),
    State("socmax-input", "value"),
    State("maxcelltemp-input", "value"),
    State("maxdeltat-input", "value"),
    State("runawaytemp-input", "value"),
    State("vol-mode", "value"),
)
def run_simulation_callback(n_clicks, c_rate, t_amb, lx_cm, ly_cm, modes, vehicle_id, battery_key,
                             range_km, mass_kg, motor_kw, fastcharge_min, duty_cycle,
                             chemistry, cell_format, rated_ah, nominal_v, cutoff_low_v, cutoff_high_v,
                             series, parallel, soc_min_pct, soc_max_pct,
                             max_cell_temp_c, max_delta_t_c, runaway_temp_c, current_vol_mode):
    skip = dash.no_update
    modes = [m for m in MODES if m in (modes or [])] or ["natural"]

    soc_min_pct = soc_min_pct if soc_min_pct is not None else 5
    soc_max_pct = soc_max_pct if soc_max_pct is not None else 95
    if not 0 <= soc_min_pct < soc_max_pct <= 100:
        msg = "Min SoC must be lower than Max SoC, both between 0 and 100 %."
        return (skip, skip, msg, skip, skip, skip, skip, skip, skip)
    q_cell_ah = rated_ah if rated_ah and rated_ah > 0 else sim.Q_CELL_AH
    chemistry = chemistry or "NMC"

    results = sim.run_simulation(Lx=lx_cm / 100.0, Ly=ly_cm / 100.0, c_rate=c_rate, T_amb_C=t_amb,
                                  cooling_modes=modes, q_cell_ah=q_cell_ah, chemistry=chemistry,
                                  soc_start=soc_max_pct / 100.0, soc_end=soc_min_pct / 100.0)
    # the browser draws from this, so it needs the labels/colours too
    results["meta"].update(colors=sim.COOLING_COLORS, labels=sim.COOLING_LABELS)
    series_only = {k: ({kk: vv for kk, vv in v.items() if kk != "T"} if k != "meta" else v)
                   for k, v in results.items()}

    n_frames = len(results[modes[0]]["times"])
    status = f"Done. {len(modes)} cooling mode{'s' if len(modes) > 1 else ''}, {n_frames} frames."

    design = dict(
        range_km=range_km, mass_kg=mass_kg, motor_kw=motor_kw, fast_charge_min=fastcharge_min,
        duty_cycle=duty_cycle, chemistry=chemistry, cell_format=cell_format or "Pouch",
        rated_ah=q_cell_ah, nominal_v=nominal_v, cutoff_low_v=cutoff_low_v, cutoff_high_v=cutoff_high_v,
        series=series, parallel=parallel, soc_min_pct=soc_min_pct, soc_max_pct=soc_max_pct,
        max_cell_temp_c=max_cell_temp_c, max_delta_t_c=max_delta_t_c, thermal_runaway_temp_c=runaway_temp_c,
    )
    report_md = reportgen.generate_report(vehicle_id, battery_key, c_rate, t_amb, lx_cm, ly_cm,
                                           results, design=design)
    report_context = dict(vehicle_id=vehicle_id, battery_key=battery_key, c_rate=c_rate,
                           t_amb=t_amb, lx_cm=lx_cm, ly_cm=ly_cm, design=design)

    vol_options = [{"label": sim.COOLING_LABELS[m], "value": m} for m in modes]
    vol_value = current_vol_mode if current_vol_mode in modes else modes[0]
    return (results, series_only, status, n_frames - 1, 0, report_md, report_context,
            vol_options, vol_value)


@app.callback(
    Output("report-download", "data"),
    Input("download-report-btn", "n_clicks"),
    State("series-store", "data"),
    State("report-context-store", "data"),
    prevent_initial_call=True,
)
def download_report(n_clicks, series_results, ctx):
    if not series_results or not ctx:
        return dash.no_update
    pdf_bytes = pdf_report.build_pdf(
        ctx["vehicle_id"], ctx["battery_key"], ctx["c_rate"], ctx["t_amb"],
        ctx["lx_cm"], ctx["ly_cm"], series_results, design=ctx["design"],
    )
    return dcc.send_bytes(pdf_bytes, "cellfire_thermal_report.pdf")


@app.callback(
    Output("summary-metrics", "children"),
    Input("series-store", "data"),
    Input("cooling-checklist", "value"),
    Input("maxcelltemp-input", "value"),
    Input("maxdeltat-input", "value"),
    Input("socmin-input", "value"),
)
def update_summary_metrics(store, modes, max_cell_temp, max_delta_t, soc_min_pct):
    """Summary cards. Reads the light series-only store, so editing a safety
    limit re-judges the finished run instantly."""
    if not store or "meta" not in store:
        return []

    max_cell_temp = max_cell_temp if max_cell_temp is not None else 50
    max_delta_t = max_delta_t if max_delta_t is not None else 5
    soc_min_pct = soc_min_pct if soc_min_pct is not None else 5

    cards = []
    for mode in _active_modes(store, modes):
        d = store[mode]
        t_peak = max(d["tmax"])
        peak_dt = max(hi - lo for hi, lo in zip(d["tmax"], d["tmin"]))
        t_to_target = next((t for t, s in zip(d["times"], d["soc"]) if s <= soc_min_pct / 100.0),
                           d["times"][-1])
        temp_ok, dt_ok = t_peak <= max_cell_temp, peak_dt <= max_delta_t
        verdict = ("Within limits" if temp_ok and dt_ok
                   else "Over temperature limit" if not temp_ok else "Over ΔT limit")

        cards.append(html.Article([
            html.Div([
                html.Span(className="mode-dot", style={"backgroundColor": sim.COOLING_COLORS[mode]}),
                html.Span(sim.COOLING_LABELS[mode], className="metric-mode"),
                html.Span(verdict, className=f"verdict {'ok' if temp_ok and dt_ok else 'fail'}"),
            ], className="metric-head"),
            html.Div([
                html.Div([html.Div("Peak temp", className="metric-label"),
                          html.Div(f"{t_peak:.1f} °C", className=f"metric-value {'ok' if temp_ok else 'fail'}")]),
                html.Div([html.Div("Peak ΔT", className="metric-label"),
                          html.Div(f"{peak_dt:.2f} °C", className=f"metric-value {'ok' if dt_ok else 'fail'}")]),
                html.Div([html.Div(f"To {soc_min_pct:g}% SoC", className="metric-label"),
                          html.Div(_fmt_time_short(t_to_target), className="metric-value")]),
            ], className="metric-grid"),
        ], className="metric-card"))
    return cards


# --------------------------------------------------------------------------
# Clientside callbacks -- everything the timeline touches (see assets/cellfire.js)
# --------------------------------------------------------------------------
app.clientside_callback(
    ClientsideFunction(namespace="cellfire", function_name="render"),
    Output("volume-graph", "figure"),
    Output("tmax-graph", "figure"),
    Output("tavg-graph", "figure"),
    Output("soc-graph", "figure"),
    Output("time-readout", "children"),
    Input("timestep-slider", "value"),
    Input("sim-store", "data"),
    Input("cooling-checklist", "value"),
    Input("vol-mode", "value"),
    Input("maxcelltemp-input", "value"),
)

app.clientside_callback(
    ClientsideFunction(namespace="cellfire", function_name="toggle"),
    Output("play-interval", "disabled"),
    Output("play-button", "children"),
    Input("play-button", "n_clicks"),
    State("play-interval", "disabled"),
    prevent_initial_call=True,
)

app.clientside_callback(
    ClientsideFunction(namespace="cellfire", function_name="advance"),
    Output("timestep-slider", "value", allow_duplicate=True),
    Input("play-interval", "n_intervals"),
    Input("tmax-graph", "clickData"),
    Input("tavg-graph", "clickData"),
    Input("soc-graph", "clickData"),
    State("timestep-slider", "value"),
    State("timestep-slider", "max"),
    State("sim-store", "data"),
    prevent_initial_call=True,
)

app.clientside_callback(
    ClientsideFunction(namespace="cellfire", function_name="labels"),
    Output("crate-val", "children"),
    Output("tamb-val", "children"),
    Output("lx-val", "children"),
    Output("ly-val", "children"),
    Input("crate-slider", "value"),
    Input("tamb-slider", "value"),
    Input("lx-slider", "value"),
    Input("ly-slider", "value"),
)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8050)
