"""HTTP SAP/1 gateway for Tier 1 Linux companion agents."""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from redis.asyncio import Redis

from app.boards import AgentTier, resolve_board
from app.config import (
    DEVICE_EVENT_STREAM,
    DEVICE_INDEX,
    DEVICE_TELEMETRY_STREAM,
    FULL_AGENT_COMMAND_MAXLEN,
)
from app.device_store import (
    agent_command_stream,
    device_command_key,
    device_key,
    latest_telemetry_key,
)
from app.mqtt_protocol import validate_device_id
from app.tier_payloads import (
    FullAgentEvent,
    FullAgentHello,
    FullCommandAck,
    FullTelemetry,
    encode_full_payload,
)
from app.geospatial import record_observation


router = APIRouter(prefix="/api/agent/v1", tags=["full-agent"])


def agent_frame_key(agent_id: str, boot_id: str, sequence: int) -> str:
    return f"agent-frame:{agent_id}:{boot_id}:{sequence}"


async def _registered_full_agent(redis: Redis, agent_id: str) -> dict[str, str]:
    try:
        validate_device_id(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record = await redis.hgetall(device_key(agent_id))
    if not record or record.get("tier") != "1" or record.get("transport") != "https":
        raise HTTPException(status_code=404, detail="Full agent is not registered")
    return record


async def _accept_sequence(redis: Redis, record: dict[str, str], sequence: int) -> bool:
    return bool(
        await redis.set(
            agent_frame_key(record["id"], record["boot_id"], sequence),
            "1",
            ex=86_400,
            nx=True,
        )
    )


@router.post("/register", status_code=201)
async def register_full_agent(payload: FullAgentHello, request: Request) -> dict[str, Any]:
    profile = resolve_board(payload.board)
    if profile is None:
        raise HTTPException(status_code=404, detail="Unknown board")
    if profile.agent_tier is not AgentTier.FULL:
        raise HTTPException(status_code=422, detail="Board is not a Tier 1 full agent")
    redis: Redis = request.app.state.redis
    now = time.time()
    await redis.hset(
        device_key(payload.agent_id),
        mapping={
            "id": payload.agent_id,
            "board": profile.identifier,
            "firmware": payload.firmware,
            "tier": "1",
            "tier_code": profile.tier_code,
            "transport": "https",
            "boot_id": payload.boot_id,
            "capabilities": json.dumps(payload.capabilities, separators=(",", ":")),
            "resources": payload.resources.model_dump_json(),
            "availability": "online",
            "last_seen": str(now),
            "last_sequence": str(payload.sequence),
        },
    )
    await redis.zadd(DEVICE_INDEX, {payload.agent_id: now})
    await redis.xadd(
        DEVICE_EVENT_STREAM,
        {
            "device_id": payload.agent_id,
            "tier": "1",
            "kind": "session",
            "name": "hello",
            "value": "accepted",
            "recorded_at": str(now),
        },
        maxlen=10_000,
        approximate=True,
    )
    return {
        "protocol": "sap/1",
        "accepted": True,
        "agent_id": payload.agent_id,
        "server_time_ms": int(now * 1000),
        "heartbeat_seconds": 15,
        "commands_url": f"/api/agent/v1/{payload.agent_id}/commands",
    }


@router.post("/{agent_id}/telemetry", status_code=202)
async def ingest_full_telemetry(
    agent_id: str, payload: FullTelemetry, request: Request
) -> dict[str, Any]:
    if payload.agent_id != agent_id:
        raise HTTPException(status_code=409, detail="Payload agent ID does not match path")
    redis: Redis = request.app.state.redis
    record = await _registered_full_agent(redis, agent_id)
    if not await _accept_sequence(redis, record, payload.sequence):
        return {"accepted": True, "duplicate": True, "sequence": payload.sequence}
    now = time.time()
    try:
        encoded = encode_full_payload(payload).decode("utf-8")
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    await redis.hset(
        latest_telemetry_key(agent_id),
        mapping={
            "device_id": agent_id,
            "tier": "1",
            "kind": "flight",
            "payload": encoded,
            "recorded_at": str(now),
            "sequence": str(payload.sequence),
        },
    )
    await redis.xadd(
        DEVICE_TELEMETRY_STREAM,
        {
            "device_id": agent_id,
            "tier": "1",
            "kind": "flight",
            "payload": encoded,
            "recorded_at": str(now),
            "sequence": str(payload.sequence),
        },
        maxlen=100_000,
        approximate=True,
    )
    await redis.hset(
        device_key(agent_id),
        mapping={"last_seen": str(now), "last_sequence": str(payload.sequence)},
    )
    await redis.zadd(DEVICE_INDEX, {agent_id: now})
    if (
        payload.sensors
        and payload.flight.latitude_e7 is not None
        and payload.flight.longitude_e7 is not None
    ):
        for sensor, value in payload.sensors.items():
            await record_observation(
                redis,
                device_id=agent_id,
                vehicle_id=record.get("vehicle_id"),
                sensor=sensor,
                value=value,
                unit="unitless",
                latitude_e7=payload.flight.latitude_e7,
                longitude_e7=payload.flight.longitude_e7,
                altitude_mm=payload.flight.relative_altitude_mm,
                observed_at=payload.sent_at_ms / 1000,
                location_source="telemetry",
                observation_id=f"{record['boot_id']}:{payload.sequence}:{sensor}",
            )
    return {"accepted": True, "duplicate": False, "sequence": payload.sequence}


@router.post("/{agent_id}/events", status_code=202)
async def ingest_full_event(
    agent_id: str, payload: FullAgentEvent, request: Request
) -> dict[str, Any]:
    if payload.agent_id != agent_id:
        raise HTTPException(status_code=409, detail="Payload agent ID does not match path")
    redis: Redis = request.app.state.redis
    record = await _registered_full_agent(redis, agent_id)
    if not await _accept_sequence(redis, record, payload.sequence):
        return {"accepted": True, "duplicate": True, "sequence": payload.sequence}
    try:
        encoded = encode_full_payload(payload).decode("utf-8")
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    await redis.xadd(
        DEVICE_EVENT_STREAM,
        {
            "device_id": agent_id,
            "tier": "1",
            "kind": "agent",
            "name": payload.code,
            "value": payload.severity,
            "payload": encoded,
            "recorded_at": str(time.time()),
        },
        maxlen=10_000,
        approximate=True,
    )
    return {"accepted": True, "duplicate": False, "sequence": payload.sequence}


@router.get("/{agent_id}/commands")
async def poll_full_commands(
    agent_id: str,
    request: Request,
    after: str = Query(default="0-0", pattern=r"^(?:0-0|[0-9]+-[0-9]+)$"),
    wait_seconds: int = Query(default=20, ge=0, le=25),
) -> dict[str, Any]:
    redis: Redis = request.app.state.redis
    await _registered_full_agent(redis, agent_id)
    rows = await redis.xread(
        {agent_command_stream(agent_id): after},
        count=10,
        block=wait_seconds * 1000 if wait_seconds else None,
    )
    commands = []
    cursor = after
    for _, entries in rows:
        for entry_id, fields in entries:
            cursor = entry_id
            commands.append({"cursor": entry_id, **json.loads(fields["payload"])})
    return {"protocol": "sap/1", "cursor": cursor, "commands": commands}


@router.post("/{agent_id}/commands/{command_id}/ack", status_code=202)
async def acknowledge_full_command(
    agent_id: str, command_id: str, payload: FullCommandAck, request: Request
) -> dict[str, Any]:
    if payload.agent_id != agent_id or payload.command_id != command_id:
        raise HTTPException(status_code=409, detail="Command acknowledgement identity mismatch")
    redis: Redis = request.app.state.redis
    await _registered_full_agent(redis, agent_id)
    command_key = device_command_key(command_id)
    if await redis.hget(command_key, "device_id") != agent_id:
        raise HTTPException(status_code=404, detail="Command not found")
    await redis.hset(
        command_key,
        mapping={
            "status": payload.status,
            "code": payload.code,
            "result": json.dumps(payload.result, separators=(",", ":")),
            "updated_at": str(time.time()),
        },
    )
    return {"accepted": True, "command_id": command_id, "status": payload.status}


@router.get("/{agent_id}/telemetry/latest")
async def get_latest_telemetry(agent_id: str, request: Request) -> dict[str, Any]:
    redis: Redis = request.app.state.redis
    await _registered_full_agent(redis, agent_id)
    record = await redis.hgetall(latest_telemetry_key(agent_id))
    if not record:
        raise HTTPException(status_code=404, detail="No telemetry received")
    record["payload"] = json.loads(record["payload"])
    return record


def full_command_stream_maxlen() -> int:
    return FULL_AGENT_COMMAND_MAXLEN
