# WiFi Hotspot

A modern, open-source Wi-Fi hotspot manager for Linux.

WiFi Hotspot turns a Linux computer into a Wi-Fi access point. It is designed to
work across distributions, with a native GTK4/Libadwaita interface, a powerful
CLI, and a small privileged helper so the GUI never needs to run as root.

> **Status:** alpha. The generic core CLI is being built first; the daemon and
> GTK4 interface follow in the next milestones. The original `wifi.sh` is kept in
> `legacy/` and remains usable while the new CLI reaches feature parity.

## Why another hotspot tool?

Most existing tools are tied to one distribution, one network manager, or a
single Wi-Fi topology. WiFi Hotspot aims to support:

- **Ethernet uplink + Wi-Fi AP** (the most compatible setup).
- **Concurrent AP + STA on the same Wi-Fi card** (when the driver supports it).
- **Two Wi-Fi cards** with an independent AP channel.
- NetworkManager, systemd-networkd, or no network manager.
- `firewalld`, `iptables`, `nftables`, and `ufw` environments.
- 2.4 GHz and 5 GHz, WPA2, WPA3, and mixed WPA2/WPA3.
- Debian 13+, Ubuntu 24.04+, Linux Mint 22+, Fedora 41+, Arch Linux,
  Kali Linux, and openSUSE Tumbleweed.

## Planned features

- One-click hotspot with a modern GTK4 + Libadwaita app.
- SSID/password management and QR sharing.
- Live client list with signal, traffic, and kick action.
- Traffic graph, logs, profiles, and autostart.
- Country/regulatory domain, band, channel, and security selection.
- CLI with `--json` output and a `doctor` command for troubleshooting.
- Native packages: `.deb`, `.rpm`, and `PKGBUILD`.
- Spanish and English UI.

## Quick start (development)

Requirements on Debian/Ubuntu/Kali:

```bash
sudo apt install python3 hostapd dnsmasq-base iw iproute2 iptables nftables
```

Run the CLI from the repository:

```bash
PYTHONPATH=src python3 -m wifi_hotspot status
PYTHONPATH=src python3 -m wifi_hotspot doctor
sudo PYTHONPATH=src python3 -m wifi_hotspot start "MyHotspot" "MyPassword123"
sudo PYTHONPATH=src python3 -m wifi_hotspot stop
```

The legacy script and its original interface are still available:

```bash
sudo ./legacy/wifi.sh start "MyHotspot" "MyPassword123"
sudo ./legacy/wifi.sh stop
```

Installing the Python package exposes the `wifi-hotspot` command and the
`wifi.sh` compatibility wrapper keeps the old commands:

```bash
python3 -m pip install --user .
wifi-hotspot doctor
sudo wifi-hotspot start "MyHotspot" "MyPassword123"
sudo ./wifi.sh stop
```

## Architecture

```
GUI (GTK4)  ──Unix socket──>  wifi-hotspotd (root)  ──>  hostapd / dnsmasq / firewall
CLI         ──Unix socket──>  (or direct mode with sudo)
```

The core is Python 3.10+ and uses only the standard library. GTK4, Libadwaita,
and PyGObject are system packages used by the GUI.

See [docs/architecture.md](docs/architecture.md) for details.

## Security

- Secrets are stored with mode `0600` in root-owned directories.
- Configurations are not written to `/tmp`.
- The daemon uses a local Unix socket and polkit; the GUI never runs as root.
- No telemetry and no network calls except the DNS servers configured by the
  user.

To report a vulnerability, please read [SECURITY.md](SECURITY.md).

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening
a pull request. Hardware reports are especially useful.

## License

Copyright (C) 2026 Carlos Del Rosario and contributors.

This project is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. See [LICENSE](LICENSE).
