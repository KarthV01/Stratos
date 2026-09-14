import asyncio
import json
import os
import time
from typing import Any

from redis.asyncio import Redis

from app.broker import redis_client
from app.lab_common import (
    LAB_GROUP,
    emit_event,
    ensure_lab_groups,
    metrics_key,
    run_key,
    worker_stream,
)

WORKER_ID = os.getenv("WORKER_ID", "lab-worker-local")
HEARTBEAT_INTERVAL = 0.5


class WorkerState:
    pause_heartbeat_until = 0.0
    state = "idle"
    run_id = ""
    task_id = ""
    generation = ""


STATE = WorkerState()


async def heartbeat(redis: Redis) -> None:
    key = f"lab:worker:{WORKER_ID}:state"
    while True:
        if time.time() >= STATE.pause_heartbeat_until:
            await redis.hset(
                key,
                mapping={
                    "worker_id": WORKER_ID,
                    "last_seen": str(time.time()),
                    "state": STATE.state,
                    "run_id": STATE.run_id,
                    "task_id": STATE.task_id,
                    "generation": STATE.generation,
                },
            )
            await redis.expire(key, 30)
        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def apply_retention_task(redis: Redis, message: dict[str, str]) -> None:
    run_id = message["run_id"]
    task_id = message["task_id"]
    strategy = json.loads(message["strategy"])
    receipt = f"lab:run:{run_id}:receipt:{task_id}"
    first_seen = await redis.set(
        receipt,
        WORKER_ID,
        nx=True,
        ex=int(strategy["receipt_ttl_seconds"]),
    )
    if first_seen:
        effect_count = await redis.hincrby(metrics_key(run_id), "effect_count", 1)
        await emit_event(
            redis,
            run_id,
            "effect",
            f"Ledger effect applied (count: {effect_count})",
            worker=WORKER_ID,
            detail={"task_id": task_id, "effect_count": effect_count},
        )
    else:
        await emit_event(
            redis,
            run_id,
            "duplicate-suppressed",
            "Completion receipt found; effect skipped",
            worker=WORKER_ID,
            detail={"task_id": task_id},
        )
    await redis.hset(
        run_key(run_id),
        mapping={"last_result": json.dumps({"task_id": task_id, "worker": WORKER_ID})},
    )
    await redis.hset(metrics_key(run_id), mapping={"completed": 1, "recent_result_visible": 1})
    if message.get("ack", "1") == "1":
        await redis.xack(worker_stream(WORKER_ID), LAB_GROUP, message["entry_id"])
        await emit_event(redis, run_id, "ack", "Broker entry acknowledged", worker=WORKER_ID)
    else:
        await emit_event(
            redis,
            run_id,
            "ack-lost",
            "Work finished; no acknowledgement became visible",
            worker=WORKER_ID,
        )


async def apply_quiet_task(redis: Redis, message: dict[str, str]) -> None:
    run_id = message["run_id"]
    task_id = message["task_id"]
    generation = int(message["generation"])
    strategy = json.loads(message["strategy"])
    delay = float(message.get("delay", "1"))
    if float(message.get("quiet_for", "0")):
        STATE.pause_heartbeat_until = time.time() + float(message["quiet_for"])
        await emit_event(
            redis,
            run_id,
            "heartbeat-blocked",
            "Heartbeat channel stopped responding; execution continues",
            worker=WORKER_ID,
        )
    await asyncio.sleep(delay)

    if await redis.exists(f"lab:run:{run_id}:cancelled"):
        await redis.xack(worker_stream(WORKER_ID), LAB_GROUP, message["entry_id"])
        await emit_event(redis, run_id, "drained", "Cancelled work stopped before commit", worker=WORKER_ID)
        return

    accepted = True
    if strategy.get("commit_policy") == "generation-fenced":
        owner_key = f"lab:run:{run_id}:owner:{task_id}"
        accepted = generation == int(await redis.get(owner_key) or 0)
    if accepted:
        metric = "dead_effect_count" if task_id == "dead-task" else "quiet_effect_count"
        if strategy.get("effect_policy") == "idempotent":
            first_effect = await redis.set(
                f"lab:run:{run_id}:effect:{task_id}", "1", nx=True, ex=86_400
            )
            effect_count = int(await redis.hget(metrics_key(run_id), metric) or 0)
            if first_effect:
                effect_count = await redis.hincrby(metrics_key(run_id), metric, 1)
        else:
            effect_count = await redis.hincrby(metrics_key(run_id), metric, 1)
        if task_id == "quiet-task":
            await redis.hset(metrics_key(run_id), "result_generation", generation)
        else:
            await redis.hset(metrics_key(run_id), "dead_task_completed", 1)
        await emit_event(
            redis,
            run_id,
            "commit",
            f"Result committed by ownership generation {generation}",
            worker=WORKER_ID,
            detail={"task_id": task_id, "generation": generation, "effect_count": effect_count},
        )
    else:
        await emit_event(
            redis,
            run_id,
            "commit-rejected",
            f"Ownership generation {generation} was no longer authoritative",
            worker=WORKER_ID,
            detail={"task_id": task_id, "generation": generation},
        )
    await redis.xack(worker_stream(WORKER_ID), LAB_GROUP, message["entry_id"])


async def process(redis: Redis, entry_id: str, message: dict[str, str]) -> None:
    run_id = message["run_id"]
    if await redis.exists(f"lab:run:{run_id}:cancelled"):
        await redis.xack(worker_stream(WORKER_ID), LAB_GROUP, entry_id)
        return

    message["entry_id"] = entry_id
    STATE.state = "working"
    STATE.run_id = run_id
    STATE.task_id = message.get("task_id", "")
    STATE.generation = message.get("generation", "")
    await emit_event(
        redis,
        run_id,
        "claimed",
        f"Claimed {STATE.task_id}",
        worker=WORKER_ID,
        detail={"generation": STATE.generation},
    )
    behavior = message["behavior"]
    if behavior == "retention":
        await apply_retention_task(redis, message)
    elif behavior == "quiet":
        await apply_quiet_task(redis, message)
    elif behavior == "crash":
        await emit_event(
            redis,
            run_id,
            "worker-crash",
            "Worker process stopped before acknowledging its task",
            worker=WORKER_ID,
            detail={"task_id": STATE.task_id},
        )
        await asyncio.sleep(0.15)
        os._exit(23)
    STATE.state = "idle"
    STATE.task_id = ""
    STATE.generation = ""


async def consume(redis: Redis) -> None:
    stream = worker_stream(WORKER_ID)
    while True:
        rows = await redis.xreadgroup(
            LAB_GROUP,
            WORKER_ID,
            {stream: ">"},
            count=1,
            block=2_000,
        )
        for _, entries in rows:
            for entry_id, message in entries:
                try:
                    await process(redis, entry_id, message)
                except Exception as exc:
                    await emit_event(
                        redis,
                        message.get("run_id", "unknown"),
                        "worker-error",
                        type(exc).__name__,
                        worker=WORKER_ID,
                    )
                    STATE.state = "idle"


async def main() -> None:
    redis = redis_client()
    await ensure_lab_groups(redis)
    print(f"[{WORKER_ID}] learning-lab worker ready", flush=True)
    try:
        await asyncio.gather(heartbeat(redis), consume(redis))
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
