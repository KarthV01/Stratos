import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException

from app.controller import ingest_vehicle_telemetry
from app.mavlink.api_models import VehicleTelemetryBatch
from app.mavlink.models import (
    FlightSnapshotV1,
    GlobalPosition,
    LinkState,
    VehicleIdentity,
)
from app.mavlink.store import (
    VehicleTelemetryStore,
    decode_track_entry,
    refresh_link_freshness,
)


def sample(vehicle_id: str = "aircraft-01") -> FlightSnapshotV1:
    return FlightSnapshotV1(
        vehicle_id=vehicle_id,
        device_id="companion-01",
        received_at=1000.25,
        identity=VehicleIdentity(
            system_id=1,
            component_id=1,
            autopilot="px4",
            vehicle_type="quadrotor",
            mavlink_version=2,
        ),
        position=GlobalPosition(
            boot_ms=5000,
            latitude_e7=401234567,
            longitude_e7=-731234567,
            altitude_msl_mm=12_345,
            relative_altitude_mm=2_345,
            ground_speed_cms=500,
            heading_cdeg=None,
        ),
        link=LinkState(connected=True, session_id=1),
    )


def redis_with_pipeline(previous: dict[str, str] | None = None):
    redis = Mock()
    redis.hgetall = AsyncMock(return_value=previous or {})
    redis.zrevrange = AsyncMock(return_value=[])
    redis.xrevrange = AsyncMock(return_value=[])
    pipe = Mock()
    pipe.hset = Mock()
    pipe.zadd = Mock()
    pipe.xadd = Mock()
    pipe.hgetall = Mock()
    pipe.execute = AsyncMock(return_value=[])
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=None)
    redis.pipeline = Mock(return_value=pipe)
    return redis, pipe


class VehicleTelemetryStoreTests(unittest.TestCase):
    def test_staleness_is_recomputed_at_read_time(self) -> None:
        decoded = sample().as_dict()
        decoded["link"]["last_heartbeat_at"] = 10.0
        self.assertTrue(refresh_link_freshness(decoded, now=15.0)["link"]["connected"])
        self.assertFalse(refresh_link_freshness(decoded, now=15.001)["link"]["connected"])

    def test_ingest_writes_current_snapshot_track_and_discovery_event(self) -> None:
        redis, pipe = redis_with_pipeline()
        asyncio.run(VehicleTelemetryStore(redis).ingest(sample()))

        current = pipe.hset.call_args.kwargs["mapping"]
        decoded = json.loads(current["snapshot"])
        self.assertEqual("aircraft-01", decoded["vehicle_id"])
        self.assertEqual("px4", decoded["identity"]["autopilot"])
        self.assertEqual("1", current["connected"])
        self.assertEqual(2, pipe.xadd.call_count)
        track_call = pipe.xadd.call_args_list[0]
        self.assertEqual("vehicle-track:aircraft-01", track_call.args[0])
        self.assertEqual("null", track_call.args[1]["heading_cdeg"])
        event_call = pipe.xadd.call_args_list[1]
        self.assertEqual("vehicle-events", event_call.args[0])
        self.assertEqual("vehicle-discovered", event_call.args[1]["kind"])

    def test_ingest_emits_reboot_and_link_transition_events(self) -> None:
        previous = {"session_id": "1", "connected": "1"}
        redis, pipe = redis_with_pipeline(previous)
        value = sample()
        value.link.session_id = 2
        value.link.connected = False
        asyncio.run(VehicleTelemetryStore(redis).ingest(value))

        kinds = [
            call.args[1]["kind"]
            for call in pipe.xadd.call_args_list
            if call.args[0] == "vehicle-events"
        ]
        self.assertEqual(["autopilot-reboot", "link-state"], kinds)

    def test_track_decoder_preserves_integer_units_and_nulls(self) -> None:
        decoded = decode_track_entry(
            "1000-0",
            {
                "received_at": "1.25",
                "latitude_e7": "401234567",
                "longitude_e7": "-731234567",
                "heading_cdeg": "null",
            },
        )
        self.assertEqual(401234567, decoded["latitude_e7"])
        self.assertIsNone(decoded["heading_cdeg"])


class VehicleTelemetryApiTests(unittest.TestCase):
    def test_native_batch_is_accepted(self) -> None:
        redis, _ = redis_with_pipeline()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
        body = VehicleTelemetryBatch(device_id="companion-01", samples=[sample()])

        response = asyncio.run(
            ingest_vehicle_telemetry("aircraft-01", body, request)
        )
        self.assertEqual(1, response["accepted"])

    def test_path_cannot_claim_a_different_vehicle(self) -> None:
        redis, _ = redis_with_pipeline()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
        body = VehicleTelemetryBatch(device_id="companion-01", samples=[sample()])

        with self.assertRaises(HTTPException) as context:
            asyncio.run(ingest_vehicle_telemetry("aircraft-02", body, request))
        self.assertEqual(422, context.exception.status_code)


if __name__ == "__main__":
    unittest.main()
