"""Tier-specific Stratos agent payloads for drone-to-cluster communication."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


FULL_PROTOCOL = "sap/1"
FULL_MAX_PAYLOAD_BYTES = 65_536
EMBEDDED_MAX_PAYLOAD_BYTES = 256
MINIMAL_MAX_PAYLOAD_BYTES = 128

AgentId = str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FullEnvelope(StrictModel):
    protocol: Literal["sap/1"] = FULL_PROTOCOL
    agent_id: AgentId = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    sequence: int = Field(ge=0, le=9_223_372_036_854_775_807)
    sent_at_ms: int = Field(ge=0)


class AgentResources(StrictModel):
    cpu_cores: int = Field(ge=1, le=1024)
    memory_mb: int = Field(ge=16, le=16_777_216)
    storage_mb: int = Field(ge=0, le=1_073_741_824)
    gpu: str | None = Field(default=None, max_length=100)
    max_concurrency: int = Field(default=1, ge=1, le=1024)


class FullAgentHello(FullEnvelope):
    type: Literal["agent.hello"] = "agent.hello"
    board: str = Field(min_length=1, max_length=100)
    firmware: str = Field(min_length=1, max_length=64)
    boot_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    capabilities: list[str] = Field(default_factory=list, max_length=64)
    resources: AgentResources


class FlightState(StrictModel):
    armed: bool
    mode: str = Field(min_length=1, max_length=32)
    system_status: str = Field(min_length=1, max_length=32)
    latitude_e7: int | None = Field(default=None, ge=-900_000_000, le=900_000_000)
    longitude_e7: int | None = Field(
        default=None, ge=-1_800_000_000, le=1_800_000_000
    )
    relative_altitude_mm: int | None = Field(
        default=None, ge=-1_000_000, le=100_000_000
    )
    velocity_north_cms: int | None = Field(default=None, ge=-100_000, le=100_000)
    velocity_east_cms: int | None = Field(default=None, ge=-100_000, le=100_000)
    velocity_down_cms: int | None = Field(default=None, ge=-100_000, le=100_000)
    roll_mdeg: int | None = Field(default=None, ge=-180_000, le=180_000)
    pitch_mdeg: int | None = Field(default=None, ge=-90_000, le=90_000)
    yaw_mdeg: int | None = Field(default=None, ge=0, lt=360_000)
    heading_cdeg: int | None = Field(default=None, ge=0, lt=36_000)
    gps_fix: int | None = Field(default=None, ge=0, le=8)
    satellites: int | None = Field(default=None, ge=0, le=255)


class PowerState(StrictModel):
    voltage_mv: int = Field(ge=0, le=100_000)
    current_ma: int | None = Field(default=None, ge=-100_000, le=1_000_000)
    remaining_pct: int = Field(ge=0, le=100)
    consumed_mah: int | None = Field(default=None, ge=0, le=10_000_000)


class LinkState(StrictModel):
    rssi_dbm: int | None = Field(default=None, ge=-200, le=100)
    quality_pct: int = Field(ge=0, le=100)
    round_trip_ms: int | None = Field(default=None, ge=0, le=600_000)
    tx_dropped: int = Field(default=0, ge=0)


class ComputeState(StrictModel):
    cpu_pct: float = Field(ge=0, le=100)
    memory_used_mb: int = Field(ge=0)
    disk_used_mb: int = Field(ge=0)
    temperature_c: float | None = Field(default=None, ge=-100, le=250)
    load_1m: float | None = Field(default=None, ge=0)


class MissionState(StrictModel):
    mission_id: str | None = Field(default=None, max_length=64)
    status: Literal["idle", "loaded", "running", "paused", "completed", "failed"]
    current_item: int = Field(default=0, ge=0)
    item_count: int = Field(default=0, ge=0)


class FullTelemetry(FullEnvelope):
    type: Literal["telemetry.snapshot"] = "telemetry.snapshot"
    uptime_ms: int = Field(ge=0)
    flight: FlightState
    power: PowerState
    link: LinkState
    compute: ComputeState
    mission: MissionState | None = None
    sensors: dict[str, float] = Field(default_factory=dict, max_length=64)


class FullAgentEvent(FullEnvelope):
    type: Literal["agent.event"] = "agent.event"
    severity: Literal["debug", "info", "warning", "error", "critical"]
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    message: str = Field(min_length=1, max_length=1024)
    context: dict[str, Any] = Field(default_factory=dict, max_length=32)


class FullCommand(StrictModel):
    protocol: Literal["sap/1"] = FULL_PROTOCOL
    type: Literal["task.assign"] = "task.assign"
    command_id: str = Field(pattern=r"^cmd-[a-f0-9]{32}$")
    sequence: int = Field(ge=0)
    issued_at_ms: int = Field(ge=0)
    expires_at_ms: int | None = Field(default=None, ge=0)
    opcode: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=64)


class FullCommandAck(FullEnvelope):
    type: Literal["task.ack"] = "task.ack"
    command_id: str = Field(pattern=r"^cmd-[a-f0-9]{32}$")
    status: Literal["accepted", "running", "completed", "failed", "rejected"]
    code: str = Field(default="ok", pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    result: dict[str, Any] = Field(default_factory=dict, max_length=64)


FullUplink = FullAgentHello | FullTelemetry | FullAgentEvent | FullCommandAck

_FULL_TYPES: dict[str, type[FullEnvelope]] = {
    "agent.hello": FullAgentHello,
    "telemetry.snapshot": FullTelemetry,
    "agent.event": FullAgentEvent,
    "task.ack": FullCommandAck,
}


def encode_full_payload(payload: FullUplink | FullCommand) -> bytes:
    encoded = json.dumps(
        payload.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > FULL_MAX_PAYLOAD_BYTES:
        raise ValueError("Tier 1 payload exceeds 65536 bytes")
    return encoded


def decode_full_payload(payload: bytes) -> FullUplink:
    if not payload or len(payload) > FULL_MAX_PAYLOAD_BYTES:
        raise ValueError("Tier 1 payload must contain 1 to 65536 bytes")
    try:
        raw = json.loads(payload)
        model = _FULL_TYPES[raw["type"]]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("Unsupported Tier 1 payload") from exc
    return model.model_validate(raw)
