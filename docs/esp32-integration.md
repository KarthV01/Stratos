# ESP32 integration boundary

An ESP32 should not become a Kubernetes worker node. Kubernetes nodes run a full
Linux operating system, kubelet, and a container runtime; an ESP32 is a
microcontroller. Treat it as a device client at the edge of the system.

## Recommended connection

The first implementation can use HTTPS:

1. The ESP32 joins Wi-Fi and authenticates to a device-facing gateway.
2. It sends a small request containing its device ID, reading/command, sequence
   number, and firmware version.
3. The gateway validates and normalizes the message, then creates a Stratos task.
4. A worker processes the task and stores or publishes the result.
5. The ESP32 polls for a result, or receives it through MQTT in a later phase.

MQTT is often a better eventual fit for intermittent devices and
publish/subscribe telemetry, but it needs a broker, per-device credentials,
topic authorization, retained-message policy, and delivery/idempotency design.
Those components are deliberately not faked by the current HTTP task API.

## Local hardware test

Windows cannot normally route a LAN device directly to a Minikube node created
with the Docker driver. Forward the controller Service through the host instead:

```powershell
.\infra\minikube\access-api.ps1 -Lan
```

The ESP32 can then call `http://<computer-lan-ip>:8000`. This is a temporary lab
path only. It exposes an unauthenticated HTTP endpoint and may require a narrowly
scoped Windows Firewall rule for TCP port 8000. Do not use it on an untrusted
network.

## Requirements before real devices

Before field or production use, add:

- TLS with certificate validation on the ESP32.
- A unique credential and revocation path for every device.
- Authentication and authorization at a gateway, not in worker pods.
- A stable external address through a real Ingress or LoadBalancer.
- Request size/rate limits and schema validation.
- A device-generated message ID for idempotent retries.
- Timeouts, bounded retries, dead-letter handling, and stale-task recovery.
- Persistent broker/database storage, backups, and observability.
- An over-the-air firmware update and key-rotation plan.

The current controller accepts up to 10,000 characters and a maximum simulated
delay of 30 seconds. Those are application validation limits, not a security or
device protocol design.
