"""
app.py
======
Dash by Plotly frontend for the EV Battery Thermal Management dashboard.

Layout
------
The page is split spatially by role, the way a simulation tool usually is:
inputs on the left, outputs on the right.

Sidebar (left, sticky) -- everything that must be decided before running:
  - Vehicle / battery selection (pre-fills the fields below)
  - Design Parameters: vehicle requirements, cell spec, pack topology,
    safety/BMS limits -- the spec sheet an engineer settles on first
  - Simulation controls: C-rate, ambient temperature, cell footprint,
    cooling mode(s), Run button

Main column (right) -- everything produced by a run:
  - Playback bar: Play/Pause + timestep scrubber, shared by both plots below
  - Summary metrics: peak temperature / peak within-cell ΔT per cooling
    mode, colour-coded against the safety limits, for a fast scan
  - 3D Thermal Field (Plotly Volume trace)
  - Transient Response (T_max / T_avg / SoC vs time, cursor-synced to the
    scrubber, click-to-seek, with the max-cell-temperature limit overlaid)
  - Generated report (downloadable)

All simulation results are cached client-side in a dcc.Store after
"Run Simulation" is pressed; scrubbing / playback only ever reads from
that cache -- no recomputation happens while exploring the timeline.
"""

import numpy as np
import plotly.graph_objects as go

import dash
from dash import dcc, html, Input, Output, State, ctx
import dash_bootstrap_components as dbc

import simulation as sim
import ev_database as evdb
import report as reportgen
import pdf_report

# --------------------------------------------------------------------------
# App / server
# --------------------------------------------------------------------------
GOOGLE_FONTS = ("https://fonts.googleapis.com/css2?"
                "family=Source+Serif+4:opsz,wght@8..60,400;8..60,600;8..60,700&"
                "family=JetBrains+Mono:wght@400;500;600&display=swap")

app = dash.Dash(__name__, external_stylesheets=[dbc.themes.LITERA, GOOGLE_FONTS],
                 title="CellFire")
server = app.server

COOLING_OPTIONS = [
    {"label": sim.COOLING_LABELS[k], "value": k} for k in ("natural", "forced_air", "liquid")
]

# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------
DEFAULT_VEHICLE = "tata_nexon_ev"


def _num_field(field_id, label, value, step=1, unit=""):
    return dbc.Col([
        dbc.Label(f"{label}{f' ({unit})' if unit else ''}", className="param-label"),
        dbc.Input(id=field_id, type="number", value=value, step=step, size="sm", className="dark-input"),
    ], xs=6, className="mb-2")


def _mini_dropdown(field_id, label, options, value):
    return dbc.Col([
        dbc.Label(label, className="param-label"),
        dcc.Dropdown(id=field_id, options=[{"label": o, "value": o} for o in options],
                     value=value, clearable=False, className="dark-dropdown dark-dropdown-sm"),
    ], xs=6, className="mb-2")


def _section_header(text):
    return html.Div(text, className="section-header")


