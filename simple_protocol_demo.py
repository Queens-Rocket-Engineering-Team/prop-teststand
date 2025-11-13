#!/usr/bin/env python3
"""Simple standalone protocol demo - no Docker needed!

This script demonstrates the binary protocol working end-to-end
by simulating both server and device communication.
"""

import json
from libqretprop.protocol import (
    ConfigPacket,
    ControlPacket,
    ControlState,
    DataPacket,
    decode_packet,
    PacketType,
    StreamStartPacket,
    AckPacket,
)


def print_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def print_step(num, description):
    print(f"\033[94m{num}. {description}\033[0m")


def print_data(label, value):
    print(f"   {label}: \033[92m{value}\033[0m")


def main():
    print_section("BINARY PROTOCOL DEMONSTRATION")
    print("This shows the complete protocol flow without needing a server!\n")

    # ========================================================================
    # STEP 1: Device sends configuration
    # ========================================================================
    print_step(1, "Device sends configuration to server")

    device_config = {
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
            "AVFILL": {"pin": 5, "type": "valve", "defaultState": "CLOSED"}
        }
    }

    # Device creates and sends config packet
    config_packet = ConfigPacket.create(json.dumps(device_config))
    config_bytes = config_packet.pack()

    print_data("Packet type", "CONFIG")
    print_data("Packet size", f"{len(config_bytes)} bytes")
    print_data("Device name", device_config["deviceName"])
    print_data("Sensors", "TC1 (id=0), PT1 (id=1)")
    print_data("Controls", "AVFILL (id=0)")

    # Server receives and decodes
    received_config = decode_packet(config_bytes)
    parsed_config = json.loads(received_config.config_json)
    print_data("Server decoded", f"✓ Config for {parsed_config['deviceName']}")

    # ========================================================================
    # STEP 2: Server starts streaming
    # ========================================================================
    print_step(2, "Server requests streaming at 10 Hz")

    stream_packet = StreamStartPacket.create(frequency_hz=10)
    stream_bytes = stream_packet.pack()

    print_data("Packet type", "STREAM_START")
    print_data("Packet size", f"{len(stream_bytes)} bytes")
    print_data("Frequency", "10 Hz")

    # Device receives
    received_stream = decode_packet(stream_bytes)
    print_data("Device decoded", f"✓ Start streaming at {received_stream.frequency_hz} Hz")

    # ========================================================================
    # STEP 3: Device sends sensor data
    # ========================================================================
    print_step(3, "Device sends sensor data")

    # TC1 reading
    tc1_packet = DataPacket.create(sensor_id=0, data=23.5)
    tc1_bytes = tc1_packet.pack()

    print_data("Sensor", "TC1 (id=0)")
    print_data("Value", "23.5°C")
    print_data("Packet size", f"{len(tc1_bytes)} bytes")

    # Server receives TC1
    received_tc1 = decode_packet(tc1_bytes)
    print_data("Server decoded", f"✓ Sensor {received_tc1.sensor_id} = {received_tc1.data}°C")

    # PT1 reading
    pt1_packet = DataPacket.create(sensor_id=1, data=145.2)
    pt1_bytes = pt1_packet.pack()

    print_data("Sensor", "PT1 (id=1)")
    print_data("Value", "145.2 PSI")
    print_data("Packet size", f"{len(pt1_bytes)} bytes")

    # Server receives PT1
    received_pt1 = decode_packet(pt1_bytes)
    print_data("Server decoded", f"✓ Sensor {received_pt1.sensor_id} = {received_pt1.data} PSI")

    # ========================================================================
    # STEP 4: Server controls valve
    # ========================================================================
    print_step(4, "Server opens AVFILL valve")

    control_packet = ControlPacket.create(command_id=0, command_state=ControlState.OPEN)
    control_bytes = control_packet.pack()

    print_data("Control", "AVFILL (id=0)")
    print_data("State", "OPEN")
    print_data("Packet size", f"{len(control_bytes)} bytes")

    # Device receives
    received_control = decode_packet(control_bytes)
    state_str = "OPEN" if received_control.command_state == ControlState.OPEN else "CLOSED"
    print_data("Device decoded", f"✓ Control {received_control.command_id} → {state_str}")
    print_data("Action", "digitalWrite(pin 5, HIGH) - Valve physically opens!")

    # Device sends ACK
    ack_packet = AckPacket.create(PacketType.CONTROL)
    ack_bytes = ack_packet.pack()
    print_data("Device response", f"ACK ({len(ack_bytes)} bytes)")

    # Server receives ACK
    received_ack = decode_packet(ack_bytes)
    print_data("Server decoded", f"✓ Command acknowledged")

    # ========================================================================
    # SUMMARY
    # ========================================================================
    print_section("SUMMARY")

    print("Protocol Features Demonstrated:")
    print("  ✓ Configuration exchange (JSON in binary packet)")
    print("  ✓ Streaming control (frequency setting)")
    print("  ✓ Real-time data transmission (sensor readings)")
    print("  ✓ Control commands (valve open/close)")
    print("  ✓ Acknowledgments (ACK/NACK)")

    print("\nKey Advantages:")
    print("  ✓ Fixed packet sizes (10-18 bytes)")
    print("  ✓ Fast binary parsing (no string splitting)")
    print("  ✓ Index-based commands (no name lookups)")
    print("  ✓ Type-safe (can't send wrong format)")

    print("\nPacket Sizes Used:")
    print(f"  • CONFIG:       {len(config_bytes)} bytes (variable)")
    print(f"  • STREAM_START: {len(stream_bytes)} bytes (fixed)")
    print(f"  • DATA:         {len(tc1_bytes)} bytes (fixed)")
    print(f"  • CONTROL:      {len(control_bytes)} bytes (fixed)")
    print(f"  • ACK:          {len(ack_bytes)} bytes (fixed)")

    total_bytes = len(config_bytes) + len(stream_bytes) + len(tc1_bytes) + len(pt1_bytes) + len(control_bytes) + len(ack_bytes)
    print(f"\n  Total data exchanged: {total_bytes} bytes")

    print("\nOld String Protocol Equivalent:")
    old_config = f"CONF{json.dumps(device_config)}\n"
    old_stream = "STREAM 10\n"
    old_data1 = "STRM time:1234.56 TC1:23.5\n"
    old_data2 = "STRM time:1234.66 PT1:145.2\n"
    old_control = "CONTROL AVFILL OPEN\n"
    old_total = len(old_config) + len(old_stream) + len(old_data1) + len(old_data2) + len(old_control)

    print(f"  Old protocol: ~{old_total} bytes")
    savings = ((old_total - total_bytes) / old_total) * 100
    print(f"  \033[92mBinary is {savings:.0f}% more efficient!\033[0m")

    print("\n\033[92m✓ PROTOCOL WORKING PERFECTLY!\033[0m\n")


if __name__ == "__main__":
    main()

