"""Independent LAN client for the Linklemo/INO-A9 PPRPC camera."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import socket
import string
import time
from typing import Iterator

from .crypto import (
    aes_cbc_decrypt_padded,
    aes_cbc_decrypt_unpadded,
    aes_cbc_encrypt_padded,
)
from .pprpc import (
    AVPacket,
    RPCPacket,
    RawPacket,
    derive_av_key,
    derive_rpc_key,
    encode_varint,
    iter_tcp_packets,
)


LAN_AUTH_COMMAND = 2650
SYNC_COMMAND = 106
TIME_SYNC_COMMAND = 107
VIDEO_START_COMMAND = 2610
VIDEO_MAX_QOS = 30
AES_256_CBC = 3
RPC_REQUEST = 0
RPC_RESPONSE = 1
MJPEG_FORMAT = 4


class CameraError(RuntimeError):
    """Raised when the camera rejects or violates the LAN protocol."""


@dataclass(frozen=True, slots=True)
class CameraCredentials:
    """Private values learned during an authorized one-time pairing capture."""

    bootstrap_prefix: bytes
    user: str
    lan_password: str

    def __post_init__(self) -> None:
        if not self.bootstrap_prefix:
            raise ValueError("bootstrap_prefix must not be empty")
        if not self.user:
            raise ValueError("user must not be empty")
        if not self.lan_password:
            raise ValueError("lan_password must not be empty")


def encode_protobuf_varint(field_number: int, value: int) -> bytes:
    """Encode one protobuf varint field, including signed int64 bit patterns."""
    if field_number <= 0:
        raise ValueError("protobuf field number must be positive")
    return encode_varint(field_number << 3) + encode_varint(value & ((1 << 64) - 1))


def encode_protobuf_bytes(field_number: int, value: bytes) -> bytes:
    """Encode one protobuf length-delimited field."""
    if field_number <= 0:
        raise ValueError("protobuf field number must be positive")
    return encode_varint((field_number << 3) | 2) + encode_varint(len(value)) + value


def build_lan_auth_request(credentials: CameraCredentials) -> bytes:
    """Build ``LanAuth.Req`` without exposing its values in logs or arguments."""
    return (
        encode_protobuf_bytes(2, credentials.user.encode("utf-8"))
        + encode_protobuf_bytes(3, credentials.lan_password.encode("utf-8"))
    )


def build_time_sync_response(request_payload: bytes, *, now_ms: int | None = None) -> bytes:
    """Build the command-107 response expected before video can start."""
    request_timestamp = _first_varint_field(request_payload, field_number=1)
    current = int(time.time() * 1000) if now_ms is None else now_ms
    return (
        encode_protobuf_varint(1, request_timestamp)
        + encode_protobuf_varint(2, -30)
        + encode_protobuf_varint(3, current)
    )


def pack_rpc(
    *,
    sequence: int,
    command_id: int,
    plaintext: bytes,
    prefix: bytes,
    rpc_type: int = RPC_REQUEST,
    response_code: int = 0,
) -> bytes:
    """Pack one encrypted PPRPC protobuf command."""
    if rpc_type not in (RPC_REQUEST, RPC_RESPONSE):
        raise ValueError("rpc_type must be request (0) or response (1)")
    payload = b""
    if plaintext:
        key = derive_rpc_key(prefix, sequence, command_id, rpc_type)
        payload = aes_cbc_encrypt_padded(plaintext, key, key[:16])
    body = (
        encode_varint(sequence)
        + encode_varint(command_id)
        + bytes(((AES_256_CBC << 2) | rpc_type,))
    )
    if rpc_type == RPC_RESPONSE:
        body += encode_varint(response_code)
    body += payload
    return b"\x48" + encode_varint(len(body)) + body


def decrypt_rpc_payload(packet: RPCPacket, prefix: bytes) -> bytes:
    """Decrypt one RPC payload using the control-plane prefix."""
    if not packet.payload:
        return b""
    if packet.encryption_type != AES_256_CBC:
        raise CameraError(f"unsupported RPC encryption type {packet.encryption_type}")
    key = derive_rpc_key(prefix, packet.sequence, packet.command_id, packet.rpc_type)
    return aes_cbc_decrypt_padded(packet.payload, key, key[:16])


class MJPEGReassembler:
    """Reassemble and decrypt the camera's fragmented MJPEG AV packets."""

    def __init__(self, session_prefix: bytes, *, max_frame_bytes: int = 2_000_000) -> None:
        if len(session_prefix) != 32:
            raise ValueError("AV session prefix must contain 32 bytes")
        self._session_prefix = session_prefix
        self._max_frame_bytes = max_frame_bytes
        self._fragments: dict[int, list[bytes]] = defaultdict(list)
        self._sizes: dict[int, int] = defaultdict(int)
        self._metadata: dict[int, AVPacket] = {}
        self._expected_index: dict[int, int] = {}

    def push(self, packet: AVPacket) -> bytes | None:
        if packet.media_format != MJPEG_FORMAT or len(packet.payload) < 3:
            return None
        marker, fragment_index, reserved = packet.payload[:3]
        if marker != 1 or reserved != 0:
            self._drop(packet.sequence)
            return None

        sequence = packet.sequence
        if fragment_index == 1:
            self._drop(sequence)
            self._expected_index[sequence] = 1
        expected = self._expected_index.get(sequence)
        if expected is None:
            return None
        if fragment_index != 0xFF and fragment_index != expected:
            self._drop(sequence)
            return None

        fragment = packet.payload[3:]
        self._fragments[sequence].append(fragment)
        self._sizes[sequence] += len(fragment)
        if self._sizes[sequence] > self._max_frame_bytes:
            self._drop(sequence)
            return None
        if packet.encrypted_length:
            self._metadata.setdefault(sequence, packet)
        if fragment_index != 0xFF:
            self._expected_index[sequence] = expected + 1
            return None

        fragments = self._fragments.pop(sequence)
        self._sizes.pop(sequence, None)
        self._expected_index.pop(sequence, None)
        metadata = self._metadata.pop(sequence, None)
        if metadata is None:
            return None
        assembled = b"".join(fragments)
        encrypted_length = metadata.encrypted_length
        if (
            encrypted_length <= 0
            or encrypted_length > len(assembled)
            or encrypted_length % 16
        ):
            return None
        key = derive_av_key(
            self._session_prefix,
            metadata.sequence,
            metadata.timestamp,
            metadata.channel,
        )
        head = aes_cbc_decrypt_unpadded(assembled[:encrypted_length], key, key[-16:])
        frame = head + assembled[encrypted_length:]
        if not frame.startswith(b"\xff\xd8"):
            return None
        eoi = frame.find(b"\xff\xd9", 2)
        if eoi < 0:
            return None
        return frame[: eoi + 2]

    def _drop(self, sequence: int) -> None:
        self._fragments.pop(sequence, None)
        self._sizes.pop(sequence, None)
        self._metadata.pop(sequence, None)
        self._expected_index.pop(sequence, None)


