import asyncio
import socket

import pytest

from prop_teststand.drivers.esp import ESPDriver, ESPDriverConnectionClosedError
from prop_teststand.qlcp.decoding import decode_packet_client
from prop_teststand.qlcp.enums import PacketType
from prop_teststand.qlcp.packets import ConfigPacket, HeartbeatPacket


def test_esp_driver_sends_encoded_packets() -> None:
    async def run() -> None:
        driver_socket, peer_socket = socket.socketpair()
        driver_socket.setblocking(False)
        peer_socket.setblocking(False)

        try:
            driver = ESPDriver(driver_socket, "test-device")
            packet = HeartbeatPacket.create()

            await driver.send_packet(packet)

            loop = asyncio.get_running_loop()
            data = await loop.sock_recv(peer_socket, 4096)
            decoded = decode_packet_client(data)

            assert isinstance(decoded, HeartbeatPacket)
            assert decoded.packet_type == PacketType.HEARTBEAT
            assert decoded.header.sequence == packet.header.sequence
        finally:
            driver_socket.close()
            peer_socket.close()

    asyncio.run(run())



def test_esp_driver_reads_framed_server_packets() -> None:
    async def run() -> None:
        driver_socket, peer_socket = socket.socketpair()
        driver_socket.setblocking(False)
        peer_socket.setblocking(False)

        try:
            driver = ESPDriver(driver_socket, "test-device")
            packet = ConfigPacket.create('{"device_name": "TEST"}')

            loop = asyncio.get_running_loop()
            await loop.sock_sendall(peer_socket, packet.encode())

            decoded = await driver.read_packet()

            assert isinstance(decoded, ConfigPacket)
            assert decoded.config_json == packet.config_json
            assert decoded.header.sequence == packet.header.sequence
        finally:
            driver_socket.close()
            peer_socket.close()

    asyncio.run(run())


def test_esp_driver_resyncs_on_magic_number() -> None:
    async def run() -> None:
        driver_socket, peer_socket = socket.socketpair()
        driver_socket.setblocking(False)
        peer_socket.setblocking(False)

        try:
            driver = ESPDriver(driver_socket, "test-device")
            packet = ConfigPacket.create('{"device_name": "TEST"}')

            # Leading garbage (including a decoy "QL") precedes the real packet.
            loop = asyncio.get_running_loop()
            await loop.sock_sendall(peer_socket, b"\x00\x01garbageQLjunk" + packet.encode())

            decoded = await driver.read_packet()

            assert isinstance(decoded, ConfigPacket)
            assert decoded.config_json == packet.config_json
        finally:
            driver_socket.close()
            peer_socket.close()

    asyncio.run(run())


def test_esp_driver_resyncs_on_magic_number() -> None:
    async def run() -> None:
        driver_socket, peer_socket = socket.socketpair()
        driver_socket.setblocking(False)
        peer_socket.setblocking(False)

        try:
            driver = ESPDriver(driver_socket, "test-device")
            packet = ConfigPacket.create('{"device_name": "TEST"}')

            # Leading garbage (including a decoy "QL") precedes the real packet.
            loop = asyncio.get_running_loop()
            await loop.sock_sendall(peer_socket, b"\x00\x01garbageQLjunk" + packet.encode())

            decoded = await driver.read_packet()

            assert isinstance(decoded, ConfigPacket)
            assert decoded.config_json == packet.config_json
        finally:
            driver_socket.close()
            peer_socket.close()

    asyncio.run(run())


def test_esp_driver_reports_closed_socket_while_reading() -> None:
    async def run() -> None:
        driver_socket, peer_socket = socket.socketpair()
        driver_socket.setblocking(False)
        peer_socket.setblocking(False)

        try:
            driver = ESPDriver(driver_socket, "test-device")
            peer_socket.close()

            with pytest.raises(ESPDriverConnectionClosedError):
                await driver.read_exactly(1)
        finally:
            driver_socket.close()

    asyncio.run(run())
