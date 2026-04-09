# QRET Propulsion Binary Protocol v1.0 - Wire Format Specification

This document is the authoritative wire-format reference for the QRET Propulsion binary protocol. It is intended to be sufficient for implementing both the server (Python) and device (MicroPython on ESP32) sides of the protocol. All values are big-endian (network byte order). All sizes are in bytes.

---

## Network Configuration

### SSDP Discovery

The server announces its presence via SSDP multicast. Devices listen for this broadcast to discover the server.

- Multicast address: `239.255.255.250`
- Multicast port: `1900`
- Search target: `urn:qretprop:espdevice:1`

The server sends the following M-SEARCH packet:

```
M-SEARCH * HTTP/1.1\r\n
HOST: 239.255.255.250:1900\r\n
MAN: "ssdp:discover"\r\n
MX: 2\r\n
ST: urn:qretprop:espdevice:1\r\n
USER-AGENT: QRET/1.0\r\n
\r\n
```

When a device receives this packet, it extracts the server's IP address from the UDP source address of the M-SEARCH packet. It then opens a TCP connection to the server.

### TCP

- Server listen port: `50000`
- The server never connects to devices. Devices always initiate TCP connections to the server.

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

The TIMESTAMP field on device-originated packets must be the device's `time.ticks_ms()` (milliseconds since boot). This is used by the server for accurate inter-sample timing.

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
| TIMESYNC       | 0x02  |
| STREAM_STOP    | 0x06  |
| GET_SINGLE     | 0x07  |
| HEARTBEAT      | 0x08  |
| STATUS_REQUEST | 0x04  |

```
[Header 9B]
```

---

### STATUS (11 + 2*N bytes, variable)

Device status response and valve/control states. LENGTH = 11 + 2*N, where N is the number of valves/controls.

```
Offset  Size  Type    Field   Description
------  ----  ------  ------  -------------------------
0-8     9     -       header  Standard header
9       1     uint8   status  DeviceStatus enum value
10      1     uint8   count   Number of valves/controls (N)

Repeated N times (2 bytes each):
+0      1     uint8   command_id  Index in device's control array
+1      1     uint8   command_state ControlState enum value
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

### TIMESYNC (9 bytes, header-only)

Time synchronization from server. LENGTH = 9. No payload — the header's TIMESTAMP field carries the server's monotonic milliseconds, which is all the device needs.

```
[Header 9B]
```

The server sends TIMESYNC immediately after acknowledging the device's CONFIG, and then periodically every 10 minutes during normal operation.

**Device behavior**: When the device receives TIMESYNC, it must:

1. Compute a timestamp offset using the TIMESYNC packet's **header timestamp** (the server's monotonic ms):
   ```
   offset = timesync_header.timestamp - time.ticks_ms()
   ```
2. Store this offset.
3. For **all subsequent outgoing packets**, set the header timestamp to:
   ```
   timestamp = (time.ticks_ms() + offset) & 0xFFFFFFFF
   ```
4. ACK the TIMESYNC.

After this, every packet the device sends has its timestamp locked to the server's time scale. The server can use device header timestamps directly — no server-side conversion is needed.

**Why this matters**: By locking device timestamps to the server's clock, the server gets inter-sample timing derived from the device's crystal oscillator rather than from network receive times. This eliminates jitter from TCP buffering, OS scheduling, and network latency. The device's oscillator provides consistent, microsecond-resolution intervals between readings.

**Why periodic resync**: ESP32 crystal oscillators drift approximately 20 ppm. Over 10 minutes this is ~12 ms; over 1 hour it is ~72 ms. At high sample rates (hundreds of Hz), where one sample period is 5-10 ms, this drift becomes significant during long test runs. The server automatically sends a new TIMESYNC every 10 minutes. The device recomputes its offset on each TIMESYNC, keeping drift under ~12 ms.

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
| TIMESYNC       | 9                | (none)               |
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

## Device Behavior Requirements

This section defines what the device must do when it receives each server command.

### Packets Requiring ACK

The device **must** send an ACK for the following packet types:

| Packet Type  | Required Response |
|--------------|-------------------|
| CONTROL      | ACK (or NACK on error) |
| TIMESYNC     | ACK |
| STREAM_START | ACK (or NACK on error) |
| STREAM_STOP  | ACK |
| HEARTBEAT    | ACK |

STATUS_REQUEST and GET_SINGLE do not require ACK — the device responds with a STATUS or DATA packet instead.

### ESTOP

On receiving ESTOP, the device must immediately set **all controls to their default states** as defined in the device's configuration (the `default_state` field of each control). This is the safest state for the hardware. The device should also stop streaming if active.

ESTOP does not require an ACK. The server assumes immediate compliance.

### GET_SINGLE

On receiving GET_SINGLE, the device must take **one reading from every sensor** and send a single batched DATA packet containing all readings.

### STATUS_REQUEST

On receiving STATUS_REQUEST, the device must send a STATUS packet with its current DeviceStatus.

### Unknown Packet Types

If the device receives a packet with an unrecognized PACKET_TYPE, it must respond with a NACK using error code `UNKNOWN_TYPE` (0x01).

---

## Connection Flow

```
 Server                                Device
   |                                     |
   |-- SSDP M-SEARCH (multicast) ----->>|  1. Server broadcasts on 239.255.255.250:1900
   |                                     |
   |<<----------- TCP connect -----------|  2. Device opens TCP to server:50000
   |                                     |
   |<<----------- CONFIG packet ---------|  3. Device sends JSON configuration
   |                                     |
   |------------ ACK ---------------->>  |  4. Server acknowledges config
   |                                     |
   |------------ TIMESYNC ----------->>  |  5. Server sends time reference
   |                                     |
   |<<----------- ACK ------------------|  6. Device acknowledges (server records sync)
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
   |<<----------- DATA (batched) -------|  Continuous sensor data at requested Hz
   |<<----------- DATA (batched) -------|
   |                                     |
   |------------ CONTROL ------------>>  |  Valve/actuator command
   |<<----------- ACK ------------------|
   |                                     |
   |------------ STREAM_STOP -------->>  |  Stop streaming
   |<<----------- ACK ------------------|
   |                                     |
   |------------ ESTOP ------------->>  |  Emergency stop (no ACK required)
   |                                     |  Device sets all controls to defaults
