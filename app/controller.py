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
from app.agent_gateway import full_command_stream_maxlen, router as agent_router
from app.boards import (
    PROTOCOL_VERSION,
    board_directory,
    classify_capabilities,
    normalize_identifier,
    resolve_board,
    tier_catalog,
)
from app.config import (
    AGENT_INDEX,
    DEVICE_COMMAND_INDEX,
    DEVICE_COMMAND_STREAM,
    DEVICE_INDEX,
    EVENT_STREAM,
    TASK_INDEX,
    TASK_STREAM,
)
from app.device_store import (
    agent_command_stream,
    device_command_key,
    device_key,
    device_state_key,
    latest_telemetry_key,
)
from app.mqtt_protocol import PROTOCOL_NAME as MQTT_PROTOCOL_NAME
from app.mqtt_protocol import validate_device_id, validate_token
from app.stratos_gateway.backend import agent_key, agent_task_stream, validate_agent_id
from app.tier_payloads import FullCommand, encode_full_payload


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
    arguments: dict[str, Any] | None = Field(default=None, max_length=64)
    expires_at_ms: int | None = Field(default=None, ge=0)


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
app.include_router(agent_router)


@app.get("/", include_in_schema=False)
async def service_info() -> dict[str, str]:
    return {
        "service": "stratos",
        "health": "/health",
        "tasks": "/api/tasks",
        "boards": "/api/boards",
        "devices": "/api/devices",
        "agents": "/api/agents",
        "docs": "/docs",
    }


@app.get("/api/agents")
async def list_agents(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> list[dict[str, Any]]:
    redis: Redis = request.app.state.redis
    ids = await redis.zrevrange(AGENT_INDEX, 0, limit - 1)
    if not ids:
        return []
    async with redis.pipeline(transaction=False) as pipe:
        for agent_id in ids:
            pipe.hgetall(agent_key(agent_id))
        agents = await pipe.execute()
    return [agent for agent in agents if agent]


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request) -> dict[str, Any]:
    try:
        validate_agent_id(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    agent = await request.app.state.redis.hgetall(agent_key(agent_id))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@app.post("/api/agents/{agent_id}/tasks", status_code=202)
async def create_agent_task(
    agent_id: str, body: TaskRequest, request: Request
) -> dict[str, Any]:
    try:
        validate_agent_id(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    redis: Redis = request.app.state.redis
    if not await redis.exists(agent_key(agent_id)):
        raise HTTPException(status_code=404, detail="Agent not found")
    task_id = str(uuid4())
    now = time.time()
    payload = body.model_dump()
    record = {
        "id": task_id,
        "status": "queued",
        "worker": agent_id,
        "payload": json.dumps(payload),
        "created_at": str(now),
        "updated_at": str(now),
    }
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(task_key(task_id), mapping=record)
        pipe.zadd(TASK_INDEX, {task_id: now})
        pipe.xadd(
            agent_task_stream(agent_id),
            {"task_id": task_id, "payload": record["payload"]},
        )
        pipe.xadd(
            EVENT_STREAM,
            {"task_id": task_id, "status": "queued", "worker": agent_id},
        )
        await pipe.execute()
    return decode_task(record)


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


@app.get("/api/devices/{device_id}/telemetry/latest")
async def get_device_latest_telemetry(
    device_id: str, request: Request
) -> dict[str, Any]:
    try:
        validate_device_id(device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    redis: Redis = request.app.state.redis
    if not await redis.exists(device_key(device_id)):
        raise HTTPException(status_code=404, detail="Device not found")
    record = await redis.hgetall(latest_telemetry_key(device_id))
    if not record:
        raise HTTPException(status_code=404, detail="No telemetry received")
    record["payload"] = json.loads(record["payload"])
    return record


@app.get("/api/devices/{device_id}/commands/{command_id}")
async def get_device_command(
    device_id: str, command_id: str, request: Request
) -> dict[str, Any]:
    redis: Redis = request.app.state.redis
    record = await redis.hgetall(device_command_key(command_id))
    if not record or record.get("device_id") != device_id:
        raise HTTPException(status_code=404, detail="Command not found")
    for field in ("arguments", "result"):
        if field in record:
            record[field] = json.loads(record[field])
    return record


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
    device = await redis.hgetall(device_key(device_id))
    if not device:
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
        "transport": device.get("transport", "mqtt"),
    }
    full_command_payload: str | None = None
    if device.get("tier") == "1" and device.get("transport") == "https":
        command = FullCommand(
            command_id=command_id,
            sequence=sequence,
            issued_at_ms=int(now * 1000),
            expires_at_ms=body.expires_at_ms,
            opcode=body.opcode,
            arguments=body.arguments
            if body.arguments is not None
            else ({"value": body.argument} if body.argument != "-" else {}),
        )
        try:
            full_command_payload = encode_full_payload(command).decode("utf-8")
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        record["arguments"] = json.dumps(command.arguments, separators=(",", ":"))
    elif body.arguments is not None:
        raise HTTPException(
            status_code=422,
            detail="Tier 2/3 commands accept one compact argument token",
        )
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(device_command_key(command_id), mapping=record)
        pipe.zadd(DEVICE_COMMAND_INDEX, {command_id: now})
        if full_command_payload is not None:
            pipe.xadd(
                agent_command_stream(device_id),
                {"payload": full_command_payload},
                maxlen=full_command_stream_maxlen(),
                approximate=True,
            )
        else:
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
