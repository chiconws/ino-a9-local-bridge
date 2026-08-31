from __future__ import annotations

from datetime import time

import pytest


def test_control_value_types_are_public() -> None:
    import wificam_bridge

    assert hasattr(wificam_bridge, "NightVisionMode")
    assert hasattr(wificam_bridge, "ScreenFlipMode")
    assert hasattr(wificam_bridge, "VideoQuality")
    assert hasattr(wificam_bridge, "MotionDetectionSensitivity")
    assert hasattr(wificam_bridge, "IntrusionSchedule")


def test_control_payload_builders_are_public() -> None:
    import wificam_bridge

    assert callable(getattr(wificam_bridge, "build_control_value_payload", None))
    assert callable(getattr(wificam_bridge, "build_video_quality_payload", None))
    assert callable(getattr(wificam_bridge, "build_motion_detection_payload", None))
    assert callable(getattr(wificam_bridge, "build_intrusion_detection_payload", None))


def test_camera_api_types_are_public() -> None:
    import wificam_bridge

    assert callable(getattr(wificam_bridge, "CameraClient", None))
    assert callable(getattr(wificam_bridge, "CameraCredentials", None))
    assert callable(getattr(wificam_bridge, "CameraSession", None))


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [(True, bytes.fromhex("1001")), (False, bytes.fromhex("1002"))],
)
def test_status_indicator_payload_uses_observed_enum_values(
    enabled: bool, expected: bytes
) -> None:
    from wificam_bridge import build_status_indicator_payload

    assert build_status_indicator_payload(enabled) == expected


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("automatic", bytes.fromhex("1003")),
        ("enabled", bytes.fromhex("1001")),
        ("disabled", bytes.fromhex("1002")),
    ],
)
def test_night_vision_payload_maps_app_values(mode: str, expected: bytes) -> None:
    from wificam_bridge import build_night_vision_payload

    assert build_night_vision_payload(mode) == expected


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("upright", bytes.fromhex("1001")),
        ("horizontal", bytes.fromhex("1002")),
        ("vertical", bytes.fromhex("1003")),
        ("rotate_180", bytes.fromhex("1004")),
    ],
)
def test_screen_flip_payload_maps_app_values(mode: str, expected: bytes) -> None:
    from wificam_bridge import build_screen_flip_payload

    assert build_screen_flip_payload(mode) == expected


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("hd", bytes.fromhex("100a")),
        ("sd", bytes.fromhex("1005")),
        ("uhd", bytes.fromhex("100f")),
    ],
)
def test_video_quality_payload_maps_observed_values(
    quality: str, expected: bytes
) -> None:
    from wificam_bridge import build_video_quality_payload

    assert build_video_quality_payload(quality) == expected


@pytest.mark.parametrize(
    ("sensitivity", "expected"),
    [
        ("high", bytes.fromhex("1003")),
        ("medium", bytes.fromhex("1002")),
        ("low", bytes.fromhex("1001")),
        ("closed", b""),
    ],
)
def test_motion_detection_payload_maps_observed_values(
    sensitivity: str, expected: bytes
) -> None:
    from wificam_bridge import build_motion_detection_payload

    assert build_motion_detection_payload(sensitivity) == expected


def test_intrusion_detection_payload_matches_captured_schedule_shape() -> None:
    from wificam_bridge import IntrusionSchedule, build_intrusion_detection_payload

    schedule = IntrusionSchedule(
        weekdays=(1, 3, 5),
        start_time=time(0, 0),
        end_time=time(12, 0),
    )

    assert build_intrusion_detection_payload(False, schedule) == bytes.fromhex(
        "1a0d12030103051a0410c0d1022001"
    )
    assert build_intrusion_detection_payload(True, schedule) == bytes.fromhex(
        "10011a0d12030103051a0410c0d1022001"
    )


def test_intrusion_detection_payload_encodes_nonzero_start_time() -> None:
    from wificam_bridge import IntrusionSchedule, build_intrusion_detection_payload

    schedule = IntrusionSchedule(
        weekdays=(0, 6),
        start_time=time(8, 30),
        end_time=time(17, 45),
    )

    assert build_intrusion_detection_payload(True, schedule) == bytes.fromhex(
        "10011a10120200061a080888ef01109cf3032001"
    )


def test_control_value_payload_rejects_boolean_as_a_wire_integer() -> None:
    from wificam_bridge import build_control_value_payload

    with pytest.raises(TypeError, match="value must be an integer"):
        build_control_value_payload(True)
