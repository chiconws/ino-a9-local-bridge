# Provisioning additional cameras

This is the repeatable, local-only procedure validated on three cameras marked
`INO-A9-V2.8`. It avoids Linklemo account login and blocks the app and cameras
from Internet access. Keep all device identifiers, local salts, Wi-Fi keys, and
completed bridge configuration outside the repository.

The procedure is intentionally split into preparation, one-time provisioning,
and verification. Do not send Wi-Fi credentials until the DHCP reservation and
firewall policy are ready.

## What you need

- A disposable Linux setup computer with Ethernet for administration and Wi-Fi
  for the camera setup access point.
- An isolated Android/Waydroid instance containing an authorized copy of
  Linklemo (`com.xcthings.fchan`).
- Frida attached locally to that isolated app process.
- A default-deny IoT VLAN, or an equivalent firewall rule that blocks every
  address a newly joined camera might receive.
- A private copy of the matching SDK bootstrap prefix for the bridge.

The tested camera setup network uses `192.168.9.0/24`; the camera listens at
`192.168.9.252:20190`. Its factory access-point name begins with `LLM_`. Some
units use the common factory access-point password `12345678`; verify the label
or supplied manual for the unit being tested.

## 1. Prepare containment and addressing

1. Verify the intended reserved address is unused by checking DHCP
   reservations, active leases, and ARP/NDP—not only ping.
2. Put the entire IoT VLAN behind a default-deny routed policy before
   provisioning. If an address-based alias is the only available control,
   cover both the reserved camera range and the temporary DHCP pool used while
   provisioning.
3. Record the camera setup AP's BSSID.
4. Prepare the DHCP reservation for the camera's station-interface MAC.

### BSSID versus station MAC

The setup AP BSSID is not necessarily the MAC used after the camera joins the
home network. On two tested units, the station MAC was the BSSID with the low
bit of the final octet toggled:

```text
station_last_octet = ap_last_octet XOR 0x01
```

This produced both an increment and a decrement depending on whether the BSSID
ended in an even or odd value. Treat this as a tested hint, not a universal
guarantee. Verify the actual client MAC in the DHCP/ARP table immediately after
join. If it differs, block its temporary address first, then correct the
reservation and renew the lease.

## 2. Isolate the setup environment

1. Keep the setup computer's Ethernet management connection active.
2. Connect only its Wi-Fi interface to the camera's `LLM_...` network.
3. Permit the disposable Android container to reach only
   `192.168.9.0/24`; reject all other forwarded traffic from that container.
4. Verify access to `192.168.9.252:20190` and verify an Internet probe from the
   container fails.
5. Start Linklemo without signing in. The validated path needs the app's native
   local SDK but no cloud account.

Do not briefly allow Internet access to “make the app work.” That can expose
credentials, register the unit, or trigger a firmware update.

## 3. Discover and provision by explicit IP

Linklemo's ordinary setup activity performs UDP broadcast discovery. Broadcast
does not reliably cross a NATed Waydroid bridge. The reliable workaround is to
call the same local SDK with the camera's explicit setup address.

Use [`scripts/provision_linklemo_ap.js.example`](../scripts/provision_linklemo_ap.js.example)
as a template outside the repository:

1. Replace its placeholder target SSID and passphrase in the private copy.
2. Attach it to the running Linklemo process with Frida.
3. Confirm discovery returns exactly one device.
4. Save the returned `did` and the `lsign` from that same discovery in the
   private bridge configuration:
   - `user` = `did`
   - `lan_password` = `lsign`
5. Allow the script to call `addWifiApUri(...)` and `apSet(...)`.

The app sequence is:

```text
IpcAp.initWifiAp()
IpcAp.discovery(..., "192.168.9.252", 20190)
IpcAp.addWifiApUri(did, ipaddr, port)
IpcAp.apSet(channel=0, target SSID/password, local time-zone data)
```

On the tested non-IoT variant, `apSet` performs time setup, Wi-Fi setup, and
closes the setup connection. The `lsign` can change between discoveries, so
keep the value returned by the discovery used for provisioning. Never commit
the DID, LSign, Wi-Fi credentials, or bootstrap prefix.

## 4. Verify the join before moving on

1. Confirm the camera setup AP disappears.
2. Find the actual station MAC and lease in DHCP/ARP.
3. If it received a temporary address, add that address to the camera block
   immediately, correct the reservation, and renew or wait for the lease.
4. Confirm the camera appears at its reserved address.
5. Confirm local TCP port `20190` is open.
6. Confirm the firewall logs routed attempts from the camera as blocked.
7. Use the private `did`, `lsign`, and bootstrap prefix to authenticate once
   with the bridge and validate a JPEG frame without saving it.

Only after all seven checks pass should the next camera be provisioned.

## 5. Add the unit to the bridge and Home Assistant

Add another object to the private `cameras` array, restart the bridge, and
verify `/health` reports both `connected` and `has_frame` for the new camera.
Then follow [bridge.md](bridge.md) to create the full-rate and one-frame-per-
second Home Assistant entities.

## 6. Cleanup

When no more cameras need provisioning:

1. Remove the Linklemo app and disposable Android/Waydroid environment.
2. Stop and remove the Frida server/client and ADB forwarding.
3. Remove the temporary container forwarding/NAT rules.
4. Delete temporary firewall cookies, CSRF material, scripts containing Wi-Fi
   keys, and private discovery output.
5. Keep only the restricted private bridge configuration and any explicitly
   retained research artifacts.
6. Recheck that the permanent camera firewall block and DHCP reservations are
   still active.
