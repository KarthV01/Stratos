import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.broker import ensure_worker_group, redis_client
from app.boards import (
    PROTOCOL_VERSION,
    board_directory,
    classify_capabilities,
    normalize_identifier,
    resolve_board,
    tier_catalog,
)
from app.config import (
    DEVICE_COMMAND_INDEX,
    DEVICE_COMMAND_STREAM,
    DEVICE_INDEX,
    EVENT_STREAM,
    TASK_INDEX,
    TASK_STREAM,
)
from app.mqtt_bridge import device_command_key, device_key, device_state_key
from app.mqtt_protocol import PROTOCOL_NAME as MQTT_PROTOCOL_NAME
from app.mqtt_protocol import validate_device_id, validate_token


class TaskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)
    delay_seconds: float = Field(default=2, ge=0, le=30)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReportedBoardCapabilities(BaseModel):
    linux: bool = False
    ram_kb: int = Field(default=0, ge=0, le=1_073_741_824)
    accelerator: bool = False
    ip_network: bool = False


class BoardIdentityRequest(BaseModel):
    protocol: Literal["sbp/1"] = PROTOCOL_VERSION
    board: str = Field(min_length=1, max_length=100)
    device_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    firmware: str = Field(default="-", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")
    capabilities: ReportedBoardCapabilities | None = None


class DeviceCommandRequest(BaseModel):
    opcode: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,63}$")
    argument: str = Field(default="-", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,63}$")


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


app = FastAPI(title="Stratos Task API", lifespan=lifespan)


@app.get("/", include_in_schema=False)
async def service_info() -> dict[str, str]:
    return {
        "service": "stratos",
        "health": "/health",
        "tasks": "/api/tasks",
        "boards": "/api/boards",
        "devices": "/api/devices",
        "docs": "/docs",
    }


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    await request.app.state.redis.ping()
    return {"status": "ok"}


@app.get("/api/boards")
async def list_boards(
    tier: int | None = Query(default=None, ge=1, le=3),
    vendor: str | None = Query(default=None),
) -> dict[str, Any]:
    profiles = board_directory()
    if tier is not None:
        profiles = tuple(board for board in profiles if int(board.agent_tier) == tier)
    if vendor is not None:
        profiles = tuple(
            board for board in profiles if board.vendor.casefold() == vendor.casefold()
        )
    return {
        "protocol": PROTOCOL_VERSION,
        "tiers": tier_catalog(),
        "boards": [board.as_dict() for board in profiles],
    }


@app.get("/api/boards/resolve")
async def get_board(identifier: str = Query(min_length=1, max_length=100)) -> dict[str, Any]:
    profile = resolve_board(identifier)
    if profile is None:
        raise HTTPException(status_code=404, detail="Unknown board identifier")
    return {"protocol": PROTOCOL_VERSION, "board": profile.as_dict()}


@app.post("/api/boards/identify")
async def identify_board(body: BoardIdentityRequest) -> dict[str, Any]:
    profile = resolve_board(body.board)
    if profile is not None:
        return {
            "protocol": PROTOCOL_VERSION,
            "device_id": body.device_id,
            "firmware": body.firmware,
            "provisional": False,
            "board": profile.as_dict(),
        }

    if body.capabilities is None:
        raise HTTPException(status_code=404, detail="Unknown board; report capabilities")

    capabilities = body.capabilities
    try:
        agent_tier = classify_capabilities(
            linux=capabilities.linux,
            ram_kb=capabilities.ram_kb,
            accelerator=capabilities.accelerator,
            ip_network=capabilities.ip_network,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    tier_details = next(
        item for item in tier_catalog() if item["tier"] == int(agent_tier)
    )
    connection_mode = "agent" if capabilities.linux else "mqtt"
    return {
        "protocol": PROTOCOL_VERSION,
        "device_id": body.device_id,
        "firmware": body.firmware,
        "provisional": True,
        "board": {
            "id": f"reported/{normalize_identifier(body.board)}",
            "name": body.board,
            "vendor": "reported",
            "tier": int(agent_tier),
            "tier_code": tier_details["code"],
            "functionality": tier_details["functionality"],
            "linux": capabilities.linux,
            "ip_network": capabilities.ip_network,
            "accelerator": "reported" if capabilities.accelerator else None,
            "connection_mode": connection_mode,
            "aliases": [],
            "source": None,
        },
    }


@app.get("/api/devices")
async def list_devices(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> list[dict[str, Any]]:
    redis: Redis = request.app.state.redis
    ids = await redis.zrevrange(DEVICE_INDEX, 0, limit - 1)
    if not ids:
        return []
    async with redis.pipeline(transaction=False) as pipe:
        for device_id in ids:
            pipe.hgetall(device_key(device_id))
        devices = await pipe.execute()
    return [device for device in devices if device]


@app.get("/api/devices/{device_id}")
async def get_device(device_id: str, request: Request) -> dict[str, Any]:
    try:
        validate_device_id(device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    redis: Redis = request.app.state.redis
    device = await redis.hgetall(device_key(device_id))
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    device["state"] = await redis.hgetall(device_state_key(device_id))
    device["protocol"] = MQTT_PROTOCOL_NAME
    return device


@app.post("/api/devices/{device_id}/commands", status_code=202)
async def create_device_command(
    device_id: str, body: DeviceCommandRequest, request: Request
) -> dict[str, Any]:
    try:
        validate_device_id(device_id)
        validate_token(body.opcode, name="opcode")
        validate_token(body.argument, name="argument")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    redis: Redis = request.app.state.redis
    if not await redis.exists(device_key(device_id)):
        raise HTTPException(status_code=404, detail="Device not found")
    command_id = f"cmd-{uuid4().hex}"
    now = time.time()
    sequence = (await redis.incr(f"device-command-sequence:{device_id}")) & 0xFFFFFFFF
    record = {
        "id": command_id,
        "device_id": device_id,
        "opcode": body.opcode,
        "argument": body.argument,
        "sequence": str(sequence),
        "status": "queued",
        "created_at": str(now),
        "updated_at": str(now),
    }
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(device_command_key(command_id), mapping=record)
        pipe.zadd(DEVICE_COMMAND_INDEX, {command_id: now})
        pipe.xadd(
            DEVICE_COMMAND_STREAM,
            {
                "command_id": command_id,
                "device_id": device_id,
                "opcode": body.opcode,
                "argument": body.argument,
                "sequence": str(sequence),
            },
        )
        await pipe.execute()
    return record


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
