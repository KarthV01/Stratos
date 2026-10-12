# Stratos Board Protocol (SBP/1)

SBP identifies a board model, assigns its Stratos agent tier, and tells the
device which connection mode to use. It deliberately separates **execution
capability** from **network connectivity**: two boards can share a tier while
one has Wi-Fi and the other requires a gateway.

## Agent tiers

| Tier | Hardware | Stratos functionality |
| --- | --- | --- |
| 1. Full Agent | Raspberry Pi, Jetson, x86 PCs, Linux SBCs | Task execution, local scheduling, networking, telemetry, complex workloads |
| 2. Embedded Agent | ESP32, STM32, RP2040 | Sensor processing, predefined tasks, lightweight communication |
| 3. Minimal Agent | Arduino Uno, ATtiny, extremely constrained MCUs | Very simple commands, sensor reporting, limited state |

Tier numbers are protocol identifiers, not benchmark scores. Tier 1 is the most
autonomous. A Tier 1 device can run a resident Stratos agent and communicate
directly over the network; it does not need MQTT. A Tier 2 device normally uses
MQTT when it has an IP interface, or a serial/radio gateway when it does not. A
Tier 3 device always uses a gateway so its firmware stays tiny.

## Canonical identifiers

Every known board has a stable lowercase identifier:

```text
<vendor>/<model>
```

Examples:

```text
espressif/esp32-s3-devkitc-1
raspberry-pi/5
nvidia/jetson-orin-nano
generic/x86-64-linux
```

The directory also contains friendly aliases such as `esp32-s3`, `rpi5`, and
`x86 pc`. Aliases are normalized only for case, punctuation, and whitespace.
There is no fuzzy matching because silently choosing the wrong hardware class is
worse than returning an unknown-board error.

The data lives in `app/board_directory.json`. Each record includes its canonical
ID, human name, vendor, tier, Linux and IP-network flags, optional accelerator,
aliases, and an official source URL. Add a new model by adding one record and a
unique alias; the loader rejects collisions at startup.

## Compact wire frame

Small firmware can identify itself with one ASCII line suitable for UART, BLE,
TCP, or an MQTT payload:

```text
SBP1|<board>|<device-id>|<firmware>\n
```

Example request (42 bytes):

```text
SBP1|esp32-s3|greenhouse-07|1.2.0
```

Successful response:

```text
SBP1|OK|espressif/esp32-s3-devkitc-1|2|embedded-agent|mqtt
```

Unknown response:

```text
SBP1|ERR|unknown-board
```

Frames are ASCII, newline-terminated, and limited to 202 bytes. Tokens permit
only letters, numbers, `.`, `_`, `:`, `/`, and `-`, preventing delimiter or
newline injection. `app/boards.py` contains the encoder and decoder.

## HTTP directory API

### List or filter hardware

```http
GET /api/boards
GET /api/boards?tier=1
GET /api/boards?vendor=Espressif
```

The response contains all three tier definitions and matching board records.

### Resolve a name or alias

```http
GET /api/boards/resolve?identifier=esp32-s3
```

### Identify a device

```http
POST /api/boards/identify
Content-Type: application/json

{
  "protocol": "sbp/1",
  "board": "esp32-s3",
  "device_id": "greenhouse-07",
  "firmware": "1.2.0"
}
```

The response supplies the canonical board, Tier 2 classification, allowed
functionality, and `mqtt` connection mode.

An unknown future board may report capabilities to receive a provisional tier:

```json
{
  "protocol": "sbp/1",
  "board": "future-linux-board",
  "device_id": "future-01",
  "capabilities": {
    "linux": true,
    "ram_mb": 4096,
    "accelerator": false,
    "ip_network": true
  }
}
```

Known-directory classifications are authoritative. Self-reported classifications
are marked `provisional: true` and must not grant trust or authorization.
Identity answers what hardware a device says it is; authentication must later
prove which physical device sent the message.

## Connection modes

| Mode | Selected when | Expected implementation |
| --- | --- | --- |
| `agent` | Tier 1 | Resident Linux/Windows Stratos service using HTTPS, WebSocket, or gRPC |
| `mqtt` | Tier 2 with an IP network | Small MQTT client executing a fixed command vocabulary |
| `gateway` | Tier 2 without IP, or any Tier 3 | UART, BLE, CAN, or radio link to a trusted gateway |

MQTT is therefore an implementation option for embedded hardware, not a
requirement for every board.

## Catalog coverage and sources

The initial directory contains 54 profiles across Arduino, Microchip ATtiny,
Espressif, Raspberry Pi/Pico, micro:bit, Teensy, STM32 Nucleo, BeagleBoard,
Orange Pi, Radxa, NVIDIA Jetson, Coral, AMD Kria, PYNQ, LattePanda, and UP Board,
plus generic x86-64 and ARM64 Linux identifiers.

Important official family references include:

- Espressif's SoC and development-board catalogs:
  <https://www.espressif.com/en/products/socs> and
  <https://docs.espressif.com/projects/esp-board-manager/en/latest/references/boards/index.html>
- Raspberry Pi computers and Pico documentation:
  <https://www.raspberrypi.com/products/>
- Arduino hardware documentation: <https://docs.arduino.cc/hardware/>
- STM32 Nucleo portfolio:
  <https://www.st.com/en/evaluation-tools/stm32-nucleo-boards.html>
- BeagleBoard hardware: <https://www.beagleboard.org/boards>
- NVIDIA Jetson lineup:
  <https://developer.nvidia.com/embedded/jetson-modules>

No finite registry can literally contain every board or clone. The aliases,
generic Linux identifiers, provisional capability path, and source-per-record
format keep the protocol usable as new hardware appears without changing SBP/1.
