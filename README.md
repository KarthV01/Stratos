# Stratos

Stratos is a small distributed task system for learning and building the backend mechanics of reliable background work.

## Current architecture

- A FastAPI controller accepts tasks and exposes task state.
- Redis Streams provides durable task delivery and a shared consumer group.
- Three worker containers compete for tasks.
- Workers report queued, running, completed, and failed lifecycle events.
- Redis append-only persistence retains broker and task state across ordinary restarts.

The task handler in `app/handler.py` is intentionally small. It is the boundary where real work—an agent invocation, document processor, automation step, or other long-running operation—can replace the demonstration delay.

## Run locally

```sh
docker compose up --build
```

The API is available at `http://localhost:8000`; interactive OpenAPI documentation is at `http://localhost:8000/docs`.

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

## Backend roadmap

This is not production-ready yet. Important next steps include idempotent effects, task timeouts, bounded retries, dead-letter handling, stale-message claiming, graceful worker shutdown, authentication and authorization, retention controls, metrics, and capacity planning.

Docker Compose and a local multi-node Kubernetes environment are configured.

The removed frontend's visual system is recorded in `docs/frontend-layout-reference.md` for future reuse without keeping frontend runtime code in this backend repository.
