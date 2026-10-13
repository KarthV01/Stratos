"""Stateful, receive-only MAVLink-to-Stratos telemetry normalization."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import replace
from typing import Any

from app.mavlink.models import (
    Attitude,
    BatteryHealth,
    FlightSnapshotV1,
    GlobalPosition,
    GpsHealth,
    LinkState,
    LocalPosition,
    MavlinkEvent,
    VehicleIdentity,
    VehicleState,
)


MAV_MODE_FLAG_SAFETY_ARMED = 128
UINT16_MAX = 65_535
UINT64_MAX = 18_446_744_073_709_551_615
SUPPORTED_MESSAGES = frozenset(
    {
        "HEARTBEAT",
        "GLOBAL_POSITION_INT",
        "GPS_RAW_INT",
        "SYS_STATUS",
        "BATTERY_STATUS",
        "EXTENDED_SYS_STATE",
        "MISSION_CURRENT",
        "HOME_POSITION",
        "SYSTEM_TIME",
        "AUTOPILOT_VERSION",
        "ATTITUDE",
        "ATTITUDE_QUATERNION",
        "LOCAL_POSITION_NED",
        "STATUSTEXT",
        "HIGH_LATENCY2",
    }
)

AUTOPILOT_NAMES = {3: "ardupilot", 12: "px4"}
VEHICLE_TYPE_NAMES = {
    1: "fixed-wing",
    2: "quadrotor",
    3: "coaxial",
    4: "helicopter",
    10: "ground-rover",
    11: "surface-boat",
    12: "submarine",
    13: "hexarotor",
    14: "octorotor",
    19: "vtol",
}


def _optional(value: Any, invalid: Any) -> Any | None:
    return None if value is None or value == invalid else value


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _source(message: Any) -> tuple[int | None, int | None]:
    system = message.get_srcSystem() if hasattr(message, "get_srcSystem") else None
    component = message.get_srcComponent() if hasattr(message, "get_srcComponent") else None
    return system, component


def _sequence(message: Any) -> int | None:
    if hasattr(message, "get_seq"):
        return int(message.get_seq())
    header = getattr(message, "_header", None)
    return int(header.seq) if header is not None and hasattr(header, "seq") else None


def _wire_version(message: Any) -> int | None:
    if hasattr(message, "get_msgbuf"):
        payload = message.get_msgbuf()
        if payload:
            if payload[0] == 0xFD:
                return 2
            if payload[0] == 0xFE:
                return 1
    return getattr(message, "wire_version", None)


class MavlinkTelemetryAccumulator:
    """Normalize selected MAVLink messages without emitting MAVLink traffic."""

    def __init__(self, vehicle_id: str, device_id: str, stale_seconds: float = 5.0):
        if not vehicle_id or not device_id:
            raise ValueError("vehicle_id and device_id are required")
        self.vehicle_id = vehicle_id
        self.device_id = device_id
        self.stale_seconds = stale_seconds
        self.identity = VehicleIdentity()
        self.position = GlobalPosition()
        self.gps = GpsHealth()
        self.battery = BatteryHealth()
        self.state = VehicleState()
        self.attitude = Attitude()
        self.local_position = LocalPosition()
        self.link = LinkState()
        self.unix_time_us: int | None = None
        self.home_latitude_e7: int | None = None
        self.home_longitude_e7: int | None = None
        self.home_altitude_mm: int | None = None
        self._last_boot_ms: int | None = None
        self._last_sequences: dict[tuple[int | None, int | None], int] = {}
        self._events: deque[MavlinkEvent] = deque(maxlen=50)

    def process(self, message: Any, received_at: float | None = None) -> bool:
        now = time.time() if received_at is None else received_at
        message_type = message.get_type()
        if message_type == "BAD_DATA":
            self.link.parse_errors += 1
            return False

        self.link.received_messages += 1
        self._track_sequence(message)
        if message_type not in SUPPORTED_MESSAGES:
            self.link.unknown_messages += 1
            return False

        handler = getattr(self, f"_handle_{message_type.lower()}")
        handler(message, now)
        boot_ms = getattr(message, "time_boot_ms", None)
        if boot_ms is not None:
            self._track_boot_time(int(boot_ms), now)
        return True

    def snapshot(self, received_at: float | None = None) -> FlightSnapshotV1:
        now = time.time() if received_at is None else received_at
        last_heartbeat = self.link.last_heartbeat_at
        self.link.connected = bool(
            last_heartbeat is not None and now - last_heartbeat <= self.stale_seconds
        )
        return FlightSnapshotV1(
            vehicle_id=self.vehicle_id,
            device_id=self.device_id,
            received_at=now,
            identity=replace(self.identity),
            position=replace(self.position),
            gps=replace(self.gps),
            battery=replace(self.battery),
            state=replace(self.state),
            attitude=replace(self.attitude),
            local_position=replace(self.local_position),
            link=replace(self.link),
            unix_time_us=self.unix_time_us,
            home_latitude_e7=self.home_latitude_e7,
            home_longitude_e7=self.home_longitude_e7,
            home_altitude_mm=self.home_altitude_mm,
        )

    def drain_events(self) -> list[MavlinkEvent]:
        events = list(self._events)
        self._events.clear()
        return events

    def _track_sequence(self, message: Any) -> None:
        sequence = _sequence(message)
        if sequence is None:
            return
        source = _source(message)
        previous = self._last_sequences.get(source)
        if previous is not None:
            gap = (sequence - previous - 1) & 0xFF
            if gap <= 127:
                self.link.dropped_messages += gap
        self._last_sequences[source] = sequence

    def _track_boot_time(self, boot_ms: int, now: float) -> None:
        if self._last_boot_ms is not None and boot_ms < self._last_boot_ms:
            self.link.session_id += 1
            self._last_sequences.clear()
            self._events.append(
                MavlinkEvent(
                    self.vehicle_id, now, "autopilot-reboot", None, "boot-time-reset"
                )
            )
        self._last_boot_ms = boot_ms

    def _handle_heartbeat(self, message: Any, now: float) -> None:
        system_id, component_id = _source(message)
        autopilot = int(message.autopilot)
        vehicle_type = int(message.type)
        self.identity.system_id = system_id
        self.identity.component_id = component_id
        self.identity.autopilot = AUTOPILOT_NAMES.get(autopilot, f"mav-autopilot-{autopilot}")
        self.identity.vehicle_type = VEHICLE_TYPE_NAMES.get(
            vehicle_type, f"mav-type-{vehicle_type}"
        )
        self.identity.mavlink_version = _wire_version(message)
        self.state.base_mode = int(message.base_mode)
        self.state.custom_mode = int(message.custom_mode)
        self.state.system_status = int(message.system_status)
        self.state.armed = bool(self.state.base_mode & MAV_MODE_FLAG_SAFETY_ARMED)
        self.link.last_heartbeat_at = now
        self.link.connected = True

    def _handle_global_position_int(self, message: Any, now: float) -> None:
        vx = _optional(int(message.vx), 32_767)
        vy = _optional(int(message.vy), 32_767)
        self.position = GlobalPosition(
            boot_ms=int(message.time_boot_ms),
            latitude_e7=int(message.lat),
            longitude_e7=int(message.lon),
            altitude_msl_mm=int(message.alt),
            relative_altitude_mm=int(message.relative_alt),
            velocity_north_cms=vx,
            velocity_east_cms=vy,
            velocity_down_cms=_optional(int(message.vz), 32_767),
            heading_cdeg=_optional(int(message.hdg), UINT16_MAX),
            ground_speed_cms=(
                round(math.hypot(vx, vy)) if vx is not None and vy is not None else None
            ),
        )

    def _handle_gps_raw_int(self, message: Any, now: float) -> None:
        self.gps = GpsHealth(
            fix_type=int(message.fix_type),
            satellites_visible=_optional(int(message.satellites_visible), 255),
            horizontal_dilution_centi=_optional(int(message.eph), UINT16_MAX),
            vertical_dilution_centi=_optional(int(message.epv), UINT16_MAX),
        )

    def _handle_sys_status(self, message: Any, now: float) -> None:
        self.state.sensor_present = int(message.onboard_control_sensors_present)
        self.state.sensor_enabled = int(message.onboard_control_sensors_enabled)
        self.state.sensor_health = int(message.onboard_control_sensors_health)
        self.state.controller_load_decipct = int(message.load)
        self.state.communication_drop_centipct = int(message.drop_rate_comm)
        if self.battery.voltage_mv is None:
            self.battery.voltage_mv = _optional(int(message.voltage_battery), UINT16_MAX)
        if self.battery.current_ca is None:
            self.battery.current_ca = _optional(int(message.current_battery), -1)
        if self.battery.remaining_percent is None:
            self.battery.remaining_percent = _optional(int(message.battery_remaining), -1)

    def _handle_battery_status(self, message: Any, now: float) -> None:
        voltages = [int(value) for value in message.voltages if int(value) != UINT16_MAX]
        self.battery = BatteryHealth(
            battery_id=int(message.id),
            voltage_mv=sum(voltages) if voltages else None,
            current_ca=_optional(int(message.current_battery), -1),
            remaining_percent=_optional(int(message.battery_remaining), -1),
        )

    def _handle_extended_sys_state(self, message: Any, now: float) -> None:
        self.state.vtol_state = int(message.vtol_state)
        self.state.landed_state = int(message.landed_state)

    def _handle_mission_current(self, message: Any, now: float) -> None:
        self.state.mission_sequence = int(message.seq)
        self.state.mission_total = _optional(getattr(message, "total", None), UINT16_MAX)
        self.state.mission_state = _optional(getattr(message, "mission_state", None), 0)

    def _handle_home_position(self, message: Any, now: float) -> None:
        self.home_latitude_e7 = int(message.latitude)
        self.home_longitude_e7 = int(message.longitude)
        self.home_altitude_mm = int(message.altitude)

    def _handle_system_time(self, message: Any, now: float) -> None:
        self.unix_time_us = _optional(int(message.time_unix_usec), 0)

    def _handle_autopilot_version(self, message: Any, now: float) -> None:
        self.identity.capabilities = int(message.capabilities)
        self.identity.flight_sw_version = int(message.flight_sw_version)
        self.identity.hardware_uid = _optional(int(message.uid), 0)

    def _handle_attitude(self, message: Any, now: float) -> None:
        self.attitude = Attitude(
            boot_ms=int(message.time_boot_ms),
            roll_rad=_finite(message.roll),
            pitch_rad=_finite(message.pitch),
            yaw_rad=_finite(message.yaw),
            roll_speed_rad_s=_finite(message.rollspeed),
            pitch_speed_rad_s=_finite(message.pitchspeed),
            yaw_speed_rad_s=_finite(message.yawspeed),
        )

    def _handle_attitude_quaternion(self, message: Any, now: float) -> None:
        # Preserve rates and timestamp. Quaternion-to-Euler conversion stays outside
        # this constrained ingestion boundary to avoid silently changing conventions.
        self.attitude.boot_ms = int(message.time_boot_ms)
        self.attitude.roll_speed_rad_s = _finite(message.rollspeed)
        self.attitude.pitch_speed_rad_s = _finite(message.pitchspeed)
        self.attitude.yaw_speed_rad_s = _finite(message.yawspeed)

    def _handle_local_position_ned(self, message: Any, now: float) -> None:
        self.local_position = LocalPosition(
            boot_ms=int(message.time_boot_ms),
            north_m=_finite(message.x),
            east_m=_finite(message.y),
            down_m=_finite(message.z),
            velocity_north_m_s=_finite(message.vx),
            velocity_east_m_s=_finite(message.vy),
            velocity_down_m_s=_finite(message.vz),
        )

    def _handle_statustext(self, message: Any, now: float) -> None:
        raw_text = message.text
        text = (
            raw_text.decode("utf-8", errors="replace")
            if isinstance(raw_text, bytes)
            else str(raw_text)
        ).rstrip("\x00")
        self._events.append(
            MavlinkEvent(self.vehicle_id, now, "status-text", int(message.severity), text)
        )

    def _handle_high_latency2(self, message: Any, now: float) -> None:
        self.position.latitude_e7 = int(message.latitude)
        self.position.longitude_e7 = int(message.longitude)
        self.position.altitude_msl_mm = int(message.altitude) * 1_000
        self.position.heading_cdeg = int(message.heading) * 200
        self.position.ground_speed_cms = int(message.groundspeed) * 20
        self.gps.fix_type = _optional(getattr(message, "gps_fix_type", None), 0)
        self.gps.satellites_visible = _optional(getattr(message, "gps_nsat", None), 255)
        self.battery.remaining_percent = _optional(int(message.battery), -1)
        self.state.custom_mode = int(message.custom_mode)
        self.state.landed_state = int(message.landed_state)

