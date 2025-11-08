# Binary Protocol: Complete End-to-End Walkthrough

## Scenario: Server + One Sensor Monitor Device

This document walks through a complete communication session between the launch server and a single ESP32 sensor monitor device equipped with:
- 1 Thermocouple (TC1)
- 1 Pressure Transducer (PT1)
- 1 Valve (AVFILL)

---

## Phase 1: Device Discovery & Connection

### Step 1.1: Device Powers On
```
┌─────────────┐
│ ESP32 boots │
│ Connects to │
│   Wi-Fi     │
└─────────────┘
```

The ESP32:
1. Boots up and connects to the network
2. Starts listening on port 1900 (SSDP multicast)
3. Waits for discovery broadcast from server

---

### Step 1.2: Server Sends Discovery Broadcast
```python
# Server (deviceTools.py)
from libqretprop.protocol import DiscoveryPacket

# Create and send discovery packet
discovery = DiscoveryPacket.create()
ssdp_socket.sendto(discovery.pack(), (MULTICAST_ADDRESS, 1900))
```

**Packet Structure:**
```
DISCOVERY Packet (10 bytes)
┌────────┬───────┬─────────┬──────────┬───────────┐
│ 0x5150 │ 0x01  │ 0x01    │ 0x0000   │ 12345678  │
│ Magic  │ Type  │ Version │ Reserved │ Timestamp │
│ 2 byte │ 1 byte│ 1 byte  │ 2 bytes  │ 4 bytes   │
└────────┴───────┴─────────┴──────────┴───────────┘
```

**What Happens:**
- Server broadcasts to `239.255.255.250:1900`
- All devices on the network receive it
- Devices recognize the magic number `0x5150` and packet type `0x01`

---

### Step 1.3: Device Initiates TCP Connection
```
┌────────┐                           ┌────────┐
│ Device │ ───── TCP SYN ────────►  │ Server │
│        │ ◄──── TCP SYN-ACK ─────  │        │
│        │ ───── TCP ACK ────────►  │        │
└────────┘    Connection Established └────────┘
```

The ESP32:
1. Sees the discovery broadcast
2. Initiates TCP connection to server on port 50000
3. Three-way handshake completes

**Server Code:**
```python
# Device connects
deviceSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
await loop.sock_connect(deviceSocket, (server_ip, 50000))
```

---

## Phase 2: Configuration Exchange

### Step 2.1: Device Sends Configuration
```python
# Device (ESP32)
import json
from libqretprop.protocol import ConfigPacket

config = {
    "deviceName": "PropMonitor1",
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
        "AVFILL": {
            "pin": 5,
            "type": "valve",
            "defaultState": "CLOSED"
        }
    }
}

# Send config packet
config_packet = ConfigPacket.create(json.dumps(config))
sock.sendall(config_packet.pack())
```

**Packet Structure:**
```
CONFIG Packet (Variable size, ~365 bytes for this example)
┌────────────────┬────────────┬──────────────────────┐
│ PacketHeader   │ JSON Length│ JSON Data (UTF-8)    │
│ 10 bytes       │ 2 bytes    │ ~353 bytes           │
└────────────────┴────────────┴──────────────────────┘

Header:
  Magic: 0x5150
  Type: 0x10 (CONFIG)
  Version: 0x01
  Timestamp: current time

Payload:
  JSON Length: 353 (0x0161)
  JSON: {"deviceName": "PropMonitor1", ...}
```

---

### Step 2.2: Server Processes Configuration
```python
# Server (deviceTools.py)
from libqretprop.protocol import decode_packet

# Receive config
data = await loop.sock_recv(deviceSocket, 2048)
config_packet = decode_packet(data)

# Parse JSON
import json
device_config = json.loads(config_packet.config_json)

# Create device object with sensors and controls
device = SensorMonitor(deviceSocket, device_ip, device_config)

# Now server knows:
# - Sensor 0 = TC1 (thermocouple)
# - Sensor 1 = PT1 (pressure transducer)
# - Control 0 = AVFILL (valve)

# Add to device registry
deviceRegistry[device_ip] = device
```

