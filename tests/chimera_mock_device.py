#!/usr/bin/env python3
"""Chimera mock device: a GPS-only QLCP device that loops a simulated flight.

Speaks the same protocol as tests/mock_device.py (SSDP discovery, CONFIG,
TIMESYNC, streaming) but its only sensors are a `rocket_position` group
(Lat / Lon / Alt / Sats), driven by a looping flight profile instead of the
default test sine:

    pad hold -> boost -> coast -> apogee 3962 m (13 000 ft)
             -> drogue descent -> main descent -> landed -> repeat

Launch site: 47 deg 57' 56.4" N, 81 deg 52' 22.4" W (47.965667, -81.872889).

Usage (either form works):
    uv run -m tests.chimera_mock_device                    # Auto-discover server
    uv run tests/chimera_mock_device.py --server 127.0.0.1 # Connect directly
"""

import argparse
import asyncio
import logging
import math
import random
from typing import Any

from vector.qlcp.config_models import SensorConfig


try:
    from tests.mock_device import MockSensorDevice, _ColoredFormatter
except ModuleNotFoundError:
    # Running this file by path (`uv run tests/chimera_mock_device.py`) puts
    # tests/ on sys.path instead of the repo root, so the sibling is a
    # top-level module rather than tests.mock_device. mock_device.py itself
    # needs no such fallback — it only imports the installed package.
    from mock_device import MockSensorDevice, _ColoredFormatter

logger = logging.getLogger("ChimeraMock")

PAD_LAT = 47.965667   # 47 deg 57' 56.4" N
PAD_LON = -81.872889  # 81 deg 52' 22.4" W
APOGEE_M = 3962.0     # 13 000 ft

# GPS fixes update at 4 Hz regardless of the QLCP stream rate; between epochs
# the last fix is repeated (the GUI dedups unchanged positions).
GPS_EPOCH_S = 0.25
# Sim integration step — small enough that the main-deploy altitude check
# doesn't overshoot by more than a couple of metres.
SIM_DT_S = 0.05

CHIMERA_CONFIG: dict[str, Any] = {
    "device_name": "ChimeraMock",
    "device_type": "Sensor Monitor",
    "sensors": {
        "rocket_position": {
            "Lat":  {"unit": "deg"},
            "Lon":  {"unit": "deg"},
            "Alt":  {"unit": "m"},
            "Sats": {"unit": "sats"},
        },
    },
}


class FlightSim:
    """Looping flight profile: pad -> boost -> coast -> drogue -> main -> landed -> pad.

    Altitudes are metres AGL. Horizontal motion is a downrange drift along a
    fixed azimuth during powered flight plus crosswind drift under canopy,
    mirroring tile-prep/mock_gps_server.py.
    """

    PAD_HOLD_S = 15.0
    BOOST_ACCEL = 100.0      # m/s^2
    BOOST_T = 5.0            # s -> 500 m/s at burnout, ~1250 m
    COAST_DECEL = 47.5       # m/s^2 -> ~2700 m of coast, apogee ~3970 m at the 0.05 s Euler step
    DROGUE_VV = -25.0        # m/s
    MAIN_DEPLOY_ALT = 300.0  # m AGL
    MAIN_VV = -6.0           # m/s
    LANDED_HOLD_S = 30.0
    AZIMUTH_RAD = math.radians(60)
    DOWNRANGE_FACTOR = 0.06  # horizontal speed as a fraction of vertical, on the way up
    DROGUE_WIND = 15.0       # m/s crosswind drift under drogue
    MAIN_WIND = 4.0          # m/s under main

    def __init__(self, pad_lat: float = PAD_LAT, pad_lon: float = PAD_LON):
        self.pad_lat, self.pad_lon = pad_lat, pad_lon
        self.reset()

    def reset(self) -> None:
        self.phase = "pad"
        self.phase_t = 0.0
        self.alt = 0.0
        self.vv = 0.0
        self.downrange = 0.0
        self.crosswind = 0.0
        self.laps = 0  # completed flights

    def step(self, dt: float) -> None:
        self.phase_t += dt
        if self.phase == "pad":
            if self.phase_t >= self.PAD_HOLD_S:
                self._enter("boost")
        elif self.phase == "boost":
            self.vv += self.BOOST_ACCEL * dt
            self.alt += self.vv * dt
            self.downrange += self.vv * self.DOWNRANGE_FACTOR * dt
            if self.phase_t >= self.BOOST_T:
                self._enter("coast")
        elif self.phase == "coast":
            self.vv -= self.COAST_DECEL * dt
            self.alt += self.vv * dt
            self.downrange += max(self.vv, 0.0) * self.DOWNRANGE_FACTOR * dt
            if self.vv <= 0.0:
                self.vv = self.DROGUE_VV
                self._enter("drogue")
        elif self.phase == "drogue":
            self.vv = self.DROGUE_VV
            self.alt += self.vv * dt
            self.crosswind += self.DROGUE_WIND * dt
            if self.alt <= self.MAIN_DEPLOY_ALT:
                self.vv = self.MAIN_VV
                self._enter("main")
        elif self.phase == "main":
            self.vv = self.MAIN_VV
            self.alt += self.vv * dt
            self.crosswind += self.MAIN_WIND * dt
            if self.alt <= 0.0:
                self.alt = 0.0
                self.vv = 0.0
                self._enter("landed")
        elif self.phase == "landed" and self.phase_t >= self.LANDED_HOLD_S:
            landing_lap = self.laps + 1
            self.reset()
            self.laps = landing_lap

    def _enter(self, phase: str) -> None:
        self.phase = phase
        self.phase_t = 0.0

    def fix(self) -> tuple[float, float, float]:
        """Return the current (lat, lon, alt) with ~0.5 m of GPS noise."""
        # S311: simulated receiver jitter, not a security context.
        noise = lambda: (random.random() - 0.5) * 1.0  # noqa: E731, S311
        north = (self.downrange + noise()) * math.cos(self.AZIMUTH_RAD)
        east = (self.downrange + noise()) * math.sin(self.AZIMUTH_RAD) + self.crosswind
        lat = self.pad_lat + north / 111_320.0
        lon = self.pad_lon + east / (111_320.0 * math.cos(math.radians(self.pad_lat)))
        return lat, lon, self.alt