```

Key points:
- The server never connects to devices. Devices always initiate TCP.
- SSDP is broadcast-only (M-SEARCH). Devices hear it and connect back to the server IP from the UDP source address.
- The first packet on a new TCP connection is always CONFIG from the device.
- Sequence numbers in ACK/NACK match the sequence of the original request.
- Server sends TIMESYNC immediately after CONFIG ACK, then every 10 minutes.
- DATA packet timestamps come from the device clock (`time.ticks_ms()`), converted by the server using the last sync offset.

---

## CONFIG JSON Structure

The CONFIG packet carries a JSON object describing the device's capabilities. The server uses this to register sensors and controls.

### Schema

```json
{
    "device_name": "<string>",
    "device_type": "Sensor Monitor",

    "sensor_info": {
        "thermocouple": {
            "<name>": {
                "sensor_index": "<string>",
                "type": "<string>",
                "units": "<string>"
            }
        },
        "pressure_transducer": {
            "<name>": {
                "sensor_index": "<string>",
                "resistor_ohms": "<float>",
                "max_pressure_PSI": "<int>",
                "unit": "<string>"
            }
        },
        "load_cell": {
            "<name>": {
                "sensor_index": "<string>",
                "load_rating_N": "<float>",
                "excitation_V": "<float>",
                "sensitivity_vV": "<float>",
                "unit": "<string>"
            }
        },
        "resistance_sensor" : {
            "<name>" : {
                "sensor_index": "<string>",
                "injected_current_uA": "<int>",
                "r_short": "<float>",
                "unit" : "<string>"
            }
        },
        "current_sensor" : {
            "ignCurrent" : {
                "sensor_index": "<string>",
                "shunt_resistor_ohms" : "<float>",
                "csa_gain" : "<int>",
                "unit" : "<string>"
            }
        }
    },
    "controls": {
        "<name>": {
            "control_index": "<string>",
            "type": "<string>",
            "default_state": "<string>"
        }
    }
}
```

### Example (PANDA-V3)

```json
{
    "device_name": "PANDA-V3",
    "device_type": "Sensor Monitor",

    "sensor_info": {
        "thermocouple": {
            "TCRun": {
                "sensor_index": "TC1",
                "type" : "K",
                "unit" : "C"
            },
            "TCCombustionChamber": {
                "sensor_index": "TC2",
                "type" : "K",
                "unit" : "C"
            }
        },

        "pressure_transducer": {
            "PTRun": {
                "sensor_index": "PT1",
                "resistor_ohms": 250,
                "max_pressure_PSI" : 1000,
                "unit" : "PSI"
            },
            "PTCombustionChamber": {
                "sensor_index": "PT2",
                "resistor_ohms": 250,
                "max_pressure_PSI" : 1000,
                "unit" : "PSI"
            },
            "PTPreInjector": {
                "sensor_index": "PT3",
                "resistor_ohms": 250,
                "max_pressure_PSI" : 1000,
                "unit" : "PSI"
            },
            "PTN2OSupply": {
                "sensor_index": "PT4",
                "resistor_ohms": 250,
                "max_pressure_PSI" : 1000,
                "unit" : "PSI"
            },
            "PTN2Supply": {
                "sensor_index": "PT5",
                "resistor_ohms": 250,
                "max_pressure_PSI" : 200,
                "unit" : "PSI"
            }
        },

        "load_cell": {
            "LCFill": {
                "sensor_index": "LC_FILL",
                "load_rating_N" : 1962,
                "excitation_V" : 5,
                "sensitivity_vV" : 2,
                "unit" : "kg"
            },
            "LCThrust": {
                "sensor_index": "LC_THRUST",
                "load_rating_N" : 5000,
                "excitation_V" : 5,
                "sensitivity_vV" : 2,
                "unit" : "kg"
            }
        },

        "resistance_sensor" : {
            "ignResistance" : {
                "sensor_index": "IGN_RESIST_READ",
                "injected_current_uA": 1500,
                "r_short": 47.91259768,
                "unit" : "ohms"
            }
        },

        "current_sensor" : {
            "ignCurrent" : {
                "sensor_index": "IGN_CURRENT_READ",
                "shunt_resistor_ohms" : 0.025,
                "csa_gain" : 20,
                "unit" : "A"
            }
        }
    },

    "controls": {
        "AVN2OFill": {
            "control_index": "AV_FILL",
            "default_state": "CLOSED",
            "type": "solenoid"
        },
        "AVRun": {
            "control_index": "AV_RUN",
            "default_state": "CLOSED",
            "type": "solenoid"
        },
        "AVVent": {
            "control_index": "AV3",
            "default_state": "OPEN",
            "type": "solenoid"
        },
        "AVN2Fill": {
            "control_index": "AV4",
            "default_state": "OPEN",
            "type": "solenoid"
        },
        "AVPurge": {
            "control_index": "AV5",
            "default_state": "OPEN",
            "type": "solenoid"
        },
        "AVDump": {
            "control_index": "AV6",
            "default_state" : "OPEN",
            "type": "solenoid"
        },
        "Safe24": {
            "control_index": "SAFE_24V_CTL",
            "default_state" : "OPEN",
            "type" : "relay"
        },
        "IgnPrime": {
            "control_index": "IGNITOR_PRIME_CTL",
            "default_state" : "OPEN",
            "type" : "relay"
        },
        "IgnRun": {
            "control_index": "IGNITOR_RUN_CTL",
            "default_state" : "OPEN",
            "type" : "relay"
        }
    }
}
```

### Sensor and Control ID Mapping

Sensor IDs in DATA packets correspond to the order sensors appear when iterating all sensor categories: **thermocouples first, then pressure transducers, then load cells**.

In the PANDA-V3 example above (0 thermocouples, 5 pressure transducers, 2 load cells):

| sensor_id | Name                 | Type                |
|-----------|----------------------|---------------------|
| 0         | PTCombustionChamber  | pressureTransducer  |
| 1         | PTN2OSupply          | pressureTransducer  |
| 2         | PTN2Supply           | pressureTransducer  |
| 3         | PTPreInjector        | pressureTransducer  |
| 4         | PTRun                | pressureTransducer  |
| 5         | LCFill               | loadCell            |
| 6         | LCThrust             | loadCell            |

Control IDs in CONTROL packets correspond to the order controls appear in the `controls` object:

| command_id | Name      |
|------------|-----------|
| 0          | AVFill    |
| 1          | AVRun     |
| 2          | AVDump    |
| 3          | AVPurge1  |
| 4          | AVPurge2  |
| 5          | AVVent    |
| 6          | Safe24    |
| 7          | IgnPrime  |
| 8          | Ign       |

---

## Sequence Number Semantics

- Each side maintains its own wrapping 0-255 counter.
- Every packet sent increments the sender's counter.
- When responding with ACK/NACK, the `ack_sequence`/`nack_sequence` field contains the sequence number from the original request's header.
- This allows the receiver to match responses to requests when multiple are in flight.
