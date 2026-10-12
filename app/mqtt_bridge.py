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


HEARTBEAT_SECONDS = int(os.getenv("MQTT_HEARTBEAT_SECONDS", "30"))
BRIDGE_ID = os.getenv("MQTT_BRIDGE_ID", "mqtt-bridge-local")


class Publisher(Protocol):
    def publish(
        self, topic: str, payload: bytes | str, qos: int = 0, retain: bool = False
    ): ...


def device_key(device_id: str) -> str:
    return f"device:{device_id}"


def device_state_key(device_id: str) -> str:
    return f"device-state:{device_id}"


def device_command_key(command_id: str) -> str:
    return f"device-command:{command_id}"


def device_frame_key(device_id: str, frame: Frame) -> str:
    return f"device-frame:{device_id}:{frame.frame_type.value}:{frame.sequence}"


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
            await self._hello(topic, frame, now)
            return

        if not await self.redis.exists(device_key(topic.device_id)):
            self._error(topic.device_id, frame.sequence, "not-registered", "send-hello")
            return

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
            await self.redis.xadd(
                DEVICE_TELEMETRY_STREAM,
                {
                    "device_id": topic.device_id,
                    "sensor": sensor,
                    "value": value,
                    "unit": unit,
                    "recorded_at": str(now),
                    "sequence": str(frame.sequence),
                },
                maxlen=100_000,
                approximate=True,
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

    async def _hello(self, topic: Topic, frame: Frame, now: float) -> None:
        board_name, firmware, declared_tier, capabilities = frame.fields
        profile = resolve_board(board_name)
        if profile is None:
            self._error(topic.device_id, frame.sequence, "unknown-board", "not-registered")
            return
        tier = int(declared_tier)
        if profile.agent_tier is AgentTier.FULL or int(profile.agent_tier) != tier:
            self._error(topic.device_id, frame.sequence, "tier-mismatch", "not-registered")
            return
        await self.redis.hset(
            device_key(topic.device_id),
            mapping={
                "id": topic.device_id,
                "board": profile.identifier,
                "firmware": firmware,
                "tier": str(tier),
                "tier_code": profile.tier_code,
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

    async def _event(
        self, device_id: str, kind: str, name: str, value: str, now: float
    ) -> None:
        await self.redis.xadd(
            DEVICE_EVENT_STREAM,
            {
                "device_id": device_id,
                "kind": kind,
                "name": name,
                "value": value,
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
            await asyncio.gather(self.consume_mqtt(), self.dispatch_commands())
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
