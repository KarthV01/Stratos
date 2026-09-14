import asyncio
import json
import time
from typing import Any, AsyncIterator, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from redis.asyncio import Redis

from app.lab_common import (
    LAB_GROUP,
    LAB_WORKERS,
    emit_event,
    ensure_lab_groups,
    events_key,
    metrics_key,
    run_key,
    worker_stream,
)
from app.lab_models import (
    CHALLENGES,
    get_challenge,
    quiet_worker_invariants,
    retention_invariants,
    strategy_for,
)

router = APIRouter(prefix="/api/lab", tags=["learning lab"])
ACTIVE_RUN_KEY = "lab:active-run"
RUN_HISTORY_KEY = "lab:run-history"
RUN_TTL = 86_400
RUN_TASKS: dict[str, asyncio.Task[None]] = {}
RUN_DELIVERIES: dict[str, list[tuple[str, str]]] = {}


class RunRequest(BaseModel):
    challenge_id: str
    implementation: Literal["baseline", "candidate"] = "baseline"


async def set_run_fields(redis: Redis, run_id: str, **fields: Any) -> None:
    encoded = {
        key: json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
        for key, value in fields.items()
    }
    await redis.hset(run_key(run_id), mapping=encoded)
    await redis.expire(run_key(run_id), RUN_TTL)


def decode_hash(data: dict[str, str]) -> dict[str, Any]:
    decoded: dict[str, Any] = dict(data)
    for field in ("metrics", "invariants", "last_result"):
        if field in decoded:
            try:
                decoded[field] = json.loads(decoded[field])
            except json.JSONDecodeError:
                pass
    for field in ("started_at", "finished_at"):
        if field in decoded:
            decoded[field] = float(decoded[field])
    return decoded


async def read_metrics(redis: Redis, run_id: str) -> dict[str, int]:
    raw = await redis.hgetall(metrics_key(run_id))
    result: dict[str, int] = {}
    for key, value in raw.items():
        try:
            result[key] = int(value)
        except ValueError:
            result[key] = 0
    return result


async def pending_count_for(
    redis: Redis, deliveries: list[tuple[str, str]]
) -> int:
    """Count only pending entries created by this run, not earlier experiments."""
    total = 0
    for worker_id, entry_id in deliveries:
        entries = await redis.xpending_range(
            worker_stream(worker_id), LAB_GROUP, entry_id, entry_id, 1
        )
        if entries and entries[0]["message_id"] == entry_id:
            total += 1
    return total


async def checkpoint(redis: Redis, run_id: str) -> None:
    if await redis.exists(f"lab:run:{run_id}:cancelled"):
        raise asyncio.CancelledError


async def workers_snapshot(redis: Redis) -> list[dict[str, Any]]:
    now = time.time()
    workers = []
    for worker_id in LAB_WORKERS:
        state = await redis.hgetall(f"lab:worker:{worker_id}:state")
        last_seen = float(state.get("last_seen", "0"))
        age = max(0.0, now - last_seen) if last_seen else None
        workers.append(
            {
                "worker_id": worker_id,
                "state": state.get("state", "offline"),
                "task_id": state.get("task_id", ""),
                "generation": state.get("generation", ""),
                "heartbeat_age": round(age, 1) if age is not None else None,
                "communicating": age is not None and age < 1.75,
            }
        )
    return workers


async def run_snapshot(redis: Redis, run_id: str) -> dict[str, Any]:
    data = await redis.hgetall(run_key(run_id))
    if not data:
        raise HTTPException(status_code=404, detail="Lab run not found")
    snapshot = decode_hash(data)
    snapshot["metrics"] = await read_metrics(redis, run_id)
    snapshot["workers"] = await workers_snapshot(redis)
    return snapshot


async def enqueue(
    redis: Redis,
    worker_id: str,
    run_id: str,
    task_id: str,
    behavior: str,
    **fields: Any,
) -> str:
    payload = {
        "run_id": run_id,
        "task_id": task_id,
        "behavior": behavior,
        **{key: str(value) for key, value in fields.items()},
    }
    entry_id = await redis.xadd(
        worker_stream(worker_id), payload, maxlen=5_000, approximate=True
    )
    RUN_DELIVERIES.setdefault(run_id, []).append((worker_id, entry_id))
    return entry_id