**What the Server Now Knows:**
- Device name: "PropMonitor1"
- 2 sensors: TC1 (index 0), PT1 (index 1)
- 1 control: AVFILL (index 0)
- Sensor units: °C and PSI

---

### Step 2.3: Server Acknowledges
```python
# Server sends ACK
from libqretprop.protocol import AckPacket, PacketType

ack = AckPacket.create(ack_packet_type=PacketType.CONFIG)
deviceSocket.sendall(ack.pack())
```

**Packet Structure:**
```
ACK Packet (14 bytes)
┌────────────────┬──────────────┬──────────┐
│ PacketHeader   │ Ack Type     │ Reserved │
│ 10 bytes       │ 2 bytes      │ 2 bytes  │
└────────────────┴──────────────┴──────────┘

  Ack Type: 0x10 (CONFIG)
```

---

## Phase 3: Time Synchronization

### Step 3.1: Server Sends Time Sync
```python
# Server
from libqretprop.protocol import TimeSyncPacket
import time

# Send current Unix timestamp in milliseconds
server_time = int(time.time() * 1000)  # e.g., 1730998765432
timesync = TimeSyncPacket.create(server_time_ms=server_time)
deviceSocket.sendall(timesync.pack())
```

**Packet Structure:**
```
TIMESYNC Packet (18 bytes)
┌────────────────┬──────────────────────┐
│ PacketHeader   │ Server Time (ms)     │
│ 10 bytes       │ 8 bytes              │
└────────────────┴──────────────────────┘

  Server Time: 1730998765432 (Unix epoch ms)
```

---

### Step 3.2: Device Synchronizes Clock
```cpp
// Device (ESP32)
TimeSyncPacket timesync;
recv(sock, &timesync, sizeof(timesync), 0);

// Convert from network byte order
uint64_t server_time_ms = ntohll(timesync.server_time_ms);

// Calculate offset
uint32_t device_time = millis();
int64_t time_offset = server_time_ms - device_time;

// Store offset and use for all future timestamps
global_time_offset = time_offset;

// Send ACK
AckPacket ack;
ack.header.packet_type = PACKET_TYPE_ACK;
ack.ack_packet_type = PACKET_TYPE_TIMESYNC;
send(sock, &ack, sizeof(ack), 0);
```

**Device Now Synchronized:**
- All future timestamps will be `millis() + time_offset`
- Ensures all devices use same time reference
- Critical for coordinated sequences (T-minus countdowns)

---

## Phase 4: Data Streaming

### Step 4.1: Server Requests Streaming
```python
# Server wants data at 10 Hz (10 samples per second)
from libqretprop.protocol import StreamStartPacket

stream_cmd = StreamStartPacket.create(frequency_hz=10)
deviceSocket.sendall(stream_cmd.pack())
```

**Packet Structure:**
```
STREAM_START Packet (14 bytes)
┌────────────────┬──────────────┬──────────┐
│ PacketHeader   │ Frequency Hz │ Reserved │
│ 10 bytes       │ 2 bytes      │ 2 bytes  │
└────────────────┴──────────────┴──────────┘

  Frequency: 10 (0x000A) = 10 Hz
```

---

### Step 4.2: Device Starts Streaming
```cpp
// Device (ESP32)
StreamStartPacket stream_start;
recv(sock, &stream_start, sizeof(stream_start), 0);

uint16_t freq_hz = ntohs(stream_start.frequency_hz);
uint32_t interval_ms = 1000 / freq_hz;  // 100 ms for 10 Hz

// Send ACK
send_ack(PACKET_TYPE_STREAM_START);

// Start streaming loop
while (streaming_active) {
    // Read sensors
    float tc1_temp = read_thermocouple(0);      // e.g., 23.5°C
    float pt1_pressure = read_pressure(1);      // e.g., 145.2 PSI

    // Send TC1 data
    DataPacket data1;
    data1.header.packet_type = PACKET_TYPE_DATA;
    data1.header.timestamp = htonl(get_synced_time());
    data1.sensor_id = htons(0);  // TC1
    data1.data = htonf(tc1_temp);
    send(sock, &data1, sizeof(data1), 0);

    // Send PT1 data
    DataPacket data2;
    data2.header.packet_type = PACKET_TYPE_DATA;
    data2.header.timestamp = htonl(get_synced_time());
    data2.sensor_id = htons(1);  // PT1
    data2.data = htonf(pt1_pressure);
    send(sock, &data2, sizeof(data2), 0);

    delay(interval_ms);  // Wait 100ms (10 Hz)
}
```

