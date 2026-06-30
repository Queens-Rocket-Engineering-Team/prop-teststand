from __future__ import annotations
import asyncio
import json
import logging
import socket
import time
from itertools import count
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from libqretprop.drivers.esp import ESPDriver, ESPDriverConnectionClosedError
from libqretprop.qlcp.config_parser import parse_config
from libqretprop.qlcp.enums import ControlState, PacketType
from libqretprop.qlcp.packets import (
    AckPacket,
    ConfigPacket,
    ControlPacket,
    DataPacket,
    NackPacket,
    SimplePacket,
    StatusPacket,
    StreamStartPacket,
)
from libqretprop.runtime.device_registry import DeviceRegistry
from libqretprop.runtime.metrics import Metrics


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from libqretprop.qlcp.config_models import ControlConfig, SensorConfig
    from libqretprop.runtime.command_tracker import CommandRecord, CommandTracker
    from libqretprop.state import SystemState


TrackedCommandPacket = SimplePacket | ControlPacket | StreamStartPacket

TCP_PORT = 50000


class _StatePublisher(Protocol):
    def publish(self, event: dict[str, object] | None) -> None: ...


class ESPDeviceSession:
    """One active configured TCP connection for a QLCP/ESP device."""

    RESYNC_INTERVAL_S: ClassVar[float] = 600.0
    COMMAND_ACK_TIMEOUT_S: ClassVar[float] = 10.0
    HEARTBEAT_INTERVAL_S: ClassVar[float] = 5.0
    HEARTBEAT_ACK_MISS_LIMIT: ClassVar[int] = 3

    def __init__(
        self,
        tcp_socket: socket.socket,
        address: str,
        config: dict[str, Any],
        *,
        connection_key: str,
    ) -> None:
        self.address = address
        self.connection_key = connection_key
        self.qlcp_config = parse_config(config)
        self.driver = ESPDriver(tcp_socket, address)

        self.last_sync_time: float | None = None
        self._resync_pending = False
        self._missed_heartbeat_acks = 0

        self.monitor_task: asyncio.Task[Any] | None = None
        self.heartbeat_task: asyncio.Task[Any] | None = None

    @property
    def is_connected(self) -> bool:
        """True while the TCP socket is open."""
        return self.driver.socket is not None

    def close(self) -> None:
        """Close the TCP socket. Idempotent; may raise OSError on the first call."""
        sock = self.driver.socket
        if sock is not None:
            self.driver.socket = None
            sock.close()

    @property
    def name(self) -> str:
        return self.qlcp_config.name

    @property
    def type(self) -> str:
        return self.qlcp_config.device_type

    @property
    def sensors(self) -> dict[str, SensorConfig]:
        return {sensor.name: sensor for sensor in self.qlcp_config.sensors_by_id.values()}

    @property
    def controls(self) -> dict[str, ControlConfig]:
        return {control.name.upper(): control for control in self.qlcp_config.controls_by_id.values()}

    def needs_resync(self) -> bool:
        """Return True when a TIMESYNC is due for this session."""
        return not self._resync_pending and self.last_sync_time is not None and time.monotonic() - self.last_sync_time > self.RESYNC_INTERVAL_S

    def mark_resync_sent(self) -> None:
        """Mark that a TIMESYNC has been sent and is awaiting an ACK."""
        self._resync_pending = True

    def mark_synced(self) -> None:
        """Mark that a TIMESYNC has been acknowledged and the session is now in sync."""
        self._resync_pending = False

    def register_missed_heartbeat(self) -> bool:
        """Increment the missed heartbeat count and return True if the session has exceeded the miss limit."""
        self._missed_heartbeat_acks += 1
        return self._missed_heartbeat_acks >= self.HEARTBEAT_ACK_MISS_LIMIT

    @property
    def missed_heartbeat_count(self) -> int:
        return self._missed_heartbeat_acks

    def reset_heartbeat_misses(self) -> None:
        """Reset the missed heartbeat count to zero."""
        self._missed_heartbeat_acks = 0

    def record_timesync_ack(self, command: CommandRecord | None) -> None:
        """Record that a TIMESYNC ACK was received, updating the last sync time and marking the session as synced."""
        if command is None:
            return
        self.last_sync_time = time.monotonic()
        self.mark_synced()

    def record_heartbeat_ack(self, command: CommandRecord | None) -> None:
        """Record that a HEARTBEAT ACK was received, resetting the missed heartbeat count."""
        if command is None:
            return
        self.reset_heartbeat_misses()

    def control_name_for_id(self, control_id: int | None) -> str | None:
        """Return the control name for a given control ID, or None if not found."""
        if control_id is None:
            return None

        control = self.qlcp_config.controls_by_id.get(control_id)
        if control is None:
            return None

        return control.name


