# Stratos drone-to-ground payload protocols

The companion board is the boundary between the flight stack and Stratos.
MAVLink may be used locally between the flight controller and companion board,
but MAVLink frames are not forwarded to the cluster. The companion board
normalizes the current vehicle state into one of the payloads below.

## What every tier sends

Every transport carries a provisioned agent ID, protocol version, monotonic
sequence, availability, hardware/firmware identity, command correlation ID,
and a bounded status payload. Coordinates and motion use fixed-point integers
where practical: latitude/longitude in degrees times 1e7, altitude in
millimeters, velocity in centimeters per second, heading in centidegrees, and
voltage/current in millivolts/milliamps.

This keeps the values lossless across MAVLink conversion and avoids floating
point work on microcontrollers.

## Tier 1 — Full Agent (`SAP/1`)

Transport: HTTPS with JSON; 64 KiB maximum per request. A full Linux companion
can validate JSON, batch sensors, spool while offline, and poll structured task
assignments.

Hello payload:

```json
{
  "protocol":"sap/1",
  "type":"agent.hello",
  "agent_id":"drone-07-companion",
  "sequence":1,
  "sent_at_ms":1700000000000,
  "board":"nvidia/jetson-orin-nano",
  "firmware":"2.4.1",
  "boot_id":"boot-a81f",
  "capabilities":["mission.execute","vision.detect","telemetry.batch"],
  "resources":{"cpu_cores":6,"memory_mb":8192,"storage_mb":65536,"gpu":"orin-ampere","max_concurrency":4}
}
```

Telemetry contains the complete normalized flight state, battery, network
quality, companion-computer load/temperature, mission progress, and up to 64
named sensor values. Events include severity, a machine-readable code, message,
and bounded structured context. Downlink commands contain an expiry, opcode,
arguments, and idempotent command ID; acknowledgements may carry a result.

## Tier 2 — Embedded Agent (`SMP/1` extended)

Transport: MQTT 3.1.1; 256-byte maximum payload. ESP32/STM32/RP2040-class
boards send compact ASCII that can be assembled without a JSON library.

The periodic drone snapshot is:

```text
SMP1|D|seq|uptime_ms|lat_e7|lon_e7|relative_alt_mm|vn_cms|ve_cms|vd_cms|heading_cdeg|voltage_mv|remaining_pct|flags
```

Example:

```text
SMP1|D|44|88000|407128001|-740060002|42500|120|-30|5|9120|22400|76|3
```

`flags` is a bitset: bit 0 armed, bit 1 position valid, bit 2 failsafe, bit 3
mission active, bit 4 companion degraded. Tier 2 may additionally send scalar
sensor (`T`), state (`S`), and event (`V`) frames. Commands remain predefined
opcodes with a compact argument token; arbitrary code is forbidden.

## Tier 3 — Minimal Agent (`SMP/1` minimal subset)

Transport: MQTT 3.1.1; 128-byte maximum after registration. A minimal board
does not send a full position/attitude snapshot or wall-clock time. Its required
periodic status is:

```text
SMP1|M|seq|flags|battery_pct|link_pct
```

Example: `SMP1|M|9|3|76|91` (19 bytes). It may also send one scalar sensor at a
time (`T`), one state key/value (`S`), and terminal command ACKs (`A`). Downlink
commands use the existing three-field `C` frame and must map to a compiled
allowlist such as `relay.set`, `buzzer.set`, or `failsafe.trigger`.

## Frequency and priority

| Data | Tier 1 | Tier 2 | Tier 3 |
| --- | --- | --- | --- |
| Availability/heartbeat | 10–30 s | 15–60 s | 30–300 s |
| Flight snapshot | 1–10 Hz, adaptive | 1–5 Hz | not sent |
| Essential status | included in snapshot | included in snapshot | 0.2–1 Hz |
| Events/alerts | immediate | immediate | encode as state/ACK |
| Bulk logs/images | object storage reference | unsupported | unsupported |

Safety events and command acknowledgements use QoS 1. High-rate snapshots and
scalar samples use QoS 0 because freshness matters more than retransmitting old
vehicle state. Devices reduce rate before buffering when the link degrades.

## Ground-server contract

All three tiers produce the same cluster concepts: current agent identity,
latest vehicle status, an append-only telemetry/event stream, and command
lifecycle records. The HTTP agent gateway handles Tier 1. Mosquitto plus the
MQTT bridge handles Tiers 2 and 3. Both paths validate tier limits before
writing Redis, allowing the controller and future ground UI to consume one
normalized view without knowing the onboard transport.