# ---------------------------------------------------------------------
# Left column -- vehicle/battery pick + the parameters that describe WHAT
# is being designed (all fields visible at once, nothing to expand/click)
# ---------------------------------------------------------------------
left_col = dbc.Card(
    dbc.CardBody([
        html.H5("Vehicle / Battery", className="panel-title"),
        html.Label("Vehicle (Indian Market)"),
        dcc.Dropdown(id="vehicle-dropdown", options=evdb.vehicle_options(),
                     value=DEFAULT_VEHICLE, clearable=False, className="dark-dropdown dark-dropdown-sm"),
        html.Label("Battery Pack Option", className="mt-2"),
        dcc.Dropdown(id="battery-dropdown",
                     options=evdb.battery_options_for(DEFAULT_VEHICLE),
                     value=evdb.get_vehicle(DEFAULT_VEHICLE)["battery_options"][0],
                     clearable=False, className="dark-dropdown dark-dropdown-sm"),

        _section_header("Vehicle Requirements"),
        dbc.Row([
            _num_field("range-input", "Range", 325, step=5, unit="km"),
            _num_field("mass-input", "Mass", 1400, step=10, unit="kg"),
        ]),
        dbc.Row([
            _num_field("motor-power-input", "Motor Power", 106, step=1, unit="kW"),
            _num_field("fastcharge-input", "Fast-Charge", 56, step=1, unit="min"),
        ]),
        dbc.Row([_mini_dropdown("dutycycle-dropdown", "Duty Cycle", ("Urban", "Highway", "Mixed"), "Mixed")]),

        _section_header("Cell Specification"),
        dbc.Row([
            _mini_dropdown("chemistry-dropdown", "Chemistry", ("NMC", "LFP"), "NMC"),
            _mini_dropdown("cellformat-dropdown", "Cell Format", ("Pouch", "Prismatic", "Cylindrical"), "Pouch"),
        ]),
        dbc.Row([
            _num_field("ratedah-input", "Rated Capacity", 3.1, step=0.1, unit="Ah"),
            _num_field("nominalv-input", "Nominal Voltage", 3.7, step=0.05, unit="V"),
        ]),
        dbc.Row([
            _num_field("cutofflow-input", "Lower Cutoff", 2.8, step=0.05, unit="V"),
            _num_field("cutoffhigh-input", "Upper Cutoff", 4.2, step=0.05, unit="V"),
        ]),
    ]),
    className="control-card",
)

# ---------------------------------------------------------------------
# Right column -- pack topology + safety limits + the run controls that
# actually trigger the solver
# ---------------------------------------------------------------------
right_col = dbc.Card(
    dbc.CardBody([
        html.H5("Pack & Run", className="panel-title"),

        _section_header("Pack Design"),
        dbc.Row([
            _num_field("series-input", "Series (S)", 96, step=1),
            _num_field("parallel-input", "Parallel (P)", 1, step=1),
        ]),
        dbc.Row([
            dbc.Col([
                html.Div("Pack Voltage", className="param-label"),
                html.Div(id="packv-display", className="computed-value"),
            ], xs=6, className="mb-2"),
            dbc.Col([
                html.Div("Pack Energy", className="param-label"),
                html.Div(id="packenergy-display", className="computed-value"),
            ], xs=6, className="mb-2"),
        ]),

        _section_header("Safety & BMS Limits"),
        dbc.Row([
            _num_field("socmin-input", "Min SoC", 5, step=1, unit="%"),
            _num_field("socmax-input", "Max SoC", 95, step=1, unit="%"),
        ]),
        dbc.Row([
            _num_field("maxcelltemp-input", "Max Cell Temp", 50, step=1, unit="°C"),
            _num_field("maxdeltat-input", "Max ΔT", 5, step=1, unit="°C"),
        ]),
        dbc.Row([_num_field("runawaytemp-input", "Runaway Onset", 150, step=5, unit="°C")]),

        _section_header("Run Controls"),
        dbc.Row([
            dbc.Col([
                html.Label("C-rate"),
                dcc.Slider(id="crate-slider", min=0.5, max=3.0, step=0.1, value=1.0, marks=None,
                           tooltip={"placement": "bottom", "always_visible": False}),
            ], xs=6),
            dbc.Col([
                html.Label("Ambient (°C)"),
                dcc.Slider(id="tamb-slider", min=15, max=45, step=1, value=25, marks=None,
                           tooltip={"placement": "bottom", "always_visible": False}),
            ], xs=6),
        ]),
        dbc.Row([
            dbc.Col([
                html.Label("Lx (cm)"),
                dcc.Slider(id="lx-slider", min=15, max=25, step=1, value=20, marks=None,
                           tooltip={"placement": "bottom", "always_visible": False}),
            ], xs=6),
            dbc.Col([
                html.Label("Ly (cm)"),
                dcc.Slider(id="ly-slider", min=10, max=18, step=1, value=14, marks=None,
                           tooltip={"placement": "bottom", "always_visible": False}),
            ], xs=6),
        ], className="mt-1"),
        html.Label("Cooling Mode(s)", className="mt-1"),
        dcc.Checklist(id="cooling-checklist", options=COOLING_OPTIONS,
                      value=["natural", "forced_air", "liquid"],
                      className="cooling-checklist cooling-checklist-row",
                      inputClassName="cooling-checkbox",
                      labelClassName="cooling-label"),

        dbc.Button("Run Simulation", id="run-button", color="danger",
                   className="mt-2 w-100", n_clicks=0),
        html.Div(id="run-status", className="mt-1 text-muted small"),
    ]),
    className="control-card",
)

