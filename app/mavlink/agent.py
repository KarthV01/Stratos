"""Receive-only Linux MAVLink companion entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import AsyncIterator

os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil

from app.mavlink.models import FlightSnapshotV1
from app.mavlink.parser import MavlinkTelemetryAccumulator


async def receive_snapshots(
    connection_string: str,
    vehicle_id: str,
    device_id: str,
    *,
    baud: int = 115_200,
    rate_hz: float = 5.0,
) -> AsyncIterator[FlightSnapshotV1]:
    """Yield snapshots while never calling a pymavlink send method."""
    connection = mavutil.mavlink_connection(
        connection_string,
        baud=baud,
        dialect="common",
        autoreconnect=True,
    )
    accumulator = MavlinkTelemetryAccumulator(vehicle_id, device_id)
    interval = 1.0 / rate_hz
    next_emit = time.monotonic()
    try:
        while True:
            message = await asyncio.to_thread(
                connection.recv_match, blocking=True, timeout=min(interval, 1.0)
            )
            if message is not None:
                accumulator.process(message)
            now = time.monotonic()
            if now >= next_emit:
                yield accumulator.snapshot()
                next_emit = now + interval
    finally:
        connection.close()


async def _run(args: argparse.Namespace) -> None:
    async for snapshot in receive_snapshots(
        args.connection,
        args.vehicle_id,
        args.device_id,
        baud=args.baud,
        rate_hz=args.rate,
    ):
        print(json.dumps(snapshot.as_dict(), separators=(",", ":")), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Passive Stratos MAVLink telemetry agent")
    parser.add_argument("--connection", required=True, help="serial path or udpin:host:port")
    parser.add_argument("--vehicle-id", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--baud", type=int, default=115_200)
    parser.add_argument("--rate", type=float, default=5.0)
    args = parser.parse_args()
    if not 0 < args.rate <= 5:
        parser.error("--rate must be greater than zero and at most 5 Hz")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
