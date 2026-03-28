import json
from typing import Any

import pytest

from libqretprop.protocol import (
    PROTOCOL_VERSION,
    AckPacket,
    ConfigPacket,
    ControlPacket,
    ControlState,
    ControlStatus,
    DataPacket,
    DeviceStatus,
    ErrorCode,
    NackPacket,
    PacketHeader,
    PacketType,
    SensorReading,
    SimplePacket,
    StatusPacket,
    StreamStartPacket,
    Unit,
    decode_packet,
)


# Header Tests

def make_test_header() -> tuple[bytes, PacketHeader]:
    header = PacketHeader(
        version=PROTOCOL_VERSION,
        packet_type=PacketType.HEARTBEAT,
        sequence=42,
        length=9,
        timestamp=123456,
    )

    packed = header.pack()
    return packed, PacketHeader.unpack(packed)

def test_packet_header_size():
    packed, _ = make_test_header()
    assert len(packed) == 9

def test_packet_header_version():
    _, header = make_test_header()
    assert header.version == PROTOCOL_VERSION

def test_packet_header_type():
    _, header = make_test_header()
    assert header.packet_type == PacketType.HEARTBEAT

def test_packet_header_sequence():
    _, header = make_test_header()
    assert header.sequence == 42

def test_packet_header_length():
    _, header = make_test_header()
    assert header.length == 9

def test_packet_header_timestamp():
    _, header = make_test_header()
    assert header.timestamp == 123456

# Discovery Packet Tests

def test_discovery_packet_size():
    packed = SimplePacket.create(PacketType.DISCOVERY).pack()
    assert len(packed) == 9

def test_discovery_packet_type():
    packet = SimplePacket.unpack(SimplePacket.create(PacketType.DISCOVERY).pack())
    assert packet.header.packet_type == PacketType.DISCOVERY

def test_discovery_packet_length():
    packet = SimplePacket.unpack(SimplePacket.create(PacketType.DISCOVERY).pack())
    assert packet.header.length == 9

# Timesync Packet Tests


def test_timesync_packet_size():
    packed = SimplePacket.create(PacketType.TIMESYNC).pack()
    assert len(packed) == 9

def test_timesync_packet_type():
    packet = SimplePacket.unpack(SimplePacket.create(PacketType.TIMESYNC).pack())
    assert packet.header.packet_type == PacketType.TIMESYNC

def test_timesync_packet_length():
    packet = SimplePacket.unpack(SimplePacket.create(PacketType.TIMESYNC).pack())
    assert packet.header.length == 9

# Control Packet Tests

def make_control_packet(id: int = 0, state: ControlState = ControlState.OPEN) -> tuple[bytes, ControlPacket]:
    packet = ControlPacket.create(command_id=id, command_state=state)
    packed = packet.pack()
    return packed, ControlPacket.unpack(packed)

def test_control_packet_size():
    packed, _ = make_control_packet()
    assert len(packed) == 11

def test_control_packet_type():
    _, packet = make_control_packet()
    assert packet.header.packet_type == PacketType.CONTROL

@pytest.mark.parametrize("id", [0, 1, 42, 255])
def test_control_packet_command_id(id):
    _, packet = make_control_packet(id=id)
    assert packet.command_id == id

@pytest.mark.parametrize("state", [ControlState.OPEN, ControlState.CLOSED])
def test_control_packet_command_state(state):
    _, packet = make_control_packet(state=state)
    assert packet.command_state == state

# Data Packet Tests (Single Reading)

DATA_PACKET_SINGLE_TEST_CASES = [
    (0, 23.456, Unit.CELSIUS),
    (1, -10.5, Unit.CELSIUS),
    (255, 0.0, Unit.UNITLESS),
    (10, 12345.6789, Unit.PSI),
]

def make_data_packet_single(sensor_id: int = 0, data_value: float = 23.45, unit: Unit = Unit.CELSIUS) -> tuple[bytes, DataPacket]:
    packet = DataPacket.create(sensor_id=sensor_id, data=data_value, unit=unit)
    packed = packet.pack()
    return packed, DataPacket.unpack(packed)

