# SLF001: these tests drive the device's internals (_sensor_value, the parsed
# config) on purpose — that override hook is the whole contract under test.
# ruff: noqa: SLF001

import math

from vector.qlcp.config_parser import parse_config
from tests.chimera_mock_device import (
    APOGEE_M,
    CHIMERA_CONFIG,
    PAD_LAT,
    PAD_LON,
    ChimeraMockDevice,
    FlightSim,
)


def run_one_flight(sim: FlightSim, dt: float = 0.05, max_s: float = 600.0) -> tuple[list[str], float]:
    """Step through one full flight; return (phase sequence, max altitude)."""
    phases = [sim.phase]
    max_alt = sim.alt
    start_laps = sim.laps
    t = 0.0
    while sim.laps == start_laps and t < max_s:
        sim.step(dt)
        t += dt
        max_alt = max(max_alt, sim.alt)
        if sim.phase != phases[-1]:
            phases.append(sim.phase)
    assert sim.laps == start_laps + 1, "flight did not complete within the time budget"
    return phases, max_alt


def test_flight_profile_phases_and_apogee() -> None:
    sim = FlightSim()
    phases, max_alt = run_one_flight(sim)

    # Loop resets back to pad after landing.
    assert phases == ["pad", "boost", "coast", "drogue", "main", "landed", "pad"]
    # Apogee within ~2% of the 13 000 ft target.
    assert abs(max_alt - APOGEE_M) < 0.02 * APOGEE_M


def test_flight_stays_near_pad_and_loops() -> None:
    sim = FlightSim()
    max_dev = 0.0
    for _ in range(2):  # two consecutive laps — the reset must fully rewind position
        run_one_flight(sim)
    # After landing + reset the rocket is back on the pad.
    lat, lon, alt = sim.fix()
    assert alt == 0.0
    max_dev = max(abs(lat - PAD_LAT), abs(lon - PAD_LON))
    assert max_dev < 0.001  # within ~100 m of the pad (GPS noise + reset)


def test_descent_rates() -> None:
    sim = FlightSim()
    drogue_vv: list[float] = []
    main_vv: list[float] = []
    start_laps = sim.laps
    while sim.laps == start_laps:
        sim.step(0.05)
        if sim.phase == "drogue":
            drogue_vv.append(sim.vv)
        elif sim.phase == "main":
            main_vv.append(sim.vv)
    assert drogue_vv and all(math.isclose(v, FlightSim.DROGUE_VV) for v in drogue_vv)
    assert main_vv and all(math.isclose(v, FlightSim.MAIN_VV) for v in main_vv)


def test_config_parses_and_sensor_values_are_sane() -> None:
    parsed = parse_config(CHIMERA_CONFIG)
    names = sorted(s.name for s in parsed.sensors_by_id.values())
    assert names == ["Alt", "Lat", "Lon", "Sats"]
    assert not parsed.controls_by_id

    dev = ChimeraMockDevice()
    try:
        by_name = {s.name: (sid, s) for sid, s in dev._device_config.sensors_by_id.items()}

        # On the pad (t=0) the fix is at the launch site.
        lat = dev._sensor_value(*by_name["Lat"], elapsed_s=0.0)
        lon = dev._sensor_value(*by_name["Lon"], elapsed_s=0.0)
        alt = dev._sensor_value(*by_name["Alt"], elapsed_s=0.0)
        sats = dev._sensor_value(*by_name["Sats"], elapsed_s=0.0)
        assert abs(lat - PAD_LAT) < 0.001
        assert abs(lon - PAD_LON) < 0.001
        assert alt == 0.0
        assert 8 <= sats <= 14

        # Mid-ascent the altitude is climbing.
        alt_boost = dev._sensor_value(*by_name["Alt"], elapsed_s=FlightSim.PAD_HOLD_S + FlightSim.BOOST_T)
        assert alt_boost > 1000.0

        # Values within one GPS epoch are held constant.
        a = dev._sensor_value(*by_name["Lat"], elapsed_s=30.00)
        b = dev._sensor_value(*by_name["Lat"], elapsed_s=30.10)
        assert a == b
    finally:
        dev.udp_sock.close()