**Packet Structure (Data):**
```
DATA Packet (18 bytes) - TC1
┌────────────────┬───────────┬──────────┬─────────────┐
│ PacketHeader   │ Sensor ID │ Reserved │ Data (float)│
│ 10 bytes       │ 2 bytes   │ 2 bytes  │ 4 bytes     │
└────────────────┴───────────┴──────────┴─────────────┘

  Sensor ID: 0 (TC1)
  Data: 23.5 (as 32-bit IEEE 754 float)

DATA Packet (18 bytes) - PT1
  Sensor ID: 1 (PT1)
  Data: 145.2 (as 32-bit IEEE 754 float)
```

---

### Step 4.3: Server Receives and Logs Data
```python
# Server (deviceTools.py)
async def _monitorSingleDevice(device: SensorMonitor):
    """Monitor incoming data packets."""
    loop = asyncio.get_event_loop()
    buffer = b""

    while True:
        # Receive data
        chunk = await loop.sock_recv(device.socket, 1024)
        buffer += chunk

        # Try to decode packets
        while len(buffer) >= 10:  # Minimum packet size
            try:
                packet = decode_packet(buffer)

                if packet.header.packet_type == PacketType.DATA:
                    # Get sensor name from ID
                    sensor_id = packet.sensor_id
                    sensor_name = list(device.sensors.keys())[sensor_id]

                    # Store data
                    device.sensors[sensor_name].data.append(packet.data)
                    device.times.append(time.monotonic() - device.startTime)

                    # Log to Redis
                    ml.log(f"{device.name} {sensor_name}: {packet.data}")

                # Remove processed packet from buffer
                packet_size = len(packet.pack())
                buffer = buffer[packet_size:]

            except ValueError:
                # Not enough data yet
                break
```

**Example Log Output:**
```
[10:45:32] PropMonitor1 TC1: 23.5
[10:45:32] PropMonitor1 PT1: 145.2
[10:45:32] PropMonitor1 TC1: 23.6
[10:45:32] PropMonitor1 PT1: 145.3
[10:45:33] PropMonitor1 TC1: 23.7
[10:45:33] PropMonitor1 PT1: 145.1
...
```

**Data Flow (10 Hz):**
```
Time    Device → Server
-----   ----------------
0.00s   TC1: 23.5°C (18 bytes)
0.00s   PT1: 145.2 PSI (18 bytes)
0.10s   TC1: 23.6°C (18 bytes)
0.10s   PT1: 145.3 PSI (18 bytes)
0.20s   TC1: 23.7°C (18 bytes)
0.20s   PT1: 145.1 PSI (18 bytes)
...

Bandwidth: 2 sensors × 10 Hz × 18 bytes = 360 bytes/sec
```

---

## Phase 5: Control Commands

### Step 5.1: Server Sends Valve Command
```python
# User clicks "Open AVFILL" button in GUI
# Server looks up control index for AVFILL

from libqretprop.protocol import ControlPacket, ControlState

# AVFILL is control 0 (first in config)
control_id = 0  # AVFILL
command = ControlPacket.create(
    command_id=control_id,
    command_state=ControlState.OPEN
)

deviceSocket.sendall(command.pack())
ml.log(f"Sent command: OPEN AVFILL")
```

**Packet Structure:**
```
CONTROL Packet (14 bytes)
┌────────────────┬────────────┬──────────────┬──────────┐
│ PacketHeader   │ Command ID │ Command State│ Reserved │
│ 10 bytes       │ 2 bytes    │ 1 byte       │ 1 byte   │
└────────────────┴────────────┴──────────────┴──────────┘

  Command ID: 0 (AVFILL)
  Command State: 1 (OPEN)
```

---

