"""Comprehensive test suite for the binary protocol v2 implementation.

Run with: python test_protocol.py
"""

import json
import time
from typing import Any

from libqretprop.protocol import (
    PROTOCOL_VERSION,
    AckPacket,
    ConfigPacket,
    ControlPacket,
    ControlState,
    DataPacket,
    decode_packet,
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
)


# ANSI color codes for pretty output
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def print_test(name: str) -> None:
    print(f"\n{Colors.BLUE}{Colors.BOLD}Testing: {name}{Colors.RESET}")


def print_pass(message: str = "PASS") -> None:
    print(f"{Colors.GREEN}{message}{Colors.RESET}")


def print_fail(message: str = "FAIL") -> None:
    print(f"{Colors.RED}{message}{Colors.RESET}")


def print_info(message: str) -> None:
    print(f"  {message}")


def assert_equal(actual: Any, expected: Any, description: str) -> bool:
    if actual == expected:
        print_pass(f"  PASS {description}")
        return True
    else:
        print_fail(f"  FAIL {description}")
        print_info(f"    Expected: {expected}")
        print_info(f"    Got: {actual}")
        return False


def test_packet_header() -> bool:
    """Test PacketHeader encoding and decoding."""
    print_test("PacketHeader")

    header = PacketHeader(
        version=PROTOCOL_VERSION,
        packet_type=PacketType.HEARTBEAT,
        sequence=42,
        length=9,
        timestamp=123456,
    )

    packed = header.pack()
    unpacked = PacketHeader.unpack(packed)

    passed = True
    passed &= assert_equal(len(packed), 9, "Header size is 9 bytes")
    passed &= assert_equal(unpacked.version, PROTOCOL_VERSION, "Version preserved")
    passed &= assert_equal(unpacked.packet_type, PacketType.HEARTBEAT, "Packet type preserved")
    passed &= assert_equal(unpacked.sequence, 42, "Sequence preserved")
    passed &= assert_equal(unpacked.length, 9, "Length preserved")
    passed &= assert_equal(unpacked.timestamp, 123456, "Timestamp preserved")

    return passed


def test_discovery_packet() -> bool:
    """Test DiscoveryPacket (SimplePacket) encoding and decoding."""
    print_test("DiscoveryPacket")

    packet = SimplePacket.create(PacketType.DISCOVERY)

    packed = packet.pack()
    unpacked = SimplePacket.unpack(packed)

    passed = True
    passed &= assert_equal(len(packed), 9, "Packet size is 9 bytes")
    passed &= assert_equal(unpacked.header.packet_type, PacketType.DISCOVERY, "Packet type correct")
    passed &= assert_equal(unpacked.header.length, 9, "Length field correct")

    return passed


def test_timesync_packet() -> bool:
    """Test TIMESYNC as a header-only packet."""
    print_test("TIMESYNC (header-only)")

    packet = SimplePacket.create(PacketType.TIMESYNC)

    packed = packet.pack()
    unpacked = SimplePacket.unpack(packed)

    passed = True
    passed &= assert_equal(len(packed), 9, "Packet size is 9 bytes")
    passed &= assert_equal(unpacked.header.packet_type, PacketType.TIMESYNC, "Packet type correct")

    return passed


def test_control_packet() -> bool:
    """Test ControlPacket encoding and decoding."""
    print_test("ControlPacket")

    packet = ControlPacket.create(command_id=0, command_state=ControlState.OPEN)
    packed = packet.pack()
    unpacked = ControlPacket.unpack(packed)

    passed = True
    passed &= assert_equal(len(packed), 11, "Packet size is 11 bytes")
    passed &= assert_equal(unpacked.header.packet_type, PacketType.CONTROL, "Packet type correct")
    passed &= assert_equal(unpacked.command_id, 0, "Command ID preserved")
    passed &= assert_equal(unpacked.command_state, ControlState.OPEN, "Command state preserved")

    packet2 = ControlPacket.create(command_id=5, command_state=ControlState.CLOSED)
    packed2 = packet2.pack()
    unpacked2 = ControlPacket.unpack(packed2)

    passed &= assert_equal(unpacked2.command_id, 5, "Command ID 5 preserved")
    passed &= assert_equal(unpacked2.command_state, ControlState.CLOSED, "CLOSED state preserved")

    return passed


