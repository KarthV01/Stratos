# Stratos Kubernetes environment

## What was built

The local `stratos` Minikube profile contains four Kubernetes nodes:

- `stratos`: the control plane, which runs the Kubernetes API and controllers.
- `stratos-m02`, `stratos-m03`, and `stratos-m04`: worker nodes labeled
  `stratos.dev/worker=true`.

Each node is a Docker container with 2 virtual CPUs and 2 GiB of memory. The
cluster uses Kubernetes v1.37.0 and containerd. Minikube is for local development
only; it is not the eventual production or device-facing platform.

There are two meanings of "worker" in this project:

1. A **Kubernetes worker node** is a machine/container that hosts pods.
2. A **Stratos worker pod** is the Python process in `app/worker.py` that consumes
   tasks from Redis.

The cluster has three of each. Required pod anti-affinity and a topology-spread
rule place exactly one worker pod on each Kubernetes worker node. The rolling
update strategy removes an old worker before creating its replacement so this
rule remains satisfiable during deployments.

## How creation works

Run these commands from the repository root in PowerShell:

```powershell
.\infra\minikube\install-minikube.ps1
.\infra\minikube\start.ps1
```

`install-minikube.ps1` downloads the pinned Minikube v1.39.0 binary into the
ignored `.tools` directory and verifies its SHA-256 checksum. Pinning and
verification make the local tool repeatable and detect a corrupt download.

`start.ps1` performs the following sequence:

1. Verifies that Minikube, Docker, and `kubectl` are usable.
2. Creates one control plane plus three worker nodes using the Docker driver.
3. Labels the worker nodes. The label is the scheduling contract used by the
   worker Deployment.
4. Builds the repository's `Dockerfile` as `stratos:dev` and loads that image
   into every Minikube node.
5. Applies all resources in `k8s/kustomization.yaml`.
6. Restarts application Deployments so they use the freshly built development
   image, then waits for Redis, the controller, and all workers to roll out.

You can test a different local topology with three to five worker nodes:

```powershell
.\infra\minikube\start.ps1 -WorkerNodes 5
```

The application-worker replica count remains three until `replicas` in
`k8s/worker.yaml` is changed. Cluster node count and application replica count
are intentionally separate controls.

## Runtime architecture

The request path is:

```text
client/ESP32 -> controller Service -> controller pod -> Redis Stream
                                                        |
                                                        v
                                              one of three worker pods
```

- `controller` is a ClusterIP Service in front of the FastAPI controller pod.
- `redis` is a private ClusterIP Service used for service discovery at
  `redis://redis:6379/0`.
- The controller records the task and appends it to the `tasks` Redis Stream.
- All worker pods use one Redis consumer group. Redis gives each task to one
  worker, not all workers.
- `WORKER_ID` comes from the pod name, which makes task ownership visible.
- Controller and worker init containers wait for Redis before starting the Python
  processes, avoiding crash loops during a cold rollout.
- CPU/memory requests help Kubernetes place pods; limits keep a runaway test
  task from taking the whole cluster.
- Readiness probes keep traffic away from an unhealthy controller or Redis.

Redis uses `emptyDir` in this development cluster. Data survives a Redis process
restart but not deletion/replacement of its pod. This avoids pretending that
Minikube's default host-path storage is resilient across multiple nodes. Add a
real storage class and backups before treating the broker as durable.

## Useful operations

Show where everything is running:

```powershell
kubectl --context stratos get nodes -L stratos.dev/worker
kubectl --context stratos -n stratos get pods -o wide
kubectl --context stratos -n stratos get services
```

Access the API from this computer:

```powershell
.\infra\minikube\access-api.ps1
```

Then submit and inspect a task:

```powershell
$task = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/tasks `
  -ContentType application/json `
  -Body '{"message":"hello from Kubernetes","delay_seconds":2}'
Invoke-RestMethod -Uri "http://localhost:8000/api/tasks/$($task.id)"
```

Inspect logs and rollout state:

```powershell
kubectl --context stratos -n stratos logs deployment/controller
kubectl --context stratos -n stratos logs -l app.kubernetes.io/name=worker --prefix
kubectl --context stratos -n stratos rollout status deployment/worker
```

After code changes, rerun `start.ps1`. Minikube reuses the cluster, rebuilds and
loads the image, reapplies the manifests, restarts the application pods, and
waits for readiness.

Pause the cluster while preserving it:

```powershell
.\infra\minikube\stop.ps1
```

Permanently delete the cluster and its data:

```powershell
.\infra\minikube\delete.ps1
```

## Files to read, in order

1. `infra/minikube/start.ps1` — cluster topology, node labels, image build, and
   deployment sequence.
2. `k8s/kustomization.yaml` — entry point listing everything Kubernetes applies.
3. `k8s/worker.yaml` — replicas, node placement, pod identity, and resources.
4. `k8s/controller.yaml` — HTTP service, health probes, and Redis configuration.
5. `k8s/redis.yaml` — stream broker, internal Service, and development storage.
6. `Dockerfile` — how one image supports both controller and worker commands.
7. `app/controller.py` — HTTP-to-Redis task creation and status APIs.
8. `app/worker.py` — consumer-group processing and lifecycle updates.
9. `app/broker.py` — Redis client and idempotent consumer-group creation.
10. `docs/esp32-integration.md` — safe network boundary for physical devices.
