import unittest

from app.mqtt_protocol import (
    FrameType,
    availability_topic,
    decode_frame,
    device_topic,
    encode_ack,
    encode_command,
    encode_hello,
    encode_geospatial_sensor,
    encode_map_snapshot,
    encode_telemetry,
    encode_vehicle,
    ensure_topic_matches_frame,
    parse_topic,
)


class MqttTopicTests(unittest.TestCase):
    def test_device_topics_round_trip(self) -> None:
        value = device_topic("greenhouse-07", "up", "telemetry")

        self.assertEqual(
            "stratos/v1/devices/greenhouse-07/up/telemetry", value
        )
        self.assertEqual("greenhouse-07", parse_topic(value).device_id)
        self.assertEqual(
            "stratos/v1/devices/greenhouse-07/availability",
            availability_topic("greenhouse-07"),
        )

    def test_wildcards_and_path_injection_are_rejected(self) -> None:
        for device_id in ("fleet/one", "fleet+", "#", ""):
            with self.subTest(device_id=device_id), self.assertRaises(ValueError):
                device_topic(device_id, "up", "hello")


class MqttFrameTests(unittest.TestCase):
    def test_geospatial_sensor_is_atomic_and_validated(self) -> None:
        payload = encode_geospatial_sensor(
            14, "temperature", "21.5", "C", 407128001, -740060002, 12500
        )
        frame = decode_frame(payload)
        self.assertEqual(FrameType.GEO_SENSOR, frame.frame_type)
        self.assertEqual("407128001", frame.fields[3])
        self.assertLess(len(payload), 128)
        with self.assertRaises(ValueError):
            encode_geospatial_sensor(15, "temperature", 21, "C", 900000001, 0)

    def test_tier_three_hello_is_compact(self) -> None:
        payload = encode_hello(1, "arduino-uno-r4-wifi", "1.0.0", 3, "base")
        frame = decode_frame(payload)

        self.assertLess(len(payload), 64)
        self.assertEqual(FrameType.HELLO, frame.frame_type)
        self.assertEqual(("arduino-uno-r4-wifi", "1.0.0", "3", "base"), frame.fields)

    def test_telemetry_accepts_decimal_not_arbitrary_text(self) -> None:
        payload = encode_telemetry(8, "temperature", "21.75", "C")

        self.assertEqual(("temperature", "21.75", "C"), decode_frame(payload).fields)
        with self.assertRaises(ValueError):
            encode_telemetry(9, "temperature", "hot", "C")

    def test_command_and_ack_carry_idempotency_key(self) -> None:
        command = decode_frame(encode_command(12, "cmd-8f31", "relay.set", "on"))
        ack = decode_frame(encode_ack(13, "cmd-8f31", "completed"))

        self.assertEqual("cmd-8f31", command.fields[0])
        self.assertEqual("cmd-8f31", ack.fields[0])

    def test_vehicle_binding_is_compact_and_validated(self) -> None:
        payload = encode_vehicle(2, "aircraft-01", 1, 191, "px4", "quadrotor", 2)
        frame = decode_frame(payload)

        self.assertEqual(FrameType.VEHICLE, frame.frame_type)
        self.assertTrue(payload.startswith(b"SMP1|B|"))
        self.assertLess(len(payload), 96)
        self.assertEqual("aircraft-01", frame.fields[0])
        with self.assertRaises(ValueError):
            encode_vehicle(3, "aircraft-01", 0, 191, "px4", "quadrotor", 2)

    def test_map_snapshot_uses_scaled_integers_and_null_sentinel(self) -> None:
        payload = encode_map_snapshot(
            3,
            boot_ms=5000,
            latitude_e7=401234567,
            longitude_e7=-731234567,
            altitude_msl_mm=12_345,
            relative_altitude_mm=2_345,
            ground_speed_cms=500,
            heading_cdeg=None,
            gps_fix_type=3,
            satellites_visible=12,
            battery_remaining_percent=87,
            flags=57,
        )
        frame = decode_frame(payload)

        self.assertEqual(FrameType.MAP, frame.frame_type)
        self.assertTrue(payload.startswith(b"SMP1|P|"))
        self.assertEqual("na", frame.fields[6])
        self.assertEqual("1", frame.fields[11])
        self.assertLess(len(payload), 128)
        with self.assertRaises(ValueError):
            decode_frame(payload.replace(b"|5000|", b"|5.5|"))
        with self.assertRaises(ValueError):
            encode_map_snapshot(
                4,
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
                session_id=0,
            )
        with self.assertRaises(ValueError):
            encode_map_snapshot(
                4,
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
                flags=64,
            )

    def test_topic_must_match_frame_type(self) -> None:
        frame = decode_frame(encode_telemetry(1, "humidity", 45, "pct"))
        correct = parse_topic(device_topic("sensor-1", "up", "telemetry"))
        wrong = parse_topic(device_topic("sensor-1", "up", "state"))

        ensure_topic_matches_frame(correct, frame)
        with self.assertRaises(ValueError):
            ensure_topic_matches_frame(wrong, frame)

    def test_malformed_or_oversized_frames_are_rejected(self) -> None:
        for payload in (b"BAD|H|1|x", b"SMP1|T|1|temp|nan|C", b"x" * 257):
            with self.subTest(payload=payload[:20]), self.assertRaises(ValueError):
                decode_frame(payload)


if __name__ == "__main__":
    unittest.main()