def test_data_packet_single() -> bool:
    """Test DataPacket with a single reading (backwards compat convenience)."""
    print_test("DataPacket (single reading)")

    test_cases = [
        (0, 23.456, Unit.CELSIUS, "Sensor 0, positive float"),
        (1, -10.5, Unit.CELSIUS, "Sensor 1, negative float"),
        (255, 0.0, Unit.UNITLESS, "Sensor 255, zero"),
        (10, 12345.6789, Unit.PSI, "Sensor 10, large value"),
    ]

    passed = True
    for sensor_id, data_value, unit, description in test_cases:
        packet = DataPacket.create(sensor_id=sensor_id, data=data_value, unit=unit)
        packed = packet.pack()
        unpacked = DataPacket.unpack(packed)

        # Single reading: 9 header + 1 count + 6 reading = 16
        passed &= assert_equal(len(packed), 16, f"{description}: size is 16 bytes")
        passed &= assert_equal(unpacked.header.packet_type, PacketType.DATA, f"{description}: type correct")
        passed &= assert_equal(len(unpacked.readings), 1, f"{description}: 1 reading")
        passed &= assert_equal(unpacked.readings[0].sensor_id, sensor_id, f"{description}: sensor ID preserved")
        passed &= assert_equal(unpacked.readings[0].unit, unit, f"{description}: unit preserved")

        relative_error = abs(unpacked.readings[0].value - data_value) / (abs(data_value) + 1e-10)
        if relative_error < 0.0001:
            print_pass(f"  PASS {description}: data value preserved ({data_value})")
        else:
            print_fail(f"  FAIL {description}: data value mismatch")
            print_info(f"    Expected: {data_value}")
            print_info(f"    Got: {unpacked.readings[0].value}")
            passed = False

    return passed


def test_data_packet_batched() -> bool:
    """Test DataPacket with batched readings."""
    print_test("DataPacket (batched)")

    readings = [
        SensorReading(sensor_id=0, unit=Unit.CELSIUS, value=23.5),
        SensorReading(sensor_id=1, unit=Unit.CELSIUS, value=25.0),
        SensorReading(sensor_id=2, unit=Unit.PSI, value=145.2),
    ]

    packet = DataPacket.create(readings)
    packed = packet.pack()
    unpacked = DataPacket.unpack(packed)

    passed = True
    expected_size = 9 + 1 + 3 * 6  # header + count + 3 readings
    passed &= assert_equal(len(packed), expected_size, f"Batched size is {expected_size} bytes (3 readings)")
    passed &= assert_equal(unpacked.header.length, expected_size, "Length field matches")
    passed &= assert_equal(len(unpacked.readings), 3, "3 readings decoded")

    for i, (orig, decoded) in enumerate(zip(readings, unpacked.readings)):
        passed &= assert_equal(decoded.sensor_id, orig.sensor_id, f"Reading {i}: sensor_id")
        passed &= assert_equal(decoded.unit, orig.unit, f"Reading {i}: unit")
        if abs(decoded.value - orig.value) < 0.01:
            print_pass(f"  PASS Reading {i}: value ({orig.value})")
        else:
            print_fail(f"  FAIL Reading {i}: value")
            passed = False

    return passed


def test_status_packet() -> bool:
    """Test StatusPacket encoding and decoding."""
    print_test("StatusPacket")

    passed = True

    for status in [DeviceStatus.INACTIVE, DeviceStatus.ACTIVE, DeviceStatus.ERROR, DeviceStatus.CALIBRATING]:
        packet = StatusPacket.create(status=status)
        packed = packet.pack()
        unpacked = StatusPacket.unpack(packed)

        passed &= assert_equal(len(packed), 10, f"Status {status.name}: size is 10 bytes")
        passed &= assert_equal(unpacked.header.packet_type, PacketType.STATUS, f"Status {status.name}: type correct")
        passed &= assert_equal(unpacked.status, status, f"Status {status.name}: status preserved")

    return passed