@pytest.mark.parametrize(("sensor_id", "data_value", "unit"), DATA_PACKET_SINGLE_TEST_CASES)
def test_data_packet_single_size(sensor_id, data_value, unit):
    packed, _ = make_data_packet_single(sensor_id, data_value, unit)
    assert len(packed) == 16

@pytest.mark.parametrize(("sensor_id", "data_value", "unit"), DATA_PACKET_SINGLE_TEST_CASES)
def test_data_packet_single_type(sensor_id, data_value, unit):
    _, packet = make_data_packet_single(sensor_id, data_value, unit)
    assert packet.header.packet_type == PacketType.DATA

@pytest.mark.parametrize(("sensor_id", "data_value", "unit"), DATA_PACKET_SINGLE_TEST_CASES)
def test_data_packet_single_reading_count(sensor_id, data_value, unit):
    _, packet = make_data_packet_single(sensor_id, data_value, unit)
    assert len(packet.readings) == 1

@pytest.mark.parametrize(("sensor_id", "data_value", "unit"), DATA_PACKET_SINGLE_TEST_CASES)
def test_data_packet_single_sensor_id(sensor_id, data_value, unit):
    _, packet = make_data_packet_single(sensor_id, data_value, unit)
    assert packet.readings[0].sensor_id == sensor_id

@pytest.mark.parametrize(("sensor_id", "data_value", "unit"), DATA_PACKET_SINGLE_TEST_CASES)
def test_data_packet_single_unit(sensor_id, data_value, unit):
    _, packet = make_data_packet_single(sensor_id, data_value, unit)
    assert packet.readings[0].unit == unit

@pytest.mark.parametrize(("sensor_id", "data_value", "unit"), DATA_PACKET_SINGLE_TEST_CASES)
def test_data_packet_single_data_value(sensor_id, data_value, unit):
    _, packet = make_data_packet_single(sensor_id, data_value, unit)

    # Allow for small floating point errors in encoding/decoding
    relative_error = abs(packet.readings[0].value - data_value) / (abs(data_value) + 1e-10)
    assert relative_error < 0.0001

# Data Packet Tests (Batched Readings)

DATA_PACKET_BATCHED_TEST_READINGS = [
    SensorReading(sensor_id=0, unit=Unit.CELSIUS, value=23.5),
    SensorReading(sensor_id=1, unit=Unit.CELSIUS, value=25.0),
    SensorReading(sensor_id=2, unit=Unit.PSI, value=145.2),
]

def make_data_packet_batched(readings: list[SensorReading]) -> tuple[bytes, DataPacket]:
    packet = DataPacket.create(readings)
    packed = packet.pack()
    return packed, DataPacket.unpack(packed)

def test_data_packet_batched_size():
    packed, _ = make_data_packet_batched(DATA_PACKET_BATCHED_TEST_READINGS)
    expected_size = 9 + 1 + len(DATA_PACKET_BATCHED_TEST_READINGS) * 6
    assert len(packed) == expected_size

def test_data_packet_batched_type():
    _, packet = make_data_packet_batched(DATA_PACKET_BATCHED_TEST_READINGS)
    assert packet.header.packet_type == PacketType.DATA

def test_data_packet_batched_reading_count():
    _, packet = make_data_packet_batched(DATA_PACKET_BATCHED_TEST_READINGS)
    assert len(packet.readings) == len(DATA_PACKET_BATCHED_TEST_READINGS)

@pytest.mark.parametrize(("index", "expected"), enumerate(DATA_PACKET_BATCHED_TEST_READINGS))
def test_data_packet_batched_readings(index, expected):
    _, packet = make_data_packet_batched(DATA_PACKET_BATCHED_TEST_READINGS)

    assert packet.readings[index].sensor_id == expected.sensor_id
    assert packet.readings[index].unit == expected.unit

    # Allow for small floating point errors in encoding/decoding
    relative_error = abs(packet.readings[index].value - expected.value) / (abs(expected.value) + 1e-10)
    assert relative_error < 0.0001

# Status Packet Tests

STATUS_PACKET_TEST_CONTROL_STATES = [
    None,
    [ControlStatus(id=1, state=ControlState.OPEN)],
    [ControlStatus(id=1, state=ControlState.OPEN), ControlStatus(id=2, state=ControlState.CLOSED)],
]

