"""Receive-only Linux MAVLink companion entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path

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
    allow_mavlink1: bool = False,
    signing_key: bytes | None = None,
) -> AsyncIterator[FlightSnapshotV1]:
    """Yield snapshots while never calling a pymavlink send method."""
    connection = mavutil.mavlink_connection(
        connection_string,
        baud=baud,
        dialect="common",
        autoreconnect=True,
    )
    if signing_key is not None:
        if len(signing_key) != 32:
            connection.close()
            raise ValueError("MAVLink signing keys must contain exactly 32 bytes")
        connection.setup_signing(
            signing_key,
            sign_outgoing=False,
            allow_unsigned_callback=None,
        )
    accumulator = MavlinkTelemetryAccumulator(
        vehicle_id, device_id, allow_mavlink1=allow_mavlink1
    )
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
    batch: list[FlightSnapshotV1] = []
    deadline = time.monotonic() + 1.0
    signing_key = (
        Path(args.signing_key_file).read_bytes() if args.signing_key_file else None
    )
    if signing_key is not None and len(signing_key) != 32:
        raise ValueError("MAVLink signing-key file must contain exactly 32 bytes")
    async for snapshot in receive_snapshots(
        args.connection,
        args.vehicle_id,
        args.device_id,
        baud=args.baud,
        rate_hz=args.rate,
        allow_mavlink1=args.mavlink1_compatibility,
        signing_key=signing_key,
    ):
        if not args.stratos_url:
            print(json.dumps(snapshot.as_dict(), separators=(",", ":")), flush=True)
            continue
        batch.append(snapshot)
        if len(batch) >= 25 or time.monotonic() >= deadline:
            await asyncio.to_thread(
                _post_batch, args.stratos_url, args.token, args.device_id, batch
            )
            batch = []
            deadline = time.monotonic() + 1.0


def _post_batch(
    base_url: str,
    token: str | None,
    device_id: str,
    samples: list[FlightSnapshotV1],
) -> None:
    vehicle_id = samples[0].vehicle_id
    payload = json.dumps(
        {
            "device_id": device_id,
            "samples": [sample.as_dict() for sample in samples],
            "events": [],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/vehicles/{vehicle_id}/telemetry",
        data=payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status != 202:
            raise RuntimeError(f"Stratos rejected telemetry with HTTP {response.status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Passive Stratos MAVLink telemetry agent")
    parser.add_argument("--connection", required=True, help="serial path or udpin:host:port")
    parser.add_argument("--vehicle-id", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--baud", type=int, default=115_200)
    parser.add_argument("--rate", type=float, default=5.0)
    parser.add_argument("--stratos-url", help="controller base URL; omit to print JSON")
    parser.add_argument("--token", help="optional native-agent bearer token")
    parser.add_argument(
        "--mavlink1-compatibility",
        action="store_true",
        help="allow unsigned MAVLink 1 traffic (disabled by default)",
    )
    parser.add_argument(
        "--signing-key-file",
        help="path to a protected 32-byte MAVLink 2 signing-key file",
    )
    args = parser.parse_args()
    if not 0 < args.rate <= 5:
        parser.error("--rate must be greater than zero and at most 5 Hz")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
