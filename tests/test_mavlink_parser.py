import unittest
from dataclasses import dataclass
from typing import Any

from pymavlink import mavutil
from pymavlink.dialects.v20 import common as mavlink2

from app.mavlink.parser import MavlinkTelemetryAccumulator


@dataclass
class FakeMessage:
    message_type: str
    fields: dict[str, Any]
    sequence: int = 0
    system_id: int = 1
    component_id: int = 1
    wire_version: int = 2

    def __getattr__(self, name: str) -> Any:
        try:
            return self.fields[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def get_type(self) -> str:
        return self.message_type

    def get_srcSystem(self) -> int:
        return self.system_id

    def get_srcComponent(self) -> int:
        return self.component_id

    def get_seq(self) -> int:
        return self.sequence


def message(message_type: str, sequence: int = 0, **fields: Any) -> FakeMessage:
    return FakeMessage(message_type, fields, sequence=sequence)


class MavlinkTelemetryAccumulatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = MavlinkTelemetryAccumulator("aircraft-01", "companion-01")

    def heartbeat(self, *, autopilot: int = 12, sequence: int = 0) -> FakeMessage:
        return message(
            "HEARTBEAT",
            sequence,
            type=2,
            autopilot=autopilot,
            base_mode=128,
            custom_mode=4,
            system_status=4,
        )

    def test_detects_px4_and_armed_state_from_heartbeat(self) -> None:
        self.assertTrue(self.parser.process(self.heartbeat(), received_at=100.0))
        snapshot = self.parser.snapshot(received_at=101.0)

        self.assertEqual("px4", snapshot.identity.autopilot)
        self.assertEqual("quadrotor", snapshot.identity.vehicle_type)
        self.assertEqual(2, snapshot.identity.mavlink_version)
        self.assertTrue(snapshot.state.armed)
        self.assertTrue(snapshot.link.connected)

    def test_foreign_or_non_autopilot_heartbeat_cannot_replace_vehicle(self) -> None:
        self.parser.process(self.heartbeat(), received_at=100.0)
        foreign = self.heartbeat()
        foreign.system_id = 42
        self.assertFalse(self.parser.process(foreign, received_at=101.0))
        self.assertEqual(1, self.parser.identity.system_id)
        payload_component = self.heartbeat()
        payload_component.fields["autopilot"] = 8
        self.assertFalse(self.parser.process(payload_component, received_at=102.0))
        self.assertEqual("px4", self.parser.identity.autopilot)

    def test_high_latency2_does_not_invent_gps_or_landed_state(self) -> None:
        self.parser.process(self.heartbeat(), received_at=100.0)
        message = FakeMessage(
            "HIGH_LATENCY2",
            {
                "timestamp": 2000,
                "latitude": 401234567,
                "longitude": -731234567,
                "altitude": 123,
                "heading": 255,
                "groundspeed": 8,
                "battery": -1,
                "custom_mode": 4,
                "autopilot": 12,
                "type": 2,
            },
        )
        self.parser.gps.fix_type = 4
        self.parser.state.landed_state = 1
        self.parser.process(message, received_at=101.0)
        self.assertEqual(4, self.parser.gps.fix_type)
        self.assertEqual(1, self.parser.state.landed_state)
        self.assertIsNone(self.parser.position.heading_cdeg)
        self.assertEqual(160, self.parser.position.ground_speed_cms)

    def test_detects_ardupilot(self) -> None:
        self.parser.process(self.heartbeat(autopilot=3), received_at=100.0)
        self.assertEqual("ardupilot", self.parser.snapshot(100.0).identity.autopilot)

    def test_normalizes_global_position_and_invalid_heading(self) -> None:
        self.parser.process(self.heartbeat(), received_at=99.0)
        self.parser.process(
            message(
                "GLOBAL_POSITION_INT",
                time_boot_ms=10_000,
                lat=401234567,
                lon=-731234567,
                alt=12_345,
                relative_alt=2_345,
                vx=300,
                vy=400,
                vz=10,
                hdg=65_535,
            ),
            received_at=100.0,
        )
        position = self.parser.snapshot(100.0).position

        self.assertEqual(401234567, position.latitude_e7)
        self.assertEqual(-731234567, position.longitude_e7)
        self.assertEqual(500, position.ground_speed_cms)
        self.assertIsNone(position.heading_cdeg)

    def test_tracks_sequence_loss_and_autopilot_restart(self) -> None:
        self.parser.process(self.heartbeat(sequence=5), received_at=100.0)
        self.parser.process(self.heartbeat(sequence=8), received_at=101.0)
        self.parser.process(
            message(
                "GLOBAL_POSITION_INT",
                9,
                time_boot_ms=20_000,
                lat=40,
                lon=-73,
                alt=1000,
                relative_alt=100,
                vx=0,
                vy=0,
                vz=0,
                hdg=0,
            ),
            received_at=102.0,
        )
        self.parser.process(
            message(
                "ATTITUDE",
                10,
                time_boot_ms=50,
                roll=0.0,
                pitch=0.0,
                yaw=0.0,
                rollspeed=0.0,
                pitchspeed=0.0,
                yawspeed=0.0,
            ),
            received_at=103.0,
        )
        self.assertEqual(1, self.parser.snapshot(103.0).link.session_id)
        self.parser.process(
            message(
                "GLOBAL_POSITION_INT",
                11,
                time_boot_ms=50,
                lat=40,
                lon=-73,
                alt=1000,
                relative_alt=100,
                vx=0,
                vy=0,
                vz=0,
                hdg=0,
            ),
            received_at=104.0,
        )

        snapshot = self.parser.snapshot(104.0)
        self.assertEqual(2, snapshot.link.dropped_messages)
        self.assertEqual(2, snapshot.link.session_id)
        self.assertEqual("autopilot-reboot", self.parser.drain_events()[0].kind)

    def test_marks_link_stale_after_five_seconds(self) -> None:
        self.parser.process(self.heartbeat(), received_at=100.0)
        self.assertTrue(self.parser.snapshot(105.0).link.connected)
        self.assertFalse(self.parser.snapshot(105.001).link.connected)

    def test_counts_unknown_and_bad_messages(self) -> None:
        self.assertFalse(self.parser.process(message("NAMED_VALUE_FLOAT")))
        self.assertFalse(self.parser.process(message("BAD_DATA")))
        snapshot = self.parser.snapshot()
        self.assertEqual(1, snapshot.link.unknown_messages)
        self.assertEqual(1, snapshot.link.parse_errors)

    def test_mavlink1_requires_explicit_compatibility(self) -> None:
        version_one_heartbeat = FakeMessage(
            "HEARTBEAT",
            {
                "type": 2,
                "autopilot": 12,
                "base_mode": 128,
                "custom_mode": 1,
                "system_status": 4,
            },
            wire_version=1,
        )
        self.assertFalse(self.parser.process(version_one_heartbeat))
        self.assertIsNone(self.parser.identity.system_id)

        compatible = MavlinkTelemetryAccumulator(
            "aircraft-02", "companion-02", allow_mavlink1=True
        )
        self.assertTrue(compatible.process(version_one_heartbeat))
        self.assertEqual(1, compatible.identity.mavlink_version)

    def test_status_text_is_a_bounded_normalized_event(self) -> None:
        self.parser.process(self.heartbeat(), received_at=199.0)
        self.parser.process(
            message("STATUSTEXT", severity=3, text=b"GPS failsafe\x00"),
            received_at=200.0,
        )
        event = self.parser.drain_events()[0]
        self.assertEqual(3, event.severity)
        self.assertEqual("GPS failsafe", event.message)

    def test_real_mavlink2_binary_heartbeat_can_be_parsed(self) -> None:
        sender = mavlink2.MAVLink(None, srcSystem=23, srcComponent=1)
        outbound = sender.heartbeat_encode(
            mavlink2.MAV_TYPE_QUADROTOR,
            mavlink2.MAV_AUTOPILOT_PX4,
            mavlink2.MAV_MODE_FLAG_SAFETY_ARMED,
            7,
            mavlink2.MAV_STATE_ACTIVE,
        )
        payload = outbound.pack(sender)
        receiver = mavlink2.MAVLink(None)
        decoded = None
        for value in payload:
            candidate = receiver.parse_char(bytes((value,)))
            if candidate is not None:
                decoded = candidate

        self.assertIsNotNone(decoded)
        self.parser.process(decoded, received_at=300.0)
        snapshot = self.parser.snapshot(300.0)
        self.assertEqual(23, snapshot.identity.system_id)
        self.assertEqual("px4", snapshot.identity.autopilot)
        self.assertEqual(2, snapshot.identity.mavlink_version)


if __name__ == "__main__":
    unittest.main()
