import json
import time
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

LAB_WORKERS = ("lab-worker-1", "lab-worker-2", "lab-worker-3")
LAB_GROUP = "lab-workers"


def worker_stream(worker_id: str) -> str:
    return f"lab:worker:{worker_id}:tasks"


def run_key(run_id: str) -> str:
    return f"lab:run:{run_id}"


def metrics_key(run_id: str) -> str:
    return f"lab:run:{run_id}:metrics"


def events_key(run_id: str) -> str:
    return f"lab:run:{run_id}:events"


async def ensure_lab_groups(redis: Redis) -> None:
    for worker_id in LAB_WORKERS:
        try:
            await redis.xgroup_create(worker_stream(worker_id), LAB_GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise


async def emit_event(
    redis: Redis,
    run_id: str,
    kind: str,
    message: str,
    *,
    worker: str = "controller",
    detail: dict[str, Any] | None = None,
) -> str:
    fields = {
        "run_id": run_id,
        "kind": kind,
        "worker": worker,
        "message": message,
        "at": str(time.time()),
        "detail": json.dumps(detail or {}),
    }
    event_id = await redis.xadd(events_key(run_id), fields, maxlen=2_000, approximate=True)
    await redis.expire(events_key(run_id), 86_400)
    return event_id
