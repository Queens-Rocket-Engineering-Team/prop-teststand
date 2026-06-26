from __future__ import annotations
import asyncio
import json
import socket
import time
from itertools import count
from typing import TYPE_CHECKING, Any, Protocol

import libqretprop.redis_logging as ml
from libqretprop.drivers.esp import ESPDriver, ESPDriverConnectionClosedError
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
from libqretprop.runtime.command_tracker import CommandTracker
from libqretprop.runtime.esp_device_session import ESPDeviceSession
from libqretprop.state import SystemState


if TYPE_CHECKING:
    from libqretprop.runtime.command_types import CommandRecord


TrackedCommandPacket = SimplePacket | ControlPacket | StreamStartPacket

TCP_PORT = 50000


class StatePublisher(Protocol):
    def publish(self, event: dict[str, object] | None) -> None: ...


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
        state_stream: StatePublisher,
        command_tracker: CommandTracker | None = None,
        system_state: SystemState | None = None,
    ) -> None:
        self.devices: dict[str, ESPDeviceSession] = {}
        self.command_tracker = CommandTracker() if command_tracker is None else command_tracker
        if system_state is None:
            system_state = SystemState(command_tracker=self.command_tracker)
        self.system_state = system_state
        self.state_stream = state_stream
        self._connection_counter = count(1)

    def next_connection_key(self) -> str:
        return f"esp-{next(self._connection_counter)}"

    def get_registered_devices(self) -> dict[str, ESPDeviceSession]:
        return self.devices.copy()

    def is_current_connection(self, session: ESPDeviceSession) -> bool:
        registered_device = self.devices.get(session.address)
        return (
            registered_device is not None
            and registered_device.connection_key == session.connection_key
        )

    async def accept_connection(
        self,
        client_socket: socket.socket,
        address: str,
    ) -> ESPDeviceSession | None:
        """Run the config handshake for a freshly accepted TCP connection.

        Reads the first packet via a transient driver, and if it is a CONFIG packet,
        registers the device. The socket is closed (not leaked) on any other outcome.
        """
        driver = ESPDriver(client_socket, address)
        try:
            packet = await driver.read_packet()
        except ESPDriverConnectionClosedError:
            ml.elog(f"Device {address} disconnected during config.")
            client_socket.close()
            return None

        if not isinstance(packet, ConfigPacket):
            ml.elog(f"Expected CONFIG from {address}, got {type(packet).__name__}. Closing connection.")
            client_socket.close()
            return None

        config_dict = json.loads(packet.config_json)
        return await self.register_configured_device(
            client_socket,
            address,
            config_dict,
            packet.sequence,
        )

    async def register_configured_device(
        self,
        tcp_socket: socket.socket,
        address: str,
        config: dict[str, Any],
        config_sequence: int,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> ESPDeviceSession:
        new_session = ESPDeviceSession(
            tcp_socket,
            address,
            config,
            connection_key=self.next_connection_key(),
        )

        old_session = self.devices.get(address)
        if old_session is not None:
            ml.elog(
                f"Device {address} attempted to connect and is already registered. Closing old connection.",
            )
            self._disconnect_registered_device(old_session)

        self.disconnect_registered_devices_with_name(new_session.name)

        self.devices[address] = new_session
        self._publish_state_event(self.system_state.register_device(new_session))

        listener_loop = asyncio.get_running_loop() if loop is None else loop
        new_session.start(self, loop=listener_loop)

        ml.slog(f"Device {new_session.name} registered from {address}")

        ack = AckPacket.create(PacketType.CONFIG, config_sequence)
        await new_session.driver.send_packet(ack)

        await self.send_timesync(new_session, initial=True)

        status_request = SimplePacket.create(PacketType.STATUS_REQUEST)
        await self.send_tracked_command(new_session, status_request)
        ml.plog(f"Sent initial STATUS_REQUEST to {new_session.name}")

        return new_session

    def disconnect_registered_devices_with_name(self, device_name: str) -> None:
        matching_sessions = [
            session
            for session in self.devices.values()
            if session.name == device_name
        ]

        for session in matching_sessions:
            ml.elog(
                f"Device {session.name} reconnected from a new address. Closing old connection at {session.address}.",
            )
            self._disconnect_registered_device(session)

    def close_all(self) -> None:
        for session in list(self.devices.values()):
            self._publish_state_event(self.system_state.mark_disconnected(session))
            self.cleanup_device(session, reason="server_shutdown")

        self.devices.clear()
        ml.slog("Closed all device sockets and cleared registry.")

    async def send_tracked_command(
        self,
        session: ESPDeviceSession,
        packet: TrackedCommandPacket,
    ) -> CommandRecord:
        command = self._track_sent_command(session, packet)
        try:
            await session.driver.send_packet(packet)
        except Exception:
            self.command_tracker.discard(command.command_id)
            raise

        self._publish_state_event(self.system_state.record_command_sent(command))
        return command

    async def send_timesync(self, session: ESPDeviceSession, *, initial: bool = False) -> CommandRecord:
        timesync = SimplePacket.create(PacketType.TIMESYNC)
        command = await self.send_tracked_command(session, timesync)
        prefix = "initial " if initial else ""
        ml.plog(f"Sent {prefix}TIMESYNC to {session.name}")
        return command

    async def send_heartbeat(self, session: ESPDeviceSession) -> bool:
        command: CommandRecord | None = None
        try:
            packet = SimplePacket.create(PacketType.HEARTBEAT)
            command = self.command_tracker.mark_sent(
                connection_key=session.connection_key,
                device_name=session.name,
                device_address=session.address,
                packet_type=PacketType.HEARTBEAT,
                packet_sequence=packet.sequence,
                now=time.monotonic(),
            )
            await session.driver.send_packet(packet)
            self._publish_state_event(self.system_state.record_command_sent(command))
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            if command is not None:
                self.command_tracker.discard(command.command_id)

            ml.elog(f"{session.name} heartbeat send failed: {e}")
            self.remove_device(session)
            return False
        else:
            return True

    # ------------------------------------------------------------------ #
    # Device command operations                                            #
    # ------------------------------------------------------------------ #

    async def get_single(self, session: ESPDeviceSession) -> None:
        await self._send_or_remove(session, SimplePacket.create(PacketType.GET_SINGLE), "GET_SINGLE command")

    async def start_streaming(self, session: ESPDeviceSession, frequency_hz: int) -> None:
        if not frequency_hz or frequency_hz < 1 or frequency_hz > 65535:
            ml.elog(f"Invalid frequency: {frequency_hz}. Must be between 1-65535 Hz.")
            return
        await self._send_or_remove(
            session,
            StreamStartPacket.create(frequency_hz=frequency_hz),
            f"STREAM_START ({frequency_hz} Hz)",
        )

    async def stop_streaming(self, session: ESPDeviceSession) -> None:
        await self._send_or_remove(session, SimplePacket.create(PacketType.STREAM_STOP), "STREAM_STOP command")

    async def set_control(
        self,
        session: ESPDeviceSession,
        control_name: str,
        control_state: str,
    ) -> None:
        control_name = control_name.upper()
        control_state = control_state.upper()

        if control_name not in session.controls:
            ml.elog(f"Invalid control name '{control_name}'. Valid: {list(session.controls.keys())}")
            return
        if control_state not in ["OPEN", "CLOSE"]:
            ml.elog(f"Invalid state '{control_state}'. Valid: OPEN, CLOSE")
            return

        command_id = session.controls[control_name].id
        state = ControlState.OPEN if control_state == "OPEN" else ControlState.CLOSED
        await self._send_or_remove(
            session,
            ControlPacket.create(command_id=command_id, command_state=state),
            f"CONTROL command (id={command_id}, {control_name} {control_state})",
        )

    async def get_status(self, session: ESPDeviceSession) -> None:
        await self._send_or_remove(session, SimplePacket.create(PacketType.STATUS_REQUEST), "STATUS_REQUEST command")

    async def emergency_stop(self, session: ESPDeviceSession) -> None:
        await self._send_or_remove(session, SimplePacket.create(PacketType.ESTOP), "EMERGENCY STOP command")

    async def _send_or_remove(
        self,
        session: ESPDeviceSession,
        packet: TrackedCommandPacket,
        label: str,
    ) -> None:
        if not session.socket:
            ml.elog(f"No socket available for {session.name} to send {label}.")
            self.remove_device(session)
            return
        try:
            await self.send_tracked_command(session, packet)
            ml.slog(f"Sent {label} to {session.name}")
        except Exception as e:
            ml.elog(f"Error sending {label} to {session.name}: {e}")
            self.remove_device(session)

    def expire_command_timeouts(self, session: ESPDeviceSession) -> bool:
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
                ml.plog(
                    f"{session.name} command timeout: {expired.packet_type.name} seq={expired.packet_sequence}",
                )

        return False

    def handle_ack(self, session: ESPDeviceSession, packet: AckPacket) -> CommandRecord | None:
        command = self.command_tracker.mark_acked(
            connection_key=session.connection_key,
            packet_type=packet.ack_packet_type,
            packet_sequence=packet.ack_sequence,
            now=time.monotonic(),
        )
        if command is None:
            ml.plog(
                f"{session.name} unmatched ACK for {packet.ack_packet_type.name} seq={packet.ack_sequence}",
            )

        if packet.ack_packet_type == PacketType.TIMESYNC:
            session.last_sync_time = time.monotonic()
            session.mark_synced()
            ml.plog(f"{session.name} TIMESYNC completed")
        elif packet.ack_packet_type == PacketType.HEARTBEAT:
            session.record_heartbeat_ack(command)
            if command is not None:
                self._publish_state_event(self.system_state.record_command_acked(command))
            ml.plog(f"{session.name} HEARTBEAT ACK seq={packet.ack_sequence}")
        elif packet.ack_packet_type == PacketType.CONTROL:
            if command is not None:
                self._publish_state_event(self.system_state.record_command_acked(command))
                self._update_control_from_ack(session, command)
            else:
                ml.plog(f"{session.name} ACK for CONTROL seq={packet.ack_sequence}")
        else:
            if command is not None:
                self._publish_state_event(self.system_state.record_command_acked(command))
            ml.plog(f"{session.name} ACK for {packet.ack_packet_type.name} seq={packet.ack_sequence}")

        return command

    def handle_nack(self, session: ESPDeviceSession, packet: NackPacket) -> CommandRecord | None:
        command = self.command_tracker.mark_nacked(
            connection_key=session.connection_key,
            packet_type=packet.nack_packet_type,
            packet_sequence=packet.nack_sequence,
            error_code=packet.error_code,
            now=time.monotonic(),
        )
        if command is None:
            ml.plog(
                f"{session.name} unmatched NACK for {packet.nack_packet_type.name} "
                f"seq={packet.nack_sequence} error={packet.error_code.name}",
            )
        else:
            self._publish_state_event(self.system_state.record_command_nacked(command))

        ml.plog(f"{session.name} NACK for {packet.nack_packet_type.name} error={packet.error_code.name}")
        return command

    def handle_status(self, session: ESPDeviceSession, packet: StatusPacket) -> None:
        for control_state in packet.control_states:
            control = session.qlcp_config.controls_by_id.get(control_state.id)
            if control is None:
                continue

            state_str = self._control_state_string(control_state.state)
            session.set_control_state(control.name, state_str)
            self._publish_state_event(
                self.system_state.update_control_state(session, control_state.id, control_state.state),
            )

    def cleanup_device(
        self,
        session: ESPDeviceSession,
        *,
        reason: str = "connection_cleanup",
    ) -> None:
        tcp_socket = getattr(session, "socket", None)
        if tcp_socket:
            try:
                tcp_socket.close()
                ml.slog(f"Closed socket for {session.name}")
            except OSError as e:
                ml.elog(f"Error closing socket for {session.name}: {e}")
            finally:
                session.socket = None

        monitor_task = getattr(session, "monitor_task", None)
        if monitor_task is not None:
            try:
                monitor_task.cancel()
                ml.slog(f"Cancelled monitor task for {session.name}")
            except Exception as e:
                ml.elog(f"Error cancelling monitor task for {session.name}: {e}")

        heartbeat_task = getattr(session, "heartbeat_task", None)
        if heartbeat_task is not None:
            try:
                heartbeat_task.cancel()
                ml.slog(f"Cancelled heartbeat task for {session.name}")
            except Exception as e:
                ml.elog(f"Error cancelling heartbeat task for {session.name}: {e}")

        self._publish_failed_command_events(
            self.command_tracker.fail_connection(session.connection_key, reason=reason),
        )

    def remove_device(self, session: ESPDeviceSession) -> None:
        self.cleanup_device(session)
        self._publish_state_event(self.system_state.mark_disconnected(session))

        if self.is_current_connection(session):
            del self.devices[session.address]
            ml.slog(f"{session.name} removed from registry.")
        else:
            ml.plog(f"Ignored stale removal for {session.name} at {session.address}")

    def _disconnect_registered_device(self, session: ESPDeviceSession) -> None:
        self.cleanup_device(session)
        self._publish_state_event(self.system_state.mark_disconnected(session))
        self.devices.pop(session.address, None)

    async def handle_packet(
        self,
        session: ESPDeviceSession,
        packet: object,
    ) -> None:
        match packet:
            case DataPacket():
                ml.elog(
                    f"Unexpected DATA packet received over TCP from {session.name}. This should be sent over UDP. Ignoring.",
                )
            case StatusPacket(control_states=control_states) if control_states:
                self.handle_status(session, packet)
            case AckPacket():
                self.handle_ack(session, packet)
            case NackPacket():
                self.handle_nack(session, packet)
            case _:
                ml.elog(f"Received unexpected packet type {type(packet).__name__} from {session.name} over TCP")

    def _track_sent_command(
        self,
        session: ESPDeviceSession,
        packet: TrackedCommandPacket,
    ) -> CommandRecord:
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
        match packet:
            case SimplePacket(packet_type=packet_type, sequence=sequence):
                return packet_type, sequence, None, None
            case ControlPacket(sequence=sequence, command_id=command_id, command_state=command_state):
                return PacketType.CONTROL, sequence, command_id, command_state
            case StreamStartPacket(sequence=sequence):
                return PacketType.STREAM_START, sequence, None, None

        message = f"Unsupported tracked command packet: {type(packet).__name__}"
        raise TypeError(message)

    def _handle_missed_heartbeat(self, session: ESPDeviceSession, command: CommandRecord) -> bool:
        self._publish_state_event(self.system_state.record_command_timed_out(command))

        at_limit = session.register_missed_heartbeat()
        if not at_limit:
            ml.plog(
                f"{session.name} missed HEARTBEAT ACK seq={command.packet_sequence} "
                f"({session.missed_heartbeat_count}/{session.HEARTBEAT_ACK_MISS_LIMIT})",
            )
            return False

        session.mark_unresponsive()
        ml.elog(f"{session.name} marked unresponsive: missed {session.missed_heartbeat_count} HEARTBEAT ACKs")
        self.remove_device(session)
        return True

    def _update_control_from_ack(self, session: ESPDeviceSession, command: CommandRecord) -> None:
        if command.control_id is None or command.requested_state is None:
            ml.plog(f"{session.name} ACK for CONTROL seq={command.packet_sequence}")
            return

        control_name = command.control_name or session.control_name_for_id(command.control_id)
        if control_name is None:
            return

        state_str = self._control_state_string(command.requested_state)
        session.set_control_state(control_name, state_str)
        self._publish_state_event(
            self.system_state.update_control_state(
                session,
                command.control_id,
                command.requested_state,
            ),
        )

    @staticmethod
    def _control_state_string(state: ControlState) -> str:
        if state == ControlState.OPEN:
            return "OPEN"
        if state == ControlState.CLOSED:
            return "CLOSED"
        return "UNKNOWN"

    def _publish_state_event(self, event: dict[str, object] | None) -> None:
        self.state_stream.publish(event)

    def _publish_failed_command_events(self, commands: list[CommandRecord]) -> None:
        for command in commands:
            self._publish_state_event(self.system_state.record_command_timed_out(command))


class ESPConnectionListener:
    """Owns the TCP server socket and accept loop.

    Delegates each accepted connection's config handshake and registration to
    ``ESPConnectionRuntime.accept_connection``; owns no per-device or protocol logic.
    """

    def __init__(
        self,
        runtime: ESPConnectionRuntime,
        *,
        port: int = TCP_PORT,
        backlog: int = 5,
    ) -> None:
        self.runtime = runtime
        self.port = port
        self.backlog = backlog

    async def run(self) -> None:
        """Bind the TCP server socket and accept device connections until cancelled."""
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("0.0.0.0", self.port))  # noqa: S104
        server_socket.listen(self.backlog)
        server_socket.setblocking(False)

        ml.slog(f"TCP listener started on port {self.port}")

        loop = asyncio.get_event_loop()

        while True:
            try:
                client_socket, addr = await loop.sock_accept(server_socket)
                client_socket.setblocking(False)
                ml.slog(f"Accepted TCP connection from {addr[0]}")

                await self.runtime.accept_connection(client_socket, addr[0])

            except asyncio.CancelledError:
                ml.slog("TCP listener cancelled")
                server_socket.close()
                raise
            except Exception as e:
                ml.elog(f"Error in TCP listener: {e}")
                await asyncio.sleep(0.1)
