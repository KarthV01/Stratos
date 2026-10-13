import asyncio
import unittest

import grpc

from app.stratos_gateway.backend import AgentSession
from app.stratos_gateway.framing import read_frame, write_frame
from app.stratos_gateway.generated import agent_pb2, agent_pb2_grpc
from app.stratos_gateway.grpc_service import FullAgentGatewayService
from app.stratos_gateway.tcp import TcpGatewayServer


def hello_message() -> agent_pb2.AgentMessage:
    return agent_pb2.AgentMessage(
        hello=agent_pb2.AgentHello(
            protocol_version="sagp/1",
            agent_id="linux-01",
            board="raspberry-pi/5",
            agent_version="1.0.0",
        )
    )


class FakeBackend:
    heartbeat_seconds = 20

    def __init__(self) -> None:
        self.transports: list[str] = []
        self.disconnected: list[str] = []

    async def register(self, hello, transport):
        self.transports.append(transport)
        return AgentSession(hello.agent_id, f"session-{transport}", transport)

    async def heartbeat(self, session):
        return None

    async def complete(self, session, result):
        return agent_pb2.GatewayMessage()

    async def next_assignment(self, session, *, block_ms=1_000):
        await asyncio.sleep(0.05)
        return None

    async def disconnect(self, session):
        self.disconnected.append(session.transport)


class GatewayTransportTests(unittest.TestCase):
    def test_tcp_accepts_hello_and_returns_welcome(self) -> None:
        async def scenario():
            backend = FakeBackend()
            gateway = TcpGatewayServer(backend, "127.0.0.1", 0)
            server = await gateway.start()
            port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                await write_frame(writer, hello_message())
                response = await asyncio.wait_for(
                    read_frame(reader, agent_pb2.GatewayMessage), 1
                )
                self.assertEqual("session-tcp", response.welcome.session_id)
                self.assertEqual(["tcp"], backend.transports)
            finally:
                writer.close()
                await writer.wait_closed()
                await asyncio.sleep(0)
                await gateway.close()

        asyncio.run(scenario())

    def test_grpc_accepts_hello_and_returns_welcome(self) -> None:
        async def scenario():
            backend = FakeBackend()
            server = grpc.aio.server()
            agent_pb2_grpc.add_FullAgentGatewayServicer_to_server(
                FullAgentGatewayService(backend), server
            )
            port = server.add_insecure_port("127.0.0.1:0")
            await server.start()
            channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")

            async def requests():
                yield hello_message()
                await asyncio.Event().wait()

            call = agent_pb2_grpc.FullAgentGatewayStub(channel).Connect(requests())
            try:
                response = await asyncio.wait_for(call.read(), 1)
                self.assertEqual("session-grpc", response.welcome.session_id)
                self.assertEqual(["grpc"], backend.transports)
            finally:
                call.cancel()
                await channel.close()
                await server.stop(0)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
