"""gRPC bidirectional streaming transport for Full Agents."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import grpc

from app.stratos_gateway.backend import GatewayBackend, ProtocolViolation
from app.stratos_gateway.generated import agent_pb2, agent_pb2_grpc
from app.stratos_gateway.session import GatewaySession, error_message


class FullAgentGatewayService(agent_pb2_grpc.FullAgentGatewayServicer):
    def __init__(self, backend: GatewayBackend) -> None:
        self.backend = backend

    async def Connect(
        self,
        request_iterator: AsyncIterator[agent_pb2.AgentMessage],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[agent_pb2.GatewayMessage]:
        session = GatewaySession(self.backend, "grpc")
        replies: asyncio.Queue[agent_pb2.GatewayMessage | None] = asyncio.Queue(maxsize=100)
        consumer: asyncio.Task[None] | None = None
        dispatcher: asyncio.Task[None] | None = None
        try:
            try:
                first = await anext(request_iterator)
                yield await session.open(first)
            except StopAsyncIteration:
                return
            except ProtocolViolation as exc:
                yield error_message(exc)
                return

            async def consume() -> None:
                try:
                    async for message in request_iterator:
                        try:
                            reply = await session.receive(message)
                        except ProtocolViolation as exc:
                            await replies.put(error_message(exc))
                            return
                        if reply is not None:
                            await replies.put(reply)
                finally:
                    await replies.put(None)

            consumer = asyncio.create_task(consume())
            async def dispatch() -> None:
                while True:
                    assignment = await session.next_assignment()
                    if assignment is not None:
                        await replies.put(assignment)

            dispatcher = asyncio.create_task(dispatch())
            while True:
                reply = await replies.get()
                if reply is None:
                    return
                yield reply
                if reply.WhichOneof("payload") == "error" and reply.error.fatal:
                    return
        finally:
            for task in (consumer, dispatcher):
                if task is not None:
                    task.cancel()
            for task in (consumer, dispatcher):
                if task is None:
                    continue
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await session.close()


def create_grpc_server(
    backend: GatewayBackend,
    host: str,
    port: int,
    *,
    credentials: grpc.ServerCredentials | None = None,
) -> grpc.aio.Server:
    server = grpc.aio.server(
        options=(
            ("grpc.max_receive_message_length", 1024 * 1024),
            ("grpc.max_send_message_length", 1024 * 1024),
        )
    )
    agent_pb2_grpc.add_FullAgentGatewayServicer_to_server(
        FullAgentGatewayService(backend), server
    )
    address = f"{host}:{port}"
    if credentials is None:
        server.add_insecure_port(address)
    else:
        server.add_secure_port(address, credentials)
    return server