def test_config_packet() -> bool:
    """Test ConfigPacket encoding and decoding."""
    print_test("ConfigPacket")

    config = {
        "deviceName": "TestDevice",
        "deviceType": "Sensor Monitor",
        "sensorInfo": {
            "thermocouples": {
                "TC1": {"ADCIndex": 0, "highPin": 1, "lowPin": 2, "type": "K", "units": "C"}
            },
            "pressureTransducers": {
                "PT1": {"ADCIndex": 1, "pin": 3, "maxPressure_PSI": 500, "units": "PSI"}
            }
        },
        "controls": {
            "VALVE1": {"pin": 5, "type": "valve", "defaultState": "CLOSED"}
        }
    }

    config_json = json.dumps(config)
    packet = ConfigPacket.create(config_json=config_json)

    packed = packet.pack()
    unpacked = ConfigPacket.unpack(packed)

    passed = True
    expected_size = 9 + 4 + len(config_json.encode('utf-8'))
    passed &= assert_equal(len(packed), expected_size, f"Packet size is {expected_size} bytes")
    passed &= assert_equal(unpacked.header.packet_type, PacketType.CONFIG, "Packet type correct")
    passed &= assert_equal(unpacked.config_json, config_json, "JSON config preserved")

    try:
        parsed = json.loads(unpacked.config_json)
        passed &= assert_equal(parsed["deviceName"], "TestDevice", "JSON deviceName preserved")
        passed &= assert_equal(parsed["deviceType"], "Sensor Monitor", "JSON deviceType preserved")
    except json.JSONDecodeError:
        print_fail("  FAIL JSON parsing failed")
        passed = False

    return passed


def test_stream_start_packet() -> bool:
    """Test StreamStartPacket encoding and decoding."""
    print_test("StreamStartPacket")

    # uint16 now supports up to 65535 Hz
    test_frequencies = [1, 10, 100, 255, 1000, 65535]
    passed = True

    for freq in test_frequencies:
        packet = StreamStartPacket.create(frequency_hz=freq)
        packed = packet.pack()
        unpacked = StreamStartPacket.unpack(packed)

        passed &= assert_equal(len(packed), 11, f"Frequency {freq} Hz: size is 11 bytes")
        passed &= assert_equal(unpacked.header.packet_type, PacketType.STREAM_START, f"Frequency {freq} Hz: type correct")
        passed &= assert_equal(unpacked.frequency_hz, freq, f"Frequency {freq} Hz: frequency preserved")

    return passed


def test_simple_packets() -> bool:
    """Test SimplePacket for various types."""
    print_test("SimplePacket (HEARTBEAT, STREAM_STOP, GET_SINGLE, etc.)")

    passed = True

    simple_types = [
        PacketType.ESTOP,
        PacketType.HEARTBEAT,
        PacketType.STREAM_STOP,
        PacketType.GET_SINGLE,
        PacketType.STATUS_REQUEST,
        PacketType.DISCOVERY,
    ]

    for ptype in simple_types:
        packet = SimplePacket.create(packet_type=ptype)
        packed = packet.pack()
        unpacked = SimplePacket.unpack(packed)

        passed &= assert_equal(len(packed), 9, f"{ptype.name}: size is 9 bytes")
        passed &= assert_equal(unpacked.header.packet_type, ptype, f"{ptype.name}: type preserved")

    return passed


def test_ack_packet() -> bool:
    """Test ACK packet with sequence and error code."""
    print_test("AckPacket")

    passed = True

    ack = AckPacket.create(ack_packet_type=PacketType.CONTROL, ack_sequence=42)
    ack_packed = ack.pack()
    ack_unpacked = AckPacket.unpack(ack_packed)

    passed &= assert_equal(len(ack_packed), 12, "ACK: size is 12 bytes")
    passed &= assert_equal(ack_unpacked.header.packet_type, PacketType.ACK, "ACK: type is ACK")
    passed &= assert_equal(ack_unpacked.ack_packet_type, PacketType.CONTROL, "ACK: acknowledging CONTROL")
    passed &= assert_equal(ack_unpacked.ack_sequence, 42, "ACK: sequence is 42")
    passed &= assert_equal(ack_unpacked.error_code, ErrorCode.NONE, "ACK: error_code is NONE")

    return passed


