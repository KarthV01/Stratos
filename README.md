# Stratos distributed task starter

A deliberately small distributed task system you can extend into an agent runtime. It has:

- a FastAPI controller that accepts and reports tasks;
- Redis Streams as a durable message broker;
- three competing workers (each task goes to one worker);
- a live dashboard driven by server-sent events;
- Redis persistence through a Docker volume.

## Run it

```powershell
docker compose up --build
```

Open <http://localhost:8000>, submit several tasks, and watch the workers claim them. To see raw worker activity:

```powershell
docker compose logs -f worker-1 worker-2 worker-3
```

The optional distributed-systems learning lab is at <http://localhost:8000/lab>.
It uses three dedicated workers and `lab:*` Redis keys, so its failure experiments
cannot consume normal application tasks. The first two studies demonstrate:

- retention versus idempotency when an acknowledgement is lost;
- dead-worker recovery versus a live worker whose heartbeat is unreachable.

Choose **Broken baseline** to replay the preserved failure or **Your candidate**
to test the evolving implementation. The lab reports timelines, measurements,
and invariant results; it deliberately does not prescribe a repair.

To follow its worker processes:

```powershell
docker compose logs -f lab-worker-1 lab-worker-2 lab-worker-3
```

Stop the services with `docker compose down`. Add `-v` only when you intentionally want to delete the persisted broker/task data.

## API

Create a task:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/tasks `
  -ContentType application/json `
  -Body '{"message":"summarize this document","delay_seconds":2,"metadata":{"document_id":"demo"}}'
```

List recent tasks with `GET /api/tasks`; fetch one with `GET /api/tasks/{id}`. Interactive API docs are at <http://localhost:8000/docs>.

## Replace workers with agents

The queue lifecycle is isolated from the work itself. Replace `handle_task` in `app/handler.py` with an agent call and return a JSON-serializable result. The payload already includes a free-form `metadata` object, so you can add agent IDs, conversation IDs, tool permissions, or callback information without changing the broker.

This starter provides at-least-once delivery while a worker is alive. Before production use, add stale-message recovery (`XAUTOCLAIM`), retries/dead-letter handling, authentication, task timeouts, and task-data retention.

## Learning lab development

Broken strategies in `app/challenges/` are immutable after publication. When a
solution is proposed, change only its `CANDIDATE` mapping. This keeps the original
lesson replayable while the same deterministic invariants evaluate the new design.

Run the dependency-free model tests with:

```powershell
python -m unittest discover -v
```

Lab run summaries and event streams expire after 24 hours, only the latest 50 run
IDs are indexed, and each run's event stream is capped at approximately 2,000 entries.
