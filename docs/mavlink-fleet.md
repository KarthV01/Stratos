# MAVLink fleet telemetry

The MAVLink integration is a passive telemetry path from flight controllers to
the existing Stratos fleet store. It does not route raw packets and it has no
flight-control, mission-write, parameter-write, or RC-override path.

## Data path

```text
Flight controller MAVLink 2
  +-- Tier 1 Linux companion -- HTTPS /api/vehicles/{id}/telemetry --+
  +-- Tier 2/3 MCU companion -- SMP/1 over MQTT -- MQTT bridge -----+
                                                                    +-- Redis fleet state, tracks, events, API
```

The configured `vehicle_id` is the fleet identity. MAVLink's 8-bit system ID
is only a link-level source identifier and can repeat across vehicles. A
companion must bind a vehicle before publishing embedded map samples. See
[`mqtt-device-protocol.md`](mqtt-device-protocol.md) for the exact `V` binding
and `M` map-frame fields.

Tier behavior is deliberately bounded:

| Tier | Companion role | MAVLink ingest | Stratos uplink |
| --- | --- | --- | --- |
| 1 Full Agent | Linux SBC, Jetson, or x86 host | Pymavlink over serial or UDP; normalized full snapshot | Native HTTPS, up to 5 Hz |
| 2 Embedded Agent | ESP32, STM32, RP2040 class | Fixed-buffer C parser over one UART | SMP/1 MQTT, map at 2 Hz |
| 3 Minimal Agent | MQTT-capable constrained MCU | Same parser, minimal compact map | SMP/1 MQTT, map at 1 Hz |

Tier 2 and Tier 3 must themselves have a supported network path and MQTT
client. An offline board is not treated as a gateway.

## Normalized messages

| MAVLink message | Fleet fields |
| --- | --- |
| `HEARTBEAT` | vehicle type, PX4/ArduPilot identity, armed/mode/status, liveness |
| `GLOBAL_POSITION_INT` | primary location, MSL/relative altitude, velocity, heading, boot time |
| `GPS_RAW_INT` | fix type, satellites, horizontal/vertical dilution |
| `SYS_STATUS`, `BATTERY_STATUS` | sensor masks, load, link-drop rate, battery voltage/current/remaining |
| `EXTENDED_SYS_STATE` | landed and VTOL state |
| `MISSION_CURRENT` | current mission sequence/state only; no mission contents |
| `HOME_POSITION`, `SYSTEM_TIME`, `AUTOPILOT_VERSION` | home, Unix time, capabilities/version/UID |
| `ATTITUDE`, `ATTITUDE_QUATERNION`, `LOCAL_POSITION_NED` | normalized local motion and attitude values |
| `STATUSTEXT` | bounded normalized status event |
| `HIGH_LATENCY2` | optional compact Tier 3 source; no GPS or landed fields are inferred |

Integer sentinel values become `null`/`na`, never synthetic zeros. The
`GLOBAL_POSITION_INT.time_boot_ms` stream is the restart clock: if it decreases,
the native agent increments its session and the embedded SMP/1 `M` frame carries
an incremented session ID. Unknown message IDs are ignored and counted. The
flight API marks a vehicle stale after five seconds without a fresh autopilot
heartbeat; embedded map samples explicitly carry the same five-second freshness
bit so MQTT keepalive cannot disguise a disconnected flight-controller UART.

## Tests and SITL

`tests/test_mavlink_udp.py` feeds three concurrent MAVLink 2 UDP endpoints,
including two vehicles reusing system ID 1, and verifies that each reader keeps
its provisioned fleet identity and autopilot type separate. The full suite also
tests malformed CRCs, truncation, unsupported messages, reboot timestamps,
signed frames, and SMP/1 frame limits. Run it with:

```text
python -m pytest -q
cmake -S tests/c -B build/c-tests
cmake --build build/c-tests
ctest --test-dir build/c-tests --output-on-failure
```

For a real PX4 smoke test, start the official headless SIH container and the
agent in separate terminals. Linux can use host networking; on Docker Desktop,
publish UDP 14550. In either case, enable PX4's `MAV_0_BROADCAST` parameter and
reboot so the MAVLink GCS stream is reachable outside the container.
`PX4_SIM_HOST_ADDR` controls the simulator/DDS target; it does not by itself
expose the MAVLink GCS link.

```text
# Terminal 1
docker run --rm -it -p 14550:14550/udp -e PX4_SIM_MODEL=sihsim_quadx px4io/px4-sitl:latest
# In the PX4 shell:
param set MAV_0_BROADCAST 1
param save
reboot

# Terminal 2
python -m app.mavlink.agent --connection udpin:0.0.0.0:14550 --vehicle-id px4-1 --device-id companion-1
```

For ArduPilot, use the official `sim_vehicle.py` from an ArduPilot checkout and
route its MAVLink stream to the listener. With the default MAVProxy GCS, use
`--out`; the official `--no-mavproxy`/`--serial0=udpclient:<host>:<port>` path
is also available for a direct UDP output. A mixed three-vehicle run needs a
distinct UDP port per source, for example:

```text
sim_vehicle.py -v ArduCopter --out=udp:<listener-host>:14551
sim_vehicle.py -v ArduCopter -I 1 --out=udp:<listener-host>:14552
python tests/sitl/validate_udp_fleet.py \
  --vehicle px4-1=udpin:0.0.0.0:14550 \
  --vehicle ardupilot-1=udpin:0.0.0.0:14551 \
  --vehicle ardupilot-2=udpin:0.0.0.0:14552
```

The harness requires at least one PX4 and one ArduPilot source and reports
per-vehicle message/version/position progress before the final result. System
IDs may repeat because the listener and `vehicle_id` are provisioned
separately.

SITL traffic is telemetry-only and the agent never transmits MAVLink. A live
flight controller connection uses a 3.3 V UART: cross TX/RX, share ground, and
verify the selected TELEM/UART port and baud rate in the flight-stack setup.
The ESP32, Pico W, and constrained Arduino integration points and RAM guard are
documented in [`firmware/mavlink/README.md`](../firmware/mavlink/README.md).

## Official references

- [MAVLink common message definitions](https://mavlink.io/en/messages/common.html)
- [MAVLink C library generation and integration](https://mavlink.io/en/mavgen_c/)
- [MAVLink message signing](https://mavlink.io/en/guide/message_signing.html)
- [PX4 SITL](https://docs.px4.io/main/en/simulation/index.html)
- [ArduPilot SITL setup](https://ardupilot.org/dev/docs/setting-up-sitl-on-linux.html)
- [ArduPilot UDP output from SITL](https://ardupilot.org/dev/docs/using-sitl-for-ardupilot-testing.html)
