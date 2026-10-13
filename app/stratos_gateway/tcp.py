"""Raw TCP transport for the Stratos Agent Gateway Protocol."""

from __future__ import annotations

import asyncio
import contextlib
import ssl

from app.stratos_gateway.backend import GatewayBackend, ProtocolViolation
from app.stratos_gateway.framing import FrameError, read_frame, write_frame
from app.stratos_gateway.generated import agent_pb2
from app.stratos_gateway.session import GatewaySession, error_message


HELLO_TIMEOUT_SECONDS = 10


class TcpGatewayServer:
    def __init__(
        self,
        backend: GatewayBackend,
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.backend = backend
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.server: asyncio.Server | None = None

    async def start(self) -> asyncio.Server:
        self.server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
            ssl=self.ssl_context,
            limit=1024 * 1024 + 4,
        )
        return self.server

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session = GatewaySession(self.backend, "tcp")
        try:
            first = await asyncio.wait_for(
                read_frame(reader, agent_pb2.AgentMessage), HELLO_TIMEOUT_SECONDS
            )
            await write_frame(writer, await session.open(first))
            outgoing: asyncio.Queue[agent_pb2.GatewayMessage] = asyncio.Queue(maxsize=100)

            async def receive() -> None:
                while True:
                    message = await read_frame(reader, agent_pb2.AgentMessage)
                    reply = await session.receive(message)
                    if reply is not None:
                        await outgoing.put(reply)

            async def dispatch() -> None:
                while True:
                    task_message = await session.next_assignment()
                    if task_message is not None:
                        await outgoing.put(task_message)

            async def send() -> None:
                while True:
                    await write_frame(writer, await outgoing.get())

            tasks = {
                asyncio.create_task(receive()),
                asyncio.create_task(dispatch()),
                asyncio.create_task(send()),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for task in done:
                task.result()
        except (EOFError, asyncio.TimeoutError, ConnectionError):
            pass
        except (FrameError, ProtocolViolation) as exc:
            violation = (
                exc
                if isinstance(exc, ProtocolViolation)
                else ProtocolViolation("invalid-frame", str(exc))
            )
            with contextlib.suppress(ConnectionError, RuntimeError):
                await write_frame(writer, error_message(violation))
        finally:
            await session.close()
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
