"""Configuration and validation for companion-computer camera sources."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit


CameraInput = Literal["v4l2", "rtsp", "network", "file"]
_CAMERA_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NETWORK_SCHEMES = {"http", "https", "rtmp", "rtp", "srt", "tcp", "udp"}


@dataclass(frozen=True, slots=True)
class CameraSource:
    """One camera input terminated on this companion computer."""

    camera_id: str
    input: CameraInput
    uri: str
    vehicle_id: str | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CameraSource":
        allowed = {
            "camera_id", "input", "uri", "vehicle_id", "width", "height", "frame_rate"
        }
        unexpected = set(value) - allowed
        if unexpected:
            raise ValueError(f"unknown camera fields: {', '.join(sorted(unexpected))}")
        try:
            source = cls(**value)
        except TypeError as exc:
            raise ValueError(f"invalid camera source: {exc}") from exc
        source.validate()
        return source

    def validate(self) -> None:
        if not _CAMERA_ID.fullmatch(self.camera_id):
            raise ValueError("camera_id must be 1-64 URL-safe characters")
        if self.input not in {"v4l2", "rtsp", "network", "file"}:
            raise ValueError(f"unsupported camera input: {self.input}")
        if not self.uri or "\x00" in self.uri:
            raise ValueError("camera uri must not be empty or contain NUL")
        parsed = urlsplit(self.uri)
        if self.input == "v4l2" and not self.uri.startswith("/dev/video"):
            raise ValueError("v4l2 cameras must use an absolute /dev/video* device")
        if self.input == "rtsp" and parsed.scheme not in {"rtsp", "rtsps"}:
            raise ValueError("rtsp cameras must use an rtsp:// or rtsps:// URI")
        if self.input == "network" and parsed.scheme not in _NETWORK_SCHEMES:
            raise ValueError("network camera URI uses an unsupported scheme")
        if self.input == "file" and not Path(self.uri).is_absolute():
            raise ValueError("file camera sources must use an absolute path")
        if self.width is not None and not 16 <= self.width <= 16_384:
            raise ValueError("width must be between 16 and 16384")
        if self.height is not None and not 16 <= self.height <= 16_384:
            raise ValueError("height must be between 16 and 16384")
        if self.frame_rate is not None and not 0 < self.frame_rate <= 240:
            raise ValueError("frame_rate must be greater than zero and at most 240")

    def public_uri(self) -> str:
        """Return a diagnostics-safe URI with network credentials removed."""
        parsed = urlsplit(self.uri)
        if not parsed.hostname:
            return self.uri
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "vehicle_id": self.vehicle_id,
            "input": self.input,
            "source": self.public_uri(),
            "width": self.width,
            "height": self.height,
            "frame_rate": self.frame_rate,
        }


def load_camera_sources(path: str | Path) -> tuple[CameraSource, ...]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("cameras"), list):
        raise ValueError("camera config must be an object containing a cameras list")
    sources = tuple(CameraSource.from_dict(item) for item in value["cameras"])
    ids = [source.camera_id for source in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("camera_id values must be unique")
    return sources