# Test all combinations of DeviceStatus and test control states
STATUS_CONTROL_STATE_PAIRS = [(s, cs) for s in DeviceStatus for cs in STATUS_PACKET_TEST_CONTROL_STATES]

# Used to fold STATUS_CONTROL_STATE_PAIRS with an index and expected control state for parameterized tests
STATUS_CONTROL_STATE_PAIRS_EXPECTED = [
    (status, control_states, index, expected)
    for (status, control_states) in STATUS_CONTROL_STATE_PAIRS
    for index in range(len(control_states) if control_states else 0)
    for expected in (control_states[index] if control_states else None,)
]

def make_status_packet(status: DeviceStatus = DeviceStatus.ACTIVE, control_states: list[ControlStatus] | None = None) -> tuple[bytes, StatusPacket]:
    packet = StatusPacket.create(status=status, control_states=control_states)
    packed = packet.pack()
    return packed, StatusPacket.unpack(packed)

@pytest.mark.parametrize(("status", "control_states"), STATUS_CONTROL_STATE_PAIRS)
def test_status_packet_size(status, control_states):
    packed, _ = make_status_packet(status, control_states)
    num_controls = len(control_states) if control_states else 0
    expected_size = 11 + num_controls * 2
    assert len(packed) == expected_size

@pytest.mark.parametrize(("status", "control_states"), STATUS_CONTROL_STATE_PAIRS)
def test_status_packet_type(status, control_states):
    _, packet = make_status_packet(status, control_states)
    assert packet.header.packet_type == PacketType.STATUS

@pytest.mark.parametrize(("status", "control_states"), STATUS_CONTROL_STATE_PAIRS)
def test_status_packet_status_field(status, control_states):
    _, packet = make_status_packet(status, control_states)
    assert packet.status == status

@pytest.mark.parametrize(("status", "control_states", "index", "expected"), STATUS_CONTROL_STATE_PAIRS_EXPECTED)
def test_status_packet_control_id(status, control_states, index, expected):
    _, packet = make_status_packet(status, control_states)
    num_controls = len(control_states)

    assert len(packet.control_states) == num_controls
    assert packet.control_states[index].id == expected.id

@pytest.mark.parametrize(("status", "control_states", "index", "expected"), STATUS_CONTROL_STATE_PAIRS_EXPECTED)
def test_status_packet_control_state(status, control_states, index, expected):
    _, packet = make_status_packet(status, control_states)
    num_controls = len(control_states)

    assert len(packet.control_states) == num_controls
    assert packet.control_states[index].state == expected.state

# Config Packet Tests

CONFIG_PACKET_TEST_CONFIG = {
    "deviceName": "TestDevice",
    "deviceType": "Sensor Monitor",
    "sensorInfo": {
        "thermocouples": {
            "TC1": {"ADCIndex": 0, "highPin": 1, "lowPin": 2, "type": "K", "units": "C"}
        },
        "pressureTransducers": {
            "PT1": {"ADCIndex": 1, "pin": 3, "maxPressure_PSI": 500, "units": "PSI"}
        },
    },
    "controls": {
        "VALVE1": {"pin": 5, "type": "valve", "defaultState": "CLOSED"}
    },
}

def make_config_packet(config_dict: dict[str, Any]) -> tuple[bytes, ConfigPacket]:
    config_json = json.dumps(config_dict)
    packet = ConfigPacket.create(config_json=config_json)
    packed = packet.pack()
    return packed, ConfigPacket.unpack(packed)

def test_config_packet_size():
    packed, _ = make_config_packet(CONFIG_PACKET_TEST_CONFIG)
    expected_size = 9 + 4 + len(json.dumps(CONFIG_PACKET_TEST_CONFIG).encode('utf-8'))
    assert len(packed) == expected_size

def test_config_packet_type():
    _, packet = make_config_packet(CONFIG_PACKET_TEST_CONFIG)
    assert packet.header.packet_type == PacketType.CONFIG

def test_config_packet_json():
    _, packet = make_config_packet(CONFIG_PACKET_TEST_CONFIG)
    assert packet.config_json == json.dumps(CONFIG_PACKET_TEST_CONFIG)

def test_config_packet_json_parsing():
    _, packet = make_config_packet(CONFIG_PACKET_TEST_CONFIG)

    parsed = json.loads(packet.config_json)
    assert parsed["deviceName"] == "TestDevice"
    assert parsed["deviceType"] == "Sensor Monitor"

