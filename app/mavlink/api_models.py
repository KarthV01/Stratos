"""Validated API envelopes for native companion telemetry."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.mavlink.models import FlightSnapshotV1, MavlinkEvent


class VehicleTelemetryBatch(BaseModel):
    device_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    samples: list[FlightSnapshotV1] = Field(min_length=1, max_length=25)
    events: list[MavlinkEvent] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def matching_device_ids(self) -> "VehicleTelemetryBatch":
        if any(sample.device_id != self.device_id for sample in self.samples):
            raise ValueError("Sample device ID does not match batch device ID")
        return self
