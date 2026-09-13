import asyncio
import json
import os
import time
import traceback
from typing import Any

from redis.asyncio import Redis

from app.broker import ensure_worker_group, redis_client
from app.config import EVENT_STREAM, TASK_STREAM, WORKER_GROUP
from app.handler import handle_task

WORKER_ID = os.getenv("WORKER_ID", "worker-local")


async def set_status(
    redis: Redis,
    task_id: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    values = {"status": status, "worker": WORKER_ID, "updated_at": str(time.time())}
    if result is not None:
        values["result"] = json.dumps(result)
    if error is not None:
        values["error"] = error
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(f"task:{task_id}", mapping=values)
        pipe.xadd(
            EVENT_STREAM,
            {"task_id": task_id, "status": status, "worker": WORKER_ID},
            maxlen=10_000,
            approximate=True,
        )
        await pipe.execute()


async def process(redis: Redis, entry_id: str, message: dict[str, str]) -> None:
    task_id = message["task_id"]
    try:
        await set_status(redis, task_id, "running")
        result = await handle_task(json.loads(message["payload"]), WORKER_ID)
        await set_status(redis, task_id, "completed", result=result)
        print(f"[{WORKER_ID}] completed {task_id}", flush=True)
    except Exception as exc:
        await set_status(redis, task_id, "failed", error=str(exc))
        traceback.print_exc()
    finally:
        await redis.xack(TASK_STREAM, WORKER_GROUP, entry_id)


async def main() -> None:
    redis = redis_client()
    await ensure_worker_group(redis)
    print(f"[{WORKER_ID}] waiting for tasks", flush=True)
    try:
        while True:
            streams = await redis.xreadgroup(
                WORKER_GROUP,
                WORKER_ID,
                {TASK_STREAM: ">"},
                count=1,
                block=5_000,
            )
            for _, entries in streams:
                for entry_id, message in entries:
                    await process(redis, entry_id, message)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())

