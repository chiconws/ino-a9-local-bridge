"""Payload builders for the observed Linklemo camera controls."""

from __future__ import annotations

from datetime import time
from typing import Final

from .controls import (
    IntrusionSchedule,
    MotionDetectionSensitivity,
    NightVisionMode,
    ScreenFlipMode,
    VideoQuality,
)
from .pprpc import decode_varint, encode_varint

STATUS_INDICATOR_COMMAND: Final = 2633
NIGHT_VISION_SET_COMMAND: Final = 2635
NIGHT_VISION_GET_COMMAND: Final = 2636
SCREEN_FLIP_SET_COMMAND: Final = 2613
SCREEN_FLIP_GET_COMMAND: Final = 2649
VIDEO_QUALITY_COMMAND: Final = 2612
INTRUSION_DETECTION_COMMAND: Final = 2639
MOTION_DETECTION_COMMAND: Final = 2661
REBOOT_COMMAND: Final = 2647


def _require_nonnegative_int(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _encode_bytes_field(field_number: int, value: bytes) -> bytes:
    field_number = _require_nonnegative_int(field_number, "field_number")
    if field_number == 0:
        raise ValueError("field_number must be positive")
    if not isinstance(value, bytes):
        raise TypeError("value must be bytes")
    return encode_varint((field_number << 3) | 2) + encode_varint(len(value)) + value


def _encode_varint_field(field_number: int, value: int) -> bytes:
    field_number = _require_nonnegative_int(field_number, "field_number")
    if field_number == 0:
        raise ValueError("field_number must be positive")
    return encode_varint(field_number << 3) + encode_varint(
        _require_nonnegative_int(value, "value")
    )


def build_control_value_payload(wire_value: int) -> bytes:
    """Build the common setter payload whose value is protobuf field 2."""

    return _encode_varint_field(2, wire_value)


def build_status_indicator_payload(enabled: bool) -> bytes:
    """Build the status-LED setter payload.

    The camera uses enum values ``1`` and ``2`` rather than a boolean. The
    observed app maps ``True`` to ``1`` and ``False`` to ``2``.
    """

    if not isinstance(enabled, bool):
        raise TypeError("enabled must be a boolean")
    return build_control_value_payload(1 if enabled else 2)


def build_night_vision_payload(mode: NightVisionMode | str) -> bytes:
    """Build the observed night-vision setter payload."""

    mode = NightVisionMode(mode)
    return build_control_value_payload(
        {
            NightVisionMode.AUTOMATIC: 3,
            NightVisionMode.ENABLED: 1,
            NightVisionMode.DISABLED: 2,
        }[mode]
    )


def build_screen_flip_payload(mode: ScreenFlipMode | str) -> bytes:
    """Build the observed screen-orientation setter payload."""

    mode = ScreenFlipMode(mode)
    return build_control_value_payload(
        {
            ScreenFlipMode.UPRIGHT: 1,
            ScreenFlipMode.HORIZONTAL: 2,
            ScreenFlipMode.VERTICAL: 3,
            ScreenFlipMode.ROTATE_180: 4,
        }[mode]
    )


def build_video_quality_payload(quality: VideoQuality | str) -> bytes:
    """Build the observed video-quality setter payload."""

    quality = VideoQuality(quality)
    return build_control_value_payload(
        {
            VideoQuality.HD: 10,
            VideoQuality.SD: 5,
            VideoQuality.UHD: 15,
        }[quality]
    )


def build_motion_detection_payload(
    sensitivity: MotionDetectionSensitivity | str,
) -> bytes:
    """Build the observed motion-sensitivity setter payload."""

    sensitivity = MotionDetectionSensitivity(sensitivity)
    wire_value = {
        MotionDetectionSensitivity.HIGH: 3,
        MotionDetectionSensitivity.MEDIUM: 2,
        MotionDetectionSensitivity.LOW: 1,
        MotionDetectionSensitivity.CLOSED: 0,
    }[sensitivity]
    return b"" if wire_value == 0 else build_control_value_payload(wire_value)


def _time_seconds(value: time) -> int:
    return value.hour * 60 * 60 + value.minute * 60 + value.second


def build_intrusion_detection_payload(
    enabled: bool,
    schedule: IntrusionSchedule,
) -> bytes:
    """Build the observed intrusion-detection configuration payload.

    The schedule is encoded as weekday bytes and a nested time range. A zero
    start time is omitted because it is the protobuf default. The non-zero
    start-time field follows the inferred schema from the observed app traffic.
    """

    if not isinstance(enabled, bool):
        raise TypeError("enabled must be a boolean")
    if not isinstance(schedule, IntrusionSchedule):
        raise TypeError("schedule must be an IntrusionSchedule")

    time_range = bytearray()
    start_seconds = _time_seconds(schedule.start_time)
    if start_seconds:
        time_range.extend(_encode_varint_field(1, start_seconds))
    time_range.extend(_encode_varint_field(2, _time_seconds(schedule.end_time)))
    schedule_payload = (
        _encode_bytes_field(2, bytes(schedule.weekdays))
        + _encode_bytes_field(3, bytes(time_range))
        + _encode_varint_field(4, 1)
    )
    payload = _encode_bytes_field(3, schedule_payload)
    if enabled:
        payload = _encode_varint_field(2, 1) + payload
    return payload


def read_varint_field(payload: bytes, field_number: int) -> int | None:
    """Read the first protobuf varint field with ``field_number``."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    field_number = _require_nonnegative_int(field_number, "field_number")
    if field_number == 0:
        raise ValueError("field_number must be positive")

    cursor = 0
    while cursor < len(payload):
        key, cursor = decode_varint(payload, cursor)
        current_field, wire_type = key >> 3, key & 0x07
        if current_field == 0:
            raise ValueError("protobuf field number must be positive")
        if wire_type == 0:
            value, cursor = decode_varint(payload, cursor)
            if current_field == field_number:
                return value
        elif wire_type == 1:
            cursor += 8
        elif wire_type == 2:
            length, cursor = decode_varint(payload, cursor)
            cursor += length
        elif wire_type == 5:
            cursor += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")
        if cursor > len(payload):
            raise ValueError("truncated protobuf field")
    return None