def test_nack_packet() -> bool:
    """Test NACK packet with error codes."""
    print_test("NackPacket")

    passed = True

    nack = NackPacket.create(
        nack_packet_type=PacketType.STREAM_START,
        nack_sequence=7,
        error_code=ErrorCode.BUSY,
    )
    nack_packed = nack.pack()
    nack_unpacked = NackPacket.unpack(nack_packed)

    passed &= assert_equal(len(nack_packed), 12, "NACK: size is 12 bytes")
    passed &= assert_equal(nack_unpacked.header.packet_type, PacketType.NACK, "NACK: type is NACK")
    passed &= assert_equal(nack_unpacked.nack_packet_type, PacketType.STREAM_START, "NACK: nacking STREAM_START")
    passed &= assert_equal(nack_unpacked.nack_sequence, 7, "NACK: sequence is 7")
    passed &= assert_equal(nack_unpacked.error_code, ErrorCode.BUSY, "NACK: error_code is BUSY")

    # Test all error codes
    for ec in ErrorCode:
        n = NackPacket.create(PacketType.CONTROL, 0, ec)
        n2 = NackPacket.unpack(n.pack())
        passed &= assert_equal(n2.error_code, ec, f"ErrorCode.{ec.name} roundtrip")

    return passed


def test_sequence_numbers() -> bool:
    """Test that sequence numbers auto-increment."""
    print_test("Sequence Numbers")

    passed = True

    p1 = SimplePacket.create(PacketType.HEARTBEAT)
    p2 = SimplePacket.create(PacketType.HEARTBEAT)
    p3 = SimplePacket.create(PacketType.HEARTBEAT)

    seq1 = p1.header.sequence
    seq2 = p2.header.sequence
    seq3 = p3.header.sequence

    passed &= assert_equal(seq2, (seq1 + 1) & 0xFF, "Sequence increments by 1")
    passed &= assert_equal(seq3, (seq1 + 2) & 0xFF, "Sequence increments by 2")

    return passed


def test_decode_packet() -> bool:
    """Test the generic decode_packet function."""
    print_test("decode_packet() function")

    passed = True

    packets = [
        (SimplePacket.create(PacketType.DISCOVERY), SimplePacket, "Discovery"),
        (SimplePacket.create(PacketType.TIMESYNC), SimplePacket, "TIMESYNC"),
        (ControlPacket.create(0, ControlState.OPEN), ControlPacket, "ControlPacket"),
        (DataPacket.create(sensor_id=0, data=23.45), DataPacket, "DataPacket"),
        (StatusPacket.create(DeviceStatus.ACTIVE), StatusPacket, "StatusPacket"),
        (StreamStartPacket.create(10), StreamStartPacket, "StreamStartPacket"),
        (SimplePacket.create(PacketType.HEARTBEAT), SimplePacket, "SimplePacket"),
        (AckPacket.create(PacketType.CONTROL), AckPacket, "AckPacket"),
        (NackPacket.create(PacketType.CONTROL, 0, ErrorCode.INVALID_ID), NackPacket, "NackPacket"),
    ]

    for packet, expected_type, name in packets:
        packed = packet.pack()
        decoded = decode_packet(packed)

        if isinstance(decoded, expected_type):
            print_pass(f"  PASS {name}: correctly decoded as {expected_type.__name__}")
        else:
            print_fail(f"  FAIL {name}: expected {expected_type.__name__}, got {type(decoded).__name__}")
            passed = False

    return passed


def test_length_framing() -> bool:
    """Test that LENGTH field enables correct TCP framing."""
    print_test("LENGTH-based TCP framing")

    passed = True

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

    passed &= assert_equal(len(decoded_packets), 3, "Decoded 3 packets from stream")
    passed &= assert_equal(decoded_packets[0].header.packet_type, PacketType.HEARTBEAT, "First: HEARTBEAT")
    passed &= assert_equal(decoded_packets[1].header.packet_type, PacketType.DATA, "Second: DATA")
    passed &= assert_equal(decoded_packets[2].header.packet_type, PacketType.ACK, "Third: ACK")

    return passed


