#!/usr/bin/env python3
"""Count PPRPC media headers without decrypting or saving media payloads."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import time

from wificam_bridge.bridge import CameraSettings, load_config
from wificam_bridge.camera import CameraClient, decrypt_rpc_payload
from wificam_bridge.pprpc import AVPacket, RPCPacket, decode_varint


AUDIO_START_COMMAND = 2614


FORMAT_NAMES = {
    1: "H.264",
    2: "H.265",
    3: "MPEG",
    4: "MJPEG",
    21: "G.711 A-law",
    31: "G.711 u-law",
    41: "G.711 u-law (variant)",
    51: "Opus",
    101: "AAC",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Count live PPRPC media formats without recording content"
    )
    parser.add_argument("--config", required=True, help="private bridge JSON configuration")
    parser.add_argument("--camera", help="camera name; defaults to the first configured camera")
    parser.add_argument("--duration", type=float, default=20.0, help="probe duration in seconds")
    parser.add_argument(
        "--start-audio",
        action="store_true",
        help="send the SDK AudioPlay command for channel 0 before counting headers",
    )
    return parser


def select_camera(config_path: str, name: str | None) -> CameraSettings:
    cameras = load_config(config_path).cameras
    if name is None:
        return cameras[0]
    try:
        return next(camera for camera in cameras if camera.name == name)
    except StopIteration as error:
        raise SystemExit(f"camera {name!r} is not present in the configuration") from error


def protobuf_varints(payload: bytes) -> dict[int, int]:
    """Decode a response made solely from protobuf varint fields."""
    fields: dict[int, int] = {}
    offset = 0
    while offset < len(payload):
        tag, offset = decode_varint(payload, offset)
        field_number = tag >> 3
        wire_type = tag & 0x07
        if field_number == 0 or wire_type != 0:
            raise ValueError(
                f"unexpected protobuf field {field_number} with wire type {wire_type}"
            )
        fields[field_number], offset = decode_varint(payload, offset)
    return fields


def main() -> int:
    args = build_parser().parse_args()
    if args.duration <= 0:
        raise SystemExit("--duration must be positive")
    camera = select_camera(args.config, args.camera)
    packets: Counter[tuple[int, int, int]] = Counter()
    payload_bytes: Counter[tuple[int, int, int]] = Counter()
    sequences: dict[tuple[int, int, int], set[int]] = defaultdict(set)
    rpc_commands: Counter[tuple[int, int]] = Counter()
    audio_start = None
    started = time.monotonic()

    with CameraClient(camera.host, camera.credentials, port=camera.port) as client:
        if args.start_audio:
            # Linklemo waits briefly after starting video, then invokes
            # FTConn.audioPlay(0). Channel zero is omitted in protobuf, so the
            # request body is empty.
            time.sleep(0.3)
            client._send_request(4, AUDIO_START_COMMAND, b"")
            response = client._wait_response(4, AUDIO_START_COMMAND)
            payload = decrypt_rpc_payload(response, camera.credentials.bootstrap_prefix)
            fields = protobuf_varints(payload)
            audio_start = {
                "command": AUDIO_START_COMMAND,
                "response_code": response.response_code,
                "codec": fields.get(5),
                "sample_rate_hz": fields.get(6),
                "sample_bits": fields.get(7),
                "tracks": fields.get(8),
                "fields": {
                    str(field): value for field, value in fields.items()
                },
            }
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            received = []
            while client._pending:  # Diagnostic access; no media data is retained.
                received.append(client._pending.popleft())
            if not received:
                received = client._receive(min(deadline, time.monotonic() + 2.0))
            for packet in received:
                if isinstance(packet, RPCPacket):
                    rpc_commands[(packet.rpc_type, packet.command_id)] += 1
                    client._handle_rpc_request(packet)
                elif isinstance(packet, AVPacket):
                    key = (packet.media_format, packet.channel, packet.encryption_type)
                    packets[key] += 1
                    payload_bytes[key] += len(packet.payload)
                    sequences[key].add(packet.sequence)

    formats = []
    for key in sorted(packets):
        media_format, channel, encryption = key
        formats.append(
            {
                "format": media_format,
                "name": FORMAT_NAMES.get(media_format, "unknown"),
                "channel": channel,
                "encryption": encryption,
                "packets": packets[key],
                "sequences": len(sequences[key]),
                "payload_bytes": payload_bytes[key],
            }
        )
    result = {
        "camera": camera.name,
        "duration_seconds": round(time.monotonic() - started, 3),
        "audio_start": audio_start,
        "formats": formats,
        "rpc_commands": [
            {"rpc_type": rpc_type, "command": command, "packets": count}
            for (rpc_type, command), count in sorted(rpc_commands.items())
        ],
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
