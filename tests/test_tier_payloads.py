import unittest

from pydantic import ValidationError

from app.mqtt_protocol import (
    FrameType,
    decode_frame,
    encode_drone_snapshot,
    encode_event,
    encode_minimal_status,
)
from app.tier_payloads import (
    AgentResources,
    ComputeState,
    FlightState,
    FullAgentHello,
    FullTelemetry,
    LinkState,
    PowerState,
    decode_full_payload,
    encode_full_payload,
)


class FullAgentPayloadTests(unittest.TestCase):
    def test_full_agent_hello_round_trip(self) -> None:
        hello = FullAgentHello(
            agent_id="drone-07-companion",
            sequence=1,
            sent_at_ms=1_700_000_000_000,
            board="nvidia/jetson-orin-nano",
            firmware="2.4.1",
            boot_id="boot-a81f",
            capabilities=["mission.execute", "vision.detect", "telemetry.batch"],
            resources=AgentResources(
                cpu_cores=6,
                memory_mb=8192,
                storage_mb=65536,
                gpu="orin-ampere",
                max_concurrency=4,
            ),
        )

        encoded = encode_full_payload(hello)
        decoded = decode_full_payload(encoded)

        self.assertIsInstance(decoded, FullAgentHello)
        self.assertEqual("drone-07-companion", decoded.agent_id)
        self.assertLess(len(encoded), 1024)

    def test_full_telemetry_carries_flight_compute_and_link_state(self) -> None:
        telemetry = FullTelemetry(
            agent_id="drone-07-companion",
            sequence=22,
            sent_at_ms=1_700_000_001_000,
            uptime_ms=88_000,
            flight=FlightState(
                armed=True,
                mode="AUTO",
                system_status="active",
                latitude_e7=407_128_001,
                longitude_e7=-740_060_002,
                relative_altitude_mm=42_500,
                heading_cdeg=9_120,
                gps_fix=3,
                satellites=17,
            ),
            power=PowerState(voltage_mv=22_400, current_ma=8_200, remaining_pct=76),
            link=LinkState(rssi_dbm=-61, quality_pct=91, round_trip_ms=38),
            compute=ComputeState(
                cpu_pct=37.5,
                memory_used_mb=2810,
                disk_used_mb=9000,
                temperature_c=54.2,
            ),
            sensors={"rangefinder_m": 12.4},
        )

        decoded = decode_full_payload(encode_full_payload(telemetry))

        self.assertEqual("AUTO", decoded.flight.mode)
        self.assertEqual(54.2, decoded.compute.temperature_c)

    def test_full_payload_rejects_out_of_range_coordinates(self) -> None:
        with self.assertRaises(ValidationError):
            FlightState(
                armed=False,
                mode="STANDBY",
                system_status="standby",
                latitude_e7=1_000_000_000,
            )


class ConstrainedPayloadTests(unittest.TestCase):
    def test_embedded_snapshot_uses_fixed_point_integers(self) -> None:
        payload = encode_drone_snapshot(
            44,
            88_000,
            407_128_001,
            -740_060_002,
            42_500,
            120,
            -30,
            5,
            9_120,
            22_400,
            76,
            3,
        )
        frame = decode_frame(payload)

        self.assertEqual(FrameType.DRONE_SNAPSHOT, frame.frame_type)
        self.assertLess(len(payload), 128)
        self.assertEqual("407128001", frame.fields[1])

    def test_minimal_status_stays_tiny(self) -> None:
        payload = encode_minimal_status(9, flags=3, battery_pct=76, link_pct=91)

        self.assertEqual(b"SMP1|M|9|3|76|91", payload)
        self.assertLess(len(payload), 32)

    def test_minimal_status_rejects_invalid_percentage(self) -> None:
        with self.assertRaises(ValueError):
            encode_minimal_status(9, flags=0, battery_pct=101, link_pct=50)

    def test_embedded_event_has_bounded_token_fields(self) -> None:
        payload = encode_event(10, "gps.lost", "critical", "fix-dropped")

        self.assertEqual(FrameType.EVENT, decode_frame(payload).frame_type)


if __name__ == "__main__":
    unittest.main()
