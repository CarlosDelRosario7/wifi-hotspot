[Español](README.md) · **English**

# WiFi Hotspot

[![CI](https://github.com/CarlosDelRosario7/wifi-hotspot/actions/workflows/ci.yml/badge.svg)](https://github.com/CarlosDelRosario7/wifi-hotspot/actions/workflows/ci.yml)
[![GitHub release](https://img.shields.io/github/v/release/CarlosDelRosario7/wifi-hotspot)](https://github.com/CarlosDelRosario7/wifi-hotspot/releases)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)

A modern, open-source Wi-Fi hotspot manager for Linux.

WiFi Hotspot turns a Linux computer into a Wi-Fi access point. It works across
distributions and provides a native GTK4/Libadwaita interface, a complete CLI,
and a privileged daemon so the GUI never has to run as root.

<img src="data/screenshots/wifi-hotspot.png" alt="WiFi Hotspot interface" width="600">

> **Status: 0.1.0 (alpha).** The core, CLI, daemon, GUI, and packages are
> implemented and have been tested on Kali Rolling with real hardware in AP+STA
> mode. The system tray, hotspot autostart, traffic graph, and GUI translation
> are on the roadmap.

## Why another hotspot tool?

Most existing tools are tied to one distribution, one network manager, or a
single Wi-Fi topology. WiFi Hotspot supports:

- **Ethernet uplink + Wi-Fi AP** (the most compatible setup).
- **Concurrent AP + STA on the same card** when the driver allows it.
- **Two Wi-Fi cards** with independent channel and band.
- NetworkManager, systemd-networkd, or no network manager.
- `firewalld`, `iptables`, and `nftables` firewall backends.
- 2.4 GHz and 5 GHz, WPA2, WPA3, mixed WPA2/WPA3, and open networks.
- Debian 13+, Ubuntu 24.04+, Linux Mint 22+, Fedora 41+, Arch Linux,
  Kali Linux, and openSUSE Tumbleweed.

## Features

- GTK4 + Libadwaita GUI to start, stop, and configure the hotspot.
- CLI with human-readable or `--json` output, profiles, and a `doctor` command.
- Root daemon with a Unix socket, `SO_PEERCRED` authentication, and polkit.
- Live client list with signal, RX/TX counters, and kick action.
- QR code sharing from the GUI or CLI.
- Country/regulatory domain, band, channel, hidden SSID, and client limit.
- Subnet, DHCP range, lease time, DNS, and client isolation.
- Saved profiles with `0600` permissions.
- `.deb`, `.rpm`, AppImage, and Arch `PKGBUILD` packaging.
- No telemetry and no network connections other than the configured DNS
  servers.

## Installation

### Debian, Ubuntu, Linux Mint, and Kali

Download the `.deb` from [Releases](https://github.com/CarlosDelRosario7/wifi-hotspot/releases)
and install it with `apt` so dependencies are resolved:

```bash
sudo apt install ./wifi-hotspot_0.1.0_all.deb
sudo systemctl start wifi-hotspotd   # optional: the GUI can start it too
wifi-hotspot-gui
```

The package installs `wifi-hotspot`, `wifi-hotspotd`, `wifi-hotspot-gui`, the
`/usr/bin/wifi.sh` wrapper, the desktop entry, icon, polkit policy, and systemd
unit. The service is enabled but not started automatically.

### Fedora and openSUSE

```bash
# Fedora
sudo dnf install ./wifi-hotspot-0.1.0-1.noarch.rpm

# openSUSE Tumbleweed
sudo zypper install ./wifi-hotspot-0.1.0-1.noarch.rpm

sudo systemctl start wifi-hotspotd
wifi-hotspot-gui
```

### Arch Linux

The `PKGBUILD` lives in `packaging/arch` and uses the `v0.1.0` tag as its
source:

```bash
git clone --branch v0.1.0 https://github.com/CarlosDelRosario7/wifi-hotspot.git
cd wifi-hotspot/packaging/arch
makepkg -si
sudo systemctl start wifi-hotspotd
```

### AppImage

The AppImage bundles the CLI and GUI but **not** GTK4, Libadwaita, PyGObject, or
the privileged daemon: it uses the system libraries. The GUI needs the daemon
and polkit policy from your distribution package to start the hotspot.

```bash
chmod +x WiFi_Hotspot-0.1.0-x86_64.AppImage
./WiFi_Hotspot-0.1.0-x86_64.AppImage            # GUI
./WiFi_Hotspot-0.1.0-x86_64.AppImage doctor     # CLI
./WiFi_Hotspot-0.1.0-x86_64.AppImage --version
```

### From source

Requirements on Debian/Ubuntu/Kali:

```bash
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
  hostapd dnsmasq-base iw iproute2 iptables nftables qrencode
```

```bash
git clone https://github.com/CarlosDelRosario7/wifi-hotspot.git
cd wifi-hotspot
python3 -m pip install --user .
wifi-hotspot doctor
```

You can also run it without installing:

```bash
PYTHONPATH=src python3 -m wifi_hotspot doctor
PYTHONPATH=src python3 -m wifi_hotspot gui
```

## Quick start

When the daemon is running, the CLI talks to it and polkit asks for
authorization when needed. You can also run the engine directly with `sudo`:

```bash
sudo wifi-hotspot start "MyHotspot" "MyPassword123"
wifi-hotspot status
wifi-hotspot clients
wifi-hotspot qr
sudo wifi-hotspot stop
```

Common options:

```bash
sudo wifi-hotspot start \
  --profile cafe \
  --ssid "CafeCentral" \
  --password "AStrongPassword123" \
  --security wpa3 \
  --band 5 \
  --channel 36 \
  --country ES
```

When the AP shares a radio with the STA connection, the AP channel must match
the station channel; the engine detects this and rejects an incompatible
channel.

Available commands:

| Command | Description |
| --- | --- |
| `start` | Create the AP, configure DHCP/NAT, and start hostapd/dnsmasq. |
| `stop` | Stop processes and clean up interfaces, routes, firewall, and NetworkManager. |
| `restart` | Restart the hotspot with the given configuration. |
| `status` | Show state, interfaces, channel, firewall, and clients. |
| `clients` | List clients with IP, MAC, signal, and traffic. |
| `doctor` | Diagnose dependencies, distro, firewall, regdomain, and Wi-Fi hardware. |
| `profiles` | List saved profiles. |
| `qr` | Print the profile QR code in the terminal. |
| `gui` | Open the GTK4 interface. |
| `version` | Print the version. |

Profiles are created and loaded from the GUI. The CLI can start from an existing
profile with `--profile NAME`.

## GUI

Open **WiFi Hotspot** from the application menu or run:

```bash
wifi-hotspot-gui
```

The GUI lets you:

- Start and stop the hotspot.
- Edit SSID, password, security, band, channel, country, subnet, DHCP, DNS,
  firewall, client limit, and IPv6.
- See connected clients, their signal, and accumulated traffic.
- Kick a client.
- Generate a strong password.
- Show the QR code for sharing.
- Save and load profiles.
- Start the daemon with `pkexec` when it is not running.

The interface is currently in Spanish. English localization will follow.

## Daemon and security

```
GUI (GTK4)  ──Unix socket──>  wifi-hotspotd (root)  ──>  hostapd / dnsmasq / firewall
CLI         ──Unix socket──>  (or direct mode with sudo)
```

- Socket: `/run/wifi-hotspot/daemon.sock`.
- State and PID files: `/run/wifi-hotspot`.
- Profiles and secrets: `/etc/wifi-hotspot`, mode `0700`, files `0600`.
- The daemon checks the peer UID with `SO_PEERCRED`. Root is always allowed;
  other users go through polkit action
  `io.github.carlosdelrosario7.WifiHotspot.manage`.
- The GUI never runs as root.
- Logs contain no passwords.

```bash
sudo systemctl status wifi-hotspotd
sudo journalctl -u wifi-hotspotd -f
```

## The `wifi.sh` wrapper

The original script is preserved unchanged in [`legacy/wifi.sh`](legacy/wifi.sh).
`wifi.sh` is a compatibility wrapper that maps the old interface to the new CLI:

```bash
sudo ./wifi.sh start "MyHotspot" "MyPassword123"
sudo ./wifi.sh stop
./wifi.sh status
./wifi.sh doctor
```

The package installs the wrapper as `/usr/bin/wifi.sh`, so it can be used like
any other command.

### Does copying `wifi.sh` to `/usr/bin` make it a command?

**Yes, but copying the file alone is not enough.** Linux runs any executable
file found in a directory on `PATH`, so a `wifi.sh` copied to `/usr/bin` or
`/usr/local/bin` can be invoked by name. The wrapper still needs to find the
engine:

1. It first looks for the `wifi-hotspot` command on `PATH`.
2. If that is missing, it looks for `src/wifi_hotspot` next to the script.

If you copy only the wrapper to `/usr/bin` without installing the package, step
1 fails and `/usr/bin` does not contain `src/wifi_hotspot`, so the wrapper exits
with an error. The recommended approach is to install the package, which already
provides `/usr/bin/wifi-hotspot` and `/usr/bin/wifi.sh`, or run `./wifi.sh` from
the repository. To use the wrapper from another location with the source tree,
create a symlink to the repository script instead of copying it:

```bash
sudo ln -s "$PWD/wifi.sh" /usr/local/bin/wifi.sh
```

## Building packages

```bash
make check-version   # verify that every version agrees
make test
make lint
make deb             # dist/wifi-hotspot_0.1.0_all.deb
make rpm             # dist/wifi-hotspot-0.1.0-1.noarch.rpm
make appimage        # dist/WiFi_Hotspot-0.1.0-x86_64.AppImage
make packages        # deb + rpm + appimage
```

For Arch:

```bash
cd packaging/arch
makepkg -si
```

The AppImage downloads `appimagetool` into the user cache when it is not
available. The `PKGBUILD`, `.spec`, and `.deb` use the same version as
`pyproject.toml`; `make check-version` verifies it.

## Compatibility

| Distribution | Status |
| --- | --- |
| Kali Rolling | Tested on real hardware (AP+STA, 2.4 GHz). |
| Debian 13+ | Target; `.deb` included. |
| Ubuntu 24.04+ / Mint 22+ | Target; `.deb` included. |
| Fedora 41+ | Target; `.spec` included. |
| openSUSE Tumbleweed | Target; `.spec` included. |
| Arch Linux | Target; `PKGBUILD` included. |

AP+STA support depends on the driver and firmware. `wifi-hotspot doctor` shows
the detected capabilities. Hardware reports are welcome.

## Architecture

```
GUI (GTK4)  ──Unix socket──>  wifi-hotspotd (root)  ──>  hostapd / dnsmasq / firewall
CLI         ──Unix socket──>  (or direct mode with sudo)
```

The core is Python 3.10+ and uses only the standard library. GTK4, Libadwaita,
and PyGObject are system packages used by the GUI. See
[docs/architecture.md](docs/architecture.md) for the full design.

## Security

- Secrets are stored with `0600` permissions in root-owned directories.
- Configurations are not written to `/tmp`.
- The daemon uses a local Unix socket and polkit; the GUI never runs as root.
- The firewall uses dedicated chains/tables so it does not mix with foreign
  rules.
- No telemetry.

To report a vulnerability, please read [SECURITY.md](SECURITY.md).

## Roadmap

1. System tray and hotspot autostart.
2. Traffic graph and client history.
3. Complete English translation of the GUI.
4. More testing on Fedora, Arch, openSUSE, and Ubuntu.
5. Hardware and driver compatibility database.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening
a pull request. Hardware reports are especially useful.

## License

Copyright (C) 2026 Carlos Del Rosario and contributors.

This project is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. See [LICENSE](LICENSE).
