# Embedded MAVLink companion adapter

This directory contains the receive-only C reference adapter used by Tier 2
and Tier 3 companions. The generated dialect is the official MAVLink C library
as a Git submodule at `c_library_v2`, pinned to
`8c63a49fe35aead7f9ef14031381c5e076ff743a`. It is a header-only library; this
adapter selects the official `common` dialect and limits the parser to one
UART/channel and one signing stream.

## Build and test

Compile as C99 and include both this directory and `c_library_v2`:

```text
cc -std=c99 -Ifirmware/mavlink -Ifirmware/mavlink/c_library_v2 \
  -Ifirmware/smp1 firmware/mavlink/stratos_mavlink.c \
  firmware/smp1/smp1.c your_uart_adapter.c
```

The host C tests are in `tests/c`. With CMake and a C compiler installed:

```text
cmake -S tests/c -B build/c-tests
cmake --build build/c-tests
ctest --test-dir build/c-tests --output-on-failure
```

The tests cover MAVLink 2 parsing, signed coordinates in SMP/1, the binding and
map frames, foreign-system filtering, unknown messages, bad CRC recovery,
truncated packets, explicit MAVLink 1 compatibility, signing verification, and
the parser-context RAM bound. The context must remain at or below 512 bytes;
the generated parser is configured for one RX buffer and one signing stream.

## Minimal application loop

Provision `vehicle_id` independently from the flight controller's 8-bit system
ID. Read bytes from exactly one passive UART and pass them to the parser. Do
not transmit MAVLink bytes on that UART.

```c
static stratos_mavlink_t flight;
static char frame[SMP1_MAX_FRAME_BYTES + 1u];
static uint32_t smp_sequence;

void telemetry_init(void) {
    stratos_mavlink_init(&flight, false); /* MAVLink 1 is opt-in. */
}

void on_flight_uart_byte(uint8_t byte) {
    (void)stratos_mavlink_feed(&flight, byte, monotonic_millis());
}

void publish_vehicle_binding(void) {
    int size = stratos_mavlink_encode_binding(
        &flight, "aircraft-17", smp_sequence++, frame, sizeof frame);
    if (size > 0) mqtt_publish("stratos/v1/devices/companion-17/up/state",
                               frame, (size_t)size, 1, false);
}

void publish_fleet_map(void) {
    int size = stratos_mavlink_encode_map(
        &flight, smp_sequence++, monotonic_millis(), frame, sizeof frame);
    if (size > 0) mqtt_publish("stratos/v1/devices/companion-17/up/telemetry",
                               frame, (size_t)size, 0, false);
}
```

Publish the binding after the parser identifies a PX4 or ArduPilot heartbeat,
and again after a parser/session reset. Publish Tier 2 map samples at 2 Hz and
Tier 3 at 1 Hz. The application owns MQTT reconnects, TLS, authentication,
availability/Last Will, and its rate scheduler. The bridge applies the same
rate limits and latest-value-wins policy. Use the same device ID that was
accepted in the SMP/1 `HELLO` frame.

## Board integration points

The parser is platform-neutral; only the byte source and MQTT client differ.
These are the intended integration points, not board pin assignments:

- **ESP32 / ESP-IDF:** configure one UART (commonly UART2), then pass each byte
  returned by `uart_read_bytes()` to `stratos_mavlink_feed()` with the current
  monotonic millisecond counter from a dedicated task. Use the ESP-IDF MQTT
  client for SMP/1.
- **Raspberry Pi Pico W:** configure one UART with the Pico SDK, drain bytes
  using `uart_is_readable()` / `uart_getc()`, and publish through the chosen
  Wi-Fi MQTT client.
- **Arduino Wi-Fi companion:** read from `Serial1` (or the board's second UART)
  and feed each byte. Keep the Wi-Fi/MQTT library's buffers outside the parser
  RAM budget. A board without a hardware UART needs a suitable external UART
  bridge; software serial is not guaranteed at flight-controller baud rates.

Connect companion RX to flight-controller TX, companion TX to flight-controller
RX only if the companion must transmit for a separately approved use, and share
ground. This adapter itself is receive-only, so the minimal wiring is flight
controller TX to companion RX plus common ground. Verify the flight controller
port's voltage before connecting: use 3.3 V UART logic, not 5 V logic. Select a
spare telemetry UART and set its baud/stream configuration in the flight-stack
configuration; do not assume USB serial and TELEM pins are interchangeable.

## Memory, messages, and signing

The adapter stores normalized state and a bounded latest status-text field; it
does not retain MAVLink packets or allocate dynamically. The public parser
context is compile-time checked at 512 bytes. Include the generated channel
buffer and call stack in a board's linker-map measurement; keep network/TLS
buffers out of that MAVLink budget. The host test's static bound is a regression
guard, not a substitute for checking the final firmware image.

The common telemetry subset covers heartbeat, global position, GPS health,
system and battery health, extended system state, mission progress, home and
system time, autopilot version, attitude, local position, status text, and
`HIGH_LATENCY2`. High-latency values are converted from their defined scaled
units; fields not present in that message remain unavailable instead of being
invented. MAVLink 1 is rejected by default and must be explicitly enabled for
compatibility.

When a 32-byte signing key is provisioned with
`stratos_mavlink_set_signing_key()`, the generated parser verifies incoming
signatures and rejects unsigned packets. Persist keys and signing timestamps
using platform secure storage; do not place a production key in source code.
Signing never enables outbound MAVLink messages.
