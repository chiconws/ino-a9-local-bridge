from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from wificam_bridge.bridge import (
    BridgeHandler,
    CameraState,
    PREVIEW_INTERVAL_SECONDS,
)


def _handler(path: str) -> tuple[BridgeHandler, CameraState]:
    state = CameraState()
    handler = object.__new__(BridgeHandler)
    handler.path = path
    handler.server = SimpleNamespace(states={"camera1": state})
    handler._snapshot = Mock()
    handler._stream = Mock()
    handler._audio = Mock()
    handler.send_error = Mock()
    return handler, state


def test_preview_endpoint_uses_one_frame_per_second_limit() -> None:
    handler, state = _handler("/camera1/preview.mjpeg")

    handler.do_GET()

    handler._stream.assert_called_once_with(
        state,
        minimum_interval=PREVIEW_INTERVAL_SECONDS,
    )
    handler.send_error.assert_not_called()


def test_full_stream_endpoint_has_no_frame_rate_limit() -> None:
    handler, state = _handler("/camera1/stream.mjpeg")

    handler.do_GET()

    handler._stream.assert_called_once_with(state)
    handler.send_error.assert_not_called()


def test_audio_endpoint_uses_camera_state() -> None:
    handler, state = _handler("/camera1/audio.alaw")

    handler.do_GET()

    handler._audio.assert_called_once_with(state)
    handler.send_error.assert_not_called()
