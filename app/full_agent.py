"""Reference Full Agent client supporting SAGP/1 over TCP or gRPC."""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import grpc

from app.stratos_gateway import PROTOCOL_VERSION
from app.stratos_gateway.framing import read_frame, write_frame
from app.stratos_gateway.generated import agent_pb2, agent_pb2_grpc


TaskHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class FullAgentConfig:
    agent_id: str
    board: str
    agent_version: str
    host: str
    port: int
    transport: Literal["tcp", "grpc"] = "grpc"
    capabilities: tuple[str, ...] = ()
    token: str = ""
    ca_cert: str | None = None
    client_cert: str | None = None
    client_key: str | None = None
    server_hostname: str | None = None


class FullAgent:
    """Maintain a gateway session and execute one task at a time."""

    def __init__(self, config: FullAgentConfig, handler: TaskHandler) -> None:
        self.config = config
        self.handler = handler
        self.heartbeat_seconds = 20
        self.running_task_ids: set[str] = set()

    def hello(self) -> agent_pb2.AgentMessage:
        return agent_pb2.AgentMessage(
            hello=agent_pb2.AgentHello(
                protocol_version=PROTOCOL_VERSION,
                agent_id=self.config.agent_id,
                board=self.config.board,
                agent_version=self.config.agent_version,
                capabilities=self.config.capabilities,
                token=self.config.token,
                max_concurrent_tasks=1,
            )
        )

    def heartbeat(self) -> agent_pb2.AgentMessage:
        return agent_pb2.AgentMessage(
            heartbeat=agent_pb2.Heartbeat(
                sent_at_unix_ms=int(time.time() * 1000),
                running_task_ids=sorted(self.running_task_ids),
                available_task_slots=0 if self.running_task_ids else 1,
            )
        )

    async def run(self) -> None:
        if self.config.transport == "tcp":
            await self._run_tcp()
        else:
            await self._run_grpc()

    async def run_forever(self, *, max_backoff_seconds: float = 30) -> None:
        backoff = 1.0
        while True:
            try:
                await self.run()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except (ConnectionError, EOFError, OSError, grpc.RpcError):
                await asyncio.sleep(backoff)
                backoff = min(max_backoff_seconds, backoff * 2)

    async def _execute(
        self, assignment: agent_pb2.TaskAssignment
    ) -> agent_pb2.AgentMessage:
        self.running_task_ids.add(assignment.task_id)
        try:
            payload = json.loads(assignment.payload_json)
            result = await self.handler(payload)
            result_json = json.dumps(result, separators=(",", ":"))
            return agent_pb2.AgentMessage(
                task_result=agent_pb2.TaskResult(
                    task_id=assignment.task_id,
                    lease_id=assignment.lease_id,
                    status=agent_pb2.TASK_STATUS_SUCCEEDED,
                    result_json=result_json,
                )
            )
        except Exception as exc:
            return agent_pb2.AgentMessage(
                task_result=agent_pb2.TaskResult(
                    task_id=assignment.task_id,
                    lease_id=assignment.lease_id,
                    status=agent_pb2.TASK_STATUS_FAILED,
                    error=str(exc)[:2000],
                )
            )
        finally:
            self.running_task_ids.discard(assignment.task_id)

    def _accept_gateway_message(self, message: agent_pb2.GatewayMessage) -> None:
        kind = message.WhichOneof("payload")
        if kind == "welcome":
            self.heartbeat_seconds = message.welcome.heartbeat_interval_seconds
        elif kind == "error" and message.error.fatal:
            raise RuntimeError(f"gateway rejected session: {message.error.code}: {message.error.detail}")

    async def _run_tcp(self) -> None:
        ssl_context = self._tcp_ssl_context()
        reader, writer = await asyncio.open_connection(
            self.config.host,
            self.config.port,
            ssl=ssl_context,
            server_hostname=(self.config.server_hostname or self.config.host)
            if ssl_context
            else None,
        )
        write_lock = asyncio.Lock()

        async def send(message: agent_pb2.AgentMessage) -> None:
            async with write_lock:
                await write_frame(writer, message)

        heartbeat_task: asyncio.Task[None] | None = None
        try:
            await send(self.hello())
            welcome = await read_frame(reader, agent_pb2.GatewayMessage)
            self._accept_gateway_message(welcome)
            if welcome.WhichOneof("payload") != "welcome":
                raise RuntimeError("gateway did not welcome the agent")

            async def send_heartbeats() -> None:
                while True:
                    await asyncio.sleep(self.heartbeat_seconds)
                    await send(self.heartbeat())

            heartbeat_task = asyncio.create_task(send_heartbeats())
            while True:
                message = await read_frame(reader, agent_pb2.GatewayMessage)
                self._accept_gateway_message(message)
                if message.WhichOneof("payload") == "task":
                    await send(await self._execute(message.task))
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def _run_grpc(self) -> None:
        target = f"{self.config.host}:{self.config.port}"
        credentials = self._grpc_credentials()
        channel = (
            grpc.aio.secure_channel(
                target,
                credentials,
                options=(("grpc.ssl_target_name_override", self.config.server_hostname),)
                if self.config.server_hostname
                else None,
            )
            if credentials is not None
            else grpc.aio.insecure_channel(target)
        )
        outgoing: asyncio.Queue[agent_pb2.AgentMessage | None] = asyncio.Queue(maxsize=100)
        await outgoing.put(self.hello())

        async def requests():
            while True:
                message = await outgoing.get()
                if message is None:
                    return
                yield message

        heartbeat_task: asyncio.Task[None] | None = None
        try:
            responses = agent_pb2_grpc.FullAgentGatewayStub(channel).Connect(requests())
            first = await responses.read()
            if first is grpc.aio.EOF:
                raise EOFError("gateway closed before welcome")
            self._accept_gateway_message(first)
            if first.WhichOneof("payload") != "welcome":
                raise RuntimeError("gateway did not welcome the agent")

            async def send_heartbeats() -> None:
                while True:
                    await asyncio.sleep(self.heartbeat_seconds)
                    await outgoing.put(self.heartbeat())

            heartbeat_task = asyncio.create_task(send_heartbeats())
            while True:
                message = await responses.read()
                if message is grpc.aio.EOF:
                    return
                self._accept_gateway_message(message)
                if message.WhichOneof("payload") == "task":
                    await outgoing.put(await self._execute(message.task))
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
            await outgoing.put(None)
            await channel.close()

    def _tcp_ssl_context(self) -> ssl.SSLContext | None:
        if self.config.ca_cert is None:
            if self.config.client_cert or self.config.client_key:
                raise ValueError("ca_cert is required when client credentials are set")
            return None
        context = ssl.create_default_context(cafile=self.config.ca_cert)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if bool(self.config.client_cert) != bool(self.config.client_key):
            raise ValueError("client_cert and client_key must be set together")
        if self.config.client_cert:
            context.load_cert_chain(self.config.client_cert, self.config.client_key)
        return context

    def _grpc_credentials(self) -> grpc.ChannelCredentials | None:
        if self.config.ca_cert is None:
            if self.config.client_cert or self.config.client_key:
                raise ValueError("ca_cert is required when client credentials are set")
            return None
        root = Path(self.config.ca_cert).read_bytes()
        cert = Path(self.config.client_cert).read_bytes() if self.config.client_cert else None
        key = Path(self.config.client_key).read_bytes() if self.config.client_key else None
        if bool(cert) != bool(key):
            raise ValueError("client_cert and client_key must be set together")
        return grpc.ssl_channel_credentials(
            root_certificates=root, private_key=key, certificate_chain=cert
        )