def test_error_handling() -> bool:
    """Test error handling for invalid packets."""
    print_test("Error Handling")

    passed = True

    # Test 1: Packet too small
    try:
        decode_packet(b"short")
        print_fail("  FAIL Should reject packets smaller than header")
        passed = False
    except ValueError as e:
        if "too small" in str(e).lower():
            print_pass("  PASS Rejects packets smaller than header")
        else:
            print_fail(f"  FAIL Wrong error message: {e}")
            passed = False

    # Test 2: Incomplete packet (length says 17, only 9 bytes present)
    header = PacketHeader(
        version=PROTOCOL_VERSION,
        packet_type=PacketType.TIMESYNC,
        sequence=0,
        length=17,
        timestamp=0,
    )
    try:
        decode_packet(header.pack())
        print_fail("  FAIL Should reject incomplete packets")
        passed = False
    except ValueError as e:
        if "incomplete" in str(e).lower() or "need" in str(e).lower():
            print_pass("  PASS Rejects incomplete packets")
        else:
            print_fail(f"  FAIL Wrong error message: {e}")
            passed = False

    # Test 3: Unknown packet type
    bad_header = PacketHeader(
        version=PROTOCOL_VERSION,
        packet_type=255,
        sequence=0,
        length=9,
        timestamp=0,
    )
    try:
        decode_packet(bad_header.pack())
        print_fail("  FAIL Should reject unknown packet type")
        passed = False
    except (ValueError, KeyError):
        print_pass("  PASS Rejects unknown packet type")

    return passed


def test_roundtrip_all_packets() -> bool:
    """Test full encode/decode roundtrip for all packet types."""
    print_test("Full Roundtrip (Encode -> Decode) All Packets")

    passed = True

    # Discovery
    p1 = SimplePacket.create(PacketType.DISCOVERY)
    passed &= assert_equal(
        decode_packet(p1.pack()).header.packet_type,
        PacketType.DISCOVERY,
        "Discovery roundtrip"
    )

    # TimeSync
    p2 = SimplePacket.create(PacketType.TIMESYNC)
    decoded_p2 = decode_packet(p2.pack())
    passed &= assert_equal(decoded_p2.header.packet_type, PacketType.TIMESYNC, "TimeSync roundtrip")

    # Control
    p3 = ControlPacket.create(7, ControlState.OPEN)
    decoded_p3 = decode_packet(p3.pack())
    passed &= assert_equal(decoded_p3.command_id, 7, "Control roundtrip: command_id")
    passed &= assert_equal(decoded_p3.command_state, ControlState.OPEN, "Control roundtrip: state")

    # Data (single)
    p4 = DataPacket.create(sensor_id=3, data=99.99, unit=Unit.VOLTS)
    decoded_p4 = decode_packet(p4.pack())
    passed &= assert_equal(decoded_p4.readings[0].sensor_id, 3, "Data roundtrip: sensor_id")
    passed &= assert_equal(decoded_p4.readings[0].unit, Unit.VOLTS, "Data roundtrip: unit")
    if abs(decoded_p4.readings[0].value - 99.99) < 0.01:
        print_pass("  PASS Data roundtrip: data value")
    else:
        print_fail("  FAIL Data roundtrip: data value")
        passed = False

    # Data (batched)
    readings = [
        SensorReading(0, Unit.CELSIUS, 20.0),
        SensorReading(1, Unit.PSI, 100.0),
    ]
    p4b = DataPacket.create(readings)
    decoded_p4b = decode_packet(p4b.pack())
    passed &= assert_equal(len(decoded_p4b.readings), 2, "Batched data roundtrip: 2 readings")

    # Status
    p5 = StatusPacket.create(DeviceStatus.CALIBRATING)
    decoded_p5 = decode_packet(p5.pack())
    passed &= assert_equal(decoded_p5.status, DeviceStatus.CALIBRATING, "Status roundtrip")

    # Config
    p6 = ConfigPacket.create('{"test": "value"}')
    decoded_p6 = decode_packet(p6.pack())
    passed &= assert_equal(decoded_p6.config_json, '{"test": "value"}', "Config roundtrip")

    # StreamStart
    p7 = StreamStartPacket.create(50)
    decoded_p7 = decode_packet(p7.pack())
    passed &= assert_equal(decoded_p7.frequency_hz, 50, "StreamStart roundtrip")

    # StreamStart high freq (uint16)
    p7b = StreamStartPacket.create(10000)
    decoded_p7b = decode_packet(p7b.pack())
    passed &= assert_equal(decoded_p7b.frequency_hz, 10000, "StreamStart 10000 Hz roundtrip")

    # Simple packets
    p8 = SimplePacket.create(PacketType.HEARTBEAT)
    passed &= assert_equal(
        decode_packet(p8.pack()).header.packet_type,
        PacketType.HEARTBEAT,
        "SimplePacket (HEARTBEAT) roundtrip"
    )

    # ACK
    p9 = AckPacket.create(PacketType.CONTROL, ack_sequence=99)
    decoded_p9 = decode_packet(p9.pack())
    passed &= assert_equal(decoded_p9.header.packet_type, PacketType.ACK, "ACK roundtrip: type")
    passed &= assert_equal(decoded_p9.ack_packet_type, PacketType.CONTROL, "ACK roundtrip: ack_type")
    passed &= assert_equal(decoded_p9.ack_sequence, 99, "ACK roundtrip: sequence")

    # NACK
    p10 = NackPacket.create(PacketType.CONTROL, 55, ErrorCode.HARDWARE_FAULT)
    decoded_p10 = decode_packet(p10.pack())
    passed &= assert_equal(decoded_p10.header.packet_type, PacketType.NACK, "NACK roundtrip: type")
    passed &= assert_equal(decoded_p10.nack_packet_type, PacketType.CONTROL, "NACK roundtrip: nack_type")
    passed &= assert_equal(decoded_p10.nack_sequence, 55, "NACK roundtrip: sequence")
    passed &= assert_equal(decoded_p10.error_code, ErrorCode.HARDWARE_FAULT, "NACK roundtrip: error")

    return passed


