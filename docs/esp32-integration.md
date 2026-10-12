# Board integration boundary

Board behavior is selected by the three-tier Stratos Board Protocol documented
in `docs/board-identity-protocol.md`. ESP32 devices are Tier 2 Embedded Agents;
Linux SBCs and PCs are Tier 1 Full Agents; constrained MQTT-capable devices such
as ESP8266 and small Arduino Wi-Fi boards are Tier 3 Minimal Agents.

An ESP32 should not become a Kubernetes worker node. Kubernetes nodes run a full
Linux operating system, kubelet, and a container runtime; an ESP32 is a
microcontroller. A Raspberry Pi, Jetson, x86 PC, or another Linux SBC can instead
run a resident Stratos agent directly without using MQTT.

## Recommended connection

The embedded runtime path uses MQTT:

1. The ESP32 joins Wi-Fi and authenticates to the MQTT broker.
2. It publishes a small message containing its device ID, reading/command,
   sequence number, and firmware version.
3. The MQTT ingress validates and normalizes the message, then creates a Stratos
   task.
4. A worker processes the task and stores or publishes the result.
5. The ESP32 receives commands and results through its authorized MQTT topics.

MQTT needs a broker, per-device credentials, topic authorization,
retained-message policy, and delivery/idempotency design. Tier 1 devices use
direct agent communication; both Tier 2 and Tier 3 use MQTT.

## Local hardware test

After the MQTT implementation branch is merged, expose the broker on a host
address reachable from the lab Wi-Fi network and configure the ESP32 with that
host, its assigned device ID, and its per-device credential. Use TCP 1883 only
on an isolated test network; use MQTT over TLS on 8883 outside that lab setup.
Do not point embedded boards at the controller's HTTP API.

## Requirements before real devices

Before field or production use, add:

- TLS with certificate validation on the ESP32.
- A unique credential and revocation path for every device.
- Authentication and authorization at the MQTT broker/ingress, not in workers.
- A stable external address through a real Ingress or LoadBalancer.
- Request size/rate limits and schema validation.
- A device-generated message ID for idempotent retries.
- Timeouts, bounded retries, dead-letter handling, and stale-task recovery.
- Persistent broker/database storage, backups, and observability.
- An over-the-air firmware update and key-rotation plan.

The current controller accepts up to 10,000 characters and a maximum simulated
delay of 30 seconds. Those are application validation limits, not a security or
device protocol design.
