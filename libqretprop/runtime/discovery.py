from __future__ import annotations
import asyncio
import contextlib
import socket

import libqretprop.redis_logging as ml


MULTICAST_ADDRESS = "239.255.255.250"
MULTICAST_PORT = 1900

# How long the periodic loop sleeps between checks while periodic discovery is disabled.
_DISABLED_POLL_INTERVAL_S = 0.5


class DiscoveryService:
    """Owns device discovery: a periodic discovery loop and one-shot discovery requests.

    The transport (currently SSDP over UDP multicast) is an internal detail; callers use the
    protocol-agnostic ``discover()`` / ``run()`` surface and the ``periodic_*`` config.
    """

    def __init__(
        self,
        *,
        periodic_enabled: bool = True,
        periodic_interval_s: float = 30.0,
        multicast_address: str = MULTICAST_ADDRESS,
        multicast_port: int = MULTICAST_PORT,
    ) -> None:
        self.periodic_enabled = periodic_enabled
        self.periodic_interval_s = periodic_interval_s
        self.multicast_address = multicast_address
        self.multicast_port = multicast_port
        self._socket: socket.socket | None = None

    def discover(self) -> None:
        """Send a single discovery request to the network."""
        if self._socket is None:
            self._socket = self._create_socket()

        ml.dlog("Sending discovery request.")

        request = (
            "M-SEARCH * HTTP/1.1\r\n"
            f"HOST: {self.multicast_address}:{self.multicast_port}\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 2\r\n"
            "ST: urn:qretprop:espdevice:1\r\n"
            "USER-AGENT: QRET/1.0\r\n"
            "\r\n"
        )

        self._socket.sendto(request.encode(), (self.multicast_address, self.multicast_port))

    async def run(self) -> None:
        """Periodically issue discovery requests while periodic discovery is enabled."""
        while True:
            if self.periodic_enabled:
                self.discover()
                await asyncio.sleep(self.periodic_interval_s)
            else:
                await asyncio.sleep(_DISABLED_POLL_INTERVAL_S)

    def _create_socket(self) -> socket.socket:
        """Create a send-only socket for issuing multicast discovery requests."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        # Choose outbound interface
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)

        sock.setblocking(False)
        ml.slog(f"Discovery socket initialized for {self.multicast_address}:{self.multicast_port}")
        return sock


discovery_service = DiscoveryService()
