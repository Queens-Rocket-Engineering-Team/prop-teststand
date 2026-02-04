# QRET Propulsion Binary Protocol v2 - Wire Format Specification

This document is the authoritative wire-format reference for the QRET Propulsion binary protocol. All values are big-endian (network byte order). All sizes are in bytes.

---

## Packet Header (9 bytes)

Every packet begins with this header. The LENGTH field enables trivial TCP framing.

```
Offset  Size  Type    Field        Description
------  ----  ------  -----------  ----------------------------------------
0       1     uint8   VERSION      Protocol revision (currently 0x02)
1       1     uint8   PACKET_TYPE  Packet type enum (see below)
2       1     uint8   SEQUENCE     Wrapping counter 0-255 for req/resp matching
3       2     uint16  LENGTH       Total packet size including this header
5       4     uint32  TIMESTAMP    Milliseconds since boot (device) or session start (server)
```

Struct format: `>BBBHI` (9 bytes)

### TCP Framing

To parse a TCP stream:

1. Read 9 bytes (header)
2. Extract LENGTH from bytes 3-4
3. Read `LENGTH - 9` more bytes (payload)
4. Decode the complete packet
5. Repeat

---

## Packet Type Enum

```
Value  Name            Direction        Description
-----  --------------  ---------------  --------------------------------
0x00   ESTOP           Server -> Device Emergency stop, highest priority
0x01   DISCOVERY       Server -> *      Discovery broadcast
0x02   TIMESYNC        Server -> Device Time synchronization
0x03   CONTROL         Server -> Device Control command (valve, etc.)
0x04   STATUS_REQUEST  Server -> Device Request device status
0x05   STREAM_START    Server -> Device Start streaming at given Hz
0x06   STREAM_STOP     Server -> Device Stop streaming
0x07   GET_SINGLE      Server -> Device Request single data reading
0x08   HEARTBEAT       Server -> Device Keep-alive

0x10   CONFIG          Device -> Server Device configuration (JSON)
0x11   DATA            Device -> Server Batched sensor data
0x12   STATUS          Device -> Server Device status response
0x13   ACK             Device -> Server Positive acknowledgment
0x14   NACK            Device -> Server Negative acknowledgment with error
```

---

## Packet Formats

### Header-Only Packets (9 bytes)

These packets have no payload. LENGTH = 9.

| Packet Type    | Value |
|----------------|-------|
| ESTOP          | 0x00  |
| DISCOVERY      | 0x01  |
| STREAM_STOP    | 0x06  |
| GET_SINGLE     | 0x07  |
| HEARTBEAT      | 0x08  |
| STATUS_REQUEST | 0x04  |

```
[Header 9B]
```

---

### STATUS (10 bytes)

Device status response. LENGTH = 10.

```
Offset  Size  Type    Field   Description
------  ----  ------  ------  -------------------------
0-8     9     -       header  Standard header
9       1     uint8   status  DeviceStatus enum value
```

---

### STREAM_START (11 bytes)

Start streaming at specified frequency. LENGTH = 11.

```
Offset  Size  Type    Field         Description
------  ----  ------  ------------  -------------------------
0-8     9     -       header        Standard header
9       2     uint16  frequency_hz  Samples per second (1-65535)
```

---

### CONTROL (11 bytes)

Control command for valves/actuators. LENGTH = 11.

```
Offset  Size  Type    Field          Description
------  ----  ------  -------------  -------------------------
0-8     9     -       header         Standard header
9       1     uint8   command_id     Index in device's control array
10      1     uint8   command_state  ControlState enum value
```

---

### ACK (12 bytes)

Positive acknowledgment. LENGTH = 12. error_code is always 0x00.

```
Offset  Size  Type    Field           Description
------  ----  ------  --------------  -------------------------
0-8     9     -       header          Standard header (type=0x13)
9       1     uint8   ack_packet_type Type of packet being acknowledged
10      1     uint8   ack_sequence    Sequence number of acknowledged packet
11      1     uint8   error_code      Always 0x00 (NONE) for ACK
```

---

### NACK (12 bytes)

Negative acknowledgment with error code. LENGTH = 12.

