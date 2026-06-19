from __future__ import annotations
import asyncio
import socket
import time
from itertools import count
from typing import Any, Protocol

import libqretprop.mylogging as ml
from libqretprop.Devices.ESPDevice import ESPDevice
from libqretprop.qlcp.constants import HEADER_SIZE
from libqretprop.qlcp.decoding import decode_packet_server
from libqretprop.qlcp.enums import ControlState, PacketType
from libqretprop.qlcp.framing import get_packet_len
from libqretprop.qlcp.packets import (
    AckPacket,
    ControlPacket,
    DataPacket,
    NackPacket,
    SimplePacket,
    StatusPacket,
    StreamStartPacket,
)
from libqretprop.runtime.command_tracker import CommandRecord, CommandTracker
from libqretprop.runtime.command_tracker import command_tracker as runtime_command_tracker
from libqretprop.runtime.state_stream import state_stream as runtime_state_stream
from libqretprop.state import SystemState
from libqretprop.state import system_state as runtime_system_state


TrackedCommandPacket = SimplePacket | ControlPacket | StreamStartPacket


class StatePublisher(Protocol):
    def publish(self, event: dict[str, object] | None) -> None: ...


# Temporary compatibility hook for deviceTools-owned GUI log strings.
# The runtime must not format or own the legacy textual log contract.
class ESPConnectionLegacyLogSink(Protocol):
    def device_connected(self, device: ESPDevice) -> None: ...

    def device_disconnected(self, device: ESPDevice) -> None: ...

    def control_status(self, device: ESPDevice, control_name: str, state: str) -> None: ...


