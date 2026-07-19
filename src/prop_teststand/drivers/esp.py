from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING

from prop_teststand.qlcp.decoding import ServerReceivedPacket, decode_packet_server
from prop_teststand.qlcp.native import HEADER_SIZE, MAGIC_NUM_SIZE, find_magic_num, get_packet_len


if TYPE_CHECKING:
    import socket

    from prop_teststand.qlcp.packets import EncodablePacket


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
        self.socket: socket.socket | None = tcp_socket
        self.address = address

    async def send_packet(self, packet: EncodablePacket) -> None:
        """Send a QLCP packet over TCP."""
        assert self.socket is not None
        loop = asyncio.get_running_loop()
        await loop.sock_sendall(self.socket, packet.encode())

    async def read_packet(self) -> ServerReceivedPacket:
        """Read an incoming QLCP packet over TCP."""
        packet_data = await self.read_packet_bytes()
        return decode_packet_server(packet_data)

    async def read_packet_bytes(self) -> bytes:
        """Read the raw bytes of an incoming QLCP packet over TCP.

        Frames on the magic number per PROTOCOL_SPECIFICATION section 5.1:
        leading bytes before the magic number are discarded to resynchronize
        the stream after corruption or a partial read.
        """
        header = await self.read_aligned_header()
        packet_len = get_packet_len(header)
        payload = await self.read_exactly(packet_len - HEADER_SIZE)
        return header + payload

    async def read_aligned_header(self) -> bytes:
        """Read until the magic number is found and return a header aligned to it."""
        buffer = bytearray(await self.read_exactly(HEADER_SIZE))

        while True:
            index = find_magic_num(buffer)
            if index is not None:
                del buffer[:index]
                buffer.extend(await self.read_exactly(HEADER_SIZE - len(buffer)))
                return bytes(buffer)

            # No magic number yet; the tail may hold a partial one spanning the
            # next read, so keep the last few bytes and read more.
            del buffer[: len(buffer) - (MAGIC_NUM_SIZE - 1)]
            buffer.extend(await self.read_exactly(HEADER_SIZE - len(buffer)))

    async def read_exactly(self, byte_count: int) -> bytes:
        """Read exactly the specified number of bytes from the TCP socket."""
        assert self.socket is not None
        loop = asyncio.get_running_loop()
        chunks = bytearray()

        while len(chunks) < byte_count:
            chunk = await loop.sock_recv(self.socket, byte_count - len(chunks))
            if not chunk:
                message = f"ESP socket {self.address} closed while reading {byte_count} bytes"
                raise ESPDriverConnectionClosedError(message)
            chunks.extend(chunk)

        return bytes(chunks)
