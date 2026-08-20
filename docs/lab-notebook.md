# Redacted lab notebook

This document records the investigation of one user-owned `INO-A9-V2.8`
camera. Secrets and transient authentication material are deliberately omitted.
Dates use Europe/Stockholm local time.

## 2026-08-19 — identification and containment

1. Compared the PCB marking and layout with the ongoing Elektroda discussion
   about Pinmei/Linklemo A9 variants.
2. Identified the factory Wi-Fi name as an `LLM_...` network rather than the
   `Nax_...` network used by the older V720/Naxclow implementation.
3. Prepared a disposable Arch Linux laptop as the setup workstation. A first
   sudoers command contained a stray space in `chmod 440 /etc/sudoers.d/ codex`.
   This produced an error for a nonexistent `codex` path but did not damage the
   sudoers file; `/etc/sudoers.d/codex` had already been created and `visudo`
   reported the configuration as valid. Permissions and account state were
   subsequently verified.
4. Joined the camera's isolated setup network and captured only the traffic
   necessary to understand provisioning.
5. Ran Linklemo in a temporary Waydroid environment because installing it on a
   personal phone was explicitly out of scope. The environment was restricted
   from Internet access.
6. Provisioned the camera onto the IoT Wi-Fi. Credentials are not retained in
   this repository.

## Stable network state

The first camera is reserved at `192.168.1.10` by MAC address. Addresses
`.11` through `.15` were checked and kept available for subsequent cameras.

OPNsense contains:

- a `WIFI_CAMERAS` alias covering `192.168.1.10` through `.15`;
- a logged block rule placed before the general LAN allow rule;
- a DHCP reservation for the first camera.

The block rule was verified empirically. The camera remained pingable and TCP
port `20190` remained reachable from the LAN while OPNsense logged denied cloud
attempts. Observed destinations/ports are recorded for evidence, not as an
allowlist: `47.240.1.244` on TCP/UDP `8000`, TCP `465`, and UDP `80`.

## App and protocol analysis

The split APK was copied from the temporary Android environment and decompiled
locally. The following behavior was found:

- package name `com.xcthings.fchan`;
- native communications in `libgojni.so`;
- a PPRPC SDK layer under `user.*` and `com.xc.august.ipc.*`;
- LAN connection via `FTConn.lanDailByDid()`;
- live start via `FTConn.videoPlay(channel, quality, speed)`;
- ordinary single-camera call sites use `(0, 0, 0)`;
- decoded frames arrive as `CallAVPacket` objects containing format, key-frame
  flag, channel, sequence, timestamp, and payload;
- recognized video formats include H.264, H.265, MJPEG, and format value 10;
- the SDK can be initialized with its cloud push transport disabled.

The native library's Go build metadata identifies newer internal PPRPC modules,
while an older public version remains archived by the Go module proxy. That
archive provides the packet framing documented in `protocol.md`.

## 2026-08-20 — independent bridge and Home Assistant

1. Implemented a clean-room Python client for the documented PPRPC framing,
   LAN authentication, time synchronization, fragmented AV reassembly, and the
   camera's partial-frame AES-CBC encryption.
2. Validated the independent client against the physical camera. The
   independently decrypted network frame matched the decoded frame delivered
   by the vendor SDK.
3. Wrapped the client in a multi-camera HTTP service with a shared connection
   per camera, snapshot endpoint, multipart MJPEG endpoint, and JSON health
   endpoint.
4. Added a 15-second complete-frame watchdog after the test unit occasionally
   stopped producing complete images while leaving its TCP session open. The
   worker closes the stale session and retries three seconds later.
5. Added a capability-restricted, read-only Docker deployment and installed it
   on an always-on local host. A live soak test confirmed automatic recovery
   from a camera pause.
6. Ran 21 tests using synthetic protocol packets and JPEG frames only. No
   captured traffic, proprietary application files, camera credentials, or
   imagery are test fixtures.
7. Integrated the bridge with Home Assistant. Generic Camera was initially
   tested, but current stream/go2rtc handling converted the HTTP MJPEG source
   into an internal RTSP URL and returned `DESCRIBE 404 Not Found` to WebRTC.
   Replacing only that config entry with **MJPEG IP Camera** preserved
   `camera.ino_a9_camera_1` and made both the snapshot and native multipart
   stream proxies pass.
8. Created a Home Assistant dashboard titled **Cameras** with a live Picture
   Entity card for that camera. Neither the setup laptop nor Linklemo is
   required for normal viewing.

## Temporary components and cleanup

The quarantine access point and its bridge were removed after provisioning.
Temporary `hostapd` and `sshpass` packages installed on the setup laptop were
removed. The original provisioning capture and hostapd configuration were
deleted. After bridge and Home Assistant validation, the Waydroid container,
Linklemo APK/data, decompiler output, Frida server/client, captures, decrypted
test buffers, cookies, and camera-specific laptop firewall rules were removed.
Packages installed only for this work and their same-time unused dependencies
were uninstalled. Broadcast-forwarding and user-linger settings were restored
to their recorded pre-test values. The SSH maintenance account was retained as
requested.

The Home Assistant migration used a short-lived in-memory access token derived
locally on the Home Assistant host. The helper and bytecode were deleted
immediately after verification; no token or authentication database was copied
off the host. Temporary deployment sources on the bridge host and the final
verification image were also removed.

## Publication hygiene

The public repository excludes:

- Wi-Fi SSIDs and passphrases;
- SSH, firewall, and application credentials;
- CSRF tokens, cookies, and API client secrets;
- APK files and decompiled proprietary source;
- packet captures, firmware dumps, and camera imagery;
- the full camera identifier and full MAC address.

The hardware model, local addressing plan, protocol port, and observed cloud
destinations are retained because they are needed to reproduce the containment
and protocol work.