# ---------------------------------------------------------------------
# Center column -- everything produced by a run (outputs)
# ---------------------------------------------------------------------
playback_bar = dbc.Card(
    dbc.CardBody([
        html.Div([
            dbc.Button("▶ Play", id="play-button", color="secondary", size="sm", className="me-3"),
            dcc.Slider(id="timestep-slider", min=0, max=1, step=1, value=0,
                       marks=None, tooltip={"placement": "bottom", "always_visible": False},
                       className="flex-grow-1"),
            html.Span(id="time-readout", className="time-readout ms-3"),
        ], className="d-flex align-items-center"),
    ]),
    className="control-card mb-2 playback-card",
)

summary_strip = html.Div(id="summary-metrics", className="mb-2")

GRAPH_HEIGHT = "330px"

panel_a = dbc.Card(
    dbc.CardBody([
        html.H5("3D Thermal Field", className="panel-title"),
        dcc.Loading(dcc.Graph(id="volume-graph", style={"height": GRAPH_HEIGHT}),
                     type="circle", color="#ef4444"),
    ]),
    className="viz-card mb-2",
)

TRANSIENT_GRAPH_HEIGHT = "260px"


def _transient_panel(graph_id, title):
    return dbc.Card(
        dbc.CardBody([
            html.H5(title, className="panel-title"),
            dcc.Loading(dcc.Graph(id=graph_id, style={"height": TRANSIENT_GRAPH_HEIGHT}),
                         type="circle", color="#ef4444"),
        ]),
        className="viz-card mb-2",
    )


panel_tmax = _transient_panel("tmax-graph", "Max Temperature")
panel_tavg = _transient_panel("tavg-graph", "Average Temperature")
panel_soc = _transient_panel("soc-graph", "State of Charge")

panel_report = dbc.Card(
    dbc.CardBody([
        html.Div([
            html.H5("Simulation Report", className="panel-title mb-0"),
            dbc.Button("Download PDF", id="download-report-btn", color="secondary", size="sm"),
        ], className="d-flex justify-content-between align-items-center"),
        dcc.Download(id="report-download"),
        dcc.Loading(
            dcc.Markdown(id="report-markdown",
                         children="Run a simulation to generate the report.",
                         className="report-markdown mt-2"),
            type="circle", color="#ef4444",
        ),
    ]),
    className="viz-card",
)

app.layout = dbc.Container([
    dcc.Store(id="sim-store"),
    dcc.Store(id="report-context-store"),
    dcc.Interval(id="play-interval", interval=int(1000 / 30), disabled=True),

    html.Div([
        html.H2("CellFire", className="app-title"),
    ], className="app-header"),

    dbc.Row([
        dbc.Col(left_col, width=12, lg=3, className="side-col"),
        dbc.Col([
            playback_bar,
            summary_strip,
            panel_a,
            panel_tmax,
            panel_tavg,
            panel_soc,
        ], width=12, lg=6),
        dbc.Col(right_col, width=12, lg=3, className="side-col"),
    ], className="g-3"),

    dbc.Row(dbc.Col(panel_report, width=12), className="g-3 mt-1"),
], fluid=True, className="app-container")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _fmt_time_short(seconds):
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _coords(nx, ny, nz, Lx, Ly, Lz):
    xs = np.linspace(0, Lx * 100, nx)  # cm
    ys = np.linspace(0, Ly * 100, ny)
    zs = np.linspace(0, Lz * 1000, nz)  # mm
    Z, Y, X = np.meshgrid(zs, ys, xs, indexing="ij")
    return X.flatten(), Y.flatten(), Z.flatten()


def _empty_fig(text):
    fig = go.Figure()
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        annotations=[dict(text=text, xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font=dict(size=16, family="Source Serif 4, Georgia, serif",
                                                       color="#8a8168"))],
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        modebar=dict(bgcolor="rgba(0,0,0,0)", color="#8b1e2f", activecolor="#6e1825"),
    )
    return fig


