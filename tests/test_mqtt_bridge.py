import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.boards import AgentTier, board_directory
from app.mqtt_bridge import MqttBridge, DeviceMessageProcessor, device_command_key, device_key
from app.mqtt_protocol import (
    device_topic,
    encode_ack,
    encode_drone_snapshot,
    encode_hello,
    encode_map_snapshot,
    encode_minimal_status,
    encode_telemetry,
    encode_vehicle,
)


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
        self.redis.hget.return_value = "2"
        self.redis.set.return_value = True
        payload = encode_telemetry(7, "soil-moisture", 43.2, "pct")

        asyncio.run(
            self.processor.process(
                device_topic("garden-1", "up", "telemetry"), payload
            )
        )

        stream_call = self.redis.xadd.await_args
        self.assertEqual("device-telemetry", stream_call.args[0])
        self.assertIn('"value":"43.2"', stream_call.args[1]["payload"])
        self.assertEqual("2", stream_call.args[1]["tier"])
        self.assertEqual(100_000, stream_call.kwargs["maxlen"])

    def test_ack_updates_existing_command(self) -> None:
        self.redis.exists.return_value = True
        self.redis.set.return_value = True
        self.redis.hget.side_effect = ["2", "relay-1"]
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
        self.redis.hget.return_value = "2"
        self.redis.set.return_value = False
        payload = encode_ack(9, "cmd-123", "completed")

        asyncio.run(
            self.processor.process(device_topic("relay-1", "up", "ack"), payload)
        )

        self.assertEqual(1, self.redis.hget.await_count)

    def test_embedded_drone_snapshot_is_normalized(self) -> None:
        self.redis.exists.return_value = True
        self.redis.hget.return_value = "2"
        self.redis.set.return_value = True
        payload = encode_drone_snapshot(
            12, 9000, 407128001, -740060002, 42500, 120, -30, 5, 9120, 22400, 76, 3
        )

        asyncio.run(
            self.processor.process(
                device_topic("drone-2", "up", "telemetry"), payload
            )
        )

        latest_calls = [
            call
            for call in self.redis.hset.await_args_list
            if call.args and call.args[0] == "device-telemetry-latest:drone-2"
        ]
        self.assertIn('"latitude_e7":407128001', latest_calls[0].kwargs["mapping"]["payload"])

    def test_minimal_agent_cannot_send_embedded_snapshot(self) -> None:
        self.redis.exists.return_value = True
        self.redis.hget.return_value = "3"
        payload = encode_drone_snapshot(
            12, 9000, 407128001, -740060002, 42500, 120, -30, 5, 9120, 22400, 76, 3
        )

        asyncio.run(
            self.processor.process(
                device_topic("minimal-3", "up", "telemetry"), payload
            )
        )

        self.redis.set.assert_not_awaited()
        self.assertIn(b"tier-frame", self.publisher.publish.call_args.args[1])

    def test_minimal_status_is_accepted(self) -> None:
        self.redis.exists.return_value = True
        self.redis.hget.return_value = "3"
        self.redis.set.return_value = True

        asyncio.run(
            self.processor.process(
                device_topic("minimal-3", "up", "telemetry"),
                encode_minimal_status(4, 3, 76, 91),
            )
        )

        stream_call = self.redis.xadd.await_args
        self.assertEqual("essential", stream_call.args[1]["kind"])

    def test_vehicle_binding_records_mavlink_identity(self) -> None:
        self.redis.exists.return_value = True
        self.redis.hget.return_value = "2"
        self.redis.set.return_value = True
        payload = encode_vehicle(2, "aircraft-01", 1, 191, "px4", "quadrotor", 2)

        asyncio.run(
            self.processor.process(device_topic("companion-01", "up", "state"), payload)
        )

        mappings = [call.kwargs.get("mapping") for call in self.redis.hset.await_args_list]
        binding = next(mapping for mapping in mappings if mapping and "vehicle_id" in mapping)
        self.assertEqual("aircraft-01", binding["vehicle_id"])
        self.assertEqual("191", binding["mav_component_id"])

    def test_map_snapshot_is_normalized_into_shared_vehicle_store(self) -> None:
        self.redis.exists.return_value = True
        self.redis.hget.return_value = "2"
        self.redis.hgetall.return_value = {
            "tier": "2",
            "vehicle_id": "aircraft-01",
            "mav_system_id": "1",
            "mav_component_id": "191",
            "mav_autopilot": "px4",
            "mav_vehicle_type": "quadrotor",
            "mavlink_version": "2",
        }
        payload = encode_map_snapshot(
            3,
            boot_ms=5000,
            latitude_e7=401234567,
            longitude_e7=-731234567,
            altitude_msl_mm=12_345,
            relative_altitude_mm=2_345,
            ground_speed_cms=500,
            heading_cdeg=9000,
            gps_fix_type=3,
            satellites_visible=12,
            battery_remaining_percent=87,
            flags=57,
            session_id=3,
        )
        store = AsyncMock()
        with patch("app.mqtt_bridge.VehicleTelemetryStore", return_value=store):
            with patch("app.mqtt_bridge.time.time", return_value=1000.5):
                asyncio.run(
                    self.processor.process(
                        device_topic("companion-01", "up", "telemetry"), payload
                    )
                )

        snapshot = store.ingest.await_args.args[0]
        self.assertEqual("aircraft-01", snapshot.vehicle_id)
        self.assertEqual(401234567, snapshot.position.latitude_e7)
        self.assertEqual(87, snapshot.battery.remaining_percent)
        self.assertTrue(snapshot.state.armed)
        self.assertTrue(snapshot.link.connected)
        self.assertEqual(3, snapshot.link.session_id)

    def test_map_requires_vehicle_binding(self) -> None:
        self.redis.exists.return_value = True
        self.redis.hget.return_value = "3"
        self.redis.hgetall.return_value = {"tier": "3"}
        payload = encode_map_snapshot(
            3,
            boot_ms=None,
            latitude_e7=None,
            longitude_e7=None,
            altitude_msl_mm=None,
            relative_altitude_mm=None,
            ground_speed_cms=None,
            heading_cdeg=None,
            gps_fix_type=None,
            satellites_visible=None,
            battery_remaining_percent=None,
            flags=0,
        )
        asyncio.run(
            self.processor.process(
                device_topic("companion-01", "up", "telemetry"), payload
            )
        )
        self.assertIn(b"vehicle-required", self.publisher.publish.call_args.args[1])

    def test_tier_three_map_rate_is_limited_to_one_hz(self) -> None:
        self.redis.exists.return_value = True
        self.redis.hget.return_value = "3"
        self.redis.hgetall.return_value = {
            "tier": "3",
            "vehicle_id": "aircraft-01",
            "mav_system_id": "1",
            "mav_component_id": "191",
            "mav_autopilot": "ardupilot",
            "mav_vehicle_type": "quadrotor",
            "mavlink_version": "2",
            "last_map_at": "1000.0",
        }
        payload = encode_map_snapshot(
            4,
            boot_ms=5500,
            latitude_e7=401234567,
            longitude_e7=-731234567,
            altitude_msl_mm=12_345,
            relative_altitude_mm=2_345,
            ground_speed_cms=500,
            heading_cdeg=9000,
            gps_fix_type=3,
            satellites_visible=12,
            battery_remaining_percent=87,
            flags=57,
        )
        store = AsyncMock()
        with patch("app.mqtt_bridge.VehicleTelemetryStore", return_value=store):
            with patch("app.mqtt_bridge.time.time", return_value=1000.5):
                asyncio.run(
                    self.processor.process(
                        device_topic("companion-01", "up", "telemetry"), payload
                    )
                )
        store.ingest.assert_not_awaited()


class MqttBridgeQueueTests(unittest.TestCase):
    def test_only_latest_map_is_retained_per_device(self) -> None:
        async def exercise() -> None:
            with patch("app.mqtt_bridge.mqtt.Client"):
                bridge = MqttBridge(AsyncMock())
            topic = device_topic("companion-01", "up", "telemetry")
            bridge._enqueue_message((topic, b"SMP1|P|1|old"))
            bridge._enqueue_message((topic, b"SMP1|P|2|new"))
            self.assertEqual((topic, b"SMP1|P|2|new"), bridge.latest_maps[topic])
            self.assertTrue(bridge.maps_ready.is_set())

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
