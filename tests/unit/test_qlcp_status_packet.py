from prop_teststand.qlcp.decoding import decode_packet_server
from prop_teststand.qlcp.enums import ControlConfirmStatus, ControlState, ControlType, PacketType
from prop_teststand.qlcp.packets import ControlStatus, StatusPacket, StatusRequestPacket


def test_status_round_trip_carries_control_status_byte() -> None:
    """The v3.1 per-control status byte (7 bytes/control) must survive an encode/decode round trip."""
    ack_packet = StatusRequestPacket.create()
    control_states = [
        ControlStatus(id=0, type=ControlType.BOOL, state=ControlState.OPEN, status=ControlConfirmStatus.CONFIRMED),
        ControlStatus(id=1, type=ControlType.UINT32, state=42, status=ControlConfirmStatus.PENDING),
    ]
    packet = StatusPacket.create(ack_packet=ack_packet, control_states=control_states)

    decoded = decode_packet_server(packet.encode())

    assert isinstance(decoded, StatusPacket)
    assert decoded.ack_packet_type == PacketType.STATUS_REQUEST
    assert [c.status for c in decoded.control_states] == [ControlConfirmStatus.CONFIRMED, ControlConfirmStatus.PENDING]
    assert [c.state for c in decoded.control_states] == [ControlState.OPEN, 42]


def test_status_round_trip_error_state_bytes_are_not_trusted() -> None:
    """When status is ERROR, the state bytes are protocol-undefined and must not be surfaced."""
    ack_packet = StatusRequestPacket.create()
    control_states = [
        ControlStatus(id=0, type=ControlType.BOOL, state=None, status=ControlConfirmStatus.ERROR),
    ]
    packet = StatusPacket.create(ack_packet=ack_packet, control_states=control_states)

    decoded = decode_packet_server(packet.encode())

    assert isinstance(decoded, StatusPacket)
    assert decoded.control_states[0].status == ControlConfirmStatus.ERROR
    assert decoded.control_states[0].state is None


def test_unsolicited_status_round_trip_uses_no_ack_sentinel() -> None:
    """An unsolicited STATUS update must round-trip with ack_packet_type == NO_ACK."""
    control_states = [
        ControlStatus(id=0, type=ControlType.BOOL, state=ControlState.CLOSED, status=ControlConfirmStatus.CONFIRMED),
    ]
    packet = StatusPacket.create_unsolicited(control_states=control_states)

    decoded = decode_packet_server(packet.encode())

    assert isinstance(decoded, StatusPacket)
    assert decoded.ack_packet_type == PacketType.NO_ACK
