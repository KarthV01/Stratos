"""Read-only SITL smoke test for one MAVLink UDP endpoint per vehicle."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.mavlink.agent import receive_snapshots  # noqa: E402


_VEHICLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


def parse_vehicle(value: str) -> tuple[str, str]:
    vehicle_id, separator, connection = value.partition("=")
    if not separator or not _VEHICLE_ID.fullmatch(vehicle_id) or not connection:
        raise argparse.ArgumentTypeError(
            "expected a vehicle ID and MAVLink connection, e.g. aircraft-1=udpin:0.0.0.0:14550"
        )
    return vehicle_id, connection


async def read_one(
    vehicle_id: str, connection: str, device_id: str, timeout: float
) -> dict[str, Any]:
    latest: dict[str, Any] | None = None
    last_progress_at = 0.0

    async def wait_for_position() -> dict[str, Any]:
        nonlocal latest, last_progress_at
        stream = receive_snapshots(
            connection, vehicle_id, device_id, rate_hz=5.0
        )
        try:
            async for snapshot in stream:
                latest = {
                    "system_id": snapshot.identity.system_id,
                    "autopilot": snapshot.identity.autopilot,
                    "mavlink_version": snapshot.identity.mavlink_version,
                    "latitude_e7": snapshot.position.latitude_e7,
                    "longitude_e7": snapshot.position.longitude_e7,
                    "link_connected": snapshot.link.connected,
                    "received_messages": snapshot.link.received_messages,
                    "parse_errors": snapshot.link.parse_errors,
                    "unknown_messages": snapshot.link.unknown_messages,
                }
                if (
                    snapshot.link.received_messages > 0
                    and time.monotonic() - last_progress_at >= 5.0
                ):
                    print(
                        json.dumps(
                            {
                                "event": "vehicle-progress",
                                "vehicle_id": vehicle_id,
                                **latest,
                            }
                        ),
                        flush=True,
                    )
                    last_progress_at = time.monotonic()
                if (
                    snapshot.identity.system_id is not None
                    and snapshot.position.latitude_e7 is not None
                    and snapshot.position.longitude_e7 is not None
                    and snapshot.link.connected
                ):
                    return {"vehicle_id": snapshot.vehicle_id, **latest}
        finally:
            await stream.aclose()
        raise RuntimeError(f"MAVLink stream ended: {vehicle_id}")

    try:
        return await asyncio.wait_for(wait_for_position(), timeout=timeout)
    except TimeoutError as exc:
        raise RuntimeError(
            f"Timed out waiting for normalized position from {vehicle_id}; "
            f"latest snapshot: {latest}"
        ) from exc


async def run(args: argparse.Namespace) -> None:
    device_prefix = args.device_prefix
    tasks = [
        asyncio.create_task(
            read_one(
                vehicle_id,
                connection,
                f"{device_prefix}-{index + 1}",
                args.timeout,
            )
        )
        for index, (vehicle_id, connection) in enumerate(args.vehicle)
    ]
    results = []
    for task in asyncio.as_completed(tasks):
        result = await task
        results.append(result)
        print(json.dumps({"event": "vehicle-ready", **result}), flush=True)
    autopilots = {result["autopilot"] for result in results}
    if "px4" not in autopilots or "ardupilot" not in autopilots:
        raise RuntimeError("fleet smoke test requires both PX4 and ArduPilot")
    print(json.dumps(results, separators=(",", ":")), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vehicle",
        action="append",
        type=parse_vehicle,
        required=True,
        help="vehicle ID=connection; pass at least three independent endpoints",
    )
    parser.add_argument("--device-prefix", default="sitl-companion")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    if len(args.vehicle) < 3:
        parser.error("at least three --vehicle endpoints are required")
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be greater than zero and at most 300 seconds")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