def _volume_figure(store, mode, frame_idx, meta):
    nx, ny, nz = meta["nx"], meta["ny"], meta["nz"]
    Lx, Ly, Lz = meta["Lx"], meta["Ly"], meta["Lz"]
    x, y, z = _coords(nx, ny, nz, Lx, Ly, Lz)

    T = np.array(store[mode]["T"][frame_idx], dtype=float).flatten()

    fig = go.Figure(data=go.Volume(
        x=x, y=y, z=z, value=T,
        isomin=float(np.min(T)), isomax=float(np.max(T)),
        opacity=0.15, opacityscale="uniform",
        surface_count=17,
        colorscale=[[0, "#1f4e5f"], [0.5, "#b8860b"], [1, "#8b1e2f"]],
        colorbar=dict(title="°C", tickfont=dict(color="#4a4234"), title_font=dict(color="#4a4234")),
    ))
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#2b2820", family="Source Serif 4, Georgia, serif"),
        scene=dict(
            xaxis_title="Length x (cm)",
            yaxis_title="Width y (cm)",
            zaxis_title="Thickness z (mm)",
            aspectmode="data",
        ),
        margin=dict(l=0, r=0, t=10, b=0),
        hoverlabel=dict(bgcolor="#fdfbf5", font=dict(color="#2b2820"), bordercolor="#ddd6c8"),
        modebar=dict(bgcolor="rgba(0,0,0,0)", color="#8b1e2f", activecolor="#6e1825"),
    )
    return fig


def _single_transient_figure(store, modes, frame_idx, metric_key, y_title,
                              scale=1.0, max_cell_temp=None, show_legend=True):
    """One metric (tmax / tavg / soc) vs time, one line per cooling mode."""
    fig = go.Figure()

    cursor_t = None
    for mode in modes:
        d = store[mode]
        color = sim.COOLING_COLORS[mode]
        label = sim.COOLING_LABELS[mode]
        times = d["times"]
        if cursor_t is None and frame_idx < len(times):
            cursor_t = times[frame_idx]

        y = [v * scale for v in d[metric_key]]
        fig.add_trace(go.Scatter(x=times, y=y, mode="lines", name=label,
                                  legendgroup=mode, showlegend=show_legend,
                                  line=dict(color=color)))

    # add_hline/add_vline resolve against EXISTING traces' axes -- called on an
    # empty figure (no traces yet) they silently no-op, so this must run after
    # the trace-adding loop above.
    if max_cell_temp is not None:
        fig.add_hline(y=max_cell_temp, line_width=1.5, line_dash="dot",
                      line_color="rgba(139,30,47,0.75)",
                      annotation_text="max cell temp limit", annotation_position="top left",
                      annotation_font=dict(size=10, color="rgba(139,30,47,0.9)"))

    if cursor_t is not None:
        fig.add_vline(x=cursor_t, line_width=1.5, line_dash="dash",
                      line_color="rgba(43,40,32,0.45)")

    fig.update_yaxes(title_text=y_title)
    fig.update_xaxes(title_text="Time (s)")

    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#2b2820", family="Source Serif 4, Georgia, serif"),
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="right", x=1,
                    bgcolor="rgba(0,0,0,0)", font=dict(color="#2b2820")),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#fdfbf5", font=dict(color="#2b2820"), bordercolor="#ddd6c8"),
        modebar=dict(bgcolor="rgba(0,0,0,0)", color="#8b1e2f", activecolor="#6e1825"),
    )
    return fig


# --------------------------------------------------------------------------
# Callbacks
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
    opts = evdb.battery_options_for(vehicle_id)
    return opts, vehicle["battery_options"][0]


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
    Input("series-input", "value"),
    Input("parallel-input", "value"),
    Input("nominalv-input", "value"),
    Input("ratedah-input", "value"),
)
def compute_pack_totals(series, parallel, nominal_v, rated_ah):
    series = series or 0
    parallel = parallel or 0
    nominal_v = nominal_v or 0
    rated_ah = rated_ah or 0
    pack_v = series * nominal_v
    pack_kwh = series * parallel * rated_ah * nominal_v / 1000.0
    return f"{pack_v:.0f} V", f"{pack_kwh:.1f} kWh"


