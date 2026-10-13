# Stratos MQTT Device Protocol (SMP/1)

SMP/1 connects non-Linux Tier 2 and Tier 3 devices to Stratos through an MQTT
broker running beside the Kubernetes workloads. Devices do not connect to the
Kubernetes API. They connect to the broker, and the Stratos MQTT bridge moves
validated device traffic into the cluster's Redis-backed task system.

## Design constraints

- MQTT 3.1.1 is the compatibility baseline. MQTT 5 clients are allowed.
- Every application payload is ASCII. Tier 2 allows 256 bytes; Tier 3 allows
  128 bytes after its HELLO is accepted.
- A minimal implementation needs seven topics and four uplink frame types.
- No JSON parser, heap allocation, clock, or floating-point implementation is
  required on the device. Decimal telemetry may be constructed as text.
- Sequence numbers are unsigned 32-bit counters and may wrap to zero.
- Command IDs are idempotency keys. A device must remember recently completed
  IDs and acknowledge a redelivered command without repeating its side effect.

Boards without their own supported IP path are excluded. Tier 3 contains only
constrained boards that can maintain an MQTT/TCP connection themselves; SMP/1
does not use an application gateway to add networking to offline hardware.

## Topic namespace

The device ID is a 1-64 character provisioned identifier. MQTT wildcards and
slashes are forbidden.

| Direction | Topic | QoS | Retained | Purpose |
| --- | --- | ---: | --- | --- |
| up | `stratos/v1/devices/{id}/up/hello` | 1 | no | identify after every connection |
| up | `.../up/telemetry` | 0 | no | numeric sensor sample |
| up | `.../up/state` | 1 | no | state transition or current value |
| up | `.../up/event` | 1 | no | Tier 2 alert or fault event |
| up | `.../up/ack` | 1 | no | command lifecycle acknowledgement |
| down | `.../down/welcome` | 1 | no | accepted session and heartbeat interval |
| down | `.../down/command` | 1 | no | idempotent predefined command |
| down | `.../down/error` | 1 | no | protocol rejection |
| both | `.../availability` | 1 | yes | `online` or LWT value `offline` |

Each device principal may publish only to its own `up/#` and availability
topics, and subscribe only to its own `down/#`. The bridge is the inverse. Use
TLS and one credential or client certificate per device outside a trusted lab.

## Frame grammar

Frames have no newline terminator:

```text
SMP1|TYPE|SEQUENCE|FIELD...
```

Fields contain only letters, digits, `.`, `_`, `:`, `/`, `+`, and `-`, with a
maximum of 64 characters each. `|`, whitespace, wildcard characters, and empty
fields are invalid.

| Type | Channel | Fields | Example |
| --- | --- | --- | --- |
| `H` | hello | board, firmware, tier, capabilities | `SMP1|H|1|esp32-s3|1.2.0|2|temp+relay` |
| `T` | telemetry | sensor, decimal value, unit | `SMP1|T|2|temperature|21.75|C` |
| `S` | state | key, value | `SMP1|S|3|relay-1|on` |
| `A` | ack | command ID, status, code | `SMP1|A|4|cmd-8f31|completed|ok` |
| `W` | welcome | Unix time, heartbeat seconds | `SMP1|W|1|1791835200|30` |
| `C` | command | command ID, opcode, argument | `SMP1|C|2|cmd-8f31|relay.set|on` |
| `E` | error | code, detail token | `SMP1|E|3|bad-frame|field-count` |
| `D` | telemetry | 11 fixed-point flight fields | `SMP1|D|44|88000|407128001|-740060002|42500|120|-30|5|9120|22400|76|3` |
| `M` | telemetry | flags, battery %, link % | `SMP1|M|9|3|76|91` |
| `V` | event | code, severity, detail | `SMP1|V|10|gps.lost|critical|fix-dropped` |

ACK status is one of `accepted`, `running`, `completed`, `failed`, or
`rejected`. Tier 3 devices may send only the terminal statuses. Opcodes and
arguments are provisioned, device-specific tokens; arbitrary code and shell
commands are never transported.

## Session flow

1. Connect with a unique MQTT client ID, clean session disabled, and a retained
   LWT payload of `offline` on the availability topic.
2. Subscribe to all three downlink topics at QoS 1.
3. Publish retained `online`, then publish `H` at QoS 1.
4. The bridge validates identity/tier and returns `W` or `E`.
5. Publish telemetry at QoS 0. Publish state and acknowledgements at QoS 1.
6. On a command, deduplicate by command ID, execute a provisioned opcode, and
   publish at least one terminal ACK. Never execute unknown opcodes.
7. Publish `offline` before an orderly disconnect; otherwise the broker emits
   the LWT.

Tier 2 devices should send an `H` heartbeat at the negotiated interval. Tier 3
devices may use a longer provisioned interval to save power. The bridge treats
missed heartbeats as stale state; MQTT keepalive and LWT determine connectivity.

## Security and operational boundary

The public MQTT listener must not allow anonymous writes in production. Broker
ACLs provide device isolation; the bridge still validates the topic, frame,
declared tier, registered board, size, and command status. Kubernetes
NetworkPolicies should limit broker and Redis access. Secrets belong in a
secret manager or Kubernetes Secret, not firmware source or Git.

SMP/1 intentionally excludes firmware updates, bulk data, and dynamic code.
Those require a separately authenticated transfer protocol and hardware-specific
rollback strategy.
