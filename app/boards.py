"""Board directory, capability classifier, and compact identity wire protocol."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "sbp/1"
WIRE_PREFIX = "SBP1"


class AgentTier(IntEnum):
    """Stratos execution tiers; lower numbers represent more autonomous agents."""

    FULL = 1
    EMBEDDED = 2
    MINIMAL = 3


TIER_DETAILS: dict[AgentTier, dict[str, Any]] = {
    AgentTier.FULL: {
        "code": "full-agent",
        "hardware": "Raspberry Pi, Jetson, x86 PCs, and Linux SBCs",
        "functionality": [
            "task-execution",
            "local-scheduling",
            "networking",
            "telemetry",
            "complex-workloads",
        ],
    },
    AgentTier.EMBEDDED: {
        "code": "embedded-agent",
        "hardware": "Networked ESP32, STM32, RP2040, and similar capable MCUs",
        "functionality": [
            "sensor-processing",
            "predefined-tasks",
            "lightweight-communication",
        ],
    },
    AgentTier.MINIMAL: {
        "code": "minimal-agent",
        "hardware": "ESP8266 and constrained Arduino Wi-Fi boards",
        "functionality": [
            "simple-commands",
            "sensor-reporting",
            "limited-state",
        ],
    },
}


@dataclass(frozen=True, slots=True)
class BoardProfile:
    identifier: str
    name: str
    vendor: str
    agent_tier: AgentTier
    linux: bool
    ip_network: bool
    accelerator: str | None
    aliases: tuple[str, ...]
    source: str

    @property
    def tier_code(self) -> str:
        return str(TIER_DETAILS[self.agent_tier]["code"])

    @property
    def connection_mode(self) -> str:
        if self.agent_tier == AgentTier.FULL:
            return "agent"
        return "mqtt"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "name": self.name,
            "vendor": self.vendor,
            "tier": int(self.agent_tier),
            "tier_code": self.tier_code,
            "functionality": TIER_DETAILS[self.agent_tier]["functionality"],
            "linux": self.linux,
            "ip_network": self.ip_network,
            "accelerator": self.accelerator,
            "connection_mode": self.connection_mode,
            "aliases": list(self.aliases),
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class BoardHello:
    board: str
    device_id: str
    firmware: str


def normalize_identifier(value: str) -> str:
    """Normalize human-entered model names without fuzzy matching."""
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


@lru_cache(maxsize=1)
def board_directory() -> tuple[BoardProfile, ...]:
    path = Path(__file__).with_name("board_directory.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise RuntimeError("Unsupported board directory schema")

    profiles: list[BoardProfile] = []
    known_keys: dict[str, str] = {}
    for item in raw["boards"]:
        profile = BoardProfile(
            identifier=item["id"],
            name=item["name"],
            vendor=item["vendor"],
            agent_tier=AgentTier(item["tier"]),
            linux=item["linux"],
            ip_network=item["ip_network"],
            accelerator=item.get("accelerator"),
            aliases=tuple(item.get("aliases", [])),
            source=item["source"],
        )
        if not profile.ip_network:
            raise RuntimeError(
                f"Board {profile.identifier} cannot reach Stratos over an IP network"
            )
        for key in {
            normalize_identifier(candidate)
            for candidate in (profile.identifier, profile.name, *profile.aliases)
        }:
            if key in known_keys:
                raise RuntimeError(
                    f"Board key {key!r} is shared by {known_keys[key]} and "
                    f"{profile.identifier}"
                )
            known_keys[key] = profile.identifier
        profiles.append(profile)
    return tuple(profiles)


@lru_cache(maxsize=1)
def _board_index() -> dict[str, BoardProfile]:
    index: dict[str, BoardProfile] = {}
    for profile in board_directory():
        for candidate in (profile.identifier, profile.name, *profile.aliases):
            index[normalize_identifier(candidate)] = profile
    return index


def resolve_board(identifier: str) -> BoardProfile | None:
    return _board_index().get(normalize_identifier(identifier))


def tier_catalog() -> list[dict[str, Any]]:
    return [
        {"tier": int(agent_tier), **details}
        for agent_tier, details in TIER_DETAILS.items()
    ]


def classify_capabilities(
    *, linux: bool, ram_kb: int, accelerator: bool, ip_network: bool
) -> AgentTier:
    """Conservatively classify an unknown board from reported capabilities."""
    if not ip_network:
        raise ValueError("A board must support an IP network and MQTT or a full agent")
    if linux:
        return AgentTier.FULL
    if accelerator or ram_kb >= 128:
        return AgentTier.EMBEDDED
    return AgentTier.MINIMAL


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")


def _validate_token(name: str, value: str) -> str:
    if not _TOKEN.fullmatch(value):
        raise ValueError(f"Invalid {name} token")
    return value


def encode_hello(board: str, device_id: str, firmware: str = "-") -> bytes:
    """Encode a newline-terminated serial/MQTT identity request (at most 202 bytes)."""
    fields = (
        _validate_token("board", board),
        _validate_token("device_id", device_id),
        _validate_token("firmware", firmware),
    )
    return f"{WIRE_PREFIX}|{'|'.join(fields)}\n".encode("ascii")


def decode_hello(payload: bytes) -> BoardHello:
    if len(payload) > 202:
        raise ValueError("Identity frame exceeds 202 bytes")
    try:
        text = payload.decode("ascii").removesuffix("\n")
    except UnicodeDecodeError as exc:
        raise ValueError("Identity frame must be ASCII") from exc
    parts = text.split("|")
    if len(parts) != 4 or parts[0] != WIRE_PREFIX:
        raise ValueError("Unsupported identity frame")
    return BoardHello(
        board=_validate_token("board", parts[1]),
        device_id=_validate_token("device_id", parts[2]),
        firmware=_validate_token("firmware", parts[3]),
    )


def encode_identity(profile: BoardProfile) -> bytes:
    return (
        f"{WIRE_PREFIX}|OK|{profile.identifier}|{int(profile.agent_tier)}|"
        f"{profile.tier_code}|{profile.connection_mode}\n"
    ).encode("ascii")


def encode_unknown() -> bytes:
    return f"{WIRE_PREFIX}|ERR|unknown-board\n".encode("ascii")