class ESPConnectionRuntime:
    """Coordinates connected ESP/QLCP device lifecycle.

    Owns: device registry, command-send operations (including all operator-visible
    commands), inbound packet side effects, and state-event publishing.
    Per-session bookkeeping (heartbeat miss counting, resync state) lives in
    ``ESPDeviceSession``.
    """

    def __init__(
        self,
        *,
        state_stream: _StatePublisher,
        command_tracker: CommandTracker,
        system_state: SystemState,
        metrics: Metrics | None = None,
    ) -> None:
        self.devices = DeviceRegistry()
        self.metrics = metrics or Metrics()
        self.command_tracker = command_tracker
        self.system_state = system_state
        self.state_stream = state_stream
        self._connection_counter = count(1)

    def next_connection_key(self) -> str:
        """Return a unique connection key for a new device session."""
        return f"esp-{next(self._connection_counter)}"

    def get_registered_devices(self) -> dict[str, ESPDeviceSession]:
        """Return a snapshot of the currently registered devices by address."""
        return self.devices.snapshot_by_address()

    def _emit(self, event: dict[str, object] | None) -> None:
        """Emit an event to the state stream."""
        self.state_stream.publish(event)

    def is_current_connection(self, session: ESPDeviceSession) -> bool:
        """Return True if *session* is the currently registered session for its address."""
        return self.devices.is_current(session)

    def get_device_by_address(self, address: str) -> ESPDeviceSession | None:
        """Return the registered device session for a given UDP address, or None if not registered."""
        return self.devices.by_address(address)

    async def accept_connection(
        self,
        client_socket: socket.socket,
        address: str,
    ) -> ESPDeviceSession | None:
        """Run the config handshake for a freshly accepted TCP connection.

        Reads the first packet via a transient driver, and if it is a CONFIG packet,
        registers the device. Closes the socket on any pre-registration
        failure.
        """
        driver = ESPDriver(client_socket, address)
        try:
            packet = await driver.read_packet()
        except ESPDriverConnectionClosedError:
            logger.warning(f"Device {address} disconnected during config.")
            client_socket.close()
            return None

        # First packet must be CONFIG
        if not isinstance(packet, ConfigPacket):
            logger.error(f"Expected CONFIG from {address}, got {type(packet).__name__}. Closing connection.")
            client_socket.close()
            return None

        try:
            config_dict = json.loads(packet.config_json)
        except Exception as e:
            logger.error(f"Invalid CONFIG JSON from {address}: {e}. Closing connection.")
            client_socket.close()
            return None

        try:
            return await self.register_configured_device(
                client_socket,
                address,
                config_dict,
                packet.sequence,
            )
        except Exception as e:
            logger.exception(f"Failed to register device from {address}: {e}. Closing connection.")
            client_socket.close()
            return None

    async def register_configured_device(
        self,
        tcp_socket: socket.socket,
        address: str,
        config: dict[str, Any],
        config_sequence: int,
    ) -> ESPDeviceSession:
        """Register a new device session after receiving a CONFIG packet."""
        new_session = ESPDeviceSession(
            tcp_socket,
            address,
            config,
            connection_key=self.next_connection_key(),
        )

        # If a device is already registered for this address, disconnect it before registering the new session.
        old_session = self.devices.by_address(address)
        if old_session is not None:
            logger.warning(
                f"Device {address} attempted to connect and is already registered. Closing old connection.",
            )
            self._disconnect_registered_device(old_session, reason="duplicate_address")

        # If a device with the same name is already registered, disconnect it before registering the new session.
        self.disconnect_registered_devices_with_name(new_session.name)

        # Register the new session and emit a state event.
        self.devices.register(new_session)
        self.metrics.record_device_connection(device=new_session.name)
        self._emit(self.system_state.register_device(new_session))

        # Start the session's monitor and heartbeat tasks.
        self._start_session_tasks(new_session)

        logger.info("Device %s registered from %s", new_session.name, address)

        try:
            # ACK the CONFIG packet
            ack = AckPacket.create(PacketType.CONFIG, config_sequence)
            await new_session.driver.send_packet(ack)

            # Initial TIMESYNC
            await self.send_timesync(new_session, initial=True)

            # Initial STATUS_REQUEST for the device to report its control states
            status_request = SimplePacket.create(PacketType.STATUS_REQUEST)
            await self.send_tracked_command(new_session, status_request)
            logger.debug("Sent initial STATUS_REQUEST to %s", new_session.name)
        except Exception:
            logger.exception("Post-registration setup failed for %s. Removing device.", new_session.name)
            self.remove_device(new_session)
            raise

        return new_session

    def _start_session_tasks(self, session: ESPDeviceSession) -> None:
        loop = asyncio.get_running_loop()
        session.monitor_task = loop.create_task(self._monitor_session(session))
        session.heartbeat_task = loop.create_task(self._heartbeat_session(session))

    async def _monitor_session(self, session: ESPDeviceSession) -> None:
        """TCP monitoring loop for one device connection. Reads packets and dispatches them to handlers."""
        try:
            while True:
                if not session.is_connected:
                    logger.error("Device %s has no socket.", session.name)
                    self.remove_device(session)
                    break

                try:
                    packet = await session.driver.read_packet()
                except ESPDriverConnectionClosedError:
                    logger.warning("Device %s disconnected.", session.name)
                    self.remove_device(session)
                    break

                logger.debug("Decoded %s from %s", type(packet).__name__, session.name)
                await self.handle_packet(session, packet)

                if session.needs_resync():
                    session.mark_resync_sent()
                    await self.send_timesync(session)

        except asyncio.CancelledError:
            logger.info("Stopped monitoring %s", session.name)
            raise
        except Exception:
            logger.exception("Error receiving response from %s", session.name)
            self.remove_device(session)

    async def _heartbeat_session(self, session: ESPDeviceSession) -> None:
        """Run heartbeat checks for one device connection."""
        while True:
            if session.is_connected:
                if self.expire_command_timeouts(session):
                    break

                if not await self.send_heartbeat(session):
                    break

            await asyncio.sleep(session.HEARTBEAT_INTERVAL_S)

    def disconnect_registered_devices_with_name(self, device_name: str) -> None:
        matching_sessions = self.devices.sessions_named(device_name)

        for session in matching_sessions:
            logger.warning(
                "Device %s reconnected from a new address. Closing old connection at %s.",
                session.name,
                session.address,
            )
            self._disconnect_registered_device(session, reason="reconnected_name")

    def close_all(self) -> None:
        for session in list(self.devices.values()):
            self._teardown_session(session, reason="server_shutdown")
            self.metrics.record_device_disconnection("server_shutdown", device=session.name)
        self.devices.clear()
        logger.info("Closed all device sockets and cleared registry.")

    async def send_tracked_command(
        self,
        session: ESPDeviceSession,
        packet: TrackedCommandPacket,
    ) -> CommandRecord:
        """Send a command packet to a device session and track it for ACK/NACK."""
        command = self._track_sent_command(session, packet)
        try:
            await session.driver.send_packet(packet)
        except Exception:
            self.command_tracker.discard(command.command_id)
            raise

        self._emit(self.system_state.record_command_sent(command))
        return command

    async def send_timesync(self, session: ESPDeviceSession, *, initial: bool = False) -> CommandRecord:
        timesync = SimplePacket.create(PacketType.TIMESYNC)
        command = await self.send_tracked_command(session, timesync)
        prefix = "initial " if initial else ""
        logger.debug("Sent %sTIMESYNC to %s", prefix, session.name)
        return command

    async def send_heartbeat(self, session: ESPDeviceSession) -> bool:
        try:
            packet = SimplePacket.create(PacketType.HEARTBEAT)
            await self.send_tracked_command(session, packet)
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            logger.exception("%s heartbeat send failed", session.name)
            self.remove_device(session)
            return False

    # ------------------------------------------------------------------ #
    # Device command operations                                            #
    # ------------------------------------------------------------------ #

    async def get_single(self, session: ESPDeviceSession) -> None:
        """Request a single data sample from the device."""
        await self._send_or_remove(session, SimplePacket.create(PacketType.GET_SINGLE), "GET_SINGLE command")

    async def start_streaming(self, session: ESPDeviceSession, frequency_hz: int) -> None:
        """Request the device to start streaming data at the given frequency."""
        if not frequency_hz or frequency_hz < 1 or frequency_hz > 65535:
            logger.error("Invalid frequency: %d. Must be between 1-65535 Hz.", frequency_hz)
            return
        await self._send_or_remove(
            session,
            StreamStartPacket.create(frequency_hz=frequency_hz),
            f"STREAM_START ({frequency_hz} Hz)",
        )

    async def stop_streaming(self, session: ESPDeviceSession) -> None:
        """Request the device to stop streaming data."""
        await self._send_or_remove(session, SimplePacket.create(PacketType.STREAM_STOP), "STREAM_STOP command")

    async def set_control(
        self,
        session: ESPDeviceSession,
        control_name: str,
        control_state: str,
    ) -> None:
        """Request the device to set a control to a given state (OPEN or CLOSE)."""
        control_name = control_name.upper()
        control_state = control_state.upper()

        if control_name not in session.controls:
            logger.error("Invalid control name '%s'. Valid: %s", control_name, list(session.controls.keys()))
            return
        if control_state not in ["OPEN", "CLOSE"]:
            logger.error("Invalid state '%s'. Valid: OPEN, CLOSE", control_state)
            return

        command_id = session.controls[control_name].id
        state = ControlState.OPEN if control_state == "OPEN" else ControlState.CLOSED
        await self._send_or_remove(
            session,
            ControlPacket.create(command_id=command_id, command_state=state),
            f"CONTROL command (id={command_id}, {control_name} {control_state})",
        )

    async def get_status(self, session: ESPDeviceSession) -> None:
        """Request the device to report its current control states."""
        await self._send_or_remove(session, SimplePacket.create(PacketType.STATUS_REQUEST), "STATUS_REQUEST command")

    async def emergency_stop(self, session: ESPDeviceSession) -> None:
        """Request the device to perform an emergency stop."""
        await self._send_or_remove(session, SimplePacket.create(PacketType.ESTOP), "EMERGENCY STOP command")

    async def _send_or_remove(
        self,
        session: ESPDeviceSession,
        packet: TrackedCommandPacket,
        label: str,
    ) -> None:
        """Send a command packet to a device session, or remove the session if it is not connected."""
        if not session.is_connected:
            logger.error("No socket available for %s to send %s.", session.name, label)
            self.remove_device(session)
            return
        try:
            await self.send_tracked_command(session, packet)
            logger.info("Sent %s to %s", label, session.name)
        except Exception:
            logger.exception("Error sending %s to %s", label, session.name)
            self.remove_device(session)

    def expire_command_timeouts(self, session: ESPDeviceSession) -> bool:
        """Expire any pending commands for a session that have exceeded the ACK timeout."""
        expired_commands = self.command_tracker.expire_pending(
            now=time.monotonic(),
            timeout_s=session.COMMAND_ACK_TIMEOUT_S,
            connection_key=session.connection_key,
        )

        for expired in expired_commands:
            if expired.packet_type == PacketType.HEARTBEAT:
                if self._handle_missed_heartbeat(session, expired):
                    return True
            else:
                logger.debug(
                    "%s command timeout: %s seq=%d",
                    session.name,
                    expired.packet_type.name,
                    expired.packet_sequence,
                )

        return False

    def handle_ack(self, session: ESPDeviceSession, packet: AckPacket) -> CommandRecord | None:
        """Handle an ACK packet from a device session, marking the corresponding command as acknowledged and updating the system state."""
        command = self.command_tracker.mark_acked(
            connection_key=session.connection_key,
            packet_type=packet.ack_packet_type,
            packet_sequence=packet.ack_sequence,
            now=time.monotonic(),
        )
        if command is None:
            logger.debug(
                "%s unmatched ACK for %s seq=%d",
                session.name,
                packet.ack_packet_type.name,
                packet.ack_sequence,
            )

        if packet.ack_packet_type == PacketType.TIMESYNC:
            session.record_timesync_ack(command)
            if command is not None:
                self._emit(self.system_state.record_command_acked(command))
            logger.debug("%s TIMESYNC ACK seq=%d", session.name, packet.ack_sequence)
        elif packet.ack_packet_type == PacketType.HEARTBEAT:
            session.record_heartbeat_ack(command)
            if command is not None:
                self._emit(self.system_state.record_command_acked(command))
            logger.debug("%s HEARTBEAT ACK seq=%d", session.name, packet.ack_sequence)
        elif packet.ack_packet_type == PacketType.CONTROL:
            if command is not None:
                self._emit(self.system_state.record_command_acked(command))
                self._update_control_from_ack(session, command)
            else:
                logger.debug("%s ACK for CONTROL seq=%d", session.name, packet.ack_sequence)
        else:
            if command is not None:
                self._emit(self.system_state.record_command_acked(command))
            logger.debug("%s ACK for %s seq=%d", session.name, packet.ack_packet_type.name, packet.ack_sequence)

        return command

    def handle_nack(self, session: ESPDeviceSession, packet: NackPacket) -> CommandRecord | None:
        """Handle a NACK packet from a device session, marking the corresponding command as failed and updating the system state."""
        command = self.command_tracker.mark_nacked(
            connection_key=session.connection_key,
            packet_type=packet.nack_packet_type,
            packet_sequence=packet.nack_sequence,
            error_code=packet.error_code,
            now=time.monotonic(),
        )
        if command is None:
            logger.debug(
                "%s unmatched NACK for %s seq=%d error=%s",
                session.name,
                packet.nack_packet_type.name,
                packet.nack_sequence,
                packet.error_code.name,
            )
        else:
            self._emit(self.system_state.record_command_nacked(command))

        logger.debug("%s NACK for %s error=%s", session.name, packet.nack_packet_type.name, packet.error_code.name)
        return command

    def handle_status(self, session: ESPDeviceSession, packet: StatusPacket) -> None:
        """Handle a STATUS packet from a device session, updating the system state with the reported control states."""
        for control_state in packet.control_states:
            self._emit(self.system_state.record_reported_control_state(session, control_state.id, control_state.state))

    def cleanup_device(
        self,
        session: ESPDeviceSession,
        *,
        reason: str = "connection_cleanup",
    ) -> None:
        """Clean up a device session by closing its socket, cancelling its tasks, and failing any pending commands."""
        if session.is_connected:
            try:
                session.close()
                logger.info("Closed socket for %s", session.name)
            except OSError:
                logger.exception("Error closing socket for %s", session.name)

        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None

        monitor_task = session.monitor_task
        if monitor_task is not None and monitor_task is not current_task:
            monitor_task.cancel()
            logger.info("Cancelled monitor task for %s", session.name)

        heartbeat_task = session.heartbeat_task
        if heartbeat_task is not None and heartbeat_task is not current_task:
            heartbeat_task.cancel()
            logger.info("Cancelled heartbeat task for %s", session.name)

        self._publish_failed_command_events(
            self.command_tracker.fail_connection(session.connection_key, reason=reason),
        )

    def _teardown_session(self, session: ESPDeviceSession, *, reason: str) -> None:
        """Teardown a device session by cleaning it up and removing it from the registry."""
        self.cleanup_device(session, reason=reason)
        self._emit(self.system_state.mark_disconnected(session))

    def remove_device(self, session: ESPDeviceSession) -> None:
        """Remove a device session from the registry, clean it up, and emit a state event. If the session is not the current registered session for its address, it will be ignored."""
        self._teardown_session(session, reason="connection_cleanup")
        if self.is_current_connection(session):
            self.devices.remove_current(session)
            self.metrics.record_device_disconnection("connection_cleanup", device=session.name)
            logger.info("%s removed from registry.", session.name)
        else:
            logger.debug("Ignored stale removal for %s at %s", session.name, session.address)

    def _disconnect_registered_device(self, session: ESPDeviceSession, *, reason: str) -> None:
        """Disconnect a registered device session, cleaning it up and removing it from the registry."""
        self._teardown_session(session, reason=reason)
        removed = self.devices.pop(session.address, None)
        if removed is not None:
            self.metrics.record_device_disconnection(reason, device=session.name)

    async def handle_packet(
        self,
        session: ESPDeviceSession,
        packet: object,
    ) -> None:
        """Dispatch an incoming packet to the appropriate handler based on its type."""
        match packet:
            case DataPacket():
                logger.error(
                    "Unexpected DATA packet received over TCP from %s. This should be sent over UDP. Ignoring.",
                    session.name,
                )
            case StatusPacket():
                self.handle_status(session, packet)
            case AckPacket():
                self.handle_ack(session, packet)
            case NackPacket():
                self.handle_nack(session, packet)
            case _:
                logger.error("Received unexpected packet type %s from %s over TCP", type(packet).__name__, session.name)

    def _track_sent_command(
        self,
        session: ESPDeviceSession,
        packet: TrackedCommandPacket,
    ) -> CommandRecord:
        """Track a sent command packet in the command tracker, returning the corresponding CommandRecord."""
        packet_type, sequence, control_id, requested_state = self._command_packet_metadata(packet)
        control_name = session.control_name_for_id(control_id)
        return self.command_tracker.mark_sent(
            connection_key=session.connection_key,
            device_name=session.name,
            device_address=session.address,
            packet_type=packet_type,
            packet_sequence=sequence,
            now=time.monotonic(),
            control_id=control_id,
            control_name=control_name,
            requested_state=requested_state,
        )

    @staticmethod
    def _command_packet_metadata(
        packet: TrackedCommandPacket,
    ) -> tuple[PacketType, int, int | None, ControlState | None]:
        """Return the packet type, sequence number, control ID, and requested state for a given command packet."""
        match packet:
            case SimplePacket(packet_type=packet_type, sequence=sequence):
                return packet_type, sequence, None, None
            case ControlPacket(sequence=sequence, command_id=command_id, command_state=command_state):
                return PacketType.CONTROL, sequence, command_id, command_state
            case StreamStartPacket(sequence=sequence):
                return PacketType.STREAM_START, sequence, None, None
            case _:
                message = f"Unsupported tracked command packet: {type(packet).__name__}"
                raise TypeError(message)

    def _handle_missed_heartbeat(self, session: ESPDeviceSession, command: CommandRecord) -> bool:
        """Handle a missed HEARTBEAT ACK for a device session, recording the miss and potentially removing the session if it exceeds the miss limit. Returns True if the session was removed, False otherwise."""
        self.metrics.record_heartbeat_miss(session.name)
        self._emit(self.system_state.record_command_timed_out(command))

        at_limit = session.register_missed_heartbeat()
        if not at_limit:
            logger.debug(
                "%s missed HEARTBEAT ACK seq=%s (%s/%s)",
                session.name,
                command.packet_sequence,
                session.missed_heartbeat_count,
                session.HEARTBEAT_ACK_MISS_LIMIT,
            )
            return False

        logger.error("%s unresponsive: missed %s HEARTBEAT ACKs", session.name, session.missed_heartbeat_count)
        self.remove_device(session)
        return True

    def _update_control_from_ack(self, session: ESPDeviceSession, command: CommandRecord) -> None:
        """Update the system state with the control state change from a CONTROL ACK packet, if the command has a valid control ID and requested state. If either is None, log a debug message and return without updating the state."""
        if command.control_id is None or command.requested_state is None:
            logger.debug("%s ACK for CONTROL seq=%s", session.name, command.packet_sequence)
            return

        control_name = command.control_name or session.control_name_for_id(command.control_id)
        if control_name is None:
            return

        self._emit(self.system_state.record_accepted_control_state(
            session,
            command.control_id,
            command.requested_state,
        ))

    def _publish_failed_command_events(self, commands: list[CommandRecord]) -> None:
        """Publish state events for commands that have failed due to connection closure or timeout."""
        for command in commands:
            self._emit(self.system_state.record_command_timed_out(command))

    async def run_tcp_listener(self, *, port: int = TCP_PORT, backlog: int = 5) -> None:
        """Bind the TCP server socket and accept device connections until cancelled."""
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("0.0.0.0", port))  # noqa: S104
        server_socket.listen(backlog)
        server_socket.setblocking(False)

        logger.info("TCP listener started on port %s", port)

        loop = asyncio.get_event_loop()

        while True:
            try:
                client_socket, addr = await loop.sock_accept(server_socket)
                client_socket.setblocking(False)
                logger.info("Accepted TCP connection from %s", addr[0])

                await self.accept_connection(client_socket, addr[0])

            except asyncio.CancelledError:
                logger.info("TCP listener cancelled")
                server_socket.close()
                raise
            except Exception:
                logger.exception("Error in TCP listener: %s")
                await asyncio.sleep(0.1)
