# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-26

### Added

- Initial Python core with `start`, `stop`, `restart`, `status`, `clients`,
  `doctor`, `qr`, `profiles`, `gui`, and `version` commands.
- Ethernet uplink, concurrent AP+STA, and two-card topologies with 2.4 GHz and
  5 GHz channel selection.
- WPA2, WPA3, mixed WPA2/WPA3, and open network support.
- Secure runtime state in `/run/wifi-hotspot`, profile storage in
  `/etc/wifi-hotspot`, and atomic secret files with mode `0600`.
- Dedicated firewall chains and tables for iptables, nftables, and firewalld.
- Temporary NetworkManager drop-in so the AP interface stays unmanaged.
- hostapd/dnsmasq process supervision, client parsing, traffic counters, and
  QR payload generation.
- Root daemon with a Unix socket, line-delimited JSON-RPC, `SO_PEERCRED`
  authentication, polkit authorization, and systemd unit.
- GTK4 + Libadwaita interface for hotspot control, profiles, QR sharing, and
  connected clients.
- Debian `.deb`, RPM `.spec`, Arch `PKGBUILD`, and AppImage packaging with a
  shared `0.1.0` version.
- Desktop entry, AppStream metadata, scalable icon, and polkit policy.
- `wifi.sh` compatibility wrapper; the original script remains unchanged in
  `legacy/wifi.sh`.
- Spanish and English READMEs with a language selector.
- Unit tests for validation, configuration generation, firewall rules, Wi-Fi
  output parsing, state files, daemon RPC, and profiles.
- GitHub Actions CI, GPL-3.0-or-later license, and community documentation.
