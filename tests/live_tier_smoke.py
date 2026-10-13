"""Live SAP/1 + SMP/1 smoke test for a running Compose stack."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import paho.mqtt.client as mqtt

from app.mqtt_protocol import (
    device_topic,
    encode_drone_snapshot,
    encode_hello,
    encode_minimal_status,
)


API = "http://localhost:8000"


def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read())


def main() -> None:
    now_ms = int(time.time() * 1000)
    suffix = f"{now_ms % 100_000_000:08d}"
    full_id = f"smoke-full-{suffix}"
    embedded_id = f"smoke-embedded-{suffix}"
    minimal_id = f"smoke-minimal-{suffix}"
    hello = {
        "protocol": "sap/1",
        "type": "agent.hello",
        "agent_id": full_id,
        "sequence": 1,
        "sent_at_ms": now_ms,
        "board": "jetson orin nano",
        "firmware": "2.4.1",
        "boot_id": f"boot-{suffix}",
        "capabilities": ["mission.execute"],
        "resources": {
            "cpu_cores": 6,
            "memory_mb": 8192,
            "storage_mb": 65536,
            "gpu": "orin-ampere",
            "max_concurrency": 4,
        },
    }
    assert request("POST", "/api/agent/v1/register", hello)["accepted"]

    telemetry = {
        "protocol": "sap/1",
        "type": "telemetry.snapshot",
        "agent_id": full_id,
        "sequence": 2,
        "sent_at_ms": now_ms + 1,
        "uptime_ms": 88000,
        "flight": {
            "armed": True,
            "mode": "AUTO",
            "system_status": "active",
            "latitude_e7": 407128001,
            "longitude_e7": -740060002,
            "relative_altitude_mm": 42500,
            "heading_cdeg": 9120,
        },
        "power": {"voltage_mv": 22400, "remaining_pct": 76},
        "link": {"quality_pct": 91},
        "compute": {
            "cpu_pct": 37.5,
            "memory_used_mb": 2810,
            "disk_used_mb": 9000,
            "temperature_c": 54.2,
        },
        "sensors": {},
    }
    assert request(
        "POST", f"/api/agent/v1/{full_id}/telemetry", telemetry
    )["accepted"]
    command = request(
        "POST",
        f"/api/devices/{full_id}/commands",
        {"opcode": "mission.start", "arguments": {"mission_id": "survey-12"}},
    )
    polled = request(
        "GET", f"/api/agent/v1/{full_id}/commands?wait_seconds=0"
    )
    assert polled["commands"][0]["command_id"] == command["id"]

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="tier-smoke")
    client.connect("mqtt", 1883, 30)
    client.loop_start()
    try:
        client.publish(
            device_topic(embedded_id, "up", "hello"),
            encode_hello(1, "esp32-s3", "1.0.0", 2, "flight+power"),
            qos=1,
        ).wait_for_publish(5)
        client.publish(
            device_topic(embedded_id, "up", "telemetry"),
            encode_drone_snapshot(
                2,
                88000,
                407128001,
                -740060002,
                42500,
                120,
                -30,
                5,
                9120,
                22400,
                76,
                3,
            ),
            qos=0,
        ).wait_for_publish(5)
        client.publish(
            device_topic(minimal_id, "up", "hello"),
            encode_hello(1, "uno-r4-wifi", "0.4.0", 3, "base"),
            qos=1,
        ).wait_for_publish(5)
        client.publish(
            device_topic(minimal_id, "up", "telemetry"),
            encode_minimal_status(2, flags=3, battery_pct=75, link_pct=88),
            qos=0,
        ).wait_for_publish(5)
    finally:
        client.disconnect()
        client.loop_stop()

    deadline = time.monotonic() + 5
    while True:
        try:
            embedded = request(
                "GET", f"/api/devices/{embedded_id}/telemetry/latest"
            )
            minimal = request(
                "GET", f"/api/devices/{minimal_id}/telemetry/latest"
            )
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 404 or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)

    assert embedded["kind"] == "flight"
    assert embedded["payload"]["latitude_e7"] == 407128001
    assert minimal["kind"] == "essential"
    assert minimal["payload"]["battery_pct"] == 75
    assert request("GET", f"/api/devices/{full_id}/telemetry/latest")["tier"] == "1"
    print("Tier 1, Tier 2, and Tier 3 cluster paths passed")


if __name__ == "__main__":
    main()
