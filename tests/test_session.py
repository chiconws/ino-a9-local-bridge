from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from queue import Empty, Queue

import pytest

from wificam_bridge.camera import (
    CameraClient,
    CameraCredentials,
    encode_protobuf_bytes,
    encode_protobuf_varint,
    pack_rpc,
)
from wificam_bridge.crypto import aes_cbc_encrypt_unpadded
from wificam_bridge.pprpc import (
    RPCPacket,
    derive_av_key,
    encode_varint,
    parse_packet,
)

PREFIX = b"synthetic-control-prefix"
SESSION_PREFIX = b"0123456789abcdef0123456789abcdef"


def _outer_av(sequence: int, part: int, payload: bytes, encrypted_length: int) -> bytes:
    body = (
        bytes((4, 3))
        + encode_varint(0)
        + encode_varint(sequence)
        + encode_varint(456)
        + encode_varint(encrypted_length)
        + bytes((1, part, 0))
        + payload
    )
    return bytes((0x6A,)) + encode_varint(len(body)) + body


def _ready_frame() -> bytes:
    jpeg = b"\xff\xd8ready-frame-data-123\xff\xd9"
    encrypted_head = aes_cbc_encrypt_unpadded(
        jpeg[:16],
        derive_av_key(SESSION_PREFIX, sequence=3, timestamp=456, channel=0),
        derive_av_key(SESSION_PREFIX, sequence=3, timestamp=456, channel=0)[-16:],
    )
    return _outer_av(3, 1, encrypted_head, 16) + _outer_av(3, 255, jpeg[16:], 0)


@dataclass
class _ControlSocket:
    incoming: Queue[bytes | BaseException] = field(default_factory=Queue)
    sent: list[bytes] = field(default_factory=list)
    closed: bool = False
    response_code: int = 0
    controls: tuple[int, ...] = (2633, 2635, 2612, 2661, 2639, 2613)

    def settimeout(self, timeout: float) -> None:
        del timeout

    def sendall(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        packet = parse_packet(data, udp=False)
        if not isinstance(packet, RPCPacket):
            return

        if packet.command_id == 2650:
            payload = encode_protobuf_bytes(1, SESSION_PREFIX)
        elif packet.command_id == 106:
            self.incoming.put(
                pack_rpc(
                    sequence=100,
                    command_id=107,
                    plaintext=encode_protobuf_varint(1, 1),
                    prefix=PREFIX,
                )
            )
            self.incoming.put(
                pack_rpc(
                    sequence=101,
                    command_id=107,
                    plaintext=encode_protobuf_varint(1, 2),
                    prefix=PREFIX,
                )
            )
            payload = b""
        elif packet.command_id == 2610:
            payload = b""
        elif packet.command_id == 2614:
            payload = encode_protobuf_varint(5, 21) + encode_protobuf_varint(6, 8000)
        elif packet.command_id == 2636:
            payload = encode_protobuf_varint(1, 3)
        elif packet.command_id == 2649:
            payload = encode_protobuf_varint(1, 4)
        elif packet.command_id in self.controls:
            payload = b""
        elif packet.command_id == 2647:
            return
        else:
            return

        self.incoming.put(
            pack_rpc(
                sequence=packet.sequence,
                command_id=packet.command_id,
                plaintext=payload,
                prefix=PREFIX,
                rpc_type=1,
                response_code=self.response_code
                if packet.command_id in self.controls
                else 0,
            )
        )

    def recv(self, size: int) -> bytes:
        del size
        if self.closed:
            return b""
        try:
            event = self.incoming.get(timeout=0.05)
        except Empty as exc:
            raise TimeoutError from exc
        if isinstance(event, BaseException):
            raise event
        return event

    def close(self) -> None:
        self.closed = True


def test_camera_client_exposes_a_reusable_session() -> None:
    from wificam_bridge.camera import CameraClient, CameraCredentials

    client = CameraClient(
        "synthetic-camera",
        CameraCredentials(b"prefix", "user", "password"),
    )

    assert callable(getattr(client, "open_session", None))


def test_reusable_session_exposes_lifecycle_and_control_methods() -> None:
    from wificam_bridge.camera import CameraClient, CameraCredentials

    session = CameraClient(
        "synthetic-camera",
        CameraCredentials(b"prefix", "user", "password"),
    ).open_session()

    for name in (
        "start",
        "wait_ready",
        "wait",
        "close",
        "set_status_indicator",
        "set_night_vision",
        "get_night_vision",
        "set_screen_flip",
        "get_screen_flip",
        "set_video_quality",
        "set_motion_detection",
        "set_intrusion_detection",
        "reboot",
    ):
        assert callable(getattr(session, name, None)), name


def test_session_controls_share_the_active_camera_socket(monkeypatch) -> None:
    from wificam_bridge import IntrusionSchedule

    fake_socket = _ControlSocket()
    created: list[tuple[str, int]] = []

    def socket_factory(address: tuple[str, int], timeout: float) -> _ControlSocket:
        del timeout
        created.append(address)
        return fake_socket

    monkeypatch.setattr(
        "wificam_bridge.camera.socket.create_connection",
        socket_factory,
    )
    client = CameraClient(
        "synthetic-camera",
        CameraCredentials(PREFIX, "user", "password"),
        connect_timeout=0.5,
        frame_timeout=1,
    )
    session = client.open_session()

    session.start()
    fake_socket.incoming.put(_ready_frame())
    try:
        session.wait_ready(timeout=1)
        session.set_status_indicator(True)
        session.set_night_vision("enabled")
        assert session.get_night_vision().value == "automatic"
        session.set_screen_flip("vertical")
        assert session.get_screen_flip().value == "rotate_180"
        session.set_video_quality("uhd")
        session.set_motion_detection("high")
        session.set_intrusion_detection(
            False,
            schedule=IntrusionSchedule(
                weekdays=(1, 3, 5),
                start_time=time(0, 0),
                end_time=time(12, 0),
            ),
        )
        session.reboot()
    finally:
        session.close()

    packets = [parse_packet(raw, udp=False) for raw in fake_socket.sent]
    command_ids = [
        packet.command_id for packet in packets if isinstance(packet, RPCPacket)
    ]
    assert command_ids.count(2650) == 1
    assert command_ids.count(2633) == 1
    assert command_ids.count(2635) == 1
    assert command_ids.count(2636) == 1
    assert command_ids.count(2613) == 1
    assert command_ids.count(2649) == 1
    assert command_ids.count(2612) == 1
    assert command_ids.count(2661) == 1
    assert command_ids.count(2639) == 1
    assert command_ids.count(2647) == 1
    assert created == [("synthetic-camera", 20190)]


def test_session_raises_when_camera_rejects_a_control() -> None:
    fake_socket = _ControlSocket(response_code=7)
    client = CameraClient(
        "synthetic-camera",
        CameraCredentials(PREFIX, "user", "password"),
        connect_timeout=0.5,
        frame_timeout=1,
        socket_factory=lambda address, timeout: fake_socket,
    )
    session = client.open_session()

    session.start()
    try:
        from wificam_bridge.camera import CameraError

        with pytest.raises(CameraError, match="command 2633 returned code 7"):
            session.set_status_indicator(True)
    finally:
        session.close()
