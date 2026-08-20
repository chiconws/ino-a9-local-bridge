"""Small multi-camera HTTP MJPEG bridge for Home Assistant and browsers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import re
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .camera import CameraClient, CameraCredentials


LOGGER = logging.getLogger("wificam_bridge")
BOUNDARY = "ino-a9-frame"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
PREVIEW_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class CameraSettings:
    name: str
    host: str
    port: int
    credentials: CameraCredentials


@dataclass(frozen=True, slots=True)
class BridgeSettings:
    listen_host: str
    listen_port: int
    cameras: tuple[CameraSettings, ...]


def load_config(path: str | Path) -> BridgeSettings:
    """Load a private JSON configuration file."""
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("bridge config must be a JSON object")
    camera_values = raw.get("cameras")
    if not isinstance(camera_values, list) or not camera_values:
        raise ValueError("bridge config must contain a non-empty cameras list")
    cameras: list[CameraSettings] = []
    names: set[str] = set()
    for value in camera_values:
        if not isinstance(value, dict):
            raise ValueError("each camera config must be a JSON object")
        name = _required_string(value, "name")
        if not SAFE_NAME.fullmatch(name):
            raise ValueError(f"invalid camera name {name!r}")
        if name in names:
            raise ValueError(f"duplicate camera name {name!r}")
        names.add(name)
        credentials = CameraCredentials(
            bootstrap_prefix=_required_string(value, "bootstrap_prefix").encode("utf-8"),
            user=_required_string(value, "user"),
            lan_password=_required_string(value, "lan_password"),
        )
        cameras.append(
            CameraSettings(
                name=name,
                host=_required_string(value, "host"),
                port=_port(value.get("port", 20190), "camera port"),
                credentials=credentials,
            )
        )
    return BridgeSettings(
        listen_host=str(raw.get("listen_host", "127.0.0.1")),
        listen_port=_port(raw.get("listen_port", 8080), "listen_port"),
        cameras=tuple(cameras),
    )


class CameraState:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.frame: bytes | None = None
        self.frame_id = 0
        self.connected = False
        self.last_frame_at: float | None = None
        self.error: str | None = None

    def set_connected(self, connected: bool, error: str | None = None) -> None:
        with self.condition:
            self.connected = connected
            self.error = error
            self.condition.notify_all()

    def publish(self, frame: bytes) -> None:
        with self.condition:
            self.frame = frame
            self.frame_id += 1
            self.connected = True
            self.error = None
            self.last_frame_at = time.time()
            self.condition.notify_all()

    def wait_for_frame(self, after: int, timeout: float) -> tuple[int, bytes] | None:
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.frame is None or self.frame_id == after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)
            return self.frame_id, self.frame

    def status(self) -> dict[str, Any]:
        with self.condition:
            return {
                "connected": self.connected,
                "has_frame": self.frame is not None,
                "last_frame_at": self.last_frame_at,
                "error": self.error,
            }


class CameraWorker(threading.Thread):
    def __init__(
        self,
        settings: CameraSettings,
        state: CameraState,
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name=f"camera-{settings.name}", daemon=True)
        self.settings = settings
        self.state = state
        self.stop_event = stop_event

    def run(self) -> None:
        while not self.stop_event.is_set():
            client = CameraClient(
                self.settings.host,
                self.settings.credentials,
                port=self.settings.port,
            )
            try:
                LOGGER.info("connecting camera %s at %s", self.settings.name, self.settings.host)
                client.connect()
                self.state.set_connected(True)
                LOGGER.info("camera %s stream started", self.settings.name)
                for frame in client.frames():
                    self.state.publish(frame)
                    if self.stop_event.is_set():
                        break
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                self.state.set_connected(False, message)
                LOGGER.warning("camera %s disconnected: %s", self.settings.name, message)
            finally:
                client.close()
            self.stop_event.wait(3.0)


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        states: dict[str, CameraState],
    ) -> None:
        super().__init__(address, BridgeHandler)
        self.states = states


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "INOA9Bridge/0.1"

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = urlsplit(self.path).path
        if path in ("/", "/health"):
            self._health()
            return
        parts = [part for part in path.split("/") if part]
        if len(parts) != 2 or parts[0] not in self.server.states:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        state = self.server.states[parts[0]]
        if parts[1] == "snapshot.jpg":
            self._snapshot(state)
        elif parts[1] == "stream.mjpeg":
            self._stream(state)
        elif parts[1] == "preview.mjpeg":
            self._stream(state, minimum_interval=PREVIEW_INTERVAL_SECONDS)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _health(self) -> None:
        body = json.dumps(
            {name: state.status() for name, state in self.server.states.items()},
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _snapshot(self, state: CameraState) -> None:
        value = state.wait_for_frame(-1, 5.0)
        if value is None:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "camera has no frame")
            return
        _, frame = value
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(frame)

    def _stream(self, state: CameraState, *, minimum_interval: float = 0.0) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        frame_id = -1
        next_frame_at = 0.0
        try:
            while True:
                value = state.wait_for_frame(frame_id, 15.0)
                if value is None:
                    continue
                frame_id, frame = value
                if minimum_interval:
                    now = time.monotonic()
                    if now < next_frame_at:
                        continue
                    next_frame_at = now + minimum_interval
                header = (
                    f"--{BOUNDARY}\r\n"
                    "Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n"
                ).encode("ascii")
                self.wfile.write(header)
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return

    def log_message(self, message: str, *args: object) -> None:
        LOGGER.info("http %s - %s", self.client_address[0], message % args)


def _required_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"camera config field {key!r} must be a non-empty string")
    return result


def _port(value: object, label: str) -> int:
    if not isinstance(value, int) or not (1 <= value <= 65535):
        raise ValueError(f"{label} must be an integer from 1 to 65535")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Expose INO-A9 cameras as local MJPEG")
    parser.add_argument("--config", required=True, help="private bridge JSON configuration")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = load_config(args.config)
    stop_event = threading.Event()
    states = {camera.name: CameraState() for camera in settings.cameras}
    workers = [
        CameraWorker(camera, states[camera.name], stop_event)
        for camera in settings.cameras
    ]
    for worker in workers:
        worker.start()
    server = BridgeHTTPServer((settings.listen_host, settings.listen_port), states)
    LOGGER.info("HTTP bridge listening on %s:%d", settings.listen_host, settings.listen_port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        LOGGER.info("stopping")
    finally:
        stop_event.set()
        server.server_close()
        for worker in workers:
            worker.join(timeout=5.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
