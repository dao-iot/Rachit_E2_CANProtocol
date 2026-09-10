# CAN Message Specification (DBC)

This document is the human-readable reference for every CAN message used in this
project. The `message_specs` dictionary in `phase2_parser/can_parser.py` is the
machine-readable version of the exact same information — this file explains it
in plain language, and in the real Vector DBC format.

## 1. Frame format

This project uses the standard 11-bit CAN identifier format:

| Field | Size | Used in this project? |
|---|---|---|
| Identifier | 11 bits | Yes — the message ID (e.g. `0x101`) |
| RTR | 1 bit | No — simulated software frames only |
| IDE | 1 bit | No |
| DLC | 4 bits | Yes — number of data bytes (1 or 2) |
| Data | 0–8 bytes | Yes — the actual sensor value |
| CRC / ACK / EOF | 24 bits | No — handled by real CAN hardware, not simulated |

## 2. Decode formula

Every signal in this project is decoded the same way:

```
real_value = raw_value × scale + offset
```

`raw_value` is the plain integer stored in the data bytes. `scale` and `offset`
come from the table below. Offset is always `0` in this project — every signal
here only needed scaling, not shifting.

**Worked example (Motor RPM):**
Data bytes `[0x14, 0x28]`, big-endian →
`raw_value = (0x14 << 8) | 0x28 = 5160` → `scale = 1` → `real_value = 5160 RPM`

**Worked example (Battery Voltage, scaled):**
Data bytes `[0x02, 0x71]`, big-endian →
`raw_value = (0x02 << 8) | 0x71 = 625` → `scale = 0.1` → `real_value = 62.5V`

## 3. Signal table

| CAN ID | Message name | DLC | Byte order | Scale | Offset | Unit | Valid range |
|---|---|---|---|---|---|---|---|
| `0x101` | Motor_RPM | 2 bytes | Big-endian | 1 | 0 | RPM | 0 – 10,000 |
| `0x102` | Vehicle_Speed | 2 bytes | Big-endian | 0.1 | 0 | km/h | 0 – 120 |
| `0x103` | Battery_SOC | 1 byte | Big-endian | 1 | 0 | % | 0 – 100 |
| `0x104` | Battery_Voltage | 2 bytes | Big-endian | 0.1 | 0 | V | 0 – 100 |
| `0x105` | Motor_Temperature | 1 byte | Big-endian | 1 | 0 | °C | 0 – 150 |

Values outside their valid range are flagged with a warning by the Parser
(`extract_signal()` in `can_parser.py`) rather than silently accepted — see
Test Case 3 in the Testing section of this repo for a worked example
(`0x103` with data `[0xFF]` → SOC = 255%, out of range).

## 4. Vector DBC format (industry-standard syntax)

The same specification, written in the actual `.dbc` format used by real
automotive tools (Vector CANoe, `cantools`, etc.):

```
BO_ 257 Motor_RPM: 2 ECU
 SG_ RPM : 0|16@1+ (1,0) [0|10000] "RPM" Cluster

BO_ 258 Vehicle_Speed: 2 ECU
 SG_ Speed : 0|16@1+ (0.1,0) [0|120] "km/h" Cluster

BO_ 259 Battery_SOC: 1 ECU
 SG_ SOC : 0|8@1+ (1,0) [0|100] "%" Cluster

BO_ 260 Battery_Voltage: 2 ECU
 SG_ Voltage : 0|16@1+ (0.1,0) [0|100] "V" Cluster

BO_ 261 Motor_Temperature: 1 ECU
 SG_ Temperature : 0|8@1+ (1,0) [0|150] "°C" Cluster
```

**Reading `0|16@1+`:** start bit `0`, length `16` bits, byte order `1`
(big-endian), sign `+` (unsigned). **Reading `(1,0)`:** scale factor `1`,
offset `0`. **Reading `[0|10000]`:** valid range, minimum to maximum.

## 5. Message priority / send rate

Not part of the DBC signal definitions themselves, but documented here since
it affects real-time behavior: messages are sent at different rates based on
how fast each value changes in reality.

| Message | Send interval | Priority |
|---|---|---|
| Motor_RPM, Vehicle_Speed | 100 ms | High |
| Battery_Voltage | 500 ms | Medium |
| Battery_SOC, Motor_Temperature | 1000 ms | Low |

## 6. Simulator's operating bounds (separate from the spec above)

The signal table in Section 3 defines the *legal* range the Parser must
validate against — the contract any sender on the bus must honor. This
project's Simulator (`phase1_simulator/can_simulator.py`) deliberately
operates within a **tighter, physically-derived subset** of that range, not
the full legal range:

| Signal | Simulator's actual max | Derived from |
|---|---|---|
| Motor RPM | 5,500 | `99 km/h ÷ (0.3m wheel circumference × 0.018 km/h per RPM)` |
| Vehicle Speed | 99 km/h | Fixed top speed requirement |
| Motor Temperature | 135°C | Warms toward 120°C+ under full load, capped |
| Battery Voltage | 48–64V | Empty-pack to full-pack voltage range |

This is intentional realism, not a bug — see `can_simulator.py`'s constants
section for the full derivation.