class ESPConnectionRuntime:
    """Coordinates connected ESP/QLCP device lifecycle.

    This is a transitional boundary. The runtime owns registry/lifecycle concerns;
    per-connection packet routing and heartbeat behavior remain here only until a
    smaller session/router split is introduced.
    """

    def __init__(
        self,
        *,
        command_tracker: CommandTracker | None = None,
        system_state: SystemState | None = None,
        state_stream: StatePublisher | None = None,
        legacy_log_sink: ESPConnectionLegacyLogSink | None = None,
    ) -> None:
        self.devices: dict[str, ESPDevice] = {}
        self.command_tracker = runtime_command_tracker if command_tracker is None else command_tracker
        self.system_state = runtime_system_state if system_state is None else system_state
        self.state_stream = runtime_state_stream if state_stream is None else state_stream
        self.legacy_log_sink = legacy_log_sink
        self._connection_counter = count(1)

    def next_connection_key(self) -> str:
        return f"esp-{next(self._connection_counter)}"

    def get_registered_devices(self) -> dict[str, ESPDevice]:
        return self.devices.copy()

    def is_current_connection(self, device: ESPDevice) -> bool:
        registered_device = self.devices.get(device.address)
        return (
            registered_device is not None
            and registered_device.connection_key == device.connection_key
        )

    async def register_configured_device(
        self,
        tcp_socket: socket.socket,
        address: str,
        config: dict[str, Any],
        config_sequence: int,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> ESPDevice:
        new_device = ESPDevice(
            tcp_socket,
            address,
            config,
            connection_key=self.next_connection_key(),
            connection_runtime=self,
        )

        old_device = self.devices.get(address)
        if old_device is not None:
            ml.elog(
                f"Device {address} attempted to connect and is already registered. Closing old connection.",
            )
            self._disconnect_registered_device(old_device)

        self.disconnect_registered_devices_with_name(new_device.name)

        self.devices[address] = new_device
        self._publish_state_event(self.system_state.register_device(new_device))

        listener_loop = asyncio.get_running_loop() if loop is None else loop
        new_device.listenerTask = listener_loop.create_task(self.monitor_device(new_device))

        ml.slog(f"Device {new_device.name} registered from {address}")
        if self.legacy_log_sink is not None:
            self.legacy_log_sink.device_connected(new_device)

        ack = AckPacket.create(PacketType.CONFIG, config_sequence)
        await new_device.driver.send_packet(ack)

        timesync = SimplePacket.create(PacketType.TIMESYNC)
        await self.send_tracked_command(new_device, timesync)
        ml.plog(f"Sent initial TIMESYNC to {new_device.name}")

        status_request = SimplePacket.create(PacketType.STATUS_REQUEST)
        await self.send_tracked_command(new_device, status_request)
        ml.plog(f"Sent initial STATUS_REQUEST to {new_device.name}")

        return new_device

    def disconnect_registered_devices_with_name(self, device_name: str) -> None:
        matching_devices = [
            device
            for device in self.devices.values()
            if device.name == device_name
        ]

        for device in matching_devices:
            ml.elog(
                f"Device {device.name} reconnected from a new address. Closing old connection at {device.address}.",
            )
            self._disconnect_registered_device(device)

    def close_all(self) -> None:
        for device in list(self.devices.values()):
            self._publish_state_event(self.system_state.mark_disconnected(device))
            self.cleanup_device(device, reason="server_shutdown")

        self.devices.clear()
        ml.slog("Closed all device sockets and cleared registry.")

    # Transitional per-connection work. Keep this scoped to QLCP session
    # lifecycle until ESPDeviceSession/QLCPPacketHandler are extracted.
    async def monitor_device(self, device: ESPDevice) -> None:
        """Monitor a single device using LENGTH-based framing from v2 header."""
        loop = asyncio.get_event_loop()
        buffer = b""

        try:
            while True:
                tcp_socket = device.socket
                if tcp_socket is None:
                    ml.elog(f"Device {device.name} has no socket.")
                    self.remove_device(device)
                    break

                data = await loop.sock_recv(tcp_socket, 4096)
                if not data:
                    ml.elog(f"Device {device.name} disconnected.")
                    self.remove_device(device)
                    break

                buffer += data

                while len(buffer) >= HEADER_SIZE:
                    try:
                        packet_len = get_packet_len(buffer)
                        if len(buffer) < packet_len:
                            break

                        packet_data = buffer[:packet_len]
                        packet = decode_packet_server(packet_data)

                        ml.plog(f"Decoded {type(packet).__name__} from {device.name}")
                        await self._handle_tcp_packet(device, packet)

                        buffer = buffer[packet_len:]

                        if self._needs_resync(device):
                            device._resync_pending = True
                            timesync = SimplePacket.create(PacketType.TIMESYNC)
                            await self.send_tracked_command(device, timesync)
                            ml.plog(
                                f"{device.name} resync sent (stale >{ESPDevice.RESYNC_INTERVAL_S / 60:.0f} min)",
                            )

                    except ValueError:
                        break
                    except Exception as e:
                        ml.elog(f"Error decoding packet from {device.name}: {e}")
                        buffer = buffer[1:]

        except asyncio.CancelledError:
            ml.slog(f"Stopped monitoring {device.name}")
            raise
        except Exception as e:
            ml.elog(f"Error receiving response from {device.name}: {e}")
            if device.address in self.devices:
                self.remove_device(device)

    async def send_tracked_command(
        self,
        device: ESPDevice,
        packet: TrackedCommandPacket,
    ) -> CommandRecord:
        command = self._track_sent_command(device, packet)
        try:
            await device.driver.send_packet(packet)
        except Exception:
            self.command_tracker.discard(command.command_id)
            raise

        self._publish_state_event(self.system_state.record_command_sent(command))
        return command

    async def send_heartbeat(self, device: ESPDevice) -> bool:
        command: CommandRecord | None = None
        try:
            packet = SimplePacket.create(PacketType.HEARTBEAT)
            command = self.command_tracker.mark_sent(
                connection_key=device.connection_key,
                device_name=device.name,
                device_address=device.address,
                packet_type=PacketType.HEARTBEAT,
                packet_sequence=packet.sequence,
                now=time.monotonic(),
            )
            await device.driver.send_packet(packet)
            self._publish_state_event(self.system_state.record_command_sent(command))
            return True
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            if command is not None:
                self.command_tracker.discard(command.command_id)

            ml.elog(f"{device.name} heartbeat send failed: {e}")
            self.remove_device(device)
            return False

    def expire_command_timeouts(self, device: ESPDevice) -> bool:
        expired_commands = self.command_tracker.expire_pending(
            now=time.monotonic(),
            timeout_s=device.COMMAND_ACK_TIMEOUT_S,
            connection_key=device.connection_key,
        )

        for expired in expired_commands:
            if expired.packet_type == PacketType.HEARTBEAT:
                if self._handle_missed_heartbeat(device, expired):
                    return True
            else:
                ml.plog(
                    f"{device.name} command timeout: {expired.packet_type.name} seq={expired.packet_sequence}",
                )

        return False

    def handle_ack(self, device: ESPDevice, packet: AckPacket) -> CommandRecord | None:
        command = self.command_tracker.mark_acked(
            connection_key=device.connection_key,
            packet_type=packet.ack_packet_type,
            packet_sequence=packet.ack_sequence,
            now=time.monotonic(),
        )
        if command is None:
            ml.plog(
                f"{device.name} unmatched ACK for {packet.ack_packet_type.name} seq={packet.ack_sequence}",
            )

        if packet.ack_packet_type == PacketType.TIMESYNC:
            device.last_sync_time = time.monotonic()
            device._resync_pending = False
            ml.plog(f"{device.name} TIMESYNC completed")
        elif packet.ack_packet_type == PacketType.HEARTBEAT:
            device.handleHeartbeatAck(command)
            if command is not None:
                self._publish_state_event(self.system_state.record_command_acked(command))
            ml.plog(f"{device.name} HEARTBEAT ACK seq={packet.ack_sequence}")
        elif packet.ack_packet_type == PacketType.CONTROL:
            if command is not None:
                self._publish_state_event(self.system_state.record_command_acked(command))
                self._update_control_from_ack(device, command)
            else:
                ml.plog(f"{device.name} ACK for CONTROL seq={packet.ack_sequence}")
        else:
            if command is not None:
                self._publish_state_event(self.system_state.record_command_acked(command))
            ml.plog(f"{device.name} ACK for {packet.ack_packet_type.name} seq={packet.ack_sequence}")

        return command

    def handle_nack(self, device: ESPDevice, packet: NackPacket) -> CommandRecord | None:
        command = self.command_tracker.mark_nacked(
            connection_key=device.connection_key,
            packet_type=packet.nack_packet_type,
            packet_sequence=packet.nack_sequence,
            error_code=packet.error_code,
            now=time.monotonic(),
        )
        if command is None:
            ml.plog(
                f"{device.name} unmatched NACK for {packet.nack_packet_type.name} "
                f"seq={packet.nack_sequence} error={packet.error_code.name}",
            )
        else:
            self._publish_state_event(self.system_state.record_command_nacked(command))

        ml.plog(f"{device.name} NACK for {packet.nack_packet_type.name} error={packet.error_code.name}")
        return command

    def handle_status(self, device: ESPDevice, packet: StatusPacket) -> None:
        for control_state in packet.control_states:
            control = device.qlcp_config.controls_by_id.get(control_state.id)
            if control is None:
                continue

            state_str = self._control_state_string(control_state.state)
            device.setControlState(control.name, state_str)
            self._publish_state_event(
                self.system_state.update_control_state(device, control_state.id, control_state.state),
            )
            if self.legacy_log_sink is not None:
                self.legacy_log_sink.control_status(device, control.name, state_str)

    def cleanup_device(
        self,
        device: ESPDevice,
        *,
        reason: str = "connection_cleanup",
    ) -> None:
        tcp_socket = getattr(device, "socket", None)
        if tcp_socket:
            try:
                tcp_socket.close()
                ml.slog(f"Closed socket for {device.name}")
            except OSError as e:
                ml.elog(f"Error closing socket for {device.name}: {e}")
            finally:
                device.socket = None

        listener_task = getattr(device, "listenerTask", None)
        if listener_task is not None:
            try:
                listener_task.cancel()
                ml.slog(f"Cancelled listener task for {device.name}")
            except Exception as e:
                ml.elog(f"Error cancelling listener task for {device.name}: {e}")

        heartbeat_task = getattr(device, "heartbeat_task", None)
        if heartbeat_task is not None:
            try:
                heartbeat_task.cancel()
                ml.slog(f"Cancelled heartbeat task for {device.name}")
            except Exception as e:
                ml.elog(f"Error cancelling heartbeat task for {device.name}: {e}")

        self._publish_failed_command_events(
            self.command_tracker.fail_connection(device.connection_key, reason=reason),
        )

    def remove_device(self, device: ESPDevice) -> None:
        self.cleanup_device(device)
        self._publish_state_event(self.system_state.mark_disconnected(device))

        if self.is_current_connection(device):
            del self.devices[device.address]
            ml.slog(f"{device.name} removed from registry.")
            if self.legacy_log_sink is not None:
                self.legacy_log_sink.device_disconnected(device)
        else:
            ml.plog(f"Ignored stale removal for {device.name} at {device.address}")

    def _disconnect_registered_device(self, device: ESPDevice) -> None:
        self.cleanup_device(device)
        self._publish_state_event(self.system_state.mark_disconnected(device))
        self.devices.pop(device.address, None)

    async def _handle_tcp_packet(
        self,
        device: ESPDevice,
        packet: object,
    ) -> None:
        match packet:
            case DataPacket():
                ml.elog(
                    f"Unexpected DATA packet received over TCP from {device.name}. This should be sent over UDP. Ignoring.",
                )
            case StatusPacket(control_states=control_states) if control_states:
                self.handle_status(device, packet)
            case AckPacket():
                self.handle_ack(device, packet)
            case NackPacket():
                self.handle_nack(device, packet)
            case _:
                ml.elog(f"Received unexpected packet type {type(packet).__name__} from {device.name} over TCP")

    def _track_sent_command(
        self,
        device: ESPDevice,
        packet: TrackedCommandPacket,
    ) -> CommandRecord:
        packet_type, sequence, control_id, requested_state = self._command_packet_metadata(packet)
        control_name = self._control_name_for_command(device, control_id)
        return self.command_tracker.mark_sent(
            connection_key=device.connection_key,
            device_name=device.name,
            device_address=device.address,
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

        raise TypeError(f"Unsupported tracked command packet: {type(packet).__name__}")

    @staticmethod
    def _control_name_for_command(device: ESPDevice, control_id: int | None) -> str | None:
        if control_id is None:
            return None

        control = device.qlcp_config.controls_by_id.get(control_id)
        if control is None:
            return None

        return control.name

    def _handle_missed_heartbeat(self, device: ESPDevice, command: CommandRecord) -> bool:
        device._missed_heartbeat_acks += 1
        self._publish_state_event(self.system_state.record_command_timed_out(command))

        if device._missed_heartbeat_acks < device.HEARTBEAT_ACK_MISS_LIMIT:
            ml.plog(
                f"{device.name} missed HEARTBEAT ACK seq={command.packet_sequence} "
                f"({device._missed_heartbeat_acks}/{device.HEARTBEAT_ACK_MISS_LIMIT})",
            )
            return False

        device.is_responsive = False
        ml.elog(f"{device.name} marked unresponsive: missed {device._missed_heartbeat_acks} HEARTBEAT ACKs")
        self.remove_device(device)
        return True

    def _update_control_from_ack(self, device: ESPDevice, command: CommandRecord) -> None:
        if command.control_id is None or command.requested_state is None:
            ml.plog(f"{device.name} ACK for CONTROL seq={command.packet_sequence}")
            return

        control_name = command.control_name or self._control_name_for_command(device, command.control_id)
        if control_name is None:
            return

        state_str = self._control_state_string(command.requested_state)
        device.setControlState(control_name, state_str)
        self._publish_state_event(
            self.system_state.update_control_state(
                device,
                command.control_id,
                command.requested_state,
            ),
        )
        if self.legacy_log_sink is not None:
            self.legacy_log_sink.control_status(device, control_name, state_str)

    @staticmethod
    def _control_state_string(state: ControlState) -> str:
        if state == ControlState.OPEN:
            return "OPEN"
        if state == ControlState.CLOSED:
            return "CLOSED"
        return "UNKNOWN"

    @staticmethod
    def _needs_resync(device: ESPDevice) -> bool:
        return (
            not device._resync_pending
            and device.last_sync_time is not None
            and time.monotonic() - device.last_sync_time > ESPDevice.RESYNC_INTERVAL_S
        )

    def _publish_state_event(self, event: dict[str, object] | None) -> None:
        self.state_stream.publish(event)

    def _publish_failed_command_events(self, commands: list[CommandRecord]) -> None:
        for command in commands:
            self._publish_state_event(self.system_state.record_command_timed_out(command))


esp_runtime = ESPConnectionRuntime()