class CameraClient:
    """Connect, authenticate, start MJPEG, and yield complete JPEG images."""

    def __init__(
        self,
        host: str,
        credentials: CameraCredentials,
        *,
        port: int = 20190,
        connect_timeout: float = 5.0,
        frame_timeout: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.credentials = credentials
        self.connect_timeout = connect_timeout
        if frame_timeout <= 0:
            raise ValueError("frame_timeout must be positive")
        self.frame_timeout = frame_timeout
        self._socket: socket.socket | None = None
        self._buffer = b""
        self._pending: deque[RPCPacket | AVPacket | RawPacket] = deque()
        self._session_prefix: bytes | None = None

    def connect(self) -> None:
        self.close()
        sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        sock.settimeout(1.0)
        self._socket = sock
        self._buffer = b""
        self._pending.clear()
        try:
            self._send_request(1, LAN_AUTH_COMMAND, build_lan_auth_request(self.credentials))
            auth = self._wait_response(1, LAN_AUTH_COMMAND)
            session_prefix = _first_bytes_field(
                decrypt_rpc_payload(auth, self.credentials.bootstrap_prefix),
                field_number=1,
            )
            if len(session_prefix) != 32 or any(
                chr(byte) not in string.hexdigits for byte in session_prefix
            ):
                raise CameraError("LAN authentication returned an invalid session prefix")
            self._session_prefix = session_prefix

            self._send_request(2, SYNC_COMMAND, encode_protobuf_varint(1, 1))
            self._wait_response(2, SYNC_COMMAND)
            self._answer_initial_time_syncs()

            # Ask for the SDK's highest defined QoS. Cameras clamp this to
            # their own maximum; the tested INO-A9 reports QoS 5 (640x480).
            self._send_request(
                3,
                VIDEO_START_COMMAND,
                encode_protobuf_varint(2, VIDEO_MAX_QOS),
            )
            self._wait_response(3, VIDEO_START_COMMAND)
        except Exception:
            self.close()
            raise

    def frames(self) -> Iterator[bytes]:
        if self._socket is None or self._session_prefix is None:
            raise CameraError("camera is not connected")
        reassembler = MJPEGReassembler(self._session_prefix)
        frame_deadline = time.monotonic() + self.frame_timeout
        while self._socket is not None:
            if self._pending:
                packets = [self._pending.popleft()]
            else:
                packets = self._receive(
                    min(time.monotonic() + 2.0, frame_deadline)
                )
            for packet in packets:
                if isinstance(packet, RPCPacket):
                    self._handle_rpc_request(packet)
                elif isinstance(packet, AVPacket):
                    frame = reassembler.push(packet)
                    if frame is not None:
                        frame_deadline = time.monotonic() + self.frame_timeout
                        yield frame
            if time.monotonic() >= frame_deadline:
                raise TimeoutError(
                    f"camera stream produced no complete frame for "
                    f"{self.frame_timeout:g} seconds"
                )

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None
        self._session_prefix = None
        self._buffer = b""
        self._pending.clear()

    def __enter__(self) -> CameraClient:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _send_request(self, sequence: int, command_id: int, plaintext: bytes) -> None:
        self._send(
            pack_rpc(
                sequence=sequence,
                command_id=command_id,
                plaintext=plaintext,
                prefix=self.credentials.bootstrap_prefix,
            )
        )

    def _send(self, data: bytes) -> None:
        if self._socket is None:
            raise CameraError("camera socket is closed")
        self._socket.sendall(data)

    def _wait_response(self, sequence: int, command_id: int) -> RPCPacket:
        deadline = time.monotonic() + self.connect_timeout
        while time.monotonic() < deadline:
            found: RPCPacket | None = None
            for packet in self._receive(deadline):
                if (
                    isinstance(packet, RPCPacket)
                    and packet.rpc_type == RPC_RESPONSE
                    and packet.sequence == sequence
                    and packet.command_id == command_id
                ):
                    found = packet
                elif isinstance(packet, RPCPacket) and self._handle_rpc_request(packet):
                    continue
                else:
                    self._pending.append(packet)
            if found is not None:
                if found.response_code != 0:
                    raise CameraError(
                        f"command {command_id} returned code {found.response_code}"
                    )
                return found
        raise TimeoutError(f"camera did not answer command {command_id}")

    def _answer_initial_time_syncs(self) -> None:
        deadline = time.monotonic() + 2.0
        replies = 0
        while time.monotonic() < deadline and replies < 2:
            for packet in self._receive(deadline):
                if (
                    isinstance(packet, RPCPacket)
                    and packet.rpc_type == RPC_REQUEST
                    and packet.command_id == TIME_SYNC_COMMAND
                ):
                    self._handle_rpc_request(packet)
                    replies += 1
                else:
                    self._pending.append(packet)

    def _handle_rpc_request(self, packet: RPCPacket) -> bool:
        if packet.rpc_type != RPC_REQUEST or packet.command_id != TIME_SYNC_COMMAND:
            return False
        request = decrypt_rpc_payload(packet, self.credentials.bootstrap_prefix)
        response = build_time_sync_response(request)
        self._send(
            pack_rpc(
                sequence=packet.sequence,
                command_id=TIME_SYNC_COMMAND,
                plaintext=response,
                prefix=self.credentials.bootstrap_prefix,
                rpc_type=RPC_RESPONSE,
            )
        )
        return True

    def _receive(self, deadline: float) -> list[RPCPacket | AVPacket | RawPacket]:
        if self._socket is None:
            raise CameraError("camera socket is closed")
        while time.monotonic() < deadline:
            remaining = max(0.05, min(1.0, deadline - time.monotonic()))
            self._socket.settimeout(remaining)
            try:
                data = self._socket.recv(65536)
            except TimeoutError:
                continue
            if not data:
                raise CameraError("camera closed the connection")
            packets, self._buffer = iter_tcp_packets(self._buffer + data)
            if packets:
                return packets
        return []


def _first_varint_field(payload: bytes, *, field_number: int) -> int:
    offset = 0
    while offset < len(payload):
        tag, offset = _decode_varint(payload, offset)
        number, wire_type = tag >> 3, tag & 7
        if wire_type != 0:
            raise CameraError("unexpected protobuf wire type")
        value, offset = _decode_varint(payload, offset)
        if number == field_number:
            return value
    raise CameraError(f"protobuf field {field_number} is missing")


def _first_bytes_field(payload: bytes, *, field_number: int) -> bytes:
    offset = 0
    while offset < len(payload):
        tag, offset = _decode_varint(payload, offset)
        number, wire_type = tag >> 3, tag & 7
        if wire_type != 2:
            raise CameraError("unexpected protobuf wire type")
        length, offset = _decode_varint(payload, offset)
        end = offset + length
        if end > len(payload):
            raise CameraError("truncated protobuf field")
        if number == field_number:
            return payload[offset:end]
        offset = end
    raise CameraError(f"protobuf field {field_number} is missing")


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise CameraError("truncated protobuf varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
    raise CameraError("oversized protobuf varint")