class ChimeraMockDevice(MockSensorDevice):
    """MockSensorDevice whose sensor values come from a looping FlightSim."""

    def __init__(self, device_name: str = "ChimeraMock", server_ip: str | None = None, **kwargs: Any):
        config = {**CHIMERA_CONFIG, "device_name": device_name}
        super().__init__(server_ip=server_ip, config=config, **kwargs)
        self._sim = FlightSim()
        self._sim_t = 0.0
        self._epoch = -1.0
        self._epoch_fix = (PAD_LAT, PAD_LON, 0.0)
        self._epoch_sats = 12
        self._last_logged_phase = "pad"

    def _advance_to(self, elapsed_s: float) -> None:
        """Step the sim to the GPS epoch containing elapsed_s, caching one fix per epoch."""
        if elapsed_s < self._sim_t:
            # Stream restart / device reset rewound the elapsed clock — start a fresh flight.
            self._sim.reset()
            self._sim_t = 0.0
            self._epoch = -1.0
        epoch = math.floor(elapsed_s / GPS_EPOCH_S) * GPS_EPOCH_S
        if epoch == self._epoch:
            return
        while self._sim_t < epoch:
            dt = min(SIM_DT_S, epoch - self._sim_t)
            self._sim.step(dt)
            self._sim_t += dt
        self._epoch = epoch
        self._epoch_fix = self._sim.fix()
        # 8-14 sats: slow wander plus a little jitter (S311: simulated, not security).
        sats = 11 + 2.0 * math.sin(epoch / 17.0) + random.choice((-1, 0, 0, 1))  # noqa: S311
        self._epoch_sats = max(8, min(14, round(sats)))
        if self._sim.phase != self._last_logged_phase:
            logger.info(f"Flight phase: {self._last_logged_phase} -> {self._sim.phase} (alt {self._sim.alt:.0f} m)")
            self._last_logged_phase = self._sim.phase

    # sensor_id is part of the overridden signature; this device keys off the name.
    def _sensor_value(self, sensor_id: int, sensor: SensorConfig, elapsed_s: float) -> float:  # noqa: ARG002
        self._advance_to(elapsed_s)
        lat, lon, alt = self._epoch_fix
        match sensor.name:
            case "Lat":
                return lat
            case "Lon":
                return lon
            case "Alt":
                return alt
            case "Sats":
                return float(self._epoch_sats)
            case _:
                return 0.0


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Chimera GPS mock device: loops a simulated flight")
    parser.add_argument("--server", "-s", help="Server IP address (default: auto-discover)")
    parser.add_argument("--name", "-n", default="ChimeraMock", help="Device name")
    args = parser.parse_args()

    handler = logging.StreamHandler()
    handler.setFormatter(
        _ColoredFormatter(
            fmt="%(asctime)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        ),
    )
    for name in ("MockDevice", "ChimeraMock"):
        log = logging.getLogger(name)
        log.addHandler(handler)
        log.setLevel(logging.INFO)

    device = ChimeraMockDevice(device_name=args.name, server_ip=args.server)
    await device.run()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\n\nStopped by user")


if __name__ == "__main__":
    main()
