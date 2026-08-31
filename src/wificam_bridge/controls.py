"""Public value types for the observed INO-A9 camera controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import Enum


class NightVisionMode(str, Enum):
    """Night-vision operating modes exposed by the Linklemo app."""

    AUTOMATIC = "automatic"
    ENABLED = "enabled"
    DISABLED = "disabled"


class ScreenFlipMode(str, Enum):
    """Image-orientation modes exposed by the Linklemo app."""

    UPRIGHT = "upright"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    ROTATE_180 = "rotate_180"


class VideoQuality(str, Enum):
    """Video-quality presets exposed by the Linklemo app."""

    HD = "hd"
    SD = "sd"
    UHD = "uhd"


class MotionDetectionSensitivity(str, Enum):
    """Motion-detection sensitivity settings exposed by the app."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class IntrusionSchedule:
    """Weekly intrusion-detection schedule.

    Weekdays follow the app's order: ``0`` is Sunday and ``6`` is Saturday.
    Times have minute precision and are encoded as seconds after midnight by
    the observed camera protocol.
    """

    weekdays: tuple[int, ...] = tuple(range(7))
    start_time: time = time(0, 0)
    end_time: time = time(23, 59)

    def __post_init__(self) -> None:
        try:
            weekdays = tuple(self.weekdays)
        except TypeError as exc:
            raise TypeError("weekdays must be an iterable of integers") from exc
        if not weekdays:
            raise ValueError("weekdays must not be empty")
        if any(
            isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6
            for day in weekdays
        ):
            raise ValueError("weekdays must contain integers from 0 through 6")
        if weekdays != tuple(sorted(set(weekdays))):
            raise ValueError("weekdays must be unique and sorted")
        object.__setattr__(self, "weekdays", weekdays)

        for name, value in (
            ("start_time", self.start_time),
            ("end_time", self.end_time),
        ):
            if not isinstance(value, time):
                raise TypeError(f"{name} must be a datetime.time")
            if value.tzinfo is not None:
                raise ValueError(f"{name} must not include timezone information")
            if value.second or value.microsecond:
                raise ValueError(f"{name} must have minute precision")

        if self.start_time > self.end_time:
            raise ValueError("start_time must not be after end_time")