async def run_retention(redis: Redis, run_id: str, implementation: str) -> None:
    strategy = strategy_for("vanishing-receipt", implementation)
    await set_run_fields(redis, run_id, phase="Seeding old records")
    retained_key = f"lab:run:{run_id}:retained"
    seed_count = 2_500
    await redis.hset(retained_key, mapping={f"old-{index}": "terminal" for index in range(seed_count)})
    await redis.expire(retained_key, RUN_TTL)
    retention_limit = int(strategy.get("retention_limit", seed_count))
    if retention_limit < seed_count:
        await redis.hdel(retained_key, *[f"old-{index}" for index in range(retention_limit, seed_count)])
    retained = await redis.hlen(retained_key)
    await redis.hset(metrics_key(run_id), mapping={"retained_records": retained})
    await emit_event(
        redis,
        run_id,
        "retention-scan",
        f"Found {retained} expired records after cleanup",
        detail={"retained_records": retained, "budget": 500},
    )

    await set_run_fields(redis, run_id, phase="Processing original delivery")
    strategy_json = json.dumps(strategy)
    original_entry = await enqueue(
        redis,
        "lab-worker-1",
        run_id,
        "ledger-credit-1042",
        "retention",
        strategy=strategy_json,
        ack=0,
    )
    await asyncio.sleep(3.25)
    await checkpoint(redis, run_id)
    await emit_event(
        redis,
        run_id,
        "retry-window",
        "The unacknowledged task became eligible for recovery",
        detail={"elapsed_seconds": 3.25},
    )
    await set_run_fields(redis, run_id, phase="Redelivering pending work")
    await enqueue(
        redis,
        "lab-worker-2",
        run_id,
        "ledger-credit-1042",
        "retention",
        strategy=strategy_json,
        ack=1,
    )
    await asyncio.sleep(1.25)
    await checkpoint(redis, run_id)
    if strategy.get("resolve_recovered_pending"):
        await redis.xack(worker_stream("lab-worker-1"), LAB_GROUP, original_entry)
        await emit_event(redis, run_id, "pending-resolved", "Recovered source delivery acknowledged")
    metrics = await read_metrics(redis, run_id)
    metrics["pending_count"] = await pending_count_for(
        redis, [("lab-worker-1", original_entry)]
    )
    await redis.hset(metrics_key(run_id), mapping=metrics)
    await redis.expire(metrics_key(run_id), RUN_TTL)
    invariants = retention_invariants(metrics)
    await finish_run(redis, run_id, metrics, invariants)


async def run_quiet_worker(redis: Redis, run_id: str, implementation: str) -> None:
    strategy = strategy_for("quiet-worker", implementation)
    strategy_json = json.dumps(strategy)
    await set_run_fields(redis, run_id, phase="Watching a quiet owner")
    await redis.set(f"lab:run:{run_id}:owner:quiet-task", "1", ex=RUN_TTL)
    quiet_original = await enqueue(
        redis,
        "lab-worker-1",
        run_id,
        "quiet-task",
        "quiet",
        strategy=strategy_json,
        generation=1,
        delay=6,
        quiet_for=6,
    )
    await asyncio.sleep(2.25)
    await checkpoint(redis, run_id)
    await emit_event(
        redis,
        run_id,
        "suspected",
        "Heartbeat deadline elapsed; worker marked unavailable",
        worker="lab-worker-1",
        detail={"deadline_seconds": 2},
    )
    await set_run_fields(redis, run_id, phase="Reassigning silent work")
    await redis.set(f"lab:run:{run_id}:owner:quiet-task", "2", ex=RUN_TTL)
    await enqueue(
        redis,
        "lab-worker-2",
        run_id,
        "quiet-task",
        "quiet",
        strategy=strategy_json,
        generation=2,
        delay=1,
        quiet_for=0,
    )
    await asyncio.sleep(1.5)
    await checkpoint(redis, run_id)

    await set_run_fields(redis, run_id, phase="Testing a genuine crash")
    await redis.set(f"lab:run:{run_id}:owner:dead-task", "1", ex=RUN_TTL)
    dead_original = await enqueue(
        redis,
        "lab-worker-3",
        run_id,
        "dead-task",
        "crash",
        strategy=strategy_json,
        generation=1,
    )
    await asyncio.sleep(2.25)
    await checkpoint(redis, run_id)
    await emit_event(
        redis,
        run_id,
        "recovery",
        "Dead worker task reassigned after the same deadline",
        worker="controller",
    )
    await redis.set(f"lab:run:{run_id}:owner:dead-task", "2", ex=RUN_TTL)
    await enqueue(
        redis,
        "lab-worker-2",
        run_id,
        "dead-task",
        "quiet",
        strategy=strategy_json,
        generation=2,
        delay=1,
        quiet_for=0,
    )
    await asyncio.sleep(1.5)
    await checkpoint(redis, run_id)
    if strategy.get("resolve_recovered_pending"):
        await redis.xack(worker_stream("lab-worker-1"), LAB_GROUP, quiet_original)
        await redis.xack(worker_stream("lab-worker-3"), LAB_GROUP, dead_original)
        await emit_event(redis, run_id, "pending-resolved", "Recovered source deliveries acknowledged")
    metrics = await read_metrics(redis, run_id)
    metrics["pending_count"] = await pending_count_for(
        redis,
        [
            ("lab-worker-1", quiet_original),
            ("lab-worker-3", dead_original),
        ],
    )
    await redis.hset(metrics_key(run_id), mapping=metrics)
    await redis.expire(metrics_key(run_id), RUN_TTL)
    invariants = quiet_worker_invariants(metrics)
    await finish_run(redis, run_id, metrics, invariants)