### Step 5.2: Device Executes Command
```cpp
// Device (ESP32)
ControlPacket control;
recv(sock, &control, sizeof(control), 0);

uint16_t command_id = ntohs(control.command_id);
uint8_t command_state = control.command_state;

// Look up control in config
if (command_id == 0) {  // AVFILL
    int pin = 5;  // From config

    if (command_state == CONTROL_STATE_OPEN) {
        digitalWrite(pin, HIGH);  // Open valve
        Serial.println("AVFILL OPENED");
    } else if (command_state == CONTROL_STATE_CLOSED) {
        digitalWrite(pin, LOW);   // Close valve
        Serial.println("AVFILL CLOSED");
    }

    // Send ACK
    AckPacket ack;
    ack.header.packet_type = PACKET_TYPE_ACK;
    ack.ack_packet_type = PACKET_TYPE_CONTROL;
    send(sock, &ack, sizeof(ack), 0);
}
```

**What Happens:**
1. Device receives CONTROL packet
2. Parses command_id (0 = AVFILL)
3. Parses command_state (1 = OPEN)
4. Sets GPIO pin 5 HIGH
5. Valve physically opens
6. Sends ACK back to server

---

### Step 5.3: Server Receives Acknowledgment
```python
# Server receives ACK
data = await loop.sock_recv(deviceSocket, 1024)
ack_packet = decode_packet(data)

if ack_packet.header.packet_type == PacketType.ACK:
    if ack_packet.ack_packet_type == PacketType.CONTROL:
        ml.log("Device acknowledged: AVFILL opened")
        # Update GUI to show valve is open
```

---

## Phase 6: Heartbeat

### Step 6.1: Server Sends Periodic Heartbeat
```python
# Server (every 5 seconds)
from libqretprop.protocol import SimplePacket

heartbeat = SimplePacket.create(PacketType.HEARTBEAT)
deviceSocket.sendall(heartbeat.pack())
```

**Packet Structure:**
```
HEARTBEAT Packet (10 bytes)
┌────────────────┐
│ PacketHeader   │
│ 10 bytes       │
└────────────────┘

  Type: 0x08 (HEARTBEAT)
```

---

### Step 6.2: Device Responds
```cpp
// Device (ESP32)
HeartbeatPacket heartbeat;
recv(sock, &heartbeat, sizeof(heartbeat), 0);

// Send ACK
AckPacket ack;
ack.header.packet_type = PACKET_TYPE_ACK;
ack.ack_packet_type = PACKET_TYPE_HEARTBEAT;
send(sock, &ack, sizeof(ack), 0);
```

**Purpose:**
- Keeps TCP connection alive
- Detects dead connections quickly
- If no response → server removes device from registry

---

## Phase 7: Stop Streaming

### Step 7.1: Server Stops Streaming
```python
# User clicks "Stop" button
from libqretprop.protocol import SimplePacket

stop_cmd = SimplePacket.create(PacketType.STREAM_STOP)
deviceSocket.sendall(stop_cmd.pack())
```

**Packet Structure:**
```
STREAM_STOP Packet (10 bytes)
┌────────────────┐
│ PacketHeader   │
│ 10 bytes       │
└────────────────┘

  Type: 0x06 (STREAM_STOP)
```

---

### Step 7.2: Device Stops Streaming
```cpp
// Device (ESP32)
SimplePacket stop;
recv(sock, &stop, sizeof(stop), 0);

if (stop.header.packet_type == PACKET_TYPE_STREAM_STOP) {
    streaming_active = false;  // Exit streaming loop

    // Send ACK
    send_ack(PACKET_TYPE_STREAM_STOP);
}
```

---

## Phase 8: Data Export

### Step 8.1: Server Exports to CSV
```python
# After test completes
from libqretprop.DeviceControllers.deviceTools import exportDataToCSV

# Export all collected data
exportDataToCSV()

# Creates file: PropMonitor1_20250108-104532.csv
# Contents:
#   Time,TC1,PT1
#   0.00,23.5,145.2
#   0.10,23.6,145.3
#   0.20,23.7,145.1
#   ...
```

---

## Complete Timeline Summary

