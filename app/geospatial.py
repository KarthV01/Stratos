"""Normalized geospatial sensor observations and map-ready GeoJSON APIs."""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.config import OBSERVATION_MAXLEN
from app.mqtt_protocol import validate_device_id


OBSERVATION_INDEX = "observation-index"
OBSERVATION_STREAM = "observations"


def observation_key(observation_id: str) -> str:
    return f"observation:{observation_id}"


class FixedLocation(BaseModel):
    latitude_e7: int = Field(ge=-900_000_000, le=900_000_000)
    longitude_e7: int = Field(ge=-1_800_000_000, le=1_800_000_000)
    altitude_mm: int | None = Field(default=None, ge=-1_000_000, le=100_000_000)


async def record_observation(
    redis: Redis,
    *,
    device_id: str,
    sensor: str,
    value: float | str,
    unit: str,
    latitude_e7: int,
    longitude_e7: int,
    altitude_mm: int | None = None,
    vehicle_id: str | None = None,
    observed_at: float | None = None,
    location_source: str = "explicit",
    observation_id: str | None = None,
) -> dict[str, Any]:
    observed_at = time.time() if observed_at is None else observed_at
    observation_id = observation_id or f"obs-{uuid4().hex}"
    record = {
        "schema": "stratos.observation/1",
        "id": observation_id,
        "device_id": device_id,
        "vehicle_id": vehicle_id,
        "sensor": sensor,
        "value": float(value),
        "unit": unit,
        "observed_at": observed_at,
        "latitude_e7": latitude_e7,
        "longitude_e7": longitude_e7,
        "altitude_mm": altitude_mm,
        "location_source": location_source,
    }
    encoded = json.dumps(record, separators=(",", ":"), sort_keys=True)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(observation_key(observation_id), mapping={"payload": encoded})
        pipe.zadd(OBSERVATION_INDEX, {observation_id: observed_at})
        pipe.xadd(
            OBSERVATION_STREAM,
            {"observation_id": observation_id, "payload": encoded},
            maxlen=OBSERVATION_MAXLEN,
            approximate=True,
        )
        await pipe.execute()
    return record


def as_geojson(record: dict[str, Any]) -> dict[str, Any]:
    altitude = record.get("altitude_mm")
    coordinates: list[float] = [
        record["longitude_e7"] / 10_000_000,
        record["latitude_e7"] / 10_000_000,
    ]
    if altitude is not None:
        coordinates.append(altitude / 1000)
    properties = {
        key: value
        for key, value in record.items()
        if key not in {"latitude_e7", "longitude_e7", "altitude_mm"}
    }
    properties["altitude_mm"] = altitude
    return {
        "type": "Feature",
        "id": record["id"],
        "geometry": {"type": "Point", "coordinates": coordinates},
        "properties": properties,
    }


router = APIRouter(tags=["map"])


@router.put("/api/devices/{device_id}/location")
async def set_device_location(
    device_id: str, body: FixedLocation, request: Request
) -> dict[str, Any]:
    try:
        validate_device_id(device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    redis: Redis = request.app.state.redis
    key = f"device:{device_id}"
    if not await redis.exists(key):
        raise HTTPException(status_code=404, detail="Device not found")
    mapping = {
        "fixed_latitude_e7": str(body.latitude_e7),
        "fixed_longitude_e7": str(body.longitude_e7),
        "fixed_altitude_mm": "" if body.altitude_mm is None else str(body.altitude_mm),
    }
    await redis.hset(key, mapping=mapping)
    return {"device_id": device_id, **body.model_dump()}


@router.delete("/api/devices/{device_id}/location", status_code=204)
async def clear_device_location(device_id: str, request: Request) -> Response:
    try:
        validate_device_id(device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await request.app.state.redis.hdel(
        f"device:{device_id}",
        "fixed_latitude_e7",
        "fixed_longitude_e7",
        "fixed_altitude_mm",
    )
    return Response(status_code=204)


@router.get("/api/map/observations")
async def list_observations(
    request: Request,
    bbox: str | None = Query(default=None),
    since: float = Query(default=0, ge=0),
    until: float | None = Query(default=None, ge=0),
    sensor: str | None = Query(default=None, min_length=1, max_length=64),
    device_id: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> dict[str, Any]:
    bounds: tuple[float, float, float, float] | None = None
    if bbox is not None:
        try:
            values = tuple(float(item) for item in bbox.split(","))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="bbox must contain four numbers") from exc
        if len(values) != 4 or not (
            -180 <= values[0] <= values[2] <= 180
            and -90 <= values[1] <= values[3] <= 90
        ):
            raise HTTPException(status_code=422, detail="bbox must be minLon,minLat,maxLon,maxLat")
        bounds = values  # type: ignore[assignment]
    if device_id is not None:
        try:
            validate_device_id(device_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    redis: Redis = request.app.state.redis
    maximum = "+inf" if until is None else until
    ids = await redis.zrevrangebyscore(OBSERVATION_INDEX, maximum, since, start=0, num=limit * 4)
    if not ids:
        return {"type": "FeatureCollection", "features": []}
    async with redis.pipeline(transaction=False) as pipe:
        for observation_id in ids:
            pipe.hget(observation_key(observation_id), "payload")
        payloads = await pipe.execute()
    features = []
    for payload in payloads:
        if not payload:
            continue
        record = json.loads(payload)
        if sensor is not None and record["sensor"] != sensor:
            continue
        if device_id is not None and record["device_id"] != device_id:
            continue
        if bounds is not None:
            lon = record["longitude_e7"] / 10_000_000
            lat = record["latitude_e7"] / 10_000_000
            if not (bounds[0] <= lon <= bounds[2] and bounds[1] <= lat <= bounds[3]):
                continue
        features.append(as_geojson(record))
        if len(features) == limit:
            break
    return {"type": "FeatureCollection", "features": features}