def test_realistic_scenario() -> bool:
    """Test a realistic communication scenario."""
    print_test("Realistic Communication Scenario")

    passed = True
    print_info("Simulating device connection and data streaming...")

    # 1. Server discovers device
    discovery = SimplePacket.create(PacketType.DISCOVERY)
    print_info(f"1. Server sends DISCOVERY ({len(discovery.pack())} bytes)")

    # 2. Device sends config
    config = {
        "deviceName": "PropMonitor1",
        "deviceType": "Sensor Monitor",
        "sensorInfo": {
            "thermocouples": {"TC1": {"ADCIndex": 0, "highPin": 1, "lowPin": 2, "type": "K", "units": "C"}},
            "pressureTransducers": {"PT1": {"ADCIndex": 1, "pin": 3, "maxPressure_PSI": 500, "units": "PSI"}},
        },
        "controls": {
            "AVFILL": {"pin": 5, "type": "valve", "defaultState": "CLOSED"}
        }
    }
    config_packet = ConfigPacket.create(json.dumps(config))
    print_info(f"2. Device sends CONFIG ({len(config_packet.pack())} bytes)")

    # 3. Server acknowledges
    ack = AckPacket.create(PacketType.CONFIG, config_packet.header.sequence)
    print_info(f"3. Server sends ACK ({len(ack.pack())} bytes)")

    # 4. Server syncs time
    timesync = SimplePacket.create(PacketType.TIMESYNC)
    print_info(f"4. Server sends TIMESYNC ({len(timesync.pack())} bytes)")

    # 5. Device acknowledges
    ack2 = AckPacket.create(PacketType.TIMESYNC, timesync.header.sequence)
    print_info(f"5. Device sends ACK ({len(ack2.pack())} bytes)")

    # 6. Server requests streaming at 10 Hz
    stream_start = StreamStartPacket.create(10)
    print_info(f"6. Server sends STREAM_START at 10 Hz ({len(stream_start.pack())} bytes)")

    # 7. Device sends batched data (TC1 + PT1 in one packet)
    readings = [
        SensorReading(sensor_id=0, unit=Unit.CELSIUS, value=23.5),
        SensorReading(sensor_id=1, unit=Unit.PSI, value=145.2),
    ]
    data_batch = DataPacket.create(readings)
    print_info(f"7. Device sends batched DATA ({len(data_batch.pack())} bytes): TC1=23.5C, PT1=145.2PSI")

    # 8. Server opens valve
    control = ControlPacket.create(command_id=0, command_state=ControlState.OPEN)
    print_info(f"8. Server sends CONTROL to open AVFILL ({len(control.pack())} bytes)")

    # 9. Device acknowledges
    ack3 = AckPacket.create(PacketType.CONTROL, control.header.sequence)
    print_info(f"9. Device sends ACK ({len(ack3.pack())} bytes)")

    # 10. Server stops streaming
    stream_stop = SimplePacket.create(PacketType.STREAM_STOP)
    print_info(f"10. Server sends STREAM_STOP ({len(stream_stop.pack())} bytes)")

    total_bytes = sum([
        len(p.pack()) for p in [
            discovery, config_packet, ack, timesync, ack2,
            stream_start, data_batch, control, ack3, stream_stop
        ]
    ])
    print_info(f"\nTotal data exchanged: {total_bytes} bytes")

    # Verify all packets can be decoded
    all_packets = [
        discovery, config_packet, ack, timesync, ack2,
        stream_start, data_batch, control, ack3, stream_stop
    ]

    for i, packet in enumerate(all_packets, 1):
        try:
            decode_packet(packet.pack())
        except Exception as e:
            print_fail(f"  FAIL Failed to decode packet {i}: {e}")
            passed = False
            return passed

    print_pass("  PASS All packets in scenario encoded and decoded successfully")

    return passed