```
TIME    EVENT                                PACKET TYPE    SIZE    DIRECTION
======  ===================================  =============  ======  ===========
0.00s   Server broadcasts discovery          DISCOVERY      10 B    Server → *
0.10s   Device connects (TCP handshake)      -              -       Device ↔ Server
0.15s   Device sends configuration           CONFIG         365 B   Device → Server
0.16s   Server acknowledges config           ACK            14 B    Server → Device
0.17s   Server syncs time                    TIMESYNC       18 B    Server → Device
0.18s   Device acknowledges time sync        ACK            14 B    Device → Server
0.20s   Server starts streaming (10 Hz)      STREAM_START   14 B    Server → Device
0.21s   Device acknowledges                  ACK            14 B    Device → Server

        === Streaming Phase (10 Hz) ===
0.22s   Device sends TC1 data                DATA           18 B    Device → Server
0.22s   Device sends PT1 data                DATA           18 B    Device → Server
0.32s   Device sends TC1 data                DATA           18 B    Device → Server
0.32s   Device sends PT1 data                DATA           18 B    Device → Server
...     (continues at 10 Hz)

2.45s   Server opens AVFILL valve            CONTROL        14 B    Server → Device
2.46s   Device acknowledges                  ACK            14 B    Device → Server

5.00s   Server sends heartbeat               HEARTBEAT      10 B    Server → Device
5.01s   Device acknowledges                  ACK            14 B    Device → Server

10.00s  Server sends heartbeat               HEARTBEAT      10 B    Server → Device
10.01s  Device acknowledges                  ACK            14 B    Device → Server

15.00s  Server stops streaming               STREAM_STOP    10 B    Server → Device
15.01s  Device acknowledges                  ACK            14 B    Device → Server
15.02s  TCP connection maintained (idle)
```

---

## Bandwidth Analysis

**During 10-second streaming test (10 Hz, 2 sensors):**

| Phase | Packets | Total Bytes | Notes |
|-------|---------|-------------|-------|
| Discovery & Setup | 6 | 435 B | One-time cost |
| Streaming (10s) | 200 | 3,600 B | 2 sensors × 10 Hz × 10s × 18 B |
| Control | 2 | 28 B | 1 valve command + ACK |
| Heartbeats | 4 | 48 B | 2 heartbeats + 2 ACKs |
| **TOTAL** | **212** | **4,111 B** | **~410 B/sec average** |

**Comparison with string protocol:**
- Binary: ~410 B/sec
- String: ~500 B/sec (e.g., `"STRM TC1:23.5 PT1:145.2\n"`)
- **Binary is 18% more efficient** and provides type safety!

---

## Error Scenarios

### Scenario 1: Device Disconnects Mid-Stream

```python
# Server
try:
    data = await loop.sock_recv(device.socket, 1024)
    if not data:
        raise ConnectionError("Device disconnected")
except ConnectionError:
    ml.elog(f"Device {device.name} disconnected")
    deviceRegistry.pop(device.address)
    # Stop expecting data from this device
```

### Scenario 2: Invalid Command ID

```cpp
// Device
if (command_id >= num_controls) {
    // Send NACK
    AckPacket nack;
    nack.header.packet_type = PACKET_TYPE_NACK;
    nack.ack_packet_type = PACKET_TYPE_CONTROL;
    send(sock, &nack, sizeof(nack), 0);
}
```

### Scenario 3: Packet Corruption

```python
# Server
try:
    packet = decode_packet(buffer)
except ValueError as e:
    ml.elog(f"Corrupted packet: {e}")
    # Discard buffer and request retransmission
```

---

## Key Takeaways

1. **Discovery is simple**: One broadcast packet
2. **Configuration is automatic**: Device tells server its capabilities
3. **Time sync ensures coordination**: All timestamps align
4. **Streaming is efficient**: Small, fixed-size packets
5. **Control is index-based**: No string parsing needed
6. **ACKs provide reliability**: Know if commands succeeded
7. **Heartbeats maintain connection**: Detect failures quickly

This protocol replaces string parsing with binary efficiency while maintaining readability in Python and ease of implementation in C++ for ESP32!