async def finish_run(
    redis: Redis,
    run_id: str,
    metrics: dict[str, int],
    invariants: list[dict[str, Any]],
) -> None:
    passed = all(item["passed"] for item in invariants)
    await set_run_fields(
        redis,
        run_id,
        status="passed" if passed else "failed",
        phase="Experiment complete",
        finished_at=time.time(),
        metrics=metrics,
        invariants=invariants,
    )
    await emit_event(
        redis,
        run_id,
        "evaluation",
        "All invariants held" if passed else "One or more invariants did not hold",
        detail={"passed": passed},
    )


async def execute_run(redis: Redis, run_id: str, challenge_id: str, implementation: str) -> None:
    try:
        if challenge_id == "vanishing-receipt":
            await run_retention(redis, run_id, implementation)
        else:
            await run_quiet_worker(redis, run_id, implementation)
    except asyncio.CancelledError:
        await set_run_fields(redis, run_id, status="cancelled", phase="Experiment cancelled", finished_at=time.time())
        await emit_event(redis, run_id, "cancelled", "Experiment cancelled")
        raise
    except Exception as exc:
        await set_run_fields(redis, run_id, status="error", phase="Runtime error", finished_at=time.time())
        await emit_event(redis, run_id, "runtime-error", type(exc).__name__)
    finally:
        if await redis.exists(f"lab:run:{run_id}:cancelled"):
            for worker_id, entry_id in RUN_DELIVERIES.get(run_id, []):
                await redis.xack(worker_stream(worker_id), LAB_GROUP, entry_id)
        if await redis.get(ACTIVE_RUN_KEY) == run_id:
            await redis.delete(ACTIVE_RUN_KEY)
        RUN_DELIVERIES.pop(run_id, None)
        RUN_TASKS.pop(run_id, None)


@router.get("/challenges")
async def list_challenges(request: Request) -> list[dict[str, Any]]:
    redis: Redis = request.app.state.redis
    run_ids = await redis.lrange(RUN_HISTORY_KEY, 0, 49)
    records: list[dict[str, str]] = []
    if run_ids:
        async with redis.pipeline(transaction=False) as pipe:
            for run_id in run_ids:
                pipe.hgetall(run_key(run_id))
            records = await pipe.execute()
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        challenge_id = record.get("challenge_id")
        if challenge_id and challenge_id not in latest:
            latest[challenge_id] = {
                "run_id": record.get("id"),
                "status": record.get("status"),
                "implementation": record.get("implementation"),
                "finished_at": float(record["finished_at"])
                if record.get("finished_at")
                else None,
            }
    return [
        {**challenge, "latest_result": latest.get(challenge["id"])}
        for challenge in CHALLENGES
    ]


@router.post("/runs", status_code=202)
async def start_run(body: RunRequest, request: Request) -> dict[str, Any]:
    redis: Redis = request.app.state.redis
    challenge = get_challenge(body.challenge_id)
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if not challenge["playable"]:
        raise HTTPException(status_code=409, detail="This challenge is not playable yet")
    run_id = str(uuid4())
    acquired = await redis.set(ACTIVE_RUN_KEY, run_id, nx=True, ex=120)
    if not acquired:
        active_run = await redis.get(ACTIVE_RUN_KEY)
        raise HTTPException(status_code=409, detail={"message": "Another lab experiment is active", "run_id": active_run})
    await ensure_lab_groups(redis)
    await set_run_fields(
        redis,
        run_id,
        id=run_id,
        challenge_id=body.challenge_id,
        challenge_title=challenge["title"],
        implementation=body.implementation,
        status="running",
        phase="Preparing experiment",
        started_at=time.time(),
        invariants=[],
    )
    await redis.lpush(RUN_HISTORY_KEY, run_id)
    await redis.ltrim(RUN_HISTORY_KEY, 0, 49)
    await emit_event(redis, run_id, "run-started", f"Started {body.implementation} experiment")
    task = asyncio.create_task(execute_run(redis, run_id, body.challenge_id, body.implementation))
    RUN_TASKS[run_id] = task
    return await run_snapshot(redis, run_id)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    return await run_snapshot(request.app.state.redis, run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request) -> dict[str, str]:
    redis: Redis = request.app.state.redis
    if not await redis.exists(run_key(run_id)):
        raise HTTPException(status_code=404, detail="Lab run not found")
    await redis.set(f"lab:run:{run_id}:cancelled", "1", ex=RUN_TTL)
    await set_run_fields(redis, run_id, phase="Draining active worker")
    return {"status": "draining"}


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    after: str = Query(default="0-0"),
) -> StreamingResponse:
    redis: Redis = request.app.state.redis
    if not await redis.exists(run_key(run_id)):
        raise HTTPException(status_code=404, detail="Lab run not found")
    last_event_id = request.headers.get("last-event-id", after)

    async def stream() -> AsyncIterator[str]:
        cursor = last_event_id
        while not await request.is_disconnected():
            rows = await redis.xread({events_key(run_id): cursor}, block=10_000, count=100)
            if not rows:
                yield ": keepalive\n\n"
                continue
            for _, entries in rows:
                for event_id, event in entries:
                    cursor = event_id
                    event["detail"] = json.loads(event.get("detail", "{}"))
                    yield f"id: {event_id}\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
