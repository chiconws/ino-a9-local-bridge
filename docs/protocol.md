# PPRPC protocol notes

These notes combine packet inspection, app/native-library analysis, and the
archived public PPRPC implementation. The bridge path is live-validated.

## Transport and fixed header

The camera listens on TCP port `20190`. PPRPC-over-TCP packets begin with one
byte containing a four-bit message type and four-bit flag, followed by a
protobuf-style unsigned varint containing the remaining packet length.
PPRPC-over-UDP prepends the two bytes `51 70` (ASCII `Qp`).

Known types:

| Type | Meaning |
|---:|---|
| 3 | heartbeat |
| 4 | protobuf RPC |
| 5 | JSON RPC |
| 6 | audio/video |
| 7 | custom payload |
| 8 | file transfer |

The normal low nibble flag is `8`. On the tested firmware, live audio uses flag
`9` and fragmented live video uses flag `10`.

## RPC variable header

After the fixed header:

1. command sequence — varint
2. command ID — varint
3. encryption/RPC byte — encryption type in the upper six bits, RPC type in
   the lower two bits
4. response code — varint, present only for an RPC response
5. payload — remainder of the declared packet length

The camera family uses AES-256-CBC. The legacy key derivation is based on MD5
of a shared prefix plus public header values:

```text
MD5(prefix + ",ID:<command-id>-SEQ:<sequence>-RPC:<rpc-type>")
```

The resulting lowercase hexadecimal MD5 text is used as the 32-byte AES key;
its first 16 bytes are also used as the IV. Do not commit a device/app prefix.
RPC payloads use PKCS#7 padding. Empty protobuf requests are sent without an
encrypted padding block.

### Live-validated control sequence

| Command | Direction | Meaning |
|---:|---|---|
| 2650 | client → camera | LAN authentication |
| 106 | client → camera | connection sync; request field 1 is `1` |
| 107 | both | time synchronization/heartbeat |
| 2610 | client → camera | start live video; channel/QoS/speed/format request |
| 2614 | client → camera | start live audio on channel 0; empty request |

`LanAuth.Req` has an omitted/default channel followed by string fields `user`
and `pwd`. On the tested unit, `pwd` is the stable special local-salt form that
begins with `$L`; it is not the user's Wi-Fi or cloud password. The response
contains a fresh 32-byte hexadecimal `session_key`. Control RPCs continue to
use the bootstrap prefix, while AV key derivation uses this session key.

The connection must answer the camera's two initial command-107 requests before
sending command 2610. The bridge explicitly sends QoS `30`, the highest value
defined by Linklemo's generic resolution selector. The tested camera accepts
requests ranging from QoS 5 (SD) through 30 (4K) but clamps every one to its
reported maximum of QoS 5, 10 FPS, format 4 (MJPEG). All resulting frames were
640×480 and used identical JPEG quantization tables; the higher generic SDK
labels do not unlock hidden resolutions on this firmware. A time-sync response
echoes the request timestamp in field 1,
contains signed value `-30` in field 2, and sends the bridge wall-clock time in
milliseconds in field 3.

## Audio/video variable header

After the fixed header:

1. key-frame bit (bit 7) and media format (bits 0–6)
2. encryption type
3. channel — varint
4. AV sequence — varint
5. timestamp — varint
6. encrypted length — varint
7. payload — remainder of the declared packet length

Known format values include H.264 `1`, H.265 `2`, MPEG `3`, MJPEG `4`, G.711A
`21`, G.711U variants `31`/`41`, Opus `51`, and AAC `101`.

### Live-validated microphone stream

Linklemo starts listening by calling `FTConn.audioPlay(0)` shortly after video
start. Native-library analysis maps that call to PPRPC command `2614`. Channel
zero is its protobuf default, so the request body is empty.

The physical `INO-A9-V2.8` unit returned success and response values identifying
codec `21` (G.711 A-law), 8,000 Hz, 16 reported sample bits, and one track. It
then emitted one unencrypted type-6/flag-9 packet per audio sequence. A
30-second header-only observation counted 302 sequences and 242,204 payload
bytes without retaining audio. That is approximately 64.6 kbit/s of codec
payload, consistent with ordinary G.711; TCP, PPRPC, and Wi-Fi framing add a
small amount of overhead.

The legacy AV key derivation is:

```text
MD5(prefix + ",AVSeq:<sequence>-TT:<timestamp>-AVChannel:<channel>")
```

The lowercase ASCII hex digest is the 32-byte AES-256 key. For AV packets its
last 16 bytes are the CBC IV. Only the first 1040 bytes of each tested MJPEG
frame are encrypted and CBC is used without padding; the remainder is already
plaintext.

Network frames are split into several PPRPC AV packets. Each payload starts
with a three-byte fragmentation header `01 <index> 00`; normal indices begin at
`01`, and `ff` marks the final fragment. Strip those three bytes and concatenate
fragments in index order. Only the first fragment carries the usable timestamp
and encrypted-length metadata; later fragments set encrypted length to zero.
After decrypting the frame head, the complete image begins with JPEG SOI and has
five non-image trailer bytes after JPEG EOI. The bridge trims that trailer.

## Linklemo LAN call path

Decompilation of the locally installed Linklemo build found this sequence:

```text
IpcConfigHelper.init(..., usePpmq=false)
Ipc.connect(did, spec=0, local=true)
  -> User.newFTConn(...)
  -> FTConn.lanDailByDid()
Ipc.videoPlay(channel=0, quality=0, speed=0)
  -> FTConn.videoPlay(0, 0, 0)
Ipc.audioPlay(channel=0)
  -> FTConn.audioPlay(0)
CallAVPacket callbacks
```

The independent client does not require SDK discovery or a relay URI. It opens
a direct TCP connection to the camera's reserved address, performs commands
2650/106/107/2610, and consumes type-6 video packets. Command 2614 and the
resulting G.711 A-law audio packets were independently live-validated as a
separate diagnostic. The camera session key was independently validated
against both the encrypted network frame head and the decoded SDK callback
frame during analysis.

## What `a9-v720` does and does not provide

The `intx82/a9-v720` project targets a different Naxclow firmware line. It uses
TCP `6123`, an MQTT/fake-cloud flow in station mode, and MJPEG data over UDP.
Those ideas are useful context, but its commands cannot be sent to this PPRPC
camera.

## Primary references

- [Archived PPRPC Go package documentation](https://pkg.go.dev/github.com/pprpc/core/packets)
- [FuseTim's camera analysis](https://fusetim.me/posts/20251005-the-insecurity-camera/)
- [insecurity-camera-tools](https://github.com/fusetim/insecurity-camera-tools)
- [a9-v720](https://github.com/intx82/a9-v720)
- [beken7252-opencam](https://github.com/daniel-dona/beken7252-opencam)