@app.callback(
    Output("sim-store", "data"),
    Output("run-status", "children"),
    Output("timestep-slider", "max"),
    Output("timestep-slider", "value"),
    Output("report-markdown", "children"),
    Output("report-context-store", "data"),
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
    prevent_initial_call=True,
)
def run_simulation_callback(n_clicks, c_rate, t_amb, lx_cm, ly_cm, modes, vehicle_id, battery_key,
                             range_km, mass_kg, motor_kw, fastcharge_min, duty_cycle,
                             chemistry, cell_format, rated_ah, nominal_v, cutoff_low_v, cutoff_high_v,
                             series, parallel, soc_min_pct, soc_max_pct,
                             max_cell_temp_c, max_delta_t_c, runaway_temp_c):
    if not modes:
        modes = ["natural"]
    Lx, Ly = lx_cm / 100.0, ly_cm / 100.0

    q_cell_ah = rated_ah or sim.Q_CELL_AH
    chemistry = chemistry or "NMC"
    soc_min_pct = soc_min_pct if soc_min_pct is not None else 5
    soc_max_pct = soc_max_pct if soc_max_pct is not None else 95
    soc_start, soc_end = soc_max_pct / 100.0, soc_min_pct / 100.0

    results = sim.run_simulation(Lx=Lx, Ly=Ly, c_rate=c_rate, T_amb_C=t_amb,
                                  cooling_modes=modes, q_cell_ah=q_cell_ah, chemistry=chemistry,
                                  soc_start=soc_start, soc_end=soc_end)
    n_frames = len(results[modes[0]]["times"])
    status = f"Simulation complete -- {n_frames} frames cached, {len(modes)} cooling mode(s)."

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
    return results, status, n_frames - 1, 0, report_md, report_context


@app.callback(
    Output("report-download", "data"),
    Input("download-report-btn", "n_clicks"),
    State("sim-store", "data"),
    State("report-context-store", "data"),
    prevent_initial_call=True,
)
def download_report(n_clicks, sim_results, ctx):
    if not sim_results or not ctx:
        return dash.no_update
    pdf_bytes = pdf_report.build_pdf(
        ctx["vehicle_id"], ctx["battery_key"], ctx["c_rate"], ctx["t_amb"],
        ctx["lx_cm"], ctx["ly_cm"], sim_results, design=ctx["design"],
    )
    return dcc.send_bytes(pdf_bytes, "cellfire_thermal_report.pdf")


@app.callback(
    Output("volume-graph", "figure"),
    Output("tmax-graph", "figure"),
    Output("tavg-graph", "figure"),
    Output("soc-graph", "figure"),
    Output("time-readout", "children"),
    Input("timestep-slider", "value"),
    Input("sim-store", "data"),
    State("cooling-checklist", "value"),
    State("maxcelltemp-input", "value"),
)
def update_visuals(frame_idx, store, modes, max_cell_temp):
    if not store or "meta" not in store:
        empty = _empty_fig("Run a simulation to see transient plots")
        return _empty_fig("Run a simulation to see the 3D thermal field"), empty, empty, empty, ""

    active_modes = [m for m in (modes or []) if m in store and m != "meta"]
    if not active_modes:
        active_modes = [k for k in store.keys() if k != "meta"]

    n_frames = len(store[active_modes[0]]["times"])
    frame_idx = int(np.clip(frame_idx or 0, 0, n_frames - 1))

    vol_fig = _volume_figure(store, active_modes[0], frame_idx, store["meta"])
    tmax_fig = _single_transient_figure(store, active_modes, frame_idx, "tmax", "°C",
                                         max_cell_temp=max_cell_temp, show_legend=True)
    tavg_fig = _single_transient_figure(store, active_modes, frame_idx, "tavg", "°C",
                                         show_legend=False)
    soc_fig = _single_transient_figure(store, active_modes, frame_idx, "soc", "SoC (%)",
                                        scale=100.0, show_legend=False)

    t_sec = store[active_modes[0]]["times"][frame_idx]
    readout = f"t = {t_sec:6.0f} s  |  frame {frame_idx}/{n_frames - 1}"
    return vol_fig, tmax_fig, tavg_fig, soc_fig, readout


