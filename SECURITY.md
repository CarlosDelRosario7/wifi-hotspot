# Security Policy

## Supported versions

Only the latest release is supported with security fixes. During the alpha
phase, fixes land on `main`.

## Reporting a vulnerability

Please do not open a public issue for security-sensitive reports.

Use GitHub's private vulnerability reporting:

<https://github.com/CarlosDelRosario7/wifi-hotspot/security/advisories/new>

Include:

- A description of the issue and its impact.
- Steps to reproduce, if possible.
- Your distribution, kernel, and Wi-Fi hardware.
- Whether the hotspot daemon was running as root.

We will acknowledge the report as soon as possible and coordinate disclosure.

## Security model

- The GUI runs as an unprivileged user and talks to `wifi-hotspotd` over a local
  Unix socket.
- The daemon runs as root because creating interfaces, configuring addresses,
  and changing firewall rules require privileges.
- Mutating operations are authorized with polkit; status queries are limited to
  active local users.
- Passwords are never returned over the IPC status API and are never logged.
- Runtime secrets live in root-owned directories with mode `0700`; files
  containing secrets use mode `0600`.
- The project does not collect telemetry.

## Out of scope

- Attacks requiring an already-root local user.
- Vulnerabilities in `hostapd`, `dnsmasq`, the kernel, or the Wi-Fi driver.
- Physical access attacks.
- Misconfiguration that intentionally disables the firewall or exposes the AP to
  an untrusted network.
