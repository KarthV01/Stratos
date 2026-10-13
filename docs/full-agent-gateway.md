# Stratos Full Agent gateway (SAGP/1)

SAGP/1 connects Tier 1 Full Agents to the Stratos gateway. Full Agents never
connect to Redis or the Kubernetes API. The controller records targeted tasks in
Redis, a gateway session leases those tasks to an agent, and the agent returns a
lease-bound terminal result.

The gateway package now contains all three protocol adapters:

- `app.stratos_gateway.mqtt` exposes the existing SMP/1 adapter for Embedded and
  Minimal Agents.
- `app.stratos_gateway.tcp` exposes SAGP/1 as protobuf messages framed on a raw
  TCP stream.
- `app.stratos_gateway.grpc_service` exposes the same SAGP/1 messages through a
  bidirectional gRPC stream.

## Connection flow

1. The first agent message must be `AgentHello`. It identifies the protocol,
   provisioned agent ID, known Tier 1 board, client version, capabilities, token,
   and maximum task concurrency.
2. The gateway validates the board tier and optional shared token, records the
   active session in Redis, then replies with `Welcome`.
3. The agent sends heartbeats with its running task IDs and available slots.
4. The gateway delivers `TaskAssignment` messages only while a slot is available.
5. The agent returns `TaskResult` with the original task ID and Redis lease ID.
   The gateway rejects results from another agent or lease before acknowledging
   and completing the task.
6. An unacknowledged lease can be reclaimed after `GATEWAY_LEASE_TIMEOUT_MS`, so
   disconnecting after delivery does not permanently strand a task.

The protobuf source is `proto/stratos/gateway/v1/agent.proto`. Regenerate the
Python bindings with:

```sh
python -m grpc_tools.protoc \
  -Iproto/stratos/gateway/v1 \
  --python_out=app/stratos_gateway/generated \
  --pyi_out=app/stratos_gateway/generated \
  --grpc_python_out=app/stratos_gateway/generated \
  proto/stratos/gateway/v1/agent.proto
```

The generated gRPC module must use a package-relative import for `agent_pb2`.

## TCP transport

TCP uses a four-byte unsigned big-endian payload length followed by one serialized
protobuf message. Empty messages and frames larger than 1 MiB are rejected. TCP
does not preserve application message boundaries, so consumers must read the
complete prefix and then exactly the declared payload length.

Default development endpoint: `localhost:7442`.

## gRPC transport

The `FullAgentGateway.Connect` RPC is bidirectional streaming. Each direction is
ordered independently, allowing the agent to send heartbeats and results while
the gateway sends assignments and acknowledgements on the same long-lived RPC.
Both send and receive message sizes are limited to 1 MiB.

Default development endpoint: `localhost:7443`.

## Queue work for an agent

After an agent has registered at least once, submit a targeted task through the
control plane:

```sh
curl -X POST http://localhost:8000/api/agents/linux-01/tasks \
  -H "Content-Type: application/json" \
  -d '{"message":"inventory disks","delay_seconds":0}'
```

Use `GET /api/agents` for presence and `GET /api/tasks/{task_id}` for task state.
Offline agents may have work queued for their next session.

## Reference client

```python
import asyncio

from app.full_agent import FullAgent, FullAgentConfig


async def handle(payload):
    return {"received": payload["message"]}


agent = FullAgent(
    FullAgentConfig(
        agent_id="linux-01",
        board="raspberry-pi/5",
        agent_version="1.0.0",
        host="gateway.example.net",
        port=7443,
        transport="grpc",  # or "tcp"
        token="provisioned-secret",
        ca_cert="ca.pem",
    ),
    handle,
)
asyncio.run(agent.run_forever())
```

## Security and configuration

Compose and Minikube use plaintext listeners for local development. Do not expose
them to an untrusted network. Production deployments should set all of
`GATEWAY_TLS_CERT`, `GATEWAY_TLS_KEY`, and optionally `GATEWAY_TLS_CLIENT_CA` for
mutual TLS. `GATEWAY_AGENT_TOKEN` enables an additional provisioned shared-token
check; tokens are compared in constant time and are never persisted.

Both listeners use the same certificate settings. Clients set `ca_cert`, plus
`client_cert` and `client_key` when mutual TLS is enabled. Other settings are:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `GATEWAY_TCP_PORT` | `7442` | Raw TCP listener |
| `GATEWAY_GRPC_PORT` | `7443` | gRPC listener |
| `GATEWAY_HEARTBEAT_SECONDS` | `20` | Heartbeat interval sent in `Welcome` |
| `GATEWAY_LEASE_TIMEOUT_MS` | `30000` | Idle time before lease reclamation |

## Design sources

- The official gRPC Python tutorial defines bidirectional streaming and notes
  that ordering is preserved within each direction:
  <https://grpc.io/docs/languages/python/basics/>
- The gRPC AsyncIO API supports asynchronous iterables for stream-stream RPCs:
  <https://grpc.github.io/grpc/python/grpc_asyncio.html>
- Python's `asyncio` streams provide `start_server` and `readexactly`, used by the
  bounded TCP framing implementation:
  <https://docs.python.org/3/library/asyncio-stream.html>
- Protocol Buffers documents length-delimited encoding and recommends bounding
  serialized message sizes:
  <https://protobuf.dev/programming-guides/encoding/>
- gRPC recommends TLS for server authentication and encrypted transport, with
  optional mutual client authentication:
  <https://grpc.io/docs/guides/auth/>