@app.callback(
    Output("summary-metrics", "children"),
    Input("sim-store", "data"),
    State("cooling-checklist", "value"),
    State("maxcelltemp-input", "value"),
    State("maxdeltat-input", "value"),
    State("socmin-input", "value"),
)
def update_summary_metrics(store, modes, max_cell_temp, max_delta_t, soc_min_pct):
    if not store or "meta" not in store:
        return []

    active_modes = [m for m in (modes or []) if m in store and m != "meta"]
    if not active_modes:
        active_modes = [k for k in store.keys() if k != "meta"]

    max_cell_temp = max_cell_temp if max_cell_temp is not None else 50
    max_delta_t = max_delta_t if max_delta_t is not None else 5
    soc_min_pct = soc_min_pct if soc_min_pct is not None else 5

    cards = []
    for mode in active_modes:
        d = store[mode]
        t_peak = max(d["tmax"])
        tmin_series = d.get("tmin", d["tavg"])
        peak_dt = max(hi - lo for hi, lo in zip(d["tmax"], tmin_series))
        try:
            idx0 = next(i for i, s in enumerate(d["soc"]) if s <= soc_min_pct / 100.0)
            t_to_target = d["times"][idx0]
        except StopIteration:
            t_to_target = d["times"][-1]

        temp_ok = t_peak <= max_cell_temp
        dt_ok = peak_dt <= max_delta_t

        cards.append(dbc.Col(dbc.Card(dbc.CardBody([
            html.Div([
                html.Span(className="mode-dot", style={"backgroundColor": sim.COOLING_COLORS[mode]}),
                html.Span(sim.COOLING_LABELS[mode], className="metric-mode-label"),
            ], className="d-flex align-items-center mb-2"),
            dbc.Row([
                dbc.Col([
                    html.Div("Peak Temp", className="metric-label"),
                    html.Div(f"{t_peak:.1f}°C", className=f"metric-value {'metric-ok' if temp_ok else 'metric-fail'}"),
                ], xs=4),
                dbc.Col([
                    html.Div("Peak ΔT", className="metric-label"),
                    html.Div(f"{peak_dt:.1f}°C", className=f"metric-value {'metric-ok' if dt_ok else 'metric-fail'}"),
                ], xs=4),
                dbc.Col([
                    html.Div(f"Time to {soc_min_pct:.0f}% SoC", className="metric-label"),
                    html.Div(_fmt_time_short(t_to_target), className="metric-value metric-neutral"),
                ], xs=4),
            ]),
        ]), className="metric-card"), xs=12, md=6, lg=4))

    return dbc.Row(cards, className="g-2")


@app.callback(
    Output("play-interval", "disabled"),
    Output("play-button", "children"),
    Input("play-button", "n_clicks"),
    State("play-interval", "disabled"),
    prevent_initial_call=True,
)
def toggle_play(n_clicks, disabled):
    now_disabled = not disabled
    label = "▶ Play" if now_disabled else "⏸ Pause"
    return now_disabled, label


@app.callback(
    Output("timestep-slider", "value", allow_duplicate=True),
    Input("play-interval", "n_intervals"),
    Input("tmax-graph", "clickData"),
    Input("tavg-graph", "clickData"),
    Input("soc-graph", "clickData"),
    State("timestep-slider", "value"),
    State("timestep-slider", "max"),
    prevent_initial_call=True,
)
def advance_or_seek(n_intervals, tmax_click, tavg_click, soc_click, current, max_val):
    trigger = ctx.triggered_id
    click_data = {"tmax-graph": tmax_click, "tavg-graph": tavg_click,
                  "soc-graph": soc_click}.get(trigger)
    if click_data:
        point = click_data["points"][0]
        # curveNumber-agnostic: every trace shares the same x-axis (time), so
        # the point's index within its own trace is also the target frame index
        idx = point.get("pointIndex", current)
        return int(np.clip(idx, 0, max_val))
    # auto-advance during playback
    nxt = (current or 0) + 1
    if nxt > (max_val or 0):
        nxt = 0
    return nxt


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8050)
