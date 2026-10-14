"""Redis persistence for normalized vehicle telemetry."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from redis.asyncio import Redis

from app.config import VEHICLE_EVENT_STREAM, VEHICLE_INDEX, VEHICLE_TRACK_MAXLEN
from app.mavlink.models import FlightSnapshotV1, MavlinkEvent


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
VEHICLE_STALE_SECONDS = 5.0


def validate_vehicle_id(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid vehicle ID")
    return value


def vehicle_key(vehicle_id: str) -> str:
    return f"vehicle:{validate_vehicle_id(vehicle_id)}"


def vehicle_track_key(vehicle_id: str) -> str:
    return f"vehicle-track:{validate_vehicle_id(vehicle_id)}"


def decode_vehicle(record: dict[str, str]) -> dict[str, Any]:
    return json.loads(record["snapshot"])


def refresh_link_freshness(snapshot: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Compute staleness at read time so a silent device does not stay online forever."""
    current_time = time.time() if now is None else now
    link = snapshot.get("link", {})
    last_heartbeat = link.get("last_heartbeat_at")
    link["connected"] = bool(
        last_heartbeat is not None
        and 0 <= current_time - float(last_heartbeat) <= VEHICLE_STALE_SECONDS
    )
    snapshot["link"] = link
    return snapshot


def decode_track_entry(entry_id: str, fields: dict[str, str]) -> dict[str, Any]:
    decoded: dict[str, Any] = {"stream_id": entry_id}
    for key, value in fields.items():
        if value == "null":
            decoded[key] = None
        elif key in {
            "boot_ms",
            "latitude_e7",
            "longitude_e7",
            "altitude_msl_mm",
            "relative_altitude_mm",
            "ground_speed_cms",
            "heading_cdeg",
        }:
            decoded[key] = int(value)
        elif key == "received_at":
            decoded[key] = float(value)
        else:
            decoded[key] = value
    return decoded


def _track_value(value: int | None) -> str:
    return "null" if value is None else str(value)


class VehicleTelemetryStore:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def ingest(
        self,
        snapshot: FlightSnapshotV1,
        events: list[MavlinkEvent] | None = None,
    ) -> None:
        validate_vehicle_id(snapshot.vehicle_id)
        previous = await self.redis.hgetall(vehicle_key(snapshot.vehicle_id))
        payload = json.dumps(snapshot.as_dict(), separators=(",", ":"))
        identity = snapshot.identity
        record = {
            "vehicle_id": snapshot.vehicle_id,
            "device_id": snapshot.device_id,
            "schema": snapshot.schema,
            "received_at": str(snapshot.received_at),
            "connected": "1" if snapshot.link.connected else "0",
            "session_id": str(snapshot.link.session_id),
            "system_id": "" if identity.system_id is None else str(identity.system_id),
            "component_id": (
                "" if identity.component_id is None else str(identity.component_id)
            ),
            "autopilot": identity.autopilot or "",
            "vehicle_type": identity.vehicle_type or "",
            "snapshot": payload,
        }
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(vehicle_key(snapshot.vehicle_id), mapping=record)
            pipe.zadd(VEHICLE_INDEX, {snapshot.vehicle_id: snapshot.received_at})
            if (
                snapshot.position.latitude_e7 is not None
                and snapshot.position.longitude_e7 is not None
            ):
                position = snapshot.position
                pipe.xadd(
                    vehicle_track_key(snapshot.vehicle_id),
                    {
                        "received_at": str(snapshot.received_at),
                        "boot_ms": _track_value(position.boot_ms),
                        "latitude_e7": str(position.latitude_e7),
                        "longitude_e7": str(position.longitude_e7),
                        "altitude_msl_mm": _track_value(position.altitude_msl_mm),
                        "relative_altitude_mm": _track_value(
                            position.relative_altitude_mm
                        ),
                        "ground_speed_cms": _track_value(position.ground_speed_cms),
                        "heading_cdeg": _track_value(position.heading_cdeg),
                    },
                    maxlen=VEHICLE_TRACK_MAXLEN,
                    approximate=True,
                )
            for event in self._derived_events(snapshot, previous):
                pipe.xadd(
                    VEHICLE_EVENT_STREAM,
                    self._event_fields(event),
                    maxlen=10_000,
                    approximate=True,
                )
            for event in events or []:
                if event.vehicle_id != snapshot.vehicle_id:
                    raise ValueError("Event vehicle ID does not match snapshot")
                pipe.xadd(
                    VEHICLE_EVENT_STREAM,
                    self._event_fields(event),
                    maxlen=10_000,
                    approximate=True,
                )
            await pipe.execute()

    async def list(self, limit: int) -> list[dict[str, Any]]:
        ids = await self.redis.zrevrange(VEHICLE_INDEX, 0, limit - 1)
        if not ids:
            return []
        async with self.redis.pipeline(transaction=False) as pipe:
            for vehicle_id in ids:
                pipe.hgetall(vehicle_key(vehicle_id))
            records = await pipe.execute()
        return [
            refresh_link_freshness(decode_vehicle(record))
            for record in records
            if record
        ]

    async def get(self, vehicle_id: str) -> dict[str, Any] | None:
        record = await self.redis.hgetall(vehicle_key(vehicle_id))
        return refresh_link_freshness(decode_vehicle(record)) if record else None

    async def track(
        self, vehicle_id: str, *, since: float | None, limit: int
    ) -> list[dict[str, Any]]:
        minimum = "-" if since is None else f"{int(since * 1000)}-0"
        rows = await self.redis.xrevrange(
            vehicle_track_key(vehicle_id), max="+", min=minimum, count=limit
        )
        return [decode_track_entry(entry_id, fields) for entry_id, fields in reversed(rows)]

    def _derived_events(
        self, snapshot: FlightSnapshotV1, previous: dict[str, str]
    ) -> list[MavlinkEvent]:
        if not previous:
            return [
                MavlinkEvent(
                    snapshot.vehicle_id,
                    snapshot.received_at,
                    "vehicle-discovered",
                    None,
                    snapshot.device_id,
                )
            ]
        events: list[MavlinkEvent] = []
        previous_session = int(previous.get("session_id", "1"))
        if snapshot.link.session_id > previous_session:
            events.append(
                MavlinkEvent(
                    snapshot.vehicle_id,
                    snapshot.received_at,
                    "autopilot-reboot",
                    None,
                    "session-changed",
                )
            )
        connected = "1" if snapshot.link.connected else "0"
        if previous.get("connected") != connected:
            events.append(
                MavlinkEvent(
                    snapshot.vehicle_id,
                    snapshot.received_at,
                    "link-state",
                    None,
                    "connected" if snapshot.link.connected else "stale",
                )
            )
        return events

    @staticmethod
    def _event_fields(event: MavlinkEvent) -> dict[str, str]:
        return {
            "vehicle_id": event.vehicle_id,
            "received_at": str(event.received_at),
            "kind": event.kind,
            "severity": "" if event.severity is None else str(event.severity),
            "message": event.message,
        }
