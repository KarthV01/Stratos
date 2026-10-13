"""Shared Redis key names for all Stratos agent transports."""

from app.mqtt_protocol import Frame


def device_key(device_id: str) -> str:
    return f"device:{device_id}"


def device_state_key(device_id: str) -> str:
    return f"device-state:{device_id}"


def device_command_key(command_id: str) -> str:
    return f"device-command:{command_id}"


def device_frame_key(device_id: str, frame: Frame) -> str:
    return f"device-frame:{device_id}:{frame.frame_type.value}:{frame.sequence}"


def latest_telemetry_key(device_id: str) -> str:
    return f"device-telemetry-latest:{device_id}"


def agent_command_stream(agent_id: str) -> str:
    return f"agent-commands:{agent_id}"
