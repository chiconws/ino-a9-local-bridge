# Security policy

This repository intentionally contains no operational credentials, vendor APKs,
firmware images, packet captures, or private video. Please report accidental
secret disclosure privately to the repository owner rather than opening a
public issue.

Run the tooling only against devices and networks you own or are authorized to
test. Keep untrusted cameras isolated from the Internet during analysis.

The Python control API can change camera settings and request a device reboot.
Keep `CameraCredentials` in an ignored configuration or secret manager, never
publish them in issues, logs, examples, or commits, and do not expose the
bridge's HTTP listener beyond a trusted network. The command-line bridge is
read-only; applications embedding `CameraSession` are responsible for
authorizing and protecting control access.
