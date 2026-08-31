"""Reusable camera sessions for streaming and control commands."""

from __future__ import annotations

import time
from collections.abc import Callable
from math import isfinite
from threading import Event, Lock, Thread, current_thread
from typing import Self

from .camera import CameraClient, CameraError, decrypt_rpc_payload
from .commands import (
    INTRUSION_DETECTION_COMMAND,
    MOTION_DETECTION_COMMAND,
    NIGHT_VISION_GET_COMMAND,
    NIGHT_VISION_SET_COMMAND,
    REBOOT_COMMAND,
    SCREEN_FLIP_GET_COMMAND,
    SCREEN_FLIP_SET_COMMAND,
    STATUS_INDICATOR_COMMAND,
    VIDEO_QUALITY_COMMAND,
    build_intrusion_detection_payload,
    build_motion_detection_payload,
    build_night_vision_payload,
    build_screen_flip_payload,
    build_status_indicator_payload,
    build_video_quality_payload,
    read_varint_field,
)
from .controls import (
    IntrusionSchedule,
    MotionDetectionSensitivity,
    NightVisionMode,
    ScreenFlipMode,
    VideoQuality,
)
from .pprpc import RPCPacket

FrameCallback = Callable[[bytes], None]
AudioCallback = Callable[[bytes], None]


