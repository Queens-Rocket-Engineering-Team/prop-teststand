from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING

from libqretprop.qlcp.constants import HEADER_SIZE
from libqretprop.qlcp.decoding import ServerReceivedPacket, decode_packet_server
from libqretprop.qlcp.framing import get_packet_len


if TYPE_CHECKING:
    import socket

    from libqretprop.qlcp.packets import EncodablePacket


class ESPDriverError(Exception):
    """Raised when low-level ESP communication fails."""


class ESPDriverConnectionClosedError(ESPDriverError):
    """Raised when an ESP socket closes while reading."""


class ESPDriver:
    """Low-level socket and QLCP framing interface for an ESP device."""

    def __init__(
        self,
        tcp_socket: socket.socket,
        address: str,
    ) -> None:
        self.socket = tcp_socket
        self.address = address

    async def send_packet(self, packet: EncodablePacket) -> None:
        loop = asyncio.get_running_loop()
        await loop.sock_sendall(self.socket, packet.encode())

    async def read_packet(self) -> ServerReceivedPacket:
        packet_data = await self.read_packet_bytes()
        return decode_packet_server(packet_data)

    async def read_packet_bytes(self) -> bytes:
        header = await self.read_exactly(HEADER_SIZE)
        packet_len = get_packet_len(header)
        payload = await self.read_exactly(packet_len - HEADER_SIZE)
        return header + payload

    async def read_exactly(self, byte_count: int) -> bytes:
        loop = asyncio.get_running_loop()
        chunks = bytearray()

        while len(chunks) < byte_count:
            chunk = await loop.sock_recv(self.socket, byte_count - len(chunks))
            if not chunk:
                message = f"ESP socket {self.address} closed while reading {byte_count} bytes"
                raise ESPDriverConnectionClosedError(message)
            chunks.extend(chunk)

        return bytes(chunks)
