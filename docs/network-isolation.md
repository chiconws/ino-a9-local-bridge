# Network isolation

The safe default is local access in, no routed access out. A static DHCP lease
alone does not prevent cloud access; the firewall rule is the security control.

## Address plan

| Unit | Reserved address |
|---|---:|
| Camera 1 | `192.168.1.10` |
| Camera 2 | `192.168.1.11` |
| Camera 3 | `192.168.1.12` |
| Camera 4 | `192.168.1.13` |
| Camera 5 | `192.168.1.14` |
| Camera 6 | `192.168.1.15` |

Create DHCP reservations keyed by each camera's MAC address. Verify each address
is unused before assigning it; do not rely only on a silent ping response.
Check the DHCP lease table, ARP/NDP table, and existing reservations as well.

## OPNsense rule model

1. Create an alias such as `WIFI_CAMERAS` containing the six addresses.
2. On the interface where camera packets enter the firewall, add a rule:
   `block`, IPv4, source `WIFI_CAMERAS`, destination `any`, logging enabled.
3. Place it before any broad LAN-to-any allow rule.
4. Apply changes, renew the camera lease, and confirm its reserved address.
5. Verify both halves of the policy:
   - LAN clients can reach the camera's local service at TCP `20190`.
   - firewall live view shows the camera's routed attempts being blocked.

If the cameras are moved to a dedicated VLAN, apply the rule on that VLAN's
ingress interface. Permit only explicit local destinations (for example the
bridge host and local DNS/NTP, if actually required) above the final block.

## Validation commands

From an authorized LAN host, substitute the selected camera address:

```bash
ping -c 3 192.168.1.10
timeout 3 bash -c '</dev/tcp/192.168.1.10/20190'
```

An open local port plus logged WAN blocks demonstrates the desired separation.
Do not temporarily allow Internet access merely to see whether the app works;
that changes the state being measured and can trigger firmware updates.