class CameraSession:
    """One camera connection that can stream and accept controls.

    The session owns one background reader for the camera socket. Control
    methods send requests on that same connection, while the reader continues
    to demultiplex video, audio, and RPC responses. This avoids opening a
    second camera stream when an application changes a setting.
    """

    def __init__(self, client: CameraClient, *, start_audio: bool = True) -> None:
        if not isinstance(client, CameraClient):
            raise TypeError("client must be a CameraClient")
        if not isinstance(start_audio, bool):
            raise TypeError("start_audio must be a boolean")
        self.client = client
        self._start_audio = start_audio
        self._state_lock = Lock()
        self._started = False
        self._closed = False
        self._thread: Thread | None = None
        self._stop_event = Event()
        self._done_event = Event()
        self._ready_event = Event()
        self._on_frame: FrameCallback | None = None
        self._on_audio: AudioCallback | None = None
        self._frame_count = 0
        self._thread_error: Exception | None = None

    @property
    def is_running(self) -> bool:
        """Whether the session's background reader is active."""

        with self._state_lock:
            return self._started and not self._done_event.is_set()

    @property
    def frame_count(self) -> int:
        """Return the number of complete JPEG frames delivered."""

        with self._state_lock:
            return self._frame_count

    @property
    def error(self) -> Exception | None:
        """Return the reader error, if the session ended unexpectedly."""

        with self._state_lock:
            return self._thread_error

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(
        self,
        *,
        on_frame: FrameCallback | None = None,
        on_audio: AudioCallback | None = None,
    ) -> Self:
        """Connect the camera and start its background media reader."""

        if on_frame is not None and not callable(on_frame):
            raise TypeError("on_frame must be callable")
        if on_audio is not None and not callable(on_audio):
            raise TypeError("on_audio must be callable")

        with self._state_lock:
            if self._started:
                raise CameraError("session has already been started")
            if self._closed:
                raise CameraError("session has been closed")

        try:
            self.client.connect()
            if self._start_audio:
                self.client.start_audio()
        except Exception:
            self.client.close()
            raise

        with self._state_lock:
            self._on_frame = on_frame
            self._on_audio = on_audio
            self._frame_count = 0
            self._thread_error = None
            self._stop_event.clear()
            self._done_event.clear()
            self._ready_event.clear()
            self._started = True
            self._thread = Thread(
                target=self._reader_main,
                name="wificam-camera-session",
                daemon=True,
            )
            self._thread.start()
        return self

    def wait_ready(self, timeout: float | None = None) -> None:
        """Wait until the first complete JPEG frame is available."""

        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("timeout must be a non-negative number")
            if not isfinite(timeout) or timeout < 0:
                raise ValueError("timeout must be a non-negative number")

        with self._state_lock:
            if not self._started:
                raise CameraError("session has not been started")

        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._ready_event.is_set():
            if self._done_event.is_set():
                error = self.error
                if error is not None:
                    raise error
                raise CameraError("session ended before the first frame")
            if deadline is None:
                self._done_event.wait(0.05)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("camera session did not become ready before timeout")
            self._done_event.wait(min(remaining, 0.05))

    def wait(self, *, stop_event: Event | None = None) -> int:
        """Wait for the reader to finish and return the frame count."""

        with self._state_lock:
            if not self._started:
                raise CameraError("session has not been started")
            thread = self._thread

        if thread is current_thread():
            raise CameraError("a session cannot wait from its reader thread")

        while not self._done_event.wait(0.05):
            if stop_event is not None and stop_event.is_set():
                self.close()
                break
        if thread is not None:
            thread.join()
        error = self.error
        if error is not None and not self._closed:
            raise error
        return self.frame_count

    def close(self) -> None:
        """Stop the reader and close the camera connection."""

        with self._state_lock:
            self._closed = True
            thread = self._thread
        self._stop_event.set()
        self.client.close()
        if thread is not None and thread is not current_thread():
            thread.join(timeout=self.client.connect_timeout)
        if not self._started:
            self._done_event.set()

    def send_control(self, command_id: int, payload: bytes = b"") -> RPCPacket:
        """Send a generic control request on this session's socket."""

        self._require_active()
        return self.client.send_control(command_id, payload)

    def set_status_indicator(self, enabled: bool) -> None:
        """Turn the camera's status LED on or off."""

        self.send_control(
            STATUS_INDICATOR_COMMAND,
            build_status_indicator_payload(enabled),
        )

    def set_night_vision(self, mode: NightVisionMode | str) -> None:
        """Set the camera's night-vision mode."""

        self.send_control(
            NIGHT_VISION_SET_COMMAND,
            build_night_vision_payload(mode),
        )

    def get_night_vision(self) -> NightVisionMode:
        """Read the camera's current night-vision mode."""

        response = self.send_control(NIGHT_VISION_GET_COMMAND)
        wire_value = self._read_response_value(response, NIGHT_VISION_GET_COMMAND)
        try:
            return {
                1: NightVisionMode.ENABLED,
                2: NightVisionMode.DISABLED,
                3: NightVisionMode.AUTOMATIC,
            }[wire_value]
        except KeyError as exc:
            raise CameraError(
                f"unsupported night-vision wire value {wire_value}"
            ) from exc

    def set_screen_flip(self, mode: ScreenFlipMode | str) -> None:
        """Set the camera's image orientation."""

        self.send_control(
            SCREEN_FLIP_SET_COMMAND,
            build_screen_flip_payload(mode),
        )

    def get_screen_flip(self) -> ScreenFlipMode:
        """Read the camera's current image orientation."""

        response = self.send_control(SCREEN_FLIP_GET_COMMAND)
        wire_value = self._read_response_value(response, SCREEN_FLIP_GET_COMMAND)
        try:
            return {
                1: ScreenFlipMode.UPRIGHT,
                2: ScreenFlipMode.HORIZONTAL,
                3: ScreenFlipMode.VERTICAL,
                4: ScreenFlipMode.ROTATE_180,
            }[wire_value]
        except KeyError as exc:
            raise CameraError(
                f"unsupported screen-flip wire value {wire_value}"
            ) from exc

    def set_video_quality(self, quality: VideoQuality | str) -> None:
        """Set the camera's video-quality preset."""

        self.send_control(
            VIDEO_QUALITY_COMMAND,
            build_video_quality_payload(quality),
        )

    def set_motion_detection(
        self,
        sensitivity: MotionDetectionSensitivity | str,
    ) -> None:
        """Set motion-detection sensitivity or close motion detection."""

        self.send_control(
            MOTION_DETECTION_COMMAND,
            build_motion_detection_payload(sensitivity),
        )

    def set_intrusion_detection(
        self,
        enabled: bool,
        *,
        schedule: IntrusionSchedule,
    ) -> None:
        """Set intrusion detection and its complete weekly schedule."""

        self.send_control(
            INTRUSION_DETECTION_COMMAND,
            build_intrusion_detection_payload(enabled, schedule),
        )

    def reboot(self) -> None:
        """Transmit the reboot request without waiting for an RPC response."""

        self._require_active()
        self.client.send_command(REBOOT_COMMAND)

    def _read_response_value(self, response: RPCPacket, command_id: int) -> int:
        payload = decrypt_rpc_payload(
            response,
            self.client.credentials.bootstrap_prefix,
        )
        value = read_varint_field(payload, 1)
        if value is None:
            raise CameraError(f"command {command_id} response has no field 1 value")
        return value

    def _require_active(self) -> None:
        with self._state_lock:
            if not self._started or self._closed or self._done_event.is_set():
                raise CameraError("controls require an active camera session")
            if current_thread() is self._thread:
                raise CameraError(
                    "controls cannot be called from a media callback or reader"
                )

    def _reader_main(self) -> None:
        try:
            for frame in self.client.frames(audio_callback=self._on_audio):
                with self._state_lock:
                    self._frame_count += 1
                    callback = self._on_frame
                self._ready_event.set()
                if callback is not None:
                    callback(frame)
                if self._stop_event.is_set():
                    break
        except Exception as exc:  # noqa: BLE001
            with self._state_lock:
                if not self._closed:
                    self._thread_error = exc
        finally:
            self.client.close()
            self._done_event.set()
