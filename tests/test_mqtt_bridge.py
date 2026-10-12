import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.boards import AgentTier, board_directory
from app.mqtt_bridge import DeviceMessageProcessor, device_command_key, device_key
from app.mqtt_protocol import device_topic, encode_ack, encode_hello, encode_telemetry


class DeviceMessageProcessorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = AsyncMock()
        self.publisher = Mock()
        self.processor = DeviceMessageProcessor(self.redis, self.publisher)

    def test_hello_registers_known_tier_two_board_and_welcomes_it(self) -> None:
        payload = encode_hello(1, "esp32-s3", "1.2.0", 2, "temp+relay")

        with patch("app.mqtt_bridge.time.time", return_value=1000.5):
            asyncio.run(
                self.processor.process(
                    device_topic("greenhouse-07", "up", "hello"), payload
                )
            )

        mapping = self.redis.hset.await_args.kwargs["mapping"]
        self.assertEqual("espressif/esp32-s3-devkitc-1", mapping["board"])
        self.assertEqual("2", mapping["tier"])
        published_topic = self.publisher.publish.call_args.args[0]
        self.assertEqual(
            "stratos/v1/devices/greenhouse-07/down/welcome", published_topic
        )

    def test_tier_three_board_with_wifi_adapter_can_register(self) -> None:
        profile = next(
            board for board in board_directory() if board.agent_tier == AgentTier.MINIMAL
        )
        payload = encode_hello(1, profile.identifier, "0.4.0", 3, "base")

        asyncio.run(
            self.processor.process(
                device_topic("pump-controller", "up", "hello"), payload
            )
        )

        mapping = self.redis.hset.await_args.kwargs["mapping"]
        self.assertEqual(profile.identifier, mapping["board"])
        self.assertEqual("minimal-agent", mapping["tier_code"])

    def test_tier_mismatch_is_rejected_without_registration(self) -> None:
        payload = encode_hello(1, "esp32-s3", "1.0.0", 3, "base")

        asyncio.run(
            self.processor.process(device_topic("bad-tier", "up", "hello"), payload)
        )

        self.redis.hset.assert_not_awaited()
        error_payload = self.publisher.publish.call_args.args[1]
        self.assertIn(b"tier-mismatch", error_payload)

    def test_telemetry_is_written_to_bounded_stream(self) -> None:
        self.redis.exists.return_value = True
        self.redis.set.return_value = True
        payload = encode_telemetry(7, "soil-moisture", 43.2, "pct")

        asyncio.run(
            self.processor.process(
                device_topic("garden-1", "up", "telemetry"), payload
            )
        )

        stream_call = self.redis.xadd.await_args
        self.assertEqual("device-telemetry", stream_call.args[0])
        self.assertEqual("43.2", stream_call.args[1]["value"])
        self.assertEqual(100_000, stream_call.kwargs["maxlen"])

    def test_ack_updates_existing_command(self) -> None:
        self.redis.exists.return_value = True
        self.redis.set.return_value = True
        self.redis.hget.return_value = "relay-1"
        payload = encode_ack(9, "cmd-123", "completed")

        asyncio.run(
            self.processor.process(device_topic("relay-1", "up", "ack"), payload)
        )

        command_updates = [
            call
            for call in self.redis.hset.await_args_list
            if call.args and call.args[0] == device_command_key("cmd-123")
        ]
        self.assertEqual("completed", command_updates[0].kwargs["mapping"]["status"])
        self.assertTrue(
            any(call.args[0] == device_key("relay-1") for call in self.redis.hset.await_args_list)
        )

    def test_duplicate_qos_one_frame_is_ignored(self) -> None:
        self.redis.exists.return_value = True
        self.redis.set.return_value = False
        payload = encode_ack(9, "cmd-123", "completed")

        asyncio.run(
            self.processor.process(device_topic("relay-1", "up", "ack"), payload)
        )

        self.redis.hget.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
