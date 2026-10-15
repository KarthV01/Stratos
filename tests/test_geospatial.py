import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException

from app.geospatial import (
    FixedLocation,
    as_geojson,
    list_observations,
    record_observation,
    set_device_location,
)


def redis_pipeline():
    redis = Mock()
    pipe = Mock()
    pipe.hset = Mock()
    pipe.zadd = Mock()
    pipe.xadd = Mock()
    pipe.hget = Mock()
    pipe.execute = AsyncMock(return_value=[])
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=None)
    redis.pipeline = Mock(return_value=pipe)
    redis.zcard = AsyncMock(return_value=0)
    redis.zrange = AsyncMock(return_value=[])
    return redis, pipe


class ObservationTests(unittest.TestCase):
    def test_record_uses_normalized_schema(self) -> None:
        redis, pipe = redis_pipeline()
        result = asyncio.run(
            record_observation(
                redis,
                device_id="sensor-1",
                sensor="temperature",
                value="21.5",
                unit="C",
                latitude_e7=407128001,
                longitude_e7=-740060002,
                observation_id="obs-1",
                observed_at=10.0,
            )
        )
        self.assertEqual("stratos.observation/1", result["schema"])
        self.assertEqual(21.5, result["value"])
        encoded = pipe.hset.call_args.kwargs["mapping"]["payload"]
        self.assertEqual("obs-1", json.loads(encoded)["id"])

    def test_geojson_preserves_fixed_point_properties(self) -> None:
        feature = as_geojson(
            {
                "id": "obs-1",
                "latitude_e7": 407128001,
                "longitude_e7": -740060002,
                "altitude_mm": 12500,
                "sensor": "temperature",
            }
        )
        self.assertEqual([-74.0060002, 40.7128001, 12.5], feature["geometry"]["coordinates"])

    def test_fixed_location_requires_registered_device(self) -> None:
        redis = AsyncMock()
        redis.exists.return_value = False
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
        with self.assertRaises(HTTPException) as context:
            asyncio.run(
                set_device_location(
                    "sensor-1", FixedLocation(latitude_e7=1, longitude_e7=2), request
                )
            )
        self.assertEqual(404, context.exception.status_code)

    def test_list_filters_and_emits_feature_collection(self) -> None:
        redis, pipe = redis_pipeline()
        redis.zrevrangebyscore = AsyncMock(return_value=["obs-1"])
        pipe.execute.return_value = [
            json.dumps(
                {
                    "schema": "stratos.observation/1",
                    "id": "obs-1",
                    "device_id": "sensor-1",
                    "vehicle_id": None,
                    "sensor": "temperature",
                    "value": 21.5,
                    "unit": "C",
                    "observed_at": 10.0,
                    "latitude_e7": 407128001,
                    "longitude_e7": -740060002,
                    "altitude_mm": None,
                    "location_source": "fixed",
                }
            )
        ]
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
        response = asyncio.run(
            list_observations(
                request,
                bbox="-75,40,-73,41",
                since=0,
                until=None,
                sensor="temperature",
                device_id=None,
                limit=100,
            )
        )
        self.assertEqual("FeatureCollection", response["type"])
        self.assertEqual(1, len(response["features"]))


if __name__ == "__main__":
    unittest.main()
