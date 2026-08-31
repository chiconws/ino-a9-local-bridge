# Camera control API

The package exposes a reusable `CameraSession` for changing the camera
settings that were observed in the Linklemo application. A session owns one
PPRPC connection and one background reader, so media packets and control
responses are multiplexed over the same camera connection.

These command mappings were recovered from controlled LAN observations of an
INO-A9 camera. They are useful compatibility data, not an official vendor
schema. Test a new hardware or firmware variant before relying on a setting.

## Recommended lifecycle

Keep the private credentials outside the repository and create one session per
camera process:

```python
import os
from datetime import time

from wificam_bridge import (
    CameraClient,
    CameraCredentials,
    IntrusionSchedule,
    NightVisionMode,
)

credentials = CameraCredentials(
    bootstrap_prefix=os.environ["LINKLEMO_BOOTSTRAP_PREFIX"].encode("ascii"),
    user=os.environ["LINKLEMO_LAN_USER"],
    lan_password=os.environ["LINKLEMO_LAN_PASSWORD"],
)

client = CameraClient(os.environ["CAMERA_HOST"], credentials)
with client.open_session(start_audio=False) as session:
    session.start(on_frame=publish_jpeg)
    session.wait_ready(timeout=10)

    session.set_night_vision(NightVisionMode.AUTOMATIC)
    session.set_status_indicator(False)
    session.set_intrusion_detection(
        True,
        schedule=IntrusionSchedule(
            weekdays=(1, 3, 5),  # Monday, Wednesday, Friday
            start_time=time(8, 0),
            end_time=time(18, 0),
        ),
    )

    # Keep the process alive while the media reader runs.
    session.wait()
```

`publish_jpeg` is application code supplied by the caller. Use
`start_audio=True` (the default) and an `on_audio` callback when microphone
data is needed. `wait_ready()` returns after the first complete JPEG, which is
useful before exposing a stream or applying UI state.

Control methods must be called by another application thread or event loop,
not directly from `on_frame` or `on_audio`; the session's reader must remain
free to receive the corresponding RPC response.

Do not use `CameraClient` as a context manager and then call
`open_session()` on it: `CameraSession.start()` owns the connection lifecycle.
For a media-only application, the existing `client.connect()` plus
`client.frames()` API remains available.

## Observed controls

| Command | Session method | Observed wire values | Readback |
| ---: | --- | --- | --- |
| 2633 | `set_status_indicator(bool)` | on `1`, off `2` | not observed |
| 2635 | `set_night_vision(mode)` | enabled `1`, disabled `2`, automatic `3` | `get_night_vision()` |
| 2636 | `get_night_vision()` | response field 1 | yes |
| 2613 | `set_screen_flip(mode)` | upright `1`, horizontal `2`, vertical `3`, rotate 180 `4` | `get_screen_flip()` |
| 2649 | `get_screen_flip()` | response field 1 | yes |
| 2612 | `set_video_quality(quality)` | SD `5`, HD `10`, UHD `15` | not observed |
| 2661 | `set_motion_detection(sensitivity)` | low `1`, medium `2`, high `3`, closed empty | not observed |
| 2639 | `set_intrusion_detection(enabled, schedule=...)` | enabled field plus full schedule | not observed |
| 2647 | `reboot()` | empty request | fire-and-forget |

The corresponding public value types are `NightVisionMode`,
`ScreenFlipMode`, `VideoQuality`, and `MotionDetectionSensitivity`. The
payload builders are public as well for applications that need to inspect or
extend the low-level protocol.

The status-indicator command is an enum-like setting: the observed app used
wire value `1` for on and `2` for off. Sending `0` was accepted but did not
change the LED on the tested camera.

## Intrusion detection schedules

`IntrusionSchedule.weekdays` uses the app's order: `0` is Sunday and `6` is
Saturday. It accepts one or more sorted, unique weekday numbers. `start_time`
and `end_time` use `datetime.time` with minute precision and must describe a
non-crossing interval.

The intrusion command writes the complete schedule each time. It contains a
weekday byte list, a nested time range, and the app's schedule marker. A
midnight start is omitted because it is the protobuf default; non-zero start
times are encoded using the schema inferred from the captured traffic. The
camera family does not publish an observed readback command for this setting,
so callers should retain the desired configuration themselves.

## Compatibility and limitations

The mappings above were validated against the tested INO-A9 firmware, not
against every product sold under the same housing or app. A camera may reject
a command, ignore a value, or use a different schema; the session raises
`CameraError` for a non-zero RPC response or unsupported getter value.

The video-quality names are generic SDK presets. On the tested unit, SD, HD,
and UHD all resulted in the same hardware-limited 640x480 MJPEG stream. The
quality command is still exposed because another firmware may implement the
presets differently.

`reboot()` has no normal RPC response. The camera closes or resets the
connection, so treat the session as unusable afterward and create a new one
after the camera becomes reachable again.

## Bridge behavior

The command-line HTTP bridge remains intentionally read-only: its routes serve
health, JPEG/MJPEG, and raw audio data, but do not expose unauthenticated
settings or reboot endpoints. Internally, each bridge worker now uses one
`CameraSession`, so adding controls to an embedding application does not
require opening a second media connection. Applications that need to change
settings should create and own a `CameraSession` directly, or integrate with
the worker lifecycle rather than starting a competing `CameraClient`.

## Credentials and safety

`bootstrap_prefix`, `user`, and `lan_password` are private local protocol
credentials. The names describe this project's API; they are not a guarantee
that the vendor uses the same terminology. Keep them in an ignored config or a
secret manager, never log them, and only operate on cameras and networks you
own or are authorized to test.