def run_all_tests() -> None:
    """Run all tests and print summary."""
    print(f"\n{Colors.BOLD}{'='*70}")
    print(f"  QRET Propulsion Binary Protocol v2 Test Suite")
    print(f"{'='*70}{Colors.RESET}\n")

    tests = [
        ("PacketHeader", test_packet_header),
        ("DiscoveryPacket", test_discovery_packet),
        ("TimeSyncPacket", test_timesync_packet),
        ("ControlPacket", test_control_packet),
        ("DataPacket (single)", test_data_packet_single),
        ("DataPacket (batched)", test_data_packet_batched),
        ("StatusPacket", test_status_packet),
        ("ConfigPacket", test_config_packet),
        ("StreamStartPacket", test_stream_start_packet),
        ("SimplePacket", test_simple_packets),
        ("AckPacket", test_ack_packet),
        ("NackPacket", test_nack_packet),
        ("Sequence Numbers", test_sequence_numbers),
        ("decode_packet()", test_decode_packet),
        ("LENGTH framing", test_length_framing),
        ("Error Handling", test_error_handling),
        ("Full Roundtrip", test_roundtrip_all_packets),
        ("Realistic Scenario", test_realistic_scenario),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print_fail(f"\nFAIL {name} CRASHED: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # Print summary
    print(f"\n{Colors.BOLD}{'='*70}")
    print("  Test Summary")
    print(f"{'='*70}{Colors.RESET}\n")

    passed_count = sum(1 for _, result in results if result)
    total_count = len(results)

    for name, result in results:
        status = f"{Colors.GREEN}PASS{Colors.RESET}" if result else f"{Colors.RED}FAIL{Colors.RESET}"
        print(f"  {status}  {name}")

    print(f"\n{Colors.BOLD}{'='*70}{Colors.RESET}")

    if passed_count == total_count:
        print(f"{Colors.GREEN}{Colors.BOLD}  ALL TESTS PASSED! ({passed_count}/{total_count}){Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}{Colors.BOLD}  SOME TESTS FAILED ({passed_count}/{total_count} passed){Colors.RESET}")

    print(f"{Colors.BOLD}{'='*70}{Colors.RESET}\n")

    return passed_count == total_count


if __name__ == "__main__":
    import sys
    success = run_all_tests()
    sys.exit(0 if success else 1)
