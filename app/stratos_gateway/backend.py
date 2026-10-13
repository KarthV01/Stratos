"""Redis-backed transport-neutral state for connected Full Agents."""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.boards import AgentTier, resolve_board
from app.config import (
    AGENT_EVENT_STREAM,
    AGENT_GATEWAY_GROUP,
    AGENT_INDEX,
    EVENT_STREAM,
    GATEWAY_AGENT_TOKEN,
    GATEWAY_HEARTBEAT_SECONDS,
    GATEWAY_LEASE_TIMEOUT_MS,
)
from app.stratos_gateway import PROTOCOL_VERSION
from app.stratos_gateway.generated import agent_pb2


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_CAPABILITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,63}$")


class ProtocolViolation(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class AgentSession:
    agent_id: str
    session_id: str
    transport: str


def agent_key(agent_id: str) -> str:
    return f"agent:{agent_id}"


def agent_task_stream(agent_id: str) -> str:
    return f"agent-tasks:{agent_id}"


def validate_agent_id(agent_id: str) -> str:
    if not _ID.fullmatch(agent_id):
        raise ValueError("agent_id is invalid")
    return agent_id


def validate_hello(hello: agent_pb2.AgentHello, expected_token: str | None) -> None:
    if hello.protocol_version != PROTOCOL_VERSION:
        raise ProtocolViolation("unsupported-version", f"expected {PROTOCOL_VERSION}")
    if not _ID.fullmatch(hello.agent_id):
        raise ProtocolViolation("invalid-agent-id", "agent_id is invalid")
    if not hello.agent_version or len(hello.agent_version) > 64:
        raise ProtocolViolation("invalid-agent-version", "agent_version is required")
    if len(hello.capabilities) > 64 or any(
        not _CAPABILITY.fullmatch(value) for value in hello.capabilities
    ):
        raise ProtocolViolation("invalid-capabilities", "capabilities are invalid")
    if hello.max_concurrent_tasks > 64:
        raise ProtocolViolation("invalid-capacity", "max_concurrent_tasks must be at most 64")
    profile = resolve_board(hello.board)
    if profile is None:
        raise ProtocolViolation("unknown-board", "board is not in the directory")
    if profile.agent_tier is not AgentTier.FULL:
        raise ProtocolViolation("wrong-agent-tier", "board does not support a Full Agent")
    if expected_token is not None and not secrets.compare_digest(hello.token, expected_token):
        raise ProtocolViolation("unauthenticated", "agent token was rejected")


class GatewayBackend:
    def __init__(
        self,
        redis: Redis,
        *,
        agent_token: str | None = GATEWAY_AGENT_TOKEN,
        heartbeat_seconds: int = GATEWAY_HEARTBEAT_SECONDS,
        lease_timeout_ms: int = GATEWAY_LEASE_TIMEOUT_MS,
    ) -> None:
        self.redis = redis
        self.agent_token = agent_token
        self.heartbeat_seconds = heartbeat_seconds
        self.lease_timeout_ms = lease_timeout_ms

    async def register(
        self, hello: agent_pb2.AgentHello, transport: str
    ) -> AgentSession:
        validate_hello(hello, self.agent_token)
        profile = resolve_board(hello.board)
        assert profile is not None
        now = time.time()
        session = AgentSession(hello.agent_id, uuid4().hex, transport)
        await self._ensure_delivery_group(hello.agent_id)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(
                agent_key(hello.agent_id),
                mapping={
                    "id": hello.agent_id,
                    "session_id": session.session_id,
                    "board": profile.identifier,
                    "agent_version": hello.agent_version,
                    "capabilities": json.dumps(list(hello.capabilities)),
                    "transport": transport,
                    "availability": "online",
                    "connected_at": str(now),
                    "last_seen": str(now),
                },
            )
            pipe.zadd(AGENT_INDEX, {hello.agent_id: now})
            pipe.xadd(
                AGENT_EVENT_STREAM,
                {
                    "agent_id": hello.agent_id,
                    "kind": "session",
                    "value": "connected",
                    "transport": transport,
                    "recorded_at": str(now),
                },
                maxlen=10_000,
                approximate=True,
            )
            await pipe.execute()
        return session

    async def heartbeat(self, session: AgentSession) -> None:
        now = time.time()
        if await self.redis.hget(agent_key(session.agent_id), "session_id") != session.session_id:
            raise ProtocolViolation("session-replaced", "a newer session is active")
        await self.redis.hset(agent_key(session.agent_id), "last_seen", str(now))
        await self.redis.zadd(AGENT_INDEX, {session.agent_id: now})

    async def next_assignment(
        self, session: AgentSession, *, block_ms: int = 1_000
    ) -> agent_pb2.GatewayMessage | None:
        if await self.redis.hget(agent_key(session.agent_id), "session_id") != session.session_id:
            raise ProtocolViolation("session-replaced", "a newer session is active")
        stream = agent_task_stream(session.agent_id)
        claimed = await self.redis.xautoclaim(
            stream,
            AGENT_GATEWAY_GROUP,
            session.session_id,
            min_idle_time=self.lease_timeout_ms,
            start_id="0-0",
            count=1,
        )
        entries = claimed[1]
        if not entries:
            rows = await self.redis.xreadgroup(
                AGENT_GATEWAY_GROUP,
                session.session_id,
                {stream: ">"},
                count=1,
                block=block_ms,
            )
            if not rows:
                return None
            _, entries = rows[0]
        lease_id, task = entries[0]
        task_id = task["task_id"]
        now = time.time()
        await self.redis.hset(
            f"task:{task_id}",
            mapping={
                "status": "assigned",
                "worker": session.agent_id,
                "lease_id": lease_id,
                "updated_at": str(now),
            },
        )
        await self.redis.xadd(
            EVENT_STREAM,
            {"task_id": task_id, "status": "assigned", "worker": session.agent_id},
            maxlen=10_000,
            approximate=True,
        )
        return agent_pb2.GatewayMessage(
            task=agent_pb2.TaskAssignment(
                task_id=task_id,
                lease_id=lease_id,
                payload_json=task["payload"],
            )
        )

    async def complete(
        self, session: AgentSession, result: agent_pb2.TaskResult
    ) -> agent_pb2.GatewayMessage:
        if await self.redis.hget(agent_key(session.agent_id), "session_id") != session.session_id:
            raise ProtocolViolation("session-replaced", "a newer session is active")
        if not result.task_id or not result.lease_id:
            raise ProtocolViolation("invalid-result", "task_id and lease_id are required")
        record = await self.redis.hgetall(f"task:{result.task_id}")
        if (
            not record
            or record.get("worker") != session.agent_id
            or record.get("lease_id") != result.lease_id
        ):
            return agent_pb2.GatewayMessage(
                result_ack=agent_pb2.ResultAck(
                    task_id=result.task_id,
                    accepted=False,
                    detail="lease does not belong to this session",
                )
            )
        if record.get("status") in {"completed", "failed"}:
            return agent_pb2.GatewayMessage(
                result_ack=agent_pb2.ResultAck(
                    task_id=result.task_id,
                    accepted=True,
                    detail="already recorded",
                )
            )
        if result.status not in {
            agent_pb2.TASK_STATUS_SUCCEEDED,
            agent_pb2.TASK_STATUS_FAILED,
        }:
            raise ProtocolViolation("invalid-result", "terminal task status is required")
        now = time.time()
        status = "completed" if result.status == agent_pb2.TASK_STATUS_SUCCEEDED else "failed"
        updates = {"status": status, "updated_at": str(now)}
        if result.result_json:
            try:
                json.loads(result.result_json)
            except json.JSONDecodeError as exc:
                raise ProtocolViolation("invalid-result", "result_json is not valid JSON") from exc
            updates["result"] = result.result_json
        if result.error:
            updates["error"] = result.error[:2000]
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(f"task:{result.task_id}", mapping=updates)
            pipe.xadd(
                EVENT_STREAM,
                {"task_id": result.task_id, "status": status, "worker": session.agent_id},
                maxlen=10_000,
                approximate=True,
            )
            pipe.xack(
                agent_task_stream(session.agent_id),
                AGENT_GATEWAY_GROUP,
                result.lease_id,
            )
            await pipe.execute()
        return agent_pb2.GatewayMessage(
            result_ack=agent_pb2.ResultAck(
                task_id=result.task_id, accepted=True, detail="recorded"
            )
        )

    async def disconnect(self, session: AgentSession) -> None:
        key = agent_key(session.agent_id)
        if await self.redis.hget(key, "session_id") != session.session_id:
            return
        now = time.time()
        await self.redis.hset(
            key, mapping={"availability": "offline", "last_seen": str(now)}
        )
        await self.redis.xadd(
            AGENT_EVENT_STREAM,
            {
                "agent_id": session.agent_id,
                "kind": "session",
                "value": "disconnected",
                "transport": session.transport,
                "recorded_at": str(now),
            },
            maxlen=10_000,
            approximate=True,
        )

    async def _ensure_delivery_group(self, agent_id: str) -> None:
        try:
            await self.redis.xgroup_create(
                agent_task_stream(agent_id), AGENT_GATEWAY_GROUP, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
