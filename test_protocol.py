"""Comprehensive test suite for the binary protocol implementation.

This test file validates all packet types, encoding/decoding, and error handling.
Run with: python test_protocol.py
"""

import json
import time
from typing import Any

from libqretprop.protocol import (
    MAGIC_NUMBER,
    AckPacket,
    ConfigPacket,
    ControlPacket,
    ControlState,
    DataPacket,
    decode_packet,
    DeviceStatus,
    DiscoveryPacket,
    get_packet_size,
    PacketHeader,
    PacketType,
    SimplePacket,
    StatusPacket,
    StreamStartPacket,
    TimeSyncPacket,
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
    """Print test name."""
    print(f"\n{Colors.BLUE}{Colors.BOLD}Testing: {name}{Colors.RESET}")


def print_pass(message: str = "✓ PASS") -> None:
    """Print pass message."""
    print(f"{Colors.GREEN}{message}{Colors.RESET}")


def print_fail(message: str = "✗ FAIL") -> None:
    """Print fail message."""
    print(f"{Colors.RED}{message}{Colors.RESET}")


def print_info(message: str) -> None:
    """Print info message."""
    print(f"  {message}")


def assert_equal(actual: Any, expected: Any, description: str) -> bool:
    """Assert two values are equal."""
    if actual == expected:
        print_pass(f"  ✓ {description}")
        return True
    else:
        print_fail(f"  ✗ {description}")
        print_info(f"    Expected: {expected}")
        print_info(f"    Got: {actual}")
        return False


def test_packet_header() -> bool:
    """Test PacketHeader encoding and decoding."""
    print_test("PacketHeader")

    # Create a header
    header = PacketHeader(
        magic=MAGIC_NUMBER,
        packet_type=PacketType.HEARTBEAT,
        timestamp=123456
    )

    # Pack and unpack
    packed = header.pack()
    unpacked = PacketHeader.unpack(packed)

    # Validate
    passed = True
    passed &= assert_equal(len(packed), PacketHeader.SIZE, f"Header size is {PacketHeader.SIZE} bytes")
    passed &= assert_equal(unpacked.magic, MAGIC_NUMBER, "Magic number preserved")
    passed &= assert_equal(unpacked.packet_type, PacketType.HEARTBEAT, "Packet type preserved")
    passed &= assert_equal(unpacked.timestamp, 123456, "Timestamp preserved")

    return passed


def test_discovery_packet() -> bool:
    """Test DiscoveryPacket encoding and decoding."""
    print_test("DiscoveryPacket")

    # Create packet
    packet = DiscoveryPacket.create()

    # Pack and unpack
    packed = packet.pack()
    unpacked = DiscoveryPacket.unpack(packed)

    # Validate
    passed = True
    passed &= assert_equal(len(packed), 12, "Packet size is 12 bytes")
    passed &= assert_equal(unpacked.header.packet_type, PacketType.DISCOVERY, "Packet type correct")
    passed &= assert_equal(unpacked.header.magic, MAGIC_NUMBER, "Magic number correct")

    return passed


def test_timesync_packet() -> bool:
    """Test TimeSyncPacket encoding and decoding."""
    print_test("TimeSyncPacket")

    # Create packet with known time
    test_time_ms = int(time.time() * 1000)
    packet = TimeSyncPacket.create(server_time_ms=test_time_ms)

    # Pack and unpack
    packed = packet.pack()
    unpacked = TimeSyncPacket.unpack(packed)

    # Validate
    passed = True
    passed &= assert_equal(len(packed), 20, "Packet size is 20 bytes")
    passed &= assert_equal(unpacked.header.packet_type, PacketType.TIMESYNC, "Packet type correct")
    passed &= assert_equal(unpacked.server_time_ms, test_time_ms, "Server time preserved")

    return passed


def test_control_packet() -> bool:
    """Test ControlPacket encoding and decoding."""
    print_test("ControlPacket")

    # Test opening valve 0
    packet = ControlPacket.create(command_id=0, command_state=ControlState.OPEN)
    packed = packet.pack()
    unpacked = ControlPacket.unpack(packed)

    passed = True
    passed &= assert_equal(len(packed), 16, "Packet size is 16 bytes")
    passed &= assert_equal(unpacked.header.packet_type, PacketType.CONTROL, "Packet type correct")
    passed &= assert_equal(unpacked.command_id, 0, "Command ID preserved")
    passed &= assert_equal(unpacked.command_state, ControlState.OPEN, "Command state preserved")

    # Test closing valve 5
    packet2 = ControlPacket.create(command_id=5, command_state=ControlState.CLOSED)
    packed2 = packet2.pack()
    unpacked2 = ControlPacket.unpack(packed2)

    passed &= assert_equal(unpacked2.command_id, 5, "Command ID 5 preserved")
    passed &= assert_equal(unpacked2.command_state, ControlState.CLOSED, "CLOSED state preserved")

    return passed


def test_data_packet() -> bool:
    """Test DataPacket encoding and decoding."""
    print_test("DataPacket")

    # Test with various sensor values
    test_cases = [
        (0, 23.456, "Sensor 0, positive float"),
        (1, -10.5, "Sensor 1, negative float"),
        (255, 0.0, "Sensor 255, zero"),
        (10, 12345.6789, "Sensor 10, large value"),
    ]

    passed = True
    for sensor_id, data_value, description in test_cases:
        packet = DataPacket.create(sensor_id=sensor_id, data=data_value, unit=Unit.CELSIUS)
        packed = packet.pack()
        unpacked = DataPacket.unpack(packed)

        passed &= assert_equal(len(packed), 20, f"{description}: size is 20 bytes")
        passed &= assert_equal(unpacked.header.packet_type, PacketType.DATA, f"{description}: type correct")
        passed &= assert_equal(unpacked.sensor_id, sensor_id, f"{description}: sensor ID preserved")
        passed &= assert_equal(unpacked.unit, Unit.CELSIUS, f"{description}: unit preserved")

        # Float comparison with tolerance (32-bit floats have ~7 decimal digits of precision)
        # For large values, use relative tolerance
        relative_error = abs(unpacked.data - data_value) / (abs(data_value) + 1e-10)
        if relative_error < 0.0001:  # 0.01% relative error tolerance
            print_pass(f"  ✓ {description}: data value preserved ({data_value})")
        else:
            print_fail(f"  ✗ {description}: data value mismatch")
            print_info(f"    Expected: {data_value}")
            print_info(f"    Got: {unpacked.data}")
            print_info(f"    Relative error: {relative_error:.6f}")
            passed = False

    return passed


def test_status_packet() -> bool:
    """Test StatusPacket encoding and decoding."""
    print_test("StatusPacket")

    passed = True

    # Test all status types
    for status in [DeviceStatus.INACTIVE, DeviceStatus.ACTIVE, DeviceStatus.ERROR, DeviceStatus.CALIBRATING]:
        packet = StatusPacket.create(status=status)
        packed = packet.pack()
        unpacked = StatusPacket.unpack(packed)

        passed &= assert_equal(len(packed), 16, f"Status {status.name}: size is 16 bytes")
        passed &= assert_equal(unpacked.header.packet_type, PacketType.STATUS, f"Status {status.name}: type correct")
        passed &= assert_equal(unpacked.status, status, f"Status {status.name}: status preserved")

    return passed


def test_config_packet() -> bool:
    """Test ConfigPacket encoding and decoding."""
    print_test("ConfigPacket")

    # Create a realistic config JSON
    config = {
        "deviceName": "TestDevice",
        "deviceType": "Sensor Monitor",
        "sensorInfo": {
            "thermocouples": {
                "TC1": {
                    "ADCIndex": 0,
                    "highPin": 1,
                    "lowPin": 2,
                    "type": "K",
                    "units": "C"
                }
            },
            "pressureTransducers": {
                "PT1": {
                    "ADCIndex": 1,
                    "pin": 3,
                    "maxPressure_PSI": 500,
                    "units": "PSI"
                }
            }
        },
        "controls": {
            "VALVE1": {
                "pin": 5,
                "type": "valve",
                "defaultState": "CLOSED"
            }
        }
    }

    config_json = json.dumps(config)
    packet = ConfigPacket.create(config_json=config_json)

    # Pack and unpack
    packed = packet.pack()
    unpacked = ConfigPacket.unpack(packed)

    # Validate
    passed = True
    expected_size = 16 + len(config_json.encode('utf-8'))
    passed &= assert_equal(len(packed), expected_size, f"Packet size is {expected_size} bytes")
    passed &= assert_equal(unpacked.header.packet_type, PacketType.CONFIG, "Packet type correct")
    passed &= assert_equal(unpacked.config_json, config_json, "JSON config preserved")

    # Parse JSON to verify it's valid
    try:
        parsed = json.loads(unpacked.config_json)
        passed &= assert_equal(parsed["deviceName"], "TestDevice", "JSON deviceName preserved")
        passed &= assert_equal(parsed["deviceType"], "Sensor Monitor", "JSON deviceType preserved")
    except json.JSONDecodeError:
        print_fail("  ✗ JSON parsing failed")
        passed = False

    return passed


def test_stream_start_packet() -> bool:
    """Test StreamStartPacket encoding and decoding."""
    print_test("StreamStartPacket")

    # Test various frequencies (max 255 Hz with 1-byte field)
    test_frequencies = [1, 10, 100, 255]
    passed = True

    for freq in test_frequencies:
        packet = StreamStartPacket.create(frequency_hz=freq)
        packed = packet.pack()
        unpacked = StreamStartPacket.unpack(packed)

        passed &= assert_equal(len(packed), 16, f"Frequency {freq} Hz: size is 16 bytes")
        passed &= assert_equal(unpacked.header.packet_type, PacketType.STREAM_START, f"Frequency {freq} Hz: type correct")
        passed &= assert_equal(unpacked.frequency_hz, freq, f"Frequency {freq} Hz: frequency preserved")

    return passed


def test_simple_packets() -> bool:
    """Test SimplePacket for various types."""
    print_test("SimplePacket (HEARTBEAT, STREAM_STOP, GET_SINGLE)")

    passed = True

    # Test different simple packet types
    simple_types = [
        PacketType.HEARTBEAT,
        PacketType.STREAM_STOP,
        PacketType.GET_SINGLE,
        PacketType.STATUS_REQUEST,
    ]

    for ptype in simple_types:
        packet = SimplePacket.create(packet_type=ptype)
        packed = packet.pack()
        unpacked = SimplePacket.unpack(packed)

        passed &= assert_equal(len(packed), 12, f"{ptype.name}: size is 12 bytes")
        passed &= assert_equal(unpacked.header.packet_type, ptype, f"{ptype.name}: type preserved")

    return passed


def test_ack_nack_packets() -> bool:
    """Test ACK and NACK packets."""
    print_test("ACK/NACK Packets")

    passed = True

    # Test ACK
    ack = AckPacket.create(ack_packet_type=PacketType.CONTROL, is_nack=False)
    ack_packed = ack.pack()
    ack_unpacked = AckPacket.unpack(ack_packed)

    passed &= assert_equal(len(ack_packed), 16, "ACK: size is 16 bytes")
    passed &= assert_equal(ack_unpacked.header.packet_type, PacketType.ACK, "ACK: type is ACK")
    passed &= assert_equal(ack_unpacked.ack_packet_type, PacketType.CONTROL, "ACK: acknowledging CONTROL")

    # Test NACK
    nack = AckPacket.create(ack_packet_type=PacketType.STREAM_START, is_nack=True)
    nack_packed = nack.pack()
    nack_unpacked = AckPacket.unpack(nack_packed)

    passed &= assert_equal(len(nack_packed), 16, "NACK: size is 16 bytes")
    passed &= assert_equal(nack_unpacked.header.packet_type, PacketType.NACK, "NACK: type is NACK")
    passed &= assert_equal(nack_unpacked.ack_packet_type, PacketType.STREAM_START, "NACK: acknowledging STREAM_START")

    return passed


def test_decode_packet() -> bool:
    """Test the generic decode_packet function."""
    print_test("decode_packet() function")

    passed = True

    # Create various packet types and decode them
    packets = [
        (DiscoveryPacket.create(), DiscoveryPacket, "DiscoveryPacket"),
        (TimeSyncPacket.create(12345), TimeSyncPacket, "TimeSyncPacket"),
        (ControlPacket.create(0, ControlState.OPEN), ControlPacket, "ControlPacket"),
        (DataPacket.create(0, 23.45), DataPacket, "DataPacket"),
        (StatusPacket.create(DeviceStatus.ACTIVE), StatusPacket, "StatusPacket"),
        (StreamStartPacket.create(10), StreamStartPacket, "StreamStartPacket"),
        (SimplePacket.create(PacketType.HEARTBEAT), SimplePacket, "SimplePacket"),
        (AckPacket.create(PacketType.CONTROL), AckPacket, "AckPacket"),
    ]

    for packet, expected_type, name in packets:
        packed = packet.pack()
        decoded = decode_packet(packed)

        if isinstance(decoded, expected_type):
            print_pass(f"  ✓ {name}: correctly decoded as {expected_type.__name__}")
        else:
            print_fail(f"  ✗ {name}: expected {expected_type.__name__}, got {type(decoded).__name__}")
            passed = False

    return passed


def test_error_handling() -> bool:
    """Test error handling for invalid packets."""
    print_test("Error Handling")

    passed = True

    # Test 1: Packet too small
    try:
        decode_packet(b"short")
        print_fail("  ✗ Should reject packets smaller than header")
        passed = False
    except ValueError as e:
        if "too small" in str(e).lower():
            print_pass("  ✓ Rejects packets smaller than header")
        else:
            print_fail(f"  ✗ Wrong error message: {e}")
            passed = False

    # Test 2: Invalid magic number
    bad_header = PacketHeader(
        magic=0xDEAD,  # Wrong magic
        packet_type=PacketType.HEARTBEAT,
        timestamp=12345
    )
    try:
        PacketHeader.unpack(bad_header.pack())
        print_fail("  ✗ Should reject invalid magic number")
        passed = False
    except ValueError as e:
        if "magic" in str(e).lower():
            print_pass("  ✓ Rejects invalid magic number")
        else:
            print_fail(f"  ✗ Wrong error message: {e}")
            passed = False

    # Test 3: Unknown packet type (using a reserved value)
    header_bytes = PacketHeader(
        magic=MAGIC_NUMBER,
        packet_type=255,  # Not a valid PacketType
        timestamp=12345
    )
    try:
        decode_packet(header_bytes.pack())
        print_fail("  ✗ Should reject unknown packet type")
        passed = False
    except (ValueError, KeyError):
        print_pass("  ✓ Rejects unknown packet type")

    return passed


def test_get_packet_size() -> bool:
    """Test get_packet_size utility function."""
    print_test("get_packet_size() utility")

    passed = True

    expected_sizes = {
        PacketType.DISCOVERY: 12,
        PacketType.TIMESYNC: 20,
        PacketType.CONTROL: 16,
        PacketType.DATA: 20,
        PacketType.STATUS: 16,
        PacketType.CONFIG: 0,  # Variable
        PacketType.STREAM_START: 16,
        PacketType.STREAM_STOP: 12,
        PacketType.HEARTBEAT: 12,
        PacketType.ACK: 16,
    }

    for ptype, expected_size in expected_sizes.items():
        actual_size = get_packet_size(ptype)
        passed &= assert_equal(actual_size, expected_size, f"{ptype.name}: expected size {expected_size}")

    return passed


def test_roundtrip_all_packets() -> bool:
    """Test full encode/decode roundtrip for all packet types."""
    print_test("Full Roundtrip (Encode→Decode) All Packets")

    passed = True

    # Discovery
    p1 = DiscoveryPacket.create()
    passed &= assert_equal(
        decode_packet(p1.pack()).header.packet_type,
        PacketType.DISCOVERY,
        "Discovery roundtrip"
    )

    # TimeSync
    p2 = TimeSyncPacket.create(99999)
    decoded_p2 = decode_packet(p2.pack())
    passed &= assert_equal(decoded_p2.server_time_ms, 99999, "TimeSync roundtrip")

    # Control
    p3 = ControlPacket.create(7, ControlState.OPEN)
    decoded_p3 = decode_packet(p3.pack())
    passed &= assert_equal(decoded_p3.command_id, 7, "Control roundtrip: command_id")
    passed &= assert_equal(decoded_p3.command_state, ControlState.OPEN, "Control roundtrip: state")

    # Data
    p4 = DataPacket.create(3, 99.99, Unit.VOLTS)
    decoded_p4 = decode_packet(p4.pack())
    passed &= assert_equal(decoded_p4.sensor_id, 3, "Data roundtrip: sensor_id")
    passed &= assert_equal(decoded_p4.unit, Unit.VOLTS, "Data roundtrip: unit")
    if abs(decoded_p4.data - 99.99) < 0.01:
        print_pass("  ✓ Data roundtrip: data value")
    else:
        print_fail("  ✗ Data roundtrip: data value")
        passed = False

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

    # Simple packets
    p8 = SimplePacket.create(PacketType.HEARTBEAT)
    passed &= assert_equal(
        decode_packet(p8.pack()).header.packet_type,
        PacketType.HEARTBEAT,
        "SimplePacket (HEARTBEAT) roundtrip"
    )

    # ACK/NACK
    p9 = AckPacket.create(PacketType.CONTROL, is_nack=False)
    decoded_p9 = decode_packet(p9.pack())
    passed &= assert_equal(decoded_p9.header.packet_type, PacketType.ACK, "ACK roundtrip: type")
    passed &= assert_equal(decoded_p9.ack_packet_type, PacketType.CONTROL, "ACK roundtrip: ack_type")

    return passed


def test_realistic_scenario() -> bool:
    """Test a realistic communication scenario."""
    print_test("Realistic Communication Scenario")

    passed = True
    print_info("Simulating device connection and data streaming...")

    # 1. Server discovers device
    discovery = DiscoveryPacket.create()
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
    ack = AckPacket.create(PacketType.CONFIG)
    print_info(f"3. Server sends ACK ({len(ack.pack())} bytes)")

    # 4. Server syncs time
    timesync = TimeSyncPacket.create()
    print_info(f"4. Server sends TIMESYNC ({len(timesync.pack())} bytes)")

    # 5. Device acknowledges
    ack2 = AckPacket.create(PacketType.TIMESYNC)
    print_info(f"5. Device sends ACK ({len(ack2.pack())} bytes)")

    # 6. Server requests streaming at 10 Hz
    stream_start = StreamStartPacket.create(10)
    print_info(f"6. Server sends STREAM_START at 10 Hz ({len(stream_start.pack())} bytes)")

    # 7. Device sends 2 data packets (TC1 and PT1)
    data1 = DataPacket.create(sensor_id=0, data=23.5, unit=Unit.CELSIUS)  # TC1: 23.5°C
    data2 = DataPacket.create(sensor_id=1, data=145.2, unit=Unit.PSI)  # PT1: 145.2 PSI
    print_info(f"7. Device sends DATA for TC1 ({len(data1.pack())} bytes): 23.5°C")
    print_info(f"   Device sends DATA for PT1 ({len(data2.pack())} bytes): 145.2 PSI")

    # 8. Server opens valve
    control = ControlPacket.create(command_id=0, command_state=ControlState.OPEN)
    print_info(f"8. Server sends CONTROL to open AVFILL ({len(control.pack())} bytes)")

    # 9. Device acknowledges
    ack3 = AckPacket.create(PacketType.CONTROL)
    print_info(f"9. Device sends ACK ({len(ack3.pack())} bytes)")

    # 10. Server stops streaming
    stream_stop = SimplePacket.create(PacketType.STREAM_STOP)
    print_info(f"10. Server sends STREAM_STOP ({len(stream_stop.pack())} bytes)")

    # Calculate total bandwidth
    total_bytes = sum([
        len(p.pack()) for p in [
            discovery, config_packet, ack, timesync, ack2,
            stream_start, data1, data2, control, ack3, stream_stop
        ]
    ])
    print_info(f"\nTotal data exchanged: {total_bytes} bytes")

    # Verify all packets can be decoded
    all_packets = [
        discovery, config_packet, ack, timesync, ack2,
        stream_start, data1, data2, control, ack3, stream_stop
    ]

    for i, packet in enumerate(all_packets, 1):
        try:
            decoded = decode_packet(packet.pack())
            # Just verify it decodes without error
        except Exception as e:
            print_fail(f"  ✗ Failed to decode packet {i}: {e}")
            passed = False
            return passed

    print_pass("  ✓ All packets in scenario encoded and decoded successfully")

    return passed


def run_all_tests() -> None:
    """Run all tests and print summary."""
    print(f"\n{Colors.BOLD}{'='*70}")
    print(f"  QRET Propulsion Binary Protocol Test Suite")
    print(f"{'='*70}{Colors.RESET}\n")

    tests = [
        ("PacketHeader", test_packet_header),
        ("DiscoveryPacket", test_discovery_packet),
        ("TimeSyncPacket", test_timesync_packet),
        ("ControlPacket", test_control_packet),
        ("DataPacket", test_data_packet),
        ("StatusPacket", test_status_packet),
        ("ConfigPacket", test_config_packet),
        ("StreamStartPacket", test_stream_start_packet),
        ("SimplePacket", test_simple_packets),
        ("ACK/NACK Packets", test_ack_nack_packets),
        ("decode_packet()", test_decode_packet),
        ("Error Handling", test_error_handling),
        ("get_packet_size()", test_get_packet_size),
        ("Full Roundtrip", test_roundtrip_all_packets),
        ("Realistic Scenario", test_realistic_scenario),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print_fail(f"\n✗ {name} CRASHED: {e}")
            results.append((name, False))

    # Print summary
    print(f"\n{Colors.BOLD}{'='*70}")
    print("  Test Summary")
    print(f"{'='*70}{Colors.RESET}\n")

    passed_count = sum(1 for _, result in results if result)
    total_count = len(results)

    for name, result in results:
        status = f"{Colors.GREEN}✓ PASS{Colors.RESET}" if result else f"{Colors.RED}✗ FAIL{Colors.RESET}"
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

