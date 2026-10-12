import unittest

from app.mqtt_protocol import (
    FrameType,
    availability_topic,
    decode_frame,
    device_topic,
    encode_ack,
    encode_command,
    encode_hello,
    encode_telemetry,
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
