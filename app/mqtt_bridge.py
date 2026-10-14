"""Bridge validated SMP/1 device traffic between MQTT and Redis."""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
import traceback
from typing import Protocol

import paho.mqtt.client as mqtt
from redis.asyncio import Redis

from app.boards import AgentTier, resolve_board
from app.broker import ensure_mqtt_bridge_group, redis_client
from app.config import (
    DEVICE_COMMAND_STREAM,
    DEVICE_EVENT_STREAM,
    DEVICE_INDEX,
    DEVICE_TELEMETRY_STREAM,
    MQTT_BRIDGE_GROUP,
    MQTT_HOST,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_TLS,
    MQTT_USERNAME,
)
from app.mqtt_protocol import (
    Frame,
    FrameType,
    TOPIC_PREFIX,
    Topic,
    decode_frame,
    device_topic,
    encode_command,
    encode_error,
    encode_welcome,
    ensure_topic_matches_frame,
    parse_topic,
)
from app.device_store import (
    device_command_key,
    device_frame_key,
    device_key,
    device_state_key,
    latest_telemetry_key,
)
from app.tier_payloads import MINIMAL_MAX_PAYLOAD_BYTES
from app.mavlink.models import (
    BatteryHealth,
    FlightSnapshotV1,
    GlobalPosition,
    GpsHealth,
    LinkState,
    VehicleIdentity,
    VehicleState,
)
from app.mavlink.store import VehicleTelemetryStore


HEARTBEAT_SECONDS = int(os.getenv("MQTT_HEARTBEAT_SECONDS", "30"))
BRIDGE_ID = os.getenv("MQTT_BRIDGE_ID", "mqtt-bridge-local")


class Publisher(Protocol):
    def publish(
        self, topic: str, payload: bytes | str, qos: int = 0, retain: bool = False
    ): ...