```
Offset  Size  Type    Field            Description
------  ----  ------  ---------------  -------------------------
0-8     9     -       header           Standard header (type=0x14)
9       1     uint8   nack_packet_type Type of packet being rejected
10      1     uint8   nack_sequence    Sequence number of rejected packet
11      1     uint8   error_code       ErrorCode enum value
```

---

### TIMESYNC (17 bytes)

Time synchronization from server. LENGTH = 17.

```
Offset  Size  Type    Field          Description
------  ----  ------  -------------  -------------------------
0-8     9     -       header         Standard header
9       8     uint64  server_time_ms Unix epoch milliseconds
```

The device uses this to compute an offset: `offset = server_time_ms - device_uptime_ms`. All subsequent device timestamps can be converted to absolute time by adding this offset.

**Server-side offset**: When the server receives the TIMESYNC ACK, it records:
- `sync_device_ms` = the ACK header's timestamp (device ms-since-boot)
- `sync_server_monotonic` = server's monotonic clock at ACK receipt

For any subsequent DATA packet with header timestamp `t_device_ms`:
```
delta_ms = t_device_ms - sync_device_ms
server_time = sync_server_monotonic + delta_ms / 1000.0
```

This uses the device's crystal oscillator for inter-sample timing (no network jitter), anchored to the server's reference frame.

**Periodic resync**: ESP32 crystal oscillators drift ~20 ppm (~12 ms per 10 minutes, ~72 ms per hour). At high sample rates (hundreds of Hz), this drift exceeds one sample period over long tests. The server automatically sends a new TIMESYNC every 10 minutes to keep drift under ~12 ms. If no sync has been completed, the server falls back to server-side receive timestamps.

---

### DATA (10 + 6*N bytes, variable)

Batched sensor data. LENGTH = 10 + 6*N where N is the number of readings.

```
Offset  Size  Type    Field     Description
------  ----  ------  --------  -------------------------
0-8     9     -       header    Standard header
9       1     uint8   count     Number of sensor readings (N)

Repeated N times (6 bytes each):
+0      1     uint8   sensor_id  Index in device's sensor array
+1      1     uint8   unit       Unit enum value
+2      4     float32 value      IEEE 754 single-precision float
```

A single reading uses N=1 (16 bytes total). Example with 3 sensors:

```
9 (header) + 1 (count) + 3 * 6 (readings) = 28 bytes
```

Bandwidth comparison at 100 Hz with 5 sensors:

| Method | Packets/sec | Bytes each | Total |
|--------|------------|------------|-------|
| Batched (v2) | 100 | 40 | 4.0 KB/s |
| Individual (v1) | 500 | 12 | 6.0 KB/s + 5x TCP overhead |

---

### CONFIG (13 + json_len bytes, variable)

Device configuration sent on connection. LENGTH = 13 + json_len.

```
Offset      Size      Type    Field       Description
------      ----      ------  ----------  -------------------------
0-8         9         -       header      Standard header
9           4         uint32  json_length Length of JSON data in bytes
13          json_len  bytes   json_data   UTF-8 encoded JSON string
```

---

## Packet Size Summary

| Packet         | Total Size       | Payload after header |
|----------------|------------------|----------------------|
| ESTOP          | 9                | (none)               |
| DISCOVERY      | 9                | (none)               |
| HEARTBEAT      | 9                | (none)               |
| STREAM_STOP    | 9                | (none)               |
| GET_SINGLE     | 9                | (none)               |
| STATUS_REQUEST | 9                | (none)               |
| STATUS         | 10               | 1B status            |
| STREAM_START   | 11               | 2B frequency_hz      |
| CONTROL        | 11               | 1B cmd_id + 1B state |
| ACK            | 12               | 1B type + 1B seq + 1B error |
| NACK           | 12               | 1B type + 1B seq + 1B error |
| TIMESYNC       | 17               | 8B server_time_ms    |
| DATA           | 10 + 6*N         | 1B count + N*(1B+1B+4B) |
| CONFIG         | 13 + json_len    | 4B len + json_data   |

---

## Enum Values

### DeviceStatus

```
Value  Name
-----  -----------
0x00   INACTIVE
0x01   ACTIVE
0x02   ERROR
0x03   CALIBRATING
```

### ControlState

```
Value  Name
-----  ------
0x00   CLOSED
0x01   OPEN
0xFF   ERROR
```

### Unit

