"""State machine shared by the TCP and gRPC gateway transports."""

from __future__ import annotations

import asyncio
import time

from app.stratos_gateway.backend import AgentSession, GatewayBackend, ProtocolViolation
from app.stratos_gateway.generated import agent_pb2


def error_message(exc: ProtocolViolation) -> agent_pb2.GatewayMessage:
    return agent_pb2.GatewayMessage(
        error=agent_pb2.ProtocolError(code=exc.code, detail=exc.detail, fatal=True)
    )


class GatewaySession:
    def __init__(self, backend: GatewayBackend, transport: str) -> None:
        self.backend = backend
        self.transport = transport
        self.session: AgentSession | None = None
        self.max_concurrent_tasks = 1
        self.available_task_slots = 0

    async def open(self, message: agent_pb2.AgentMessage) -> agent_pb2.GatewayMessage:
        if message.WhichOneof("payload") != "hello":
            raise ProtocolViolation("hello-required", "the first message must be hello")
        self.session = await self.backend.register(message.hello, self.transport)
        self.max_concurrent_tasks = max(1, message.hello.max_concurrent_tasks)
        self.available_task_slots = self.max_concurrent_tasks
        return agent_pb2.GatewayMessage(
            welcome=agent_pb2.Welcome(
                session_id=self.session.session_id,
                heartbeat_interval_seconds=self.backend.heartbeat_seconds,
                server_time_unix_ms=int(time.time() * 1000),
            )
        )

    async def receive(
        self, message: agent_pb2.AgentMessage
    ) -> agent_pb2.GatewayMessage | None:
        if self.session is None:
            raise ProtocolViolation("hello-required", "session is not open")
        kind = message.WhichOneof("payload")
        if kind == "heartbeat":
            await self.backend.heartbeat(self.session)
            self.available_task_slots = min(
                message.heartbeat.available_task_slots, self.max_concurrent_tasks
            )
            return None
        if kind == "task_result":
            reply = await self.backend.complete(self.session, message.task_result)
            self.available_task_slots = min(
                self.available_task_slots + 1, self.max_concurrent_tasks
            )
            return reply
        raise ProtocolViolation("unexpected-message", "hello is only valid once")

    async def next_assignment(self) -> agent_pb2.GatewayMessage | None:
        if self.session is None:
            raise ProtocolViolation("hello-required", "session is not open")
        if self.available_task_slots == 0:
            await asyncio.sleep(0.1)
            return None
        assignment = await self.backend.next_assignment(self.session)
        if assignment is not None:
            self.available_task_slots -= 1
        return assignment

    async def close(self) -> None:
        if self.session is not None:
            await self.backend.disconnect(self.session)
