# Unified Go gateway

`stratos-gateway` is the operational gateway for both agent classes. It exposes
SAGP/1 over framed TCP and bidirectional gRPC for Full Agents, and consumes SMP/1
over MQTT 3.1.1 for constrained agents. All adapters use the existing Redis
keys and streams, so the Python controller and workers require no changes.

```text
Full Agent ── SAGP/1 TCP or gRPC ──┐
                                   ├─ stratos-gateway ─ Redis
Constrained Agent ─ MQTT ─ Mosquitto┘
```

## Run

The default development stack builds the static Go image and starts the unified
gateway:

```console
docker compose up --build gateway
```

For a local build, run `go build ./cmd/stratos-gateway` from `gateway/`.

## Configuration

The binary retains the existing environment contract: `REDIS_URL`,
`GATEWAY_TCP_HOST`, `GATEWAY_TCP_PORT`, `GATEWAY_GRPC_HOST`,
`GATEWAY_GRPC_PORT`, `GATEWAY_AGENT_TOKEN`, `GATEWAY_HEARTBEAT_SECONDS`,
`GATEWAY_LEASE_TIMEOUT_MS`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`,
`MQTT_PASSWORD`, `MQTT_TLS`, and `MQTT_BRIDGE_ID`.

Set `GATEWAY_TLS_CERT` and `GATEWAY_TLS_KEY` to enable TLS on both SAGP
listeners. Setting `GATEWAY_TLS_CLIENT_CA` additionally requires and verifies a
client certificate. MQTT TLS uses the host trust store when `MQTT_TLS=true`.

## Compatibility and rollout

The Go implementation deliberately uses the same protobuf schema, four-byte
network-order TCP framing, SMP/1 topics and frames, consumer groups, and Redis
records as the Python processes. Do not run both MQTT bridges with the same
consumer identity. During a canary rollout, give each instance a unique
`MQTT_BRIDGE_ID` and expose only one set of SAGP listener ports.
