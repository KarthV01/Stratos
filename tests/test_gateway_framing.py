import asyncio
import unittest

from app.stratos_gateway.framing import MAX_FRAME_BYTES, FrameError, encode_frame, read_frame
from app.stratos_gateway.generated import agent_pb2


class GatewayFramingTests(unittest.TestCase):
    def test_round_trip_agent_message(self) -> None:
        original = agent_pb2.AgentMessage(
            hello=agent_pb2.AgentHello(
                protocol_version="sagp/1",
                agent_id="linux-01",
                board="raspberry-pi/5-model-b",
                agent_version="1.0.0",
                capabilities=["shell", "python"],
            )
        )
        async def round_trip():
            reader = asyncio.StreamReader()
            reader.feed_data(encode_frame(original))
            reader.feed_eof()
            return await read_frame(reader, agent_pb2.AgentMessage)

        decoded = asyncio.run(round_trip())

        self.assertEqual(original, decoded)

    def test_empty_and_oversized_frames_are_rejected(self) -> None:
        empty = agent_pb2.AgentMessage()
        with self.assertRaises(FrameError):
            encode_frame(empty)

        async def read_oversized():
            reader = asyncio.StreamReader()
            reader.feed_data((MAX_FRAME_BYTES + 1).to_bytes(4, "big"))
            reader.feed_eof()
            return await read_frame(reader, agent_pb2.AgentMessage)

        with self.assertRaises(FrameError):
            asyncio.run(read_oversized())

    def test_truncated_frame_is_rejected(self) -> None:
        async def read_truncated():
            reader = asyncio.StreamReader()
            reader.feed_data((10).to_bytes(4, "big") + b"short")
            reader.feed_eof()
            return await read_frame(reader, agent_pb2.AgentMessage)

        with self.assertRaises(FrameError):
            asyncio.run(read_truncated())


if __name__ == "__main__":
    unittest.main()