# Stream Start Packet Tests

def make_stream_start_packet(frequency_hz: int = 10) -> tuple[bytes, StreamStartPacket]:
    packet = StreamStartPacket.create(frequency_hz=frequency_hz)
    packed = packet.pack()
    return packed, StreamStartPacket.unpack(packed)

def test_stream_start_packet_size():
    packed, _ = make_stream_start_packet()
    assert len(packed) == 11

def test_stream_start_packet_type():
    _, packet = make_stream_start_packet()
    assert packet.header.packet_type == PacketType.STREAM_START

@pytest.mark.parametrize("frequency_hz", [1, 10, 100, 255, 1000, 65535])
def test_stream_start_packet_frequency(frequency_hz):
    _, packet = make_stream_start_packet(frequency_hz=frequency_hz)
    assert packet.frequency_hz == frequency_hz


# Simple Packet Tests (HEARTBEAT, STREAM_STOP, GET_SINGLE, etc.)

SIMPLE_PACKET_TYPES = [PacketType.ESTOP, PacketType.HEARTBEAT, PacketType.STREAM_STOP, PacketType.GET_SINGLE, PacketType.STATUS_REQUEST, PacketType.DISCOVERY]

def make_simple_packet(packet_type: PacketType) -> tuple[bytes, SimplePacket]:
    packet = SimplePacket.create(packet_type=packet_type)
    packed = packet.pack()
    return packed, SimplePacket.unpack(packed)

@pytest.mark.parametrize("packet_type", SIMPLE_PACKET_TYPES)
def test_simple_packet_size(packet_type):
    packed, _ = make_simple_packet(packet_type)
    assert len(packed) == 9

@pytest.mark.parametrize("packet_type", SIMPLE_PACKET_TYPES)
def test_simple_packet_type(packet_type):
    _, packet = make_simple_packet(packet_type)
    assert packet.header.packet_type == packet_type

@pytest.mark.parametrize("packet_type", SIMPLE_PACKET_TYPES)
def test_simple_packet_length(packet_type):
    _, packet = make_simple_packet(packet_type)
    assert packet.header.length == 9

# ACK Packet Tests

def make_ack_packet(ack_packet_type: PacketType = PacketType.CONTROL, ack_sequence: int = 0) -> tuple[bytes, AckPacket]:
    packet = AckPacket.create(ack_packet_type=ack_packet_type, ack_sequence=ack_sequence)
    packed = packet.pack()
    return packed, AckPacket.unpack(packed)

def test_ack_packet_size():
    packed, _ = make_ack_packet()
    assert len(packed) == 12

def test_ack_packet_type():
    _, packet = make_ack_packet()
    assert packet.header.packet_type == PacketType.ACK

@pytest.mark.parametrize("ack_packet_type", [PacketType.CONTROL, PacketType.STREAM_START, PacketType.HEARTBEAT])
def test_ack_packet_ack_type(ack_packet_type):
    _, packet = make_ack_packet(ack_packet_type=ack_packet_type)
    assert packet.ack_packet_type == ack_packet_type

@pytest.mark.parametrize("ack_sequence", [0, 1, 42, 255])
def test_ack_packet_ack_sequence(ack_sequence):
    _, packet = make_ack_packet(ack_sequence=ack_sequence)
    assert packet.ack_sequence == ack_sequence

def test_ack_packet_error_code():
    _, packet = make_ack_packet()
    assert packet.error_code == ErrorCode.NONE # Error codes are currently always NONE for ACK packets

def make_nack_packet(nack_packet_type: PacketType = PacketType.CONTROL, nack_sequence: int = 0, error_code: ErrorCode = ErrorCode.BUSY) -> tuple[bytes, NackPacket]:
    packet = NackPacket.create(nack_packet_type=nack_packet_type, nack_sequence=nack_sequence, error_code=error_code)
    packed = packet.pack()
    return packed, NackPacket.unpack(packed)

def test_nack_packet_size():
    packed, _ = make_nack_packet()
    assert len(packed) == 12

def test_nack_packet_type():
    _, packet = make_nack_packet()
    assert packet.header.packet_type == PacketType.NACK

