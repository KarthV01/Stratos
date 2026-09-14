import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.broker import ensure_worker_group, redis_client
from app.config import EVENT_STREAM, TASK_INDEX, TASK_STREAM
from app.lab_runtime import router as lab_router

BASE_DIR = Path(__file__).resolve().parent.parent


class TaskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)
    delay_seconds: float = Field(default=2, ge=0, le=30)
    metadata: dict[str, Any] = Field(default_factory=dict)


def task_key(task_id: str) -> str:
    return f"task:{task_id}"


def decode_task(data: dict[str, str]) -> dict[str, Any]:
    task: dict[str, Any] = dict(data)
    for field in ("payload", "result"):
        if field in task:
            task[field] = json.loads(task[field])
    for field in ("created_at", "updated_at"):
        if field in task:
            task[field] = float(task[field])
    return task


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = redis_client()
    await ensure_worker_group(redis)
    app.state.redis = redis
    yield
    await redis.aclose()


app = FastAPI(title="Distributed Task Starter", lifespan=lifespan)
app.include_router(lab_router)


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/lab", include_in_schema=False)
async def learning_lab() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "lab.html")


@app.get("/lab.css", include_in_schema=False)
async def learning_lab_css() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "lab.css", media_type="text/css")


@app.get("/lab.js", include_in_schema=False)
async def learning_lab_js() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "lab.js", media_type="text/javascript")


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    await request.app.state.redis.ping()
    return {"status": "ok"}


@app.post("/api/tasks", status_code=202)
async def create_task(body: TaskRequest, request: Request) -> dict[str, Any]:
    redis: Redis = request.app.state.redis
    task_id = str(uuid4())
    now = time.time()
    payload = body.model_dump()
    record = {
        "id": task_id,
        "status": "queued",
        "payload": json.dumps(payload),
        "created_at": str(now),
        "updated_at": str(now),
    }
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(task_key(task_id), mapping=record)
        pipe.zadd(TASK_INDEX, {task_id: now})
        pipe.xadd(TASK_STREAM, {"task_id": task_id, "payload": record["payload"]})
        pipe.xadd(EVENT_STREAM, {"task_id": task_id, "status": "queued", "worker": ""})
        await pipe.execute()
    return decode_task(record)


@app.get("/api/tasks")
async def list_tasks(
    request: Request, limit: int = Query(default=50, ge=1, le=200)
) -> list[dict[str, Any]]:
    redis: Redis = request.app.state.redis
    ids = await redis.zrevrange(TASK_INDEX, 0, limit - 1)
    if not ids:
        return []
    async with redis.pipeline(transaction=False) as pipe:
        for task_id in ids:
            pipe.hgetall(task_key(task_id))
        records = await pipe.execute()
    return [decode_task(record) for record in records if record]


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    record = await request.app.state.redis.hgetall(task_key(task_id))
    if not record:
        raise HTTPException(status_code=404, detail="Task not found")
    return decode_task(record)


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    redis: Redis = request.app.state.redis

    async def stream() -> AsyncIterator[str]:
        cursor = "$"
        while not await request.is_disconnected():
            rows = await redis.xread({EVENT_STREAM: cursor}, block=15_000, count=20)
            if not rows:
                yield ": keepalive\n\n"
                continue
            for _, entries in rows:
                for event_id, event in entries:
                    cursor = event_id
                    yield f"data: {json.dumps(event)}\n\n"
            await asyncio.sleep(0)

    return StreamingResponse(stream(), media_type="text/event-stream")
