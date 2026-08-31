from __future__ import annotations

import threading
import time

from wificam_bridge.camera import CameraClient, CameraCredentials, CameraError
from wificam_bridge.commands import VIDEO_QUALITY_COMMAND
from wificam_bridge.session import CameraSession


class _StreamingClient:
    connect_count = 0

    def __init__(self) -> None:
        self.connect_count = 0
        self.closed = threading.Event()
        self.release_first_stream = threading.Event()
        self.control_started = threading.Event()
        self._control_lock = threading.Lock()
        self.active_controls = 0
        self.max_active_controls = 0
        self.commands: list[tuple[int, bytes]] = []

    def connect(self) -> None:
        self.connect_count += 1
        self.closed.clear()

    def start_audio(self) -> None:
        return

    def frames(self, *, audio_callback=None):
        if self.connect_count == 1:
            yield b"first-frame"
            self.release_first_stream.wait(1)
            raise CameraError("link lost")
        yield b"reconnected-frame"
        self.closed.wait(1)

    def send_control(self, command_id: int, payload: bytes) -> object:
        self.control_started.set()
        with self._control_lock:
            self.active_controls += 1
            self.max_active_controls = max(self.max_active_controls, self.active_controls)
            time.sleep(0.03)
            self.commands.append((command_id, payload))
            self.active_controls -= 1
        return object()

    def send_command(self, command_id: int) -> None:
        self.commands.append((command_id, b""))

    def close(self) -> None:
        self.closed.set()


class _ClosedSocket:
    def close(self) -> None:
        return


def test_camera_session_reconnects_media_and_serializes_controls() -> None:
    client = _StreamingClient()
    frames: list[bytes] = []
    session = CameraSession(client, reconnect_delay=0.001)
    session.start(on_frame=frames.append)
    assert _wait_until(lambda: frames == [b"first-frame"])

    first = threading.Thread(target=session.set_video_quality, args=("hd",))
    second = threading.Thread(target=session.set_video_quality, args=("uhd",))
    first.start()
    second.start()
    first.join()
    second.join()
    client.release_first_stream.set()

    assert _wait_until(lambda: client.connect_count >= 2)
    assert _wait_until(lambda: b"reconnected-frame" in frames)
    assert client.max_active_controls == 1
    assert [command for command, _payload in client.commands] == [
        VIDEO_QUALITY_COMMAND,
        VIDEO_QUALITY_COMMAND,
    ]
    session.close()


def test_camera_client_closing_connection_releases_pending_control() -> None:
    client = CameraClient(
        "synthetic-camera",
        CameraCredentials(b"prefix", "user", "password"),
        control_timeout=10,
    )
    client._socket = _ClosedSocket()  # type: ignore[assignment]
    client._send_request = lambda *_args: None  # type: ignore[method-assign]
    result: list[BaseException] = []

    thread = threading.Thread(
        target=lambda: _capture_error(result, lambda: client.send_control(2633)),
    )
    thread.start()
    assert _wait_until(lambda: bool(client._pending_commands))
    client.close()
    thread.join(timeout=1)

    assert isinstance(result[0], CameraError)
    assert "connection closed" in str(result[0])


def _capture_error(result: list[BaseException], call) -> None:
    try:
        call()
    except BaseException as error:
        result.append(error)


def _wait_until(predicate) -> bool:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False
