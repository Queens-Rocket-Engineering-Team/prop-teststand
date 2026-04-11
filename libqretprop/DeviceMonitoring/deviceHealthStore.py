from dataclasses import dataclass
from enum import Enum

import asyncio
import time

class ConnectionState(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"

@dataclass
class DeviceHealth:
    device_id: str
    address: str

    tcp_last_seen: float | None = None
    udp_last_seen: float | None = None
    last_connect: float | None = None
    last_disconnect: float | None = None

    def _is_disconnected(self) -> bool:
        # Device has disconnected more recently than it has connected
        return self.last_disconnect is not None and (
            self.last_connect is None or self.last_disconnect > self.last_connect
        )

    def tcp_state(self) -> ConnectionState:
        if self._is_disconnected():
            return ConnectionState.DISCONNECTED

        # If we haven't seen the device on TCP, we don't know if it's connected or not
        # This should never happen if the device is properly configured
        if self.tcp_last_seen is None:
            return ConnectionState.UNKNOWN

        return ConnectionState.CONNECTED


    def udp_state(self) -> ConnectionState:
        if self._is_disconnected():
            return ConnectionState.DISCONNECTED

        # If we haven't seen the device on UDP, we don't know if it's connected or not
        # This should never happen if the device is properly configured
        if self.udp_last_seen is None:
            return ConnectionState.UNKNOWN

        return ConnectionState.CONNECTED

class DeviceHealthStore:
    def __init__(self):
        self._store: dict[str, DeviceHealth] = {}
        self._lock = asyncio.Lock()

    async def on_connect(self, device_id: str, address: str):
        async with self._lock:
            entry = self._store.setdefault(device_id, DeviceHealth(device_id=device_id, address=address))

            entry.last_connect = time.monotonic()
            entry.address = address # Update in case the device connects from a new IP

    async def on_disconnect(self, device_id: str):
        async with self._lock:
            if device_id in self._store:
                self._store[device_id].last_disconnect = time.monotonic()
                self._store[device_id].tcp_last_seen = None
                self._store[device_id].udp_last_seen = None

    async def on_tcp_packet(self, device_id: str):
        async with self._lock:
            if device_id in self._store:
                self._store[device_id].tcp_last_seen = time.monotonic()

    async def on_udp_packet(self, device_id: str):
        async with self._lock:
            if device_id in self._store:
                self._store[device_id].udp_last_seen = time.monotonic()

    async def snapshot(self) -> list[dict]:
        async with self._lock:
            return [
                {
                    "device_id": entry.device_id,
                    "address": entry.address,
                    "tcp_state": entry.tcp_state(),
                    "udp_state": entry.udp_state(),
                    "last_connect": entry.last_connect,
                    "last_disconnect": entry.last_disconnect,
                    "tcp_last_seen": entry.tcp_last_seen,
                    "udp_last_seen": entry.udp_last_seen,
                }
                for entry in self._store.values()
            ]

    async def connected_count(self) -> int:
        async with self._lock:
            return sum(1 for entry in self._store.values() if entry.tcp_state() == ConnectionState.CONNECTED)
