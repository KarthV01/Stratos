# Stratos

Stratos is a small distributed task system for learning and building the backend mechanics of reliable background work.

## Current architecture

- A FastAPI controller accepts tasks and exposes task state.
- Redis Streams provides durable task delivery and a shared consumer group.
- Three worker containers compete for tasks.
- Workers report queued, running, completed, and failed lifecycle events.
- Redis append-only persistence retains broker and task state across ordinary restarts.
- Mosquitto and the MQTT bridge connect constrained devices without exposing the Kubernetes API.
- The Stratos gateway accepts Full Agents over framed TCP or bidirectional gRPC and routes targeted work through Redis leases.

The task handler in `app/handler.py` is intentionally small. It is the boundary where real work—an agent invocation, document processor, automation step, or other long-running operation—can replace the demonstration delay.

## Run locally

```sh
docker compose up --build
```

The API is available at `http://localhost:8000`; interactive OpenAPI documentation is at `http://localhost:8000/docs`.
Full Agents connect to TCP port `7442` or gRPC port `7443` through the gateway.

Example task submission:

```sh
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"message":"hello","delay_seconds":2}'
```

List recent tasks:

```sh
curl http://localhost:8000/api/tasks
```

## Run on the local Kubernetes cluster

The development cluster has one control-plane node and three worker nodes. The
application also runs three worker pods, with one scheduled on each worker node.

```powershell
.\infra\minikube\install-minikube.ps1
.\infra\minikube\start.ps1
.\infra\minikube\access-api.ps1
```

Open `http://localhost:8000/docs`. Stop the port-forward with Ctrl+C. Stop the
cluster without deleting it with `.\infra\minikube\stop.ps1`.

Read [`docs/kubernetes.md`](docs/kubernetes.md) for the complete build and
request flow, and [`docs/esp32-integration.md`](docs/esp32-integration.md) before
connecting physical devices.

## Board identity protocol

Stratos classifies hardware into Full, Embedded, and Minimal Agent tiers. The
directory recognizes common MQTT-capable ESP32, STM32, Pico W, constrained
Arduino Wi-Fi, Raspberry Pi, Jetson, Linux SBC, x86, and accelerator boards.
See [`docs/board-identity-protocol.md`](docs/board-identity-protocol.md) for the
wire format, API, tier behavior, and catalog.

## MQTT devices

Tier 2 and network-capable Tier 3 devices use the compact SMP/1 protocol over
MQTT 3.1.1. The cluster bridge validates identities, records telemetry and
state, and delivers idempotent predefined commands through Redis Streams.

See [`docs/mqtt-device-protocol.md`](docs/mqtt-device-protocol.md) for the wire
contract and [`docs/mqtt-implementation-plan.md`](docs/mqtt-implementation-plan.md)
for architecture, rollout, and local-cluster connection details. Portable C99
firmware helpers are in [`firmware/smp1`](firmware/smp1).

The complete drone-to-ground payload matrix—including the Tier 1 HTTPS/JSON
agent protocol and normalized flight fields for all tiers—is documented in
[`docs/tier-payload-protocols.md`](docs/tier-payload-protocols.md).

Linux companion computers can terminate USB/CSI and RTSP/RTP camera feeds with
the local edge camera bridge. See
[`docs/edge-camera-bridge.md`](docs/edge-camera-bridge.md) for the runnable
service, supported source types, security boundary, and platform research.

## Mapping

Full agents can upload multi-drone survey photographs to the dedicated mapping
service, which submits one staged NodeODM job and publishes the resulting
orthophoto GeoTIFF. Embedded agents can contribute fixed-point geospatial sensor
samples that the gateway normalizes into map-ready GeoJSON.

See [`docs/mapping-backend.md`](docs/mapping-backend.md) for the survey API,
`SMP/1` geospatial frame, observation queries, and laptop deployment model.

## Full Agents

Tier 1 Linux and Windows agents use the Stratos Agent Gateway Protocol (SAGP/1)
over either framed TCP or a bidirectional gRPC stream. Both transports use the
same protobuf messages, Redis-backed task leases, heartbeats, capacity signaling,
and result validation. The reference Python client is `app/full_agent.py`.

See [`docs/full-agent-gateway.md`](docs/full-agent-gateway.md) for the wire
contract, client example, task API, TLS settings, deployment model, and protocol
design sources.

## Backend roadmap

This is not production-ready yet. Important next steps include idempotent effects, task timeouts, bounded retries, dead-letter handling, stale-message claiming, graceful worker shutdown, authentication and authorization, retention controls, metrics, and capacity planning.

Docker Compose and a local multi-node Kubernetes environment are configured.

The removed frontend's visual system is recorded in `docs/frontend-layout-reference.md` for future reuse without keeping frontend runtime code in this backend repository.