@pytest.mark.parametrize("nack_packet_type", [PacketType.CONTROL, PacketType.STREAM_START, PacketType.HEARTBEAT])
def test_nack_packet_nack_type(nack_packet_type):
    _, packet = make_nack_packet(nack_packet_type=nack_packet_type)
    assert packet.nack_packet_type == nack_packet_type

@pytest.mark.parametrize("nack_sequence", [0, 1, 42, 255])
def test_nack_packet_nack_sequence(nack_sequence):
    _, packet = make_nack_packet(nack_sequence=nack_sequence)
    assert packet.nack_sequence == nack_sequence

@pytest.mark.parametrize("error_code", list(ErrorCode))
def test_nack_packet_error_code(error_code):
    _, packet = make_nack_packet(error_code=error_code)
    assert packet.error_code == error_code

# Multiple Packet Tests

def test_sequence_numbers():
    p1 = SimplePacket.create(PacketType.HEARTBEAT)
    p2 = SimplePacket.create(PacketType.HEARTBEAT)
    p3 = SimplePacket.create(PacketType.HEARTBEAT)

    seq1 = p1.header.sequence
    seq2 = p2.header.sequence
    seq3 = p3.header.sequence

    assert seq2 == (seq1 + 1) & 0xFF
    assert seq3 == (seq1 + 2) & 0xFF

@pytest.mark.parametrize(("packet", "expected_type"), [
    (SimplePacket.create(PacketType.DISCOVERY), SimplePacket),
    (SimplePacket.create(PacketType.TIMESYNC), SimplePacket),
    (ControlPacket.create(0, ControlState.OPEN), ControlPacket),
    (DataPacket.create(sensor_id=0, data=23.45, unit=Unit.CELSIUS), DataPacket),
    (StatusPacket.create(DeviceStatus.ACTIVE), StatusPacket),
    (StreamStartPacket.create(10), StreamStartPacket),
    (SimplePacket.create(PacketType.HEARTBEAT), SimplePacket),
    (AckPacket.create(PacketType.CONTROL, 1), AckPacket),
    (NackPacket.create(PacketType.CONTROL, 0, ErrorCode.INVALID_ID), NackPacket),
])
def test_decode_packet(packet, expected_type):
    packed = packet.pack()
    decoded = decode_packet(packed)
    assert isinstance(decoded, expected_type)
    assert decoded.header.packet_type == packet.header.packet_type

def test_length_framing():
    # Simulate multiple packets concatenated in a TCP stream
    p1 = SimplePacket.create(PacketType.HEARTBEAT)
    p2 = DataPacket.create(sensor_id=0, data=23.5, unit=Unit.CELSIUS)
    p3 = AckPacket.create(PacketType.HEARTBEAT, 1)

    stream = p1.pack() + p2.pack() + p3.pack()

    # Parse using LENGTH field
    offset = 0
    decoded_packets = []
    while offset < len(stream):
        if len(stream) - offset < PacketHeader.SIZE:
            break
        header = PacketHeader.unpack(stream[offset:])
        if len(stream) - offset < header.length:
            break
        pkt = decode_packet(stream[offset:offset + header.length])
        decoded_packets.append(pkt)
        offset += header.length

    assert len(decoded_packets) == 3
    assert decoded_packets[0].header.packet_type == PacketType.HEARTBEAT
    assert decoded_packets[1].header.packet_type == PacketType.DATA
    assert decoded_packets[2].header.packet_type == PacketType.ACK

# Error Handling Tests

def test_error_invalid_packet_size():
    with pytest.raises(ValueError, match="too small"):
        decode_packet(b"short")

def test_error_incomplete_packet():
    header = PacketHeader(
        version=PROTOCOL_VERSION,
        packet_type=PacketType.TIMESYNC,
        sequence=0,
        length=17,
        timestamp=0,
    )
    with pytest.raises(ValueError, match="Incomplete"):
        decode_packet(header.pack())

def test_error_unknown_packet_type():
    bad_header = PacketHeader(
        version=PROTOCOL_VERSION,
        packet_type=255,
        sequence=0,
        length=9,
        timestamp=0,
    )
    with pytest.raises(ValueError, match="is not a valid PacketType"):
        decode_packet(bad_header.pack())
