from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

from wificam_bridge.bridge import (
    PREVIEW_INTERVAL_SECONDS,
    BridgeHandler,
    CameraSettings,
    CameraState,
    CameraWorker,
)
from wificam_bridge.camera import CameraCredentials


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


def test_camera_worker_uses_one_reusable_session_for_media(monkeypatch) -> None:
    events: list[str] = []

    class FakeClient:
        def __init__(self, host, credentials, *, port):
            events.append(f"client:{host}:{port}")

    class FakeSession:
        def __init__(self, client, *, start_audio):
            del client
            events.append(f"session:{start_audio}")

        def start(self, *, on_frame, on_audio):
            assert callable(on_frame)
            assert callable(on_audio)
            events.append("start")

        def wait(self, *, stop_event):
            events.append("wait")
            stop_event.set()
            return 0

        def close(self):
            events.append("close")

    monkeypatch.setattr("wificam_bridge.bridge.CameraClient", FakeClient)
    monkeypatch.setattr("wificam_bridge.bridge.CameraSession", FakeSession)
    worker = CameraWorker(
        CameraSettings(
            "camera1",
            "camera.invalid",
            20190,
            CameraCredentials(b"prefix", "user", "password"),
        ),
        CameraState(),
        threading.Event(),
    )

    worker.run()

    assert events == [
        "client:camera.invalid:20190",
        "session:True",
        "start",
        "wait",
        "close",
    ]