class DeviceMessageProcessor:
    def __init__(self, redis: Redis, publisher: Publisher) -> None:
        self.redis = redis
        self.publisher = publisher

    async def process(self, topic_value: str, payload: bytes) -> None:
        topic = parse_topic(topic_value)
        now = time.time()
        if topic.channel == "availability":
            await self._availability(topic.device_id, payload, now)
            return

        frame = decode_frame(payload)
        ensure_topic_matches_frame(topic, frame)
        if frame.frame_type is FrameType.HELLO:
            await self._hello(topic, frame, now, len(payload))
            return

        if not await self.redis.exists(device_key(topic.device_id)):
            self._error(topic.device_id, frame.sequence, "not-registered", "send-hello")
            return
        tier = int(await self.redis.hget(device_key(topic.device_id), "tier"))
        if tier == 3 and len(payload) > MINIMAL_MAX_PAYLOAD_BYTES:
            self._error(topic.device_id, frame.sequence, "frame-too-large", "tier-3-limit")
            return
        allowed = {
            2: {
                FrameType.TELEMETRY,
                FrameType.STATE,
                FrameType.ACK,
                FrameType.DRONE_SNAPSHOT,
                FrameType.EVENT,
                FrameType.VEHICLE,
                FrameType.MAP,
            },
            3: {
                FrameType.TELEMETRY,
                FrameType.STATE,
                FrameType.ACK,
                FrameType.MINIMAL_STATUS,
                FrameType.VEHICLE,
                FrameType.MAP,
            },
        }
        if frame.frame_type not in allowed.get(tier, set()):
            self._error(topic.device_id, frame.sequence, "tier-frame", "not-allowed")
            return

        if frame.frame_type not in {FrameType.TELEMETRY, FrameType.MAP}:
            first_delivery = await self.redis.set(
                device_frame_key(topic.device_id, frame), "1", ex=86_400, nx=True
            )
            if not first_delivery:
                return

        await self.redis.hset(
            device_key(topic.device_id),
            mapping={"last_seen": str(now), "last_sequence": str(frame.sequence)},
        )
        await self.redis.zadd(DEVICE_INDEX, {topic.device_id: now})
        if frame.frame_type is FrameType.TELEMETRY:
            sensor, value, unit = frame.fields
            await self._telemetry(
                topic.device_id,
                tier,
                "sensor",
                {"sensor": sensor, "value": value, "unit": unit},
                frame.sequence,
                now,
            )
        elif frame.frame_type is FrameType.DRONE_SNAPSHOT:
            names = (
                "uptime_ms",
                "latitude_e7",
                "longitude_e7",
                "relative_altitude_mm",
                "velocity_north_cms",
                "velocity_east_cms",
                "velocity_down_cms",
                "heading_cdeg",
                "voltage_mv",
                "remaining_pct",
                "flags",
            )
            await self._telemetry(
                topic.device_id,
                tier,
                "flight",
                dict(zip(names, (int(value) for value in frame.fields))),
                frame.sequence,
                now,
            )
        elif frame.frame_type is FrameType.MINIMAL_STATUS:
            flags, battery_pct, link_pct = (int(value) for value in frame.fields)
            await self._telemetry(
                topic.device_id,
                tier,
                "essential",
                {"flags": flags, "battery_pct": battery_pct, "link_pct": link_pct},
                frame.sequence,
                now,
            )
        elif frame.frame_type is FrameType.STATE:
            key, value = frame.fields
            await self.redis.hset(device_state_key(topic.device_id), key, value)
            await self._event(topic.device_id, "state", key, value, now)
        elif frame.frame_type is FrameType.ACK:
            command_id, status, code = frame.fields
            command_key = device_command_key(command_id)
            command_device_id = await self.redis.hget(command_key, "device_id")
            if command_device_id == topic.device_id:
                await self.redis.hset(
                    command_key,
                    mapping={
                        "status": status,
                        "code": code,
                        "updated_at": str(now),
                    },
                )
                await self._event(topic.device_id, "command", command_id, status, now)
        elif frame.frame_type is FrameType.EVENT:
            code, severity, detail = frame.fields
            await self._event(topic.device_id, "agent", code, severity, now, detail)
        elif frame.frame_type is FrameType.VEHICLE:
            await self._vehicle(topic.device_id, frame, now)
        elif frame.frame_type is FrameType.MAP:
            await self._map_snapshot(topic.device_id, frame, now)

    async def _hello(
        self, topic: Topic, frame: Frame, now: float, payload_size: int
    ) -> None:
        board_name, firmware, declared_tier, capabilities = frame.fields
        profile = resolve_board(board_name)
        if profile is None:
            self._error(topic.device_id, frame.sequence, "unknown-board", "not-registered")
            return
        tier = int(declared_tier)
        if profile.agent_tier is AgentTier.FULL or int(profile.agent_tier) != tier:
            self._error(topic.device_id, frame.sequence, "tier-mismatch", "not-registered")
            return
        if tier == 3 and payload_size > MINIMAL_MAX_PAYLOAD_BYTES:
            self._error(topic.device_id, frame.sequence, "frame-too-large", "tier-3-limit")
            return
        await self.redis.hset(
            device_key(topic.device_id),
            mapping={
                "id": topic.device_id,
                "board": profile.identifier,
                "firmware": firmware,
                "tier": str(tier),
                "tier_code": profile.tier_code,
                "transport": "mqtt",
                "capabilities": capabilities,
                "availability": "online",
                "last_seen": str(now),
                "last_sequence": str(frame.sequence),
            },
        )
        await self.redis.zadd(DEVICE_INDEX, {topic.device_id: now})
        await self._event(topic.device_id, "session", "hello", "accepted", now)
        self.publisher.publish(
            device_topic(topic.device_id, "down", "welcome"),
            encode_welcome(frame.sequence, int(now), HEARTBEAT_SECONDS),
            qos=1,
        )

    async def _availability(self, device_id: str, payload: bytes, now: float) -> None:
        if payload not in {b"online", b"offline"}:
            raise ValueError("Availability must be online or offline")
        value = payload.decode("ascii")
        if await self.redis.exists(device_key(device_id)):
            await self.redis.hset(
                device_key(device_id),
                mapping={"availability": value, "last_seen": str(now)},
            )
        await self._event(device_id, "availability", value, "mqtt", now)

    async def _telemetry(
        self,
        device_id: str,
        tier: int,
        kind: str,
        values: dict[str, object],
        sequence: int,
        now: float,
    ) -> None:
        encoded = json.dumps(values, separators=(",", ":"))
        record = {
            "device_id": device_id,
            "tier": str(tier),
            "kind": kind,
            "payload": encoded,
            "recorded_at": str(now),
            "sequence": str(sequence),
        }
        await self.redis.hset(latest_telemetry_key(device_id), mapping=record)
        await self.redis.xadd(
            DEVICE_TELEMETRY_STREAM,
            record,
            maxlen=100_000,
            approximate=True,
        )

    async def _vehicle(self, device_id: str, frame: Frame, now: float) -> None:
        vehicle_id, system_id, component_id, autopilot, vehicle_type, version = frame.fields
        await self.redis.hset(
            device_key(device_id),
            mapping={
                "vehicle_id": vehicle_id,
                "mav_system_id": system_id,
                "mav_component_id": component_id,
                "mav_autopilot": autopilot,
                "mav_vehicle_type": vehicle_type,
                "mavlink_version": version,
                "vehicle_bound_at": str(now),
            },
        )
        await self._event(device_id, "vehicle", vehicle_id, "bound", now)

    async def _map_snapshot(self, device_id: str, frame: Frame, now: float) -> None:
        device = await self.redis.hgetall(device_key(device_id))
        vehicle_id = device.get("vehicle_id")
        if not vehicle_id:
            self._error(device_id, frame.sequence, "vehicle-required", "send-vehicle")
            return

        tier = int(device["tier"])
        minimum_interval = 0.5 if tier == 2 else 1.0
        previous_at = float(device.get("last_map_at", "0"))
        if now - previous_at < minimum_interval:
            return

        values = [None if value == "na" else int(value) for value in frame.fields[:10]]
        (
            boot_ms,
            latitude_e7,
            longitude_e7,
            altitude_msl_mm,
            relative_altitude_mm,
            ground_speed_cms,
            heading_cdeg,
            gps_fix_type,
            satellites_visible,
            battery_remaining,
        ) = values
        flags = int(frame.fields[10])
        position_valid = bool(flags & 8)
        battery_valid = bool(flags & 16)
        snapshot = FlightSnapshotV1(
            vehicle_id=vehicle_id,
            device_id=device_id,
            received_at=now,
            identity=VehicleIdentity(
                system_id=int(device["mav_system_id"]),
                component_id=int(device["mav_component_id"]),
                autopilot=device["mav_autopilot"],
                vehicle_type=device["mav_vehicle_type"],
                mavlink_version=int(device["mavlink_version"]),
            ),
            position=GlobalPosition(
                boot_ms=boot_ms,
                latitude_e7=latitude_e7 if position_valid else None,
                longitude_e7=longitude_e7 if position_valid else None,
                altitude_msl_mm=altitude_msl_mm if position_valid else None,
                relative_altitude_mm=(
                    relative_altitude_mm if position_valid else None
                ),
                ground_speed_cms=ground_speed_cms if position_valid else None,
                heading_cdeg=heading_cdeg if position_valid else None,
            ),
            gps=GpsHealth(
                fix_type=gps_fix_type,
                satellites_visible=satellites_visible,
            ),
            battery=BatteryHealth(
                remaining_percent=battery_remaining if battery_valid else None
            ),
            state=VehicleState(
                armed=bool(flags & 1),
                landed_state=1 if flags & 2 else 2,
                failsafe=bool(flags & 4),
            ),
            link=LinkState(connected=True, last_heartbeat_at=now),
        )
        await VehicleTelemetryStore(self.redis).ingest(snapshot)
        await self.redis.hset(device_key(device_id), "last_map_at", str(now))

    async def _event(
        self,
        device_id: str,
        kind: str,
        name: str,
        value: str,
        now: float,
        detail: str = "-",
    ) -> None:
        await self.redis.xadd(
            DEVICE_EVENT_STREAM,
            {
                "device_id": device_id,
                "kind": kind,
                "name": name,
                "value": value,
                "detail": detail,
                "recorded_at": str(now),
            },
            maxlen=10_000,
            approximate=True,
        )

    def _error(self, device_id: str, sequence: int, code: str, detail: str) -> None:
        self.publisher.publish(
            device_topic(device_id, "down", "error"),
            encode_error(sequence, code, detail),
            qos=1,
        )


