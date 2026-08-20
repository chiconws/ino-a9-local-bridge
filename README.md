# INO A9 local video bridge research

Local-only research and bridge tooling for the inexpensive Wi-Fi camera marked
`INO-A9-V2.8` and sold for use with the Linklemo Android app.

The immediate goal is to expose the camera to Home Assistant without allowing
the camera to reach the Internet. This model speaks XC Things' proprietary
PPRPC protocol on TCP port `20190`; it is **not** the older Naxclow/V720 model
that speaks its unrelated protocol on port `6123`.

> [!NOTE]
> The direct LAN client and HTTP MJPEG bridge are live-tested on the physical
> camera. Pairing additional units and extracting their private LAN credentials
> still requires an authorized one-time setup/capture process.

## AI-assisted development

This project was created largely with OpenAI Codex using **GPT-5.6-Sol**.
Codex performed most of the research synthesis, protocol implementation,
testing, deployment automation, and documentation. **IvanFogel** supplied and
owned the hardware, authorized the testing, performed the required physical
actions, selected the operational and security constraints, and made the final
publication decisions.

## Confirmed on the test unit

- PCB marking: `INO-A9-V2.8`
- Factory access-point prefix: `LLM_`
- Linklemo package: `com.xcthings.fchan`
- LAN service: PPRPC over TCP `20190`
- Camera remains locally reachable while an OPNsense rule blocks all routed
  traffic from it to any destination
- Observed blocked cloud attempts included TCP/UDP port `8000`, TCP `465`, and
  UDP `80`
- Linklemo's native SDK has an explicit LAN-only connection path and requests
  live video with `videoPlay(0, 0, 0)`
- The independent client authenticates, answers time sync, starts video, and
  receives standard 640×480 MJPEG
- The bridge requests the SDK's highest QoS on every connection. The tested
  camera clamps all higher modes to its actual maximum: QoS 5, 640×480 MJPEG
  at 10 FPS
- The microphone is live-validated: command `2614` starts an unencrypted local
  G.711 A-law stream at 8 kHz, reported as 16-bit mono by the camera
- The tested firmware occasionally pauses its output; the client treats 15
  seconds without a complete frame as a dead stream and reconnects
- The HTTP bridge exposes snapshots and one shared multipart MJPEG stream; the
  setup laptop and Linklemo are not needed at runtime
- Home Assistant's native MJPEG integration and a Cameras dashboard were
  validated end to end

## Repository map

- [`docs/lab-notebook.md`](docs/lab-notebook.md) — redacted record of the work
- [`docs/network-isolation.md`](docs/network-isolation.md) — DHCP and firewall
  design for a camera subnet
- [`docs/bridge.md`](docs/bridge.md) — deployment and Home Assistant setup
- [`docs/protocol.md`](docs/protocol.md) — PPRPC framing and SDK findings
- `src/wificam_bridge/` — clean-room parser, camera client, and HTTP bridge
- `scripts/probe_media.py` — header-only live media-format diagnostic
- `tests/` — tests using synthetic packets and frames only

No APKs, firmware dumps, packet captures, Wi-Fi keys, account passwords, app
secrets, cookies, or camera video are stored here.

## Development

Requires Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
pytest
```

Inspect a PPRPC packet supplied as hexadecimal:

```bash
wificam-pprpc decode 680881020003000100
```

Run the bridge with a private configuration copied from `config.example.json`:

```bash
wificam-bridge --config /private/path/camera.json
```

For a configured camera named `camera1`, the default endpoints are:

```text
http://bridge-host:8080/camera1/snapshot.jpg
http://bridge-host:8080/camera1/stream.mjpeg
```

## Similar-looking cameras

The [`intx82/a9-v720`](https://github.com/intx82/a9-v720) project is useful for
Naxclow V720 units. Its documented camera uses `192.168.169.1:6123` and streams
MJPEG. The present `LLM_`/Linklemo camera instead exposes PPRPC at port `20190`,
so the V720 script is not wire-compatible despite similar housings and PCBs.

## Sources and attribution

Protocol behavior was compared against the archived Apache-2.0 PPRPC Go module
(`github.com/pprpc/core`, revision `5592f694d0e7`) and FuseTim's MIT-licensed
[`insecurity-camera-tools`](https://github.com/fusetim/insecurity-camera-tools).
See [`NOTICE`](NOTICE) and [`docs/protocol.md`](docs/protocol.md).

## License

This project is released under the [Zero-Clause BSD license](LICENSE): use,
copy, modify, or distribute it for any purpose, with or without fee. Attribution
is appreciated but not required.

Only analyze devices and networks you own or are authorized to test.
