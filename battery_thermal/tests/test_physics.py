"""Regression tests for the solver and the cell/pack data feeding it."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ev_database as evdb  # noqa: E402
import simulation as sim  # noqa: E402

LX, LY, LZ = 0.20, 0.14, 0.006


def _weights(n):
    w = np.ones(n)
    w[[0, -1]] = 0.5
    return w


def test_energy_balance_closes():
    """Generated heat == stored heat + convected heat (to round-off)."""
    nx, ny, nz = sim.NX, sim.NY, sim.NZ
    dx, dy, dz = LX / (nx - 1), LY / (ny - 1), LZ / (nz - 1)
    h, t_amb, q = 5.0, 298.15, 2.0e4
    T = np.full((nz, ny, nx), t_amb)
    dt = sim._stable_dt(dx, dy, dz, h)
    steps = int(600 / dt)
    vol_w = (_weights(nz)[:, None, None] * _weights(ny)[None, :, None]
             * _weights(nx)[None, None, :]) * dx * dy * dz
    lost = 0.0
    for _ in range(steps):
        for face, area in (
            ((T[:, :, 0], T[:, :, -1]), np.outer(_weights(nz), _weights(ny)) * dz * dy),
            ((T[:, 0, :], T[:, -1, :]), np.outer(_weights(nz), _weights(nx)) * dz * dx),
            ((T[0], T[-1]), np.outer(_weights(ny), _weights(nx)) * dy * dx),
        ):
            for s in face:
                lost += h * np.sum(area * (s - t_amb)) * dt
        T = sim._fdm_step(T, dx, dy, dz, dt, h, t_amb, q)
    generated = q * LX * LY * LZ * steps * dt
    stored = sim.RHO * sim.CP * np.sum(vol_w * (T - t_amb))
    assert abs(generated - stored - lost) / generated < 1e-6


def test_lumped_capacitance_limit():
    """Low-Biot cell must follow T = Tamb + (qV/hA)(1 - exp(-t/tau))."""
    nx, ny, nz = sim.NX, sim.NY, sim.NZ
    dx, dy, dz = LX / (nx - 1), LY / (ny - 1), LZ / (nz - 1)
    h, t_amb, q = 5.0, 298.15, 2.0e4
    area = 2 * (LX * LY + LX * LZ + LY * LZ)
    vol = LX * LY * LZ
    tau = sim.RHO * sim.CP * vol / (h * area)
    t_end = 0.7 * tau
    T = np.full((nz, ny, nx), t_amb)
    dt = sim._stable_dt(dx, dy, dz, h)
    steps = int(t_end / dt)
    for _ in range(steps):
        T = sim._fdm_step(T, dx, dy, dz, dt, h, t_amb, q)
    expected = q * vol / (h * area) * (1 - np.exp(-steps * dt / tau))
    assert T.mean() - t_amb == pytest.approx(expected, rel=0.03)


def test_resistance_scales_inversely_with_capacity():
    """Same chemistry, 10x the capacity -> ~1/10 the cell resistance."""
    r_small = sim.resistance(0.5, 298.15, "NMC", q_cell_ah=3.0)
    r_big = sim.resistance(0.5, 298.15, "NMC", q_cell_ah=30.0)
    assert r_small / r_big == pytest.approx(10.0, rel=1e-6)


def test_heat_per_volume_grows_with_capacity():
    """I^2 R at a fixed C-rate ~ capacity, so a bigger cell in the same
    footprint must run hotter -- not identical."""
    rise = {}
    for ah in (3.0, 20.0):
        r = sim.run_simulation(LX, LY, 1.0, 25, ["natural"], q_cell_ah=ah, n_frames=40)
        rise[ah] = max(r["natural"]["tmax"]) - 25
    assert rise[20.0] > 3 * rise[3.0]


def test_battery_options_are_physically_consistent():
    """Cells must be plausible for the 200x140x6 mm pouch the solver models,
    and S x P x Ah x V must reproduce the quoted pack energy."""
    for key, b in evdb.BATTERY_OPTIONS.items():
        v = evdb.CHEMISTRY_CELL_VOLTAGE[b["chemistry"]]["nominal_v"]
        assert 1 <= b["parallel"] <= 20, f"{key}: {b['parallel']}P is not a believable pack"
        assert 10 <= b["cell_ah"] <= 25, f"{key}: {b['cell_ah']} Ah"
        kwh = b["series"] * b["parallel"] * b["cell_ah"] * v / 1000
        assert kwh == pytest.approx(b["pack_kwh"], rel=0.01), key


def test_every_vehicle_battery_reference_resolves():
    for veh in evdb.EV_DATABASE:
        for key in veh["battery_options"]:
            assert evdb.get_battery(key), (veh["id"], key)


def test_stable_dt_keeps_solution_bounded():
    r = sim.run_simulation(LX, LY, 3.0, 45, ["liquid", "natural"], q_cell_ah=60.0, n_frames=30)
    for mode in ("liquid", "natural"):
        assert np.isfinite(r[mode]["tmax"]).all()
        assert max(r[mode]["tmax"]) < 400


def test_pdf_report_builds():
    import pdf_report
    r = sim.run_simulation(LX, LY, 1.0, 25, ["natural", "liquid"], n_frames=30)
    pdf = pdf_report.build_pdf("tata_nexon_ev", "nmc_30", 1.0, 25, 20, 14, r)
    assert pdf[:4] == b"%PDF"
