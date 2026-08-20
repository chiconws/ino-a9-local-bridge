# Local MJPEG bridge

The bridge connects directly to each camera over PPRPC, performs LAN
authentication, answers the initial time-sync exchange, starts MJPEG video,
reassembles the network fragments, and decrypts only the encrypted head of each
frame. Linklemo and the setup laptop are not needed while the bridge is running.

## Private configuration

Copy `config.example.json` outside the repository and fill in the values
recovered during an authorized pairing capture. Never commit the completed
file. Restrict it to the service account:

```bash
install -m 600 config.example.json /private/path/camera.json
```

The three private fields are:

- `bootstrap_prefix`: control-plane prefix from the matching SDK build;
- `user`: LAN-authorized user identifier stored by the camera;
- `lan_password`: stable local-salt credential sent in `LanAuth.Req`.

Add another object to `cameras` for each additional camera. Each camera needs a
unique `name` and address. Do not put Wi-Fi credentials in this file; the bridge
does not need them.

## Run directly

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install .
wificam-bridge --config /private/path/camera.json
```

The sample binds only to loopback. Set `listen_host` to `0.0.0.0` when a
trusted LAN client or Home Assistant must reach it.

## Run with Docker Compose

Place the application in an `app` directory and the private file at
`../config/camera.json`, then run:

```bash
docker compose -f compose.example.yaml up -d --build
```

The example container runs without Linux capabilities, has a read-only root
filesystem, and mounts the private configuration read-only.

## Run as a TrueNAS Custom App

Build the image on the TrueNAS host, then create a **Custom App** named
`ino-a9-local-bridge` with the equivalent Compose configuration. The tested
managed app uses:

```yaml
services:
  bridge:
    image: local/ino-a9-local-bridge:0.2.1
    pull_policy: never
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - /mnt/pool/wificam/config/camera.json:/run/wificam/camera.json:ro
    read_only: true
    tmpfs:
      - /tmp:size=16m,mode=1777
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
```

Replace `pool` with the local dataset path. This makes the service visible and
manageable under **Apps → Installed Applications** while preserving the same
read-only and capability-restricted container configuration.

## HTTP endpoints

For a camera named `camera1`:

- `/health` — connection/frame status as JSON;
- `/camera1/snapshot.jpg` — latest complete JPEG;
- `/camera1/stream.mjpeg` — full-rate multipart MJPEG stream;
- `/camera1/preview.mjpeg` — the same shared stream sampled at one frame per
  second for low-bandwidth dashboards.

Example:

```text
http://bridge-host:8080/camera1/snapshot.jpg
http://bridge-host:8080/camera1/stream.mjpeg
http://bridge-host:8080/camera1/preview.mjpeg
```

The bridge has no built-in HTTP authentication. Keep it on a trusted local
network, bind it to loopback, or place it behind an authenticated reverse proxy.

## Home Assistant

In Home Assistant, open **Settings → Devices & services → Add Integration** and
select **MJPEG IP Camera**. Create the full-rate entity with:

- MJPEG URL: `http://bridge-host:8080/camera1/stream.mjpeg`
- Still Image URL: `http://bridge-host:8080/camera1/snapshot.jpg`

Create a second MJPEG IP Camera named `INO A9 Camera 1 Preview` with:

- MJPEG URL: `http://bridge-host:8080/camera1/preview.mjpeg`
- Still Image URL: `http://bridge-host:8080/camera1/snapshot.jpg`

Leave username and password empty unless an authenticating reverse proxy was
added. Put the preview entity on a **Picture Entity** card, set **Camera view**
to `live`, and have its tap action open the full-rate entity:

```yaml
type: picture-entity
entity: camera.ino_a9_camera_1_preview
name: Camera 1
camera_view: live
show_name: true
show_state: false
tap_action:
  action: more-info
  entity: camera.ino_a9_camera_1
```

Repeat the pair of entities for each camera. The preview is a continuous MJPEG
response deliberately capped at 1 FPS, so its card normally advances once per
second. This avoids Home Assistant's slower periodic still-image refresh while
using about one tenth of the bridge-to-dashboard bandwidth of the tested 10 FPS
full stream. Tapping a card opens the normal full-rate camera entity.

Use the dedicated MJPEG integration rather than putting the multipart URL in
Generic Camera's Stream Source field. Current Home Assistant releases can route
Generic Camera streams through the stream/go2rtc stack and construct an
internal RTSP source. With this HTTP-only bridge that can fail as a WebRTC
`DESCRIBE 404 Not Found`. The MJPEG integration proxies the multipart stream
natively.

The tested deployment kept the entity ID `camera.ino_a9_camera_1` and placed
three full-rate and three preview entities on a dashboard titled **Cameras**.
Both Home Assistant's snapshot proxy and native MJPEG stream proxy returned
complete JPEG frames.

## Operational notes

- One camera connection feeds any number of local HTTP viewers; opening more
  dashboard views does not create more PPRPC sessions.
- The worker reconnects after camera power or Wi-Fi interruptions. It also
  declares a session stale after 15 seconds without a complete frame, then
  retries after three seconds.
- The tested unit normally emits roughly 10 frames per second at 640×480, but
  its firmware occasionally pauses long enough to trigger that watchdog.
- A video-only 30-second observation measured about 0.64 Mbit/s of MJPEG
  payload. Allow roughly 0.8–1.0 Mbit/s per active camera for video plus local
  protocol/TCP/Wi-Fi overhead; MJPEG varies with scene complexity.
- A preview viewer receives about one tenth of the tested camera's video
  payload because the bridge sends 1 of roughly 10 frames each second. This
  reduces bridge-to-viewer traffic; it does not change the camera-to-bridge
  Wi-Fi traffic because the shared camera connection remains full-rate.
- The microphone separately produces about 64.6 kbit/s of G.711 A-law payload,
  adding roughly 0.08 Mbit/s after local transport overhead. Audio is confirmed
  by the diagnostic but is not exposed by the current video-only HTTP bridge.
- Keep the camera's Internet block in place. The bridge needs only TCP access
  to camera port `20190`; Home Assistant needs only TCP access to the bridge's
  HTTP port.
