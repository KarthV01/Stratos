"""Transport-neutral normalized flight telemetry models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class VehicleIdentity:
    system_id: int | None = None
    component_id: int | None = None
    autopilot: str | None = None
    vehicle_type: str | None = None
    mavlink_version: int | None = None
    flight_sw_version: int | None = None
    capabilities: int | None = None
    hardware_uid: int | None = None


@dataclass(slots=True)
class GlobalPosition:
    boot_ms: int | None = None
    latitude_e7: int | None = None
    longitude_e7: int | None = None
    altitude_msl_mm: int | None = None
    relative_altitude_mm: int | None = None
    velocity_north_cms: int | None = None
    velocity_east_cms: int | None = None
    velocity_down_cms: int | None = None
    heading_cdeg: int | None = None
    ground_speed_cms: int | None = None


@dataclass(slots=True)
class GpsHealth:
    fix_type: int | None = None
    satellites_visible: int | None = None
    horizontal_dilution_centi: int | None = None
    vertical_dilution_centi: int | None = None


@dataclass(slots=True)
class BatteryHealth:
    battery_id: int | None = None
    voltage_mv: int | None = None
    current_ca: int | None = None
    remaining_percent: int | None = None


@dataclass(slots=True)
class VehicleState:
    base_mode: int | None = None
    custom_mode: int | None = None
    system_status: int | None = None
    armed: bool | None = None
    landed_state: int | None = None
    vtol_state: int | None = None
    mission_sequence: int | None = None
    mission_total: int | None = None
    mission_state: int | None = None
    sensor_present: int | None = None
    sensor_enabled: int | None = None
    sensor_health: int | None = None
    controller_load_decipct: int | None = None
    communication_drop_centipct: int | None = None


@dataclass(slots=True)
class Attitude:
    boot_ms: int | None = None
    roll_rad: float | None = None
    pitch_rad: float | None = None
    yaw_rad: float | None = None
    roll_speed_rad_s: float | None = None
    pitch_speed_rad_s: float | None = None
    yaw_speed_rad_s: float | None = None


@dataclass(slots=True)
class LocalPosition:
    boot_ms: int | None = None
    north_m: float | None = None
    east_m: float | None = None
    down_m: float | None = None
    velocity_north_m_s: float | None = None
    velocity_east_m_s: float | None = None
    velocity_down_m_s: float | None = None


@dataclass(slots=True)
class LocalPosition:
    boot_ms: int | None = None
    north_m: float | None = None
    east_m: float | None = None
    down_m: float | None = None
    velocity_north_m_s: float | None = None
    velocity_east_m_s: float | None = None
    velocity_down_m_s: float | None = None


@dataclass(slots=True)
class LinkState:
    connected: bool = False
    last_heartbeat_at: float | None = None
    received_messages: int = 0
    dropped_messages: int = 0
    parse_errors: int = 0
    unknown_messages: int = 0
    session_id: int = 1


@dataclass(slots=True)
class FlightSnapshotV1:
    vehicle_id: str
    device_id: str
    received_at: float
    identity: VehicleIdentity = field(default_factory=VehicleIdentity)
    position: GlobalPosition = field(default_factory=GlobalPosition)
    gps: GpsHealth = field(default_factory=GpsHealth)
    battery: BatteryHealth = field(default_factory=BatteryHealth)
    state: VehicleState = field(default_factory=VehicleState)
    attitude: Attitude = field(default_factory=Attitude)
    local_position: LocalPosition = field(default_factory=LocalPosition)
    local_position: LocalPosition = field(default_factory=LocalPosition)
    link: LinkState = field(default_factory=LinkState)
    unix_time_us: int | None = None
    home_latitude_e7: int | None = None
    home_longitude_e7: int | None = None
    home_altitude_mm: int | None = None
    schema: str = "flight/1"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MavlinkEvent:
    vehicle_id: str
    received_at: float
    kind: str
    severity: int | None
    message: str
