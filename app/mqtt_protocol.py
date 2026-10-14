"""Compact MQTT wire protocol shared by embedded and minimal Stratos agents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


PROTOCOL_NAME = "smp/1"
WIRE_PREFIX = "SMP1"
TOPIC_PREFIX = "stratos/v1/devices"
MAX_FRAME_BYTES = 256
MAX_SEQUENCE = 4_294_967_295


class FrameType(str, Enum):
    HELLO = "H"
    TELEMETRY = "T"
    STATE = "S"
    ACK = "A"
    WELCOME = "W"
    COMMAND = "C"
    ERROR = "E"
    VEHICLE = "V"
    MAP = "M"


UPLINK_CHANNELS = frozenset({"hello", "telemetry", "state", "ack"})
DOWNLINK_CHANNELS = frozenset({"welcome", "command", "error"})
ALL_CHANNELS = UPLINK_CHANNELS | DOWNLINK_CHANNELS | {"availability"}

FRAME_CHANNELS = {
    FrameType.HELLO: "hello",
    FrameType.TELEMETRY: "telemetry",
    FrameType.STATE: "state",
    FrameType.ACK: "ack",
    FrameType.WELCOME: "welcome",
    FrameType.COMMAND: "command",
    FrameType.ERROR: "error",
    FrameType.VEHICLE: "state",
    FrameType.MAP: "telemetry",
}

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,63}$")
_DEVICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_NUMBER = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_INTEGER = re.compile(r"^-?(?:0|[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class Topic:
    device_id: str
    direction: str
    channel: str


@dataclass(frozen=True, slots=True)
class Frame:
    frame_type: FrameType
    sequence: int
    fields: tuple[str, ...]


def validate_device_id(device_id: str) -> str:
    if not _DEVICE_ID.fullmatch(device_id):
        raise ValueError("Invalid device ID")
    return device_id


def validate_token(value: str, *, name: str = "field") -> str:
    if not _TOKEN.fullmatch(value) and not _NUMBER.fullmatch(value):
        raise ValueError(f"Invalid {name}")
    return value


def device_topic(device_id: str, direction: str, channel: str) -> str:
    validate_device_id(device_id)
    valid_channels = UPLINK_CHANNELS if direction == "up" else DOWNLINK_CHANNELS
    if direction not in {"up", "down"} or channel not in valid_channels:
        raise ValueError("Invalid MQTT topic direction or channel")
    return f"{TOPIC_PREFIX}/{device_id}/{direction}/{channel}"


def availability_topic(device_id: str) -> str:
    return f"{TOPIC_PREFIX}/{validate_device_id(device_id)}/availability"


def parse_topic(value: str) -> Topic:
    parts = value.split("/")
    if len(parts) == 5 and "/".join(parts[:3]) == TOPIC_PREFIX:
        device_id, channel = parts[3], parts[4]
        if channel == "availability":
            return Topic(validate_device_id(device_id), "", channel)
    if len(parts) != 6 or "/".join(parts[:3]) != TOPIC_PREFIX:
        raise ValueError("Invalid Stratos MQTT topic")
    device_id, direction, channel = parts[3:]
    device_topic(device_id, direction, channel)
    return Topic(device_id, direction, channel)


def _validate_sequence(sequence: int) -> int:
    if not 0 <= sequence <= MAX_SEQUENCE:
        raise ValueError("Sequence must be an unsigned 32-bit integer")
    return sequence


def encode_frame(frame_type: FrameType, sequence: int, *fields: str) -> bytes:
    _validate_sequence(sequence)
    checked = tuple(validate_token(field) for field in fields)
    payload = f"{WIRE_PREFIX}|{frame_type.value}|{sequence}|{'|'.join(checked)}".rstrip("|")
    encoded = payload.encode("ascii")
    if len(encoded) > MAX_FRAME_BYTES:
        raise ValueError(f"Frame exceeds {MAX_FRAME_BYTES} bytes")
    return encoded


def decode_frame(payload: bytes) -> Frame:
    if not payload or len(payload) > MAX_FRAME_BYTES:
        raise ValueError(f"Frame must contain 1 to {MAX_FRAME_BYTES} bytes")
    try:
        parts = payload.decode("ascii").split("|")
    except UnicodeDecodeError as exc:
        raise ValueError("Frame must be ASCII") from exc
    if len(parts) < 3 or parts[0] != WIRE_PREFIX:
        raise ValueError("Unsupported MQTT frame")
    try:
        frame_type = FrameType(parts[1])
        sequence = _validate_sequence(int(parts[2]))
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid frame type or sequence") from exc
    fields = tuple(validate_token(field) for field in parts[3:])
    _validate_shape(frame_type, fields)
    return Frame(frame_type, sequence, fields)


def _validate_shape(frame_type: FrameType, fields: tuple[str, ...]) -> None:
    expected = {
        FrameType.HELLO: 4,
        FrameType.TELEMETRY: 3,
        FrameType.STATE: 2,
        FrameType.ACK: 3,
        FrameType.WELCOME: 2,
        FrameType.COMMAND: 3,
        FrameType.ERROR: 2,
        FrameType.VEHICLE: 6,
        FrameType.MAP: 11,
    }[frame_type]
    if len(fields) != expected:
        raise ValueError(f"{frame_type.name} frame requires {expected} fields")
    if frame_type is FrameType.HELLO and fields[2] not in {"2", "3"}:
        raise ValueError("HELLO tier must be 2 or 3")
    if frame_type is FrameType.TELEMETRY and not _NUMBER.fullmatch(fields[1]):
        raise ValueError("TELEMETRY value must be a decimal number")
    if frame_type is FrameType.ACK and fields[1] not in {
        "accepted",
        "running",
        "completed",
        "failed",
        "rejected",
    }:
        raise ValueError("Invalid ACK status")
    if frame_type is FrameType.VEHICLE:
        validate_device_id(fields[0])
        if not 1 <= int(fields[1]) <= 255 or not 1 <= int(fields[2]) <= 255:
            raise ValueError("MAVLink system and component IDs must be 1 to 255")
        if fields[5] not in {"1", "2"}:
            raise ValueError("MAVLink version must be 1 or 2")
    if frame_type is FrameType.MAP:
        for value in fields[:10]:
            if value != "na" and not _INTEGER.fullmatch(value):
                raise ValueError("MAP fields must be integers or na")
        if not fields[10].isdigit() or not 0 <= int(fields[10]) <= 63:
            raise ValueError("MAP flags must be an unsigned integer")


def ensure_topic_matches_frame(topic: Topic, frame: Frame) -> None:
    expected_channel = FRAME_CHANNELS[frame.frame_type]
    expected_direction = "up" if expected_channel in UPLINK_CHANNELS else "down"
    if topic.channel != expected_channel or topic.direction != expected_direction:
        raise ValueError("Frame type does not match MQTT topic")


def encode_hello(
    sequence: int, board: str, firmware: str, tier: int, capabilities: str = "base"
) -> bytes:
    return encode_frame(
        FrameType.HELLO, sequence, board, firmware, str(tier), capabilities
    )


def encode_telemetry(
    sequence: int, sensor: str, value: int | float | str, unit: str
) -> bytes:
    number = str(value)
    if not _NUMBER.fullmatch(number):
        raise ValueError("Telemetry value must be a decimal number")
    return encode_frame(FrameType.TELEMETRY, sequence, sensor, number, unit)


def encode_state(sequence: int, key: str, value: str) -> bytes:
    return encode_frame(FrameType.STATE, sequence, key, value)


def encode_ack(sequence: int, command_id: str, status: str, code: str = "ok") -> bytes:
    payload = encode_frame(FrameType.ACK, sequence, command_id, status, code)
    decode_frame(payload)
    return payload


def encode_welcome(sequence: int, unix_time: int, heartbeat_seconds: int) -> bytes:
    return encode_frame(FrameType.WELCOME, sequence, str(unix_time), str(heartbeat_seconds))


def encode_command(
    sequence: int, command_id: str, opcode: str, argument: str = "-"
) -> bytes:
    return encode_frame(FrameType.COMMAND, sequence, command_id, opcode, argument)


def encode_error(sequence: int, code: str, detail: str) -> bytes:
    return encode_frame(FrameType.ERROR, sequence, code, detail)


def encode_vehicle(
    sequence: int,
    vehicle_id: str,
    system_id: int,
    component_id: int,
    autopilot: str,
    vehicle_type: str,
    mavlink_version: int,
) -> bytes:
    payload = encode_frame(
        FrameType.VEHICLE,
        sequence,
        validate_device_id(vehicle_id),
        str(system_id),
        str(component_id),
        autopilot,
        vehicle_type,
        str(mavlink_version),
    )
    decode_frame(payload)
    return payload


def encode_map_snapshot(
    sequence: int,
    *,
    boot_ms: int | None,
    latitude_e7: int | None,
    longitude_e7: int | None,
    altitude_msl_mm: int | None,
    relative_altitude_mm: int | None,
    ground_speed_cms: int | None,
    heading_cdeg: int | None,
    gps_fix_type: int | None,
    satellites_visible: int | None,
    battery_remaining_percent: int | None,
    flags: int,
) -> bytes:
    def value(item: int | None) -> str:
        return "na" if item is None else str(item)

    payload = encode_frame(
        FrameType.MAP,
        sequence,
        value(boot_ms),
        value(latitude_e7),
        value(longitude_e7),
        value(altitude_msl_mm),
        value(relative_altitude_mm),
        value(ground_speed_cms),
        value(heading_cdeg),
        value(gps_fix_type),
        value(satellites_visible),
        value(battery_remaining_percent),
        str(flags),
    )
    decode_frame(payload)
    return payload