```
Value  Name
-----  ------------
0x00   VOLTS
0x01   AMPS
0x02   CELSIUS
0x03   FAHRENHEIT
0x04   KELVIN
0x05   PSI
0x06   BAR
0x07   PASCAL
0x08   GRAMS
0x09   KILOGRAMS
0x0A   POUNDS
0x0B   NEWTONS
0x0C   SECONDS
0x0D   MILLISECONDS
0x0E   HERTZ
0x0F   PERCENT
0xFF   UNITLESS
```

### ErrorCode

```
Value  Name
-----  --------------
0x00   NONE            No error (used in ACK)
0x01   UNKNOWN_TYPE    Unrecognized packet type
0x02   INVALID_ID      Invalid sensor/control ID
0x03   HARDWARE_FAULT  Hardware error
0x04   BUSY            Device busy
0x05   NOT_STREAMING   Not currently streaming
0x06   INVALID_PARAM   Invalid parameter value
```

---

## Connection Flow

```
 Server                                Device
   |                                     |
   |-- SSDP M-SEARCH (multicast) ----->>|  1. Server broadcasts discovery
   |                                     |
   |<<----------- TCP connect -----------|  2. Device opens TCP to server:50000
   |                                     |
   |<<----------- CONFIG packet ---------|  3. Device sends JSON configuration
   |                                     |
   |------------ ACK ---------------->>  |  4. Server acknowledges config
   |                                     |
   |------------ TIMESYNC ----------->>  |  5. Server sends time reference
   |                                     |
   |<<----------- ACK ------------------|  6. Device acknowledges
   |                                     |
   |        (normal operation)           |
   |                                     |
   |------------ HEARTBEAT ---------->>  |  Periodic keep-alive (every 5s)
   |<<----------- ACK ------------------|
   |                                     |
   |------------ TIMESYNC ---------->>  |  Periodic resync (every 10 min)
   |<<----------- ACK ------------------|
   |                                     |
   |------------ STREAM_START ------->>  |  Start data streaming
   |<<----------- ACK ------------------|
   |<<----------- DATA (batched) -------|  Continuous sensor data
   |<<----------- DATA (batched) -------|
   |                                     |
   |------------ CONTROL ------------>>  |  Valve command
   |<<----------- ACK ------------------|
   |                                     |
   |------------ STREAM_STOP -------->>  |  Stop streaming
   |<<----------- ACK ------------------|
```

Key points:
- The server never connects to devices. Devices always initiate TCP.
- SSDP is broadcast-only (M-SEARCH). Devices hear it and connect back.
- The first packet on a new TCP connection is always CONFIG from the device.
- Sequence numbers in ACK/NACK match the sequence of the original request.
- Server sends TIMESYNC immediately after CONFIG ACK, then every 10 minutes.
- DATA packet timestamps come from the device clock, converted using the last sync offset.

---

## CONFIG JSON Structure

The CONFIG packet carries a JSON object describing the device's capabilities:

```
{
    "deviceName": "PropMonitor1",
    "deviceType": "Sensor Monitor",
    "sensorInfo": {
        "thermocouples": {
            "<name>": {
                "ADCIndex": <int>,
                "highPin": <int>,
                "lowPin": <int>,
                "type": "<string>",
                "units": "<string>"
            }
        },
        "pressureTransducers": {
            "<name>": {
                "ADCIndex": <int>,
                "pin": <int>,
                "maxPressure_PSI": <int>,
                "units": "<string>"
            }
        },
        "loadCells": {
            "<name>": {
                "ADCIndex": <int>,
                "pin": <int>,
                "units": "<string>"
            }
        }
    },
    "controls": {
        "<name>": {
            "pin": <int>,
            "type": "<string>",
            "defaultState": "<string>"
        }
    }
}
```

Sensor IDs in DATA packets correspond to the order sensors appear when iterating all sensor categories (thermocouples first, then pressure transducers, then load cells).

Control IDs in CONTROL packets correspond to the order controls appear in the `controls` object.

---

## Sequence Number Semantics

- Each side maintains its own wrapping 0-255 counter.
- Every packet sent increments the sender's counter.
- When responding with ACK/NACK, the `ack_sequence`/`nack_sequence` field contains the sequence number from the original request's header.
- This allows the receiver to match responses to requests when multiple are in flight.
