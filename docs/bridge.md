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

## HTTP endpoints

For a camera named `camera1`:

- `/health` — connection/frame status as JSON;
- `/camera1/snapshot.jpg` — latest complete JPEG;
- `/camera1/stream.mjpeg` — multipart MJPEG stream.

Example:

```text
http://bridge-host:8080/camera1/snapshot.jpg
http://bridge-host:8080/camera1/stream.mjpeg
```

The bridge has no built-in HTTP authentication. Keep it on a trusted local
network, bind it to loopback, or place it behind an authenticated reverse proxy.

## Home Assistant

In Home Assistant, open **Settings → Devices & services → Add Integration** and
select **MJPEG IP Camera**. Set:

- MJPEG URL: `http://bridge-host:8080/camera1/stream.mjpeg`
- Still Image URL: `http://bridge-host:8080/camera1/snapshot.jpg`

Leave username and password empty unless an authenticating reverse proxy was
added. Add the resulting camera entity to a **Picture Entity** dashboard card
and set **Camera view** to `live`.

Use the dedicated MJPEG integration rather than putting the multipart URL in
Generic Camera's Stream Source field. Current Home Assistant releases can route
Generic Camera streams through the stream/go2rtc stack and construct an
internal RTSP source. With this HTTP-only bridge that can fail as a WebRTC
`DESCRIBE 404 Not Found`. The MJPEG integration proxies the multipart stream
natively.

The tested deployment kept the entity ID `camera.ino_a9_camera_1` and placed
it on a dashboard titled **Cameras**. Both Home Assistant's snapshot proxy and
native MJPEG stream proxy returned complete JPEG frames.

## Operational notes

- One camera connection feeds any number of local HTTP viewers; opening more
  dashboard views does not create more PPRPC sessions.
- The worker reconnects after camera power or Wi-Fi interruptions. It also
  declares a session stale after 15 seconds without a complete frame, then
  retries after three seconds.
- The tested unit normally emits roughly 10 frames per second at 640×480, but
  its firmware occasionally pauses long enough to trigger that watchdog.
- Keep the camera's Internet block in place. The bridge needs only TCP access
  to camera port `20190`; Home Assistant needs only TCP access to the bridge's
  HTTP port.
