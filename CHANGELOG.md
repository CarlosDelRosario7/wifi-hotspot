# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial Python core with `start`, `stop`, `restart`, `status`, `clients`,
  `doctor`, and `version` commands.
- Secure runtime state in `/run/wifi-hotspot`, profile storage in
  `/etc/wifi-hotspot`, and atomic secret files with mode `0600`.
- Dedicated firewall chains for iptables, nftables, and firewalld backends.
- Hostapd and dnsmasq configuration generation with WPA2, WPA3, and mixed mode.
- Unit tests for validation, configuration generation, firewall rules, Wi-Fi
  output parsing, and state files.
- GitHub Actions CI, GPL-3.0-or-later license, and community documentation.

### Changed

- The original Bash script is preserved in `legacy/wifi.sh`.

## [0.1.0] - 2026-10-26

Initial development snapshot.
