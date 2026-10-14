"""
simulation.py
=============
3D transient heat-conduction solver for a Li-Ion EV battery cell.

Physics
-------
Governing PDE (isotropic, homogeneous solid):

    rho * cp * dT/dt = k * (d2T/dx2 + d2T/dy2 + d2T/dz2) + q_dot

where q_dot [W/m^3] is a uniform volumetric heat source derived from the
cell's instantaneous I^2*R electrical loss:

    q_dot(t) = I^2 * R(SoC, T) / V_cell

R(SoC) comes from a cubic-interpolated empirical lookup table (typical
"bathtub" resistance curve for a Li-NMC cell: high at low/high SoC, flat
in the mid-range). A linear temperature correction is then applied:

    R(SoC, T) = R(SoC) * (1 + 0.005 * (T - 298.15))

Boundary conditions are convective (Robin) on all six faces:

    -k * dT/dn = h * (T_surface - T_amb)

implemented with the standard second-order ghost-node technique so the
same 7-point Laplacian stencil can be used for interior AND boundary
nodes.

Time integration is explicit (forward Euler). The scheme is only
conditionally stable, so at every run the true stable sub-step is
computed from the grid spacing and cooling coefficient, and the
requested 5 s "reporting" interval is subdivided into as many stable
internal steps as required. Only the reporting-interval snapshots are
kept, which keeps the returned 4D array small enough to cache client-side
and scrub through instantly.
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import interp1d

# --------------------------------------------------------------------------
# Material properties -- homogeneous, isotropic Li-NMC pouch cell
# --------------------------------------------------------------------------
K_THERMAL = 2.0          # W / (m K)      effective thermal conductivity
RHO = 2300.0              # kg / m^3       cell density
CP = 1000.0               # J / (kg K)     specific heat
ALPHA = K_THERMAL / (RHO * CP)   # thermal diffusivity, m^2/s

# --------------------------------------------------------------------------
# Electrical / electrochemical properties
# --------------------------------------------------------------------------
Q_CELL_AH = 3.0            # default representative-cell capacity, Ah
Q_REF_AH = 3.0             # capacity the R(SoC) table below was measured on, Ah

# R(SoC) empirical lookup table -- published Li-NMC literature "bathtub"
# curve, values in milliohms at 25 C.
_SOC_PTS = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
_R_PTS_MOHM = np.array([45.0, 32.0, 26.0, 23.0, 21.0, 20.0, 19.0, 19.0, 20.0, 23.0, 30.0])
_R_INTERP = interp1d(_SOC_PTS, _R_PTS_MOHM / 1000.0, kind="cubic", fill_value="extrapolate")



# Chemistry correction applied on top of the base NMC-literature R(SoC) curve.
# LFP cells typically run a somewhat flatter voltage curve but slightly higher
# internal resistance than NMC at a comparable form factor -- approximated
# here as a constant multiplier (illustrative, not a fitted electrochemical
# model).
CHEMISTRY_R_MULTIPLIER = {"NMC": 1.0, "LFP": 1.15}


def resistance(soc: float, T_kelvin: float, chemistry: str = "NMC",
               q_cell_ah: float = Q_REF_AH) -> float:
    """Internal resistance [ohm] as a function of SoC, mean cell temperature,
    chemistry and cell capacity.

    The tabulated curve belongs to a ~Q_REF_AH cell. Electrode area (and hence
    conductance) grows with capacity, so resistance scales as Q_REF_AH/q_cell_ah.
    Without this a 40 Ah cell would carry a 3 Ah cell's resistance, and the
    heat generated at a given C-rate would be wrong by the same factor.
    """
    soc_c = float(np.clip(soc, 0.0, 1.0))
    r_soc = float(_R_INTERP(soc_c)) * CHEMISTRY_R_MULTIPLIER.get(chemistry, 1.0)
    r_soc *= Q_REF_AH / max(q_cell_ah, 1e-6)
    r = r_soc * (1.0 + 0.005 * (T_kelvin - 298.15))
    return max(r, 1e-6)


# --------------------------------------------------------------------------
# Cooling strategies
# --------------------------------------------------------------------------
COOLING_H = {
    "natural": 5.0,       # W/m^2K -- natural convection
    "forced_air": 35.0,   # W/m^2K -- forced air
    "liquid": 500.0,      # W/m^2K -- liquid cooling
}
COOLING_LABELS = {
    "natural": "Natural Convection",
    "forced_air": "Forced Air",
    "liquid": "Liquid Cooling",
}
COOLING_COLORS = {
    "natural": "#ff6b4a",     # coral -- weakest cooling
    "forced_air": "#f2b134",  # amber -- moderate
    "liquid": "#2ec4d6",      # cyan  -- strongest cooling
}
# Same hues, darkened for the white paper of the PDF report (amber and cyan
# are too faint on white).
COOLING_COLORS_PRINT = {
    "natural": "#c9371a",
    "forced_air": "#a86b00",
    "liquid": "#0b7f91",
}

# --------------------------------------------------------------------------
# Default grid resolution -- coarse, tuned for browser render performance
# --------------------------------------------------------------------------
NX, NY, NZ = 15, 10, 5
LZ_DEFAULT = 0.006  # 6 mm cell thickness (not user-configurable)


def _fdm_step(T, dx, dy, dz, dt, h, T_amb, qdot_vol):
    """Advance the temperature field by one explicit FDM step.

    Uses a padded array with one ghost layer per face; ghost values are
    set from the Robin (convective) boundary condition so the same
    central-difference Laplacian works for every node.
    """
    nz, ny, nx = T.shape
    Tp = np.empty((nz + 2, ny + 2, nx + 2))
    Tp[1:-1, 1:-1, 1:-1] = T

    # x faces (i = 0 and i = nx-1)
    Tp[1:-1, 1:-1, 0] = T[:, :, 1] + 2 * dx * h / K_THERMAL * (T_amb - T[:, :, 0])
    Tp[1:-1, 1:-1, -1] = T[:, :, -2] + 2 * dx * h / K_THERMAL * (T_amb - T[:, :, -1])
    # y faces
    Tp[1:-1, 0, 1:-1] = T[:, 1, :] + 2 * dy * h / K_THERMAL * (T_amb - T[:, 0, :])
    Tp[1:-1, -1, 1:-1] = T[:, -2, :] + 2 * dy * h / K_THERMAL * (T_amb - T[:, -1, :])
    # z faces
    Tp[0, 1:-1, 1:-1] = T[1, :, :] + 2 * dz * h / K_THERMAL * (T_amb - T[0, :, :])
    Tp[-1, 1:-1, 1:-1] = T[-2, :, :] + 2 * dz * h / K_THERMAL * (T_amb - T[-1, :, :])

    lap_x = (Tp[1:-1, 1:-1, 2:] - 2 * T + Tp[1:-1, 1:-1, :-2]) / dx ** 2
    lap_y = (Tp[1:-1, 2:, 1:-1] - 2 * T + Tp[1:-1, :-2, 1:-1]) / dy ** 2
    lap_z = (Tp[2:, 1:-1, 1:-1] - 2 * T + Tp[:-2, 1:-1, 1:-1]) / dz ** 2

    return T + dt * ALPHA * (lap_x + lap_y + lap_z) + dt * qdot_vol / (RHO * CP)


def _stable_dt(dx, dy, dz, h):
    """Conservative explicit-stability bound including the Robin-BC term."""
    diff_term = 2 * ALPHA * (1 / dx ** 2 + 1 / dy ** 2 + 1 / dz ** 2)
    conv_term = 2 * h / (RHO * CP) * (1 / dx + 1 / dy + 1 / dz)
    dt_max = 1.0 / (diff_term + conv_term)
    return 0.9 * dt_max  # safety factor


def _run_single(Lx, Ly, Lz, c_rate, T_amb_C, mode, nx, ny, nz, n_frames,
                 q_cell_ah=Q_CELL_AH, chemistry="NMC", soc_start=1.0, soc_end=0.0):
    dx, dy, dz = Lx / (nx - 1), Ly / (ny - 1), Lz / (nz - 1)
    T_amb = T_amb_C + 273.15
    h = COOLING_H[mode]
    V_cell = Lx * Ly * Lz
    I = c_rate * q_cell_ah  # amps

    dt_stable = _stable_dt(dx, dy, dz, h)
    # seconds to discharge from soc_start down to soc_end at this C-rate --
    # a BMS-enforced operating window is typically narrower than 100%-0%
    total_time = 3600.0 * q_cell_ah * (soc_start - soc_end) / I
    dt_report = total_time / (n_frames - 1)
    n_sub = max(1, int(np.ceil(dt_report / dt_stable)))
    dt_internal = dt_report / n_sub

    T = np.full((nz, ny, nx), T_amb)
    soc = soc_start
    t = 0.0

    T_hist = np.zeros((n_frames, nz, ny, nx), dtype=np.float32)
    times = np.zeros(n_frames)
    soc_hist = np.zeros(n_frames)
    tmax_hist = np.zeros(n_frames)
    tmin_hist = np.zeros(n_frames)
    tavg_hist = np.zeros(n_frames)

    T_hist[0] = T
    soc_hist[0] = soc
    tmax_hist[0] = T.max() - 273.15
    tmin_hist[0] = T.min() - 273.15
    tavg_hist[0] = T.mean() - 273.15

    for frame in range(1, n_frames):
        for _ in range(n_sub):
            if soc <= soc_end:
                break
            R = resistance(soc, float(T.mean()), chemistry, q_cell_ah)
            qdot_vol = (I ** 2 * R) / V_cell  # W/m^3
            T = _fdm_step(T, dx, dy, dz, dt_internal, h, T_amb, qdot_vol)
            soc -= I * dt_internal / (3600.0 * q_cell_ah)
            t += dt_internal
        soc = max(soc, soc_end)
        T_hist[frame] = T
        times[frame] = t
        soc_hist[frame] = soc
        tmax_hist[frame] = T.max() - 273.15
        tmin_hist[frame] = T.min() - 273.15
        tavg_hist[frame] = T.mean() - 273.15

    # Round before serialising -- default float repr (~17 sig figs) bloats the
    # JSON payload shipped to the browser roughly 8-10x for no visible benefit
    # at these physical scales (0.1 degC / 1 s / 0.001 SoC resolution is plenty).
    return {
        "T": np.round(T_hist.astype(np.float64) - 273.15, 2).tolist(),
        "times": np.round(times, 1).tolist(),
        "soc": np.round(soc_hist, 4).tolist(),
        "tmax": np.round(tmax_hist, 2).tolist(),
        "tmin": np.round(tmin_hist, 2).tolist(),
        "tavg": np.round(tavg_hist, 2).tolist(),
    }


def run_simulation(Lx, Ly, c_rate, T_amb_C, cooling_modes, Lz=LZ_DEFAULT,
                    nx=NX, ny=NY, nz=NZ, n_frames=150,
                    q_cell_ah=Q_CELL_AH, chemistry="NMC",
                    soc_start=1.0, soc_end=0.0):
    """Run the FDM solver for every requested cooling mode.

    Parameters
    ----------
    Lx, Ly : float   cell length / width, metres
    c_rate : float   discharge C-rate
    T_amb_C : float  ambient temperature, Celsius
    cooling_modes : list[str]  subset of {"natural", "forced_air", "liquid"}
    q_cell_ah : float  representative single-cell capacity, Ah (from the
                selected battery pack option)
    chemistry : str  "NMC" or "LFP" -- selects the resistance correction
    soc_start, soc_end : float in [0, 1]  BMS-enforced operating SoC window
                (e.g. 0.95 -> 0.05 instead of the full 1.0 -> 0.0)

    Returns
    -------
    dict keyed by cooling mode -> result dict (see _run_single), plus a
    top-level "meta" entry describing the grid used.
    """
    results = {"meta": {"nx": nx, "ny": ny, "nz": nz, "Lx": Lx, "Ly": Ly, "Lz": Lz}}
    for mode in cooling_modes:
        results[mode] = _run_single(Lx, Ly, Lz, c_rate, T_amb_C, mode, nx, ny, nz, n_frames,
                                     q_cell_ah=q_cell_ah, chemistry=chemistry,
                                     soc_start=soc_start, soc_end=soc_end)
    return results
