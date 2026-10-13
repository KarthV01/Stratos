"""State machine shared by the TCP and gRPC gateway transports."""

from __future__ import annotations

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

    async def open(self, message: agent_pb2.AgentMessage) -> agent_pb2.GatewayMessage:
        if message.WhichOneof("payload") != "hello":
            raise ProtocolViolation("hello-required", "the first message must be hello")
        self.session = await self.backend.register(message.hello, self.transport)
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
            return None
        if kind == "task_result":
            return await self.backend.complete(self.session, message.task_result)
        raise ProtocolViolation("unexpected-message", "hello is only valid once")

    async def next_assignment(self) -> agent_pb2.GatewayMessage | None:
        if self.session is None:
            raise ProtocolViolation("hello-required", "session is not open")
        return await self.backend.next_assignment(self.session)

    async def close(self) -> None:
        if self.session is not None:
            await self.backend.disconnect(self.session)