class MqttBridge:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.loop = asyncio.get_running_loop()
        self.messages: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(maxsize=1000)
        self.latest_maps: dict[str, tuple[str, bytes]] = {}
        self.maps_ready = asyncio.Event()
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=BRIDGE_ID,
            protocol=mqtt.MQTTv311,
        )
        if MQTT_USERNAME:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        if MQTT_TLS:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.processor = DeviceMessageProcessor(redis, self.client)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            print(f"[mqtt-bridge] connection rejected: {reason_code}", flush=True)
            return
        client.subscribe(f"{TOPIC_PREFIX}/+/up/+", qos=1)
        client.subscribe(f"{TOPIC_PREFIX}/+/availability", qos=1)
        print(f"[mqtt-bridge] connected to {MQTT_HOST}:{MQTT_PORT}", flush=True)

    def _on_message(self, client, userdata, message) -> None:
        item = (message.topic, bytes(message.payload))
        self.loop.call_soon_threadsafe(self._enqueue_message, item)

    def _enqueue_message(self, item: tuple[str, bytes]) -> None:
        if item[1].startswith(b"SMP1|P|"):
            self.latest_maps[item[0]] = item
            self.maps_ready.set()
            return
        try:
            self.messages.put_nowait(item)
        except asyncio.QueueFull:
            print("[mqtt-bridge] inbound queue full; message dropped", flush=True)

    async def consume_mqtt(self) -> None:
        while True:
            topic, payload = await self.messages.get()
            try:
                await self.processor.process(topic, payload)
            except ValueError as exc:
                print(f"[mqtt-bridge] rejected {topic}: {exc}", flush=True)
            except Exception:
                traceback.print_exc()
            finally:
                self.messages.task_done()

    async def consume_latest_maps(self) -> None:
        while True:
            await self.maps_ready.wait()
            items = list(self.latest_maps.values())
            self.latest_maps.clear()
            self.maps_ready.clear()
            for topic, payload in items:
                try:
                    await self.processor.process(topic, payload)
                except ValueError as exc:
                    print(f"[mqtt-bridge] rejected {topic}: {exc}", flush=True)
                except Exception:
                    traceback.print_exc()

    async def dispatch_commands(self) -> None:
        while True:
            rows = await self.redis.xreadgroup(
                MQTT_BRIDGE_GROUP,
                BRIDGE_ID,
                {DEVICE_COMMAND_STREAM: ">"},
                count=10,
                block=5_000,
            )
            for _, entries in rows:
                for entry_id, command in entries:
                    command_id = command["command_id"]
                    try:
                        payload = encode_command(
                            int(command["sequence"]),
                            command_id,
                            command["opcode"],
                            command["argument"],
                        )
                        info = self.client.publish(
                            device_topic(command["device_id"], "down", "command"),
                            payload,
                            qos=1,
                        )
                        await asyncio.to_thread(info.wait_for_publish, 10)
                        if not info.is_published():
                            raise RuntimeError("MQTT publish was not acknowledged")
                        await self.redis.hset(
                            device_command_key(command_id),
                            mapping={"status": "sent", "updated_at": str(time.time())},
                        )
                        await self.redis.xack(
                            DEVICE_COMMAND_STREAM, MQTT_BRIDGE_GROUP, entry_id
                        )
                    except Exception:
                        traceback.print_exc()

    async def run(self) -> None:
        await ensure_mqtt_bridge_group(self.redis)
        self.client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()
        try:
            await asyncio.gather(
                self.consume_mqtt(), self.consume_latest_maps(), self.dispatch_commands()
            )
        finally:
            self.client.disconnect()
            self.client.loop_stop()


async def main() -> None:
    redis = redis_client()
    try:
        await MqttBridge(redis).run()
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
