# Architecture

WiFi Hotspot is split into a small privileged core and unprivileged front ends.
The core does not depend on GTK and can be used from a terminal, a systemd
service, or another program.

```
+---------------------------+        +---------------------------+
|  GTK4 / Libadwaita GUI    |        |  wifi-hotspot CLI         |
|  (unprivileged)           |        |  (unprivileged / sudo)    |
+-------------+-------------+        +-------------+-------------+
              |                                    |
              |  Unix socket, JSON-RPC             | direct mode
              v                                    v
+---------------------------------------------------------------+
|  wifi-hotspotd (root)  or  direct root CLI invocation          |
+-------------------------------+-------------------------------+
                                |
        +-----------------------+-----------------------+
        |                       |                       |
        v                       v                       v
   hostapd                  dnsmasq               firewall backend
   (802.11 AP)          (DHCP + DNS)          (firewalld/iptables/nft)
```

## Design goals

- **No hidden magic.** Every step maps to a documented Linux command.
- **Safe cleanup.** A failed start must not leave interfaces, processes, routes,
  firewall rules, or NetworkManager configuration behind.
- **Least privilege.** The GUI never runs as root. The daemon exposes a narrow
  set of operations over a local socket and checks authorization with polkit.
- **Distribution neutral.** Nobody is assumed to use NetworkManager, systemd, or
  a particular firewall.
- **Standard library core.** Python 3.10+ is enough to run the CLI. GTK4,
  Libadwaita, and PyGObject are system packages used only by the GUI.

## Components

### `wifi_hotspot.models`

`HotspotConfig` is the validated description of a hotspot: SSID, password,
security, band, channel, country, subnet, DHCP range, DNS servers, interface
names, client limit, hidden SSID, and profile name. Validation happens before
any privileged operation.

### `wifi_hotspot.engine`

`HotspotEngine` orchestrates a start or stop:

1. Acquire the runtime lock.
2. Select the uplink (explicit config or default route).
3. Select the AP radio (same card as the uplink or a second card).
4. Configure the regulatory domain if requested.
5. Choose a channel compatible with the radio and the uplink channel.
6. Check subnet overlap with existing interfaces.
7. Make NetworkManager ignore the future AP interface.
8. Create the AP interface and configure its IPv4 address.
9. Generate hostapd and dnsmasq configuration files.
10. Enable IPv4 forwarding.
11. Install firewall/NAT rules.
12. Start hostapd, then dnsmasq.
13. Persist runtime state for status and cleanup.

The reverse order is used for stop. Every partial step is recorded in
`/run/wifi-hotspot/state.json` so a crash can still be cleaned up.

### Platform layer

- `platform/wifi.py` parses `iw` output: interfaces, PHY capabilities, bands,
  allowed channels, station statistics, and the current uplink channel.
- `platform/network.py` wraps `ip`, sysctl helpers, default-route detection, and
  the temporary NetworkManager drop-in used to keep the AP interface unmanaged.
- `platform/firewall.py` implements three backends with isolated chains/tables:
  `firewalld`, `iptables`, and `nftables`.
- `platform/deps.py` and `platform/detect.py` report missing tools and detect the
  distribution family.

### Services

- `services/hostapd.py` builds a hostapd configuration for WPA2, WPA3, mixed, or
  open networks, including 2.4/5 GHz, HT/VHT/HE, country, hidden SSID, client
  isolation, and a client limit.
- `services/dnsmasq.py` builds DHCP/DNS configuration and parses lease files.
- `services/process.py` starts daemons in their own session, keeps PID and log
  files, detects early exits, and performs narrow cleanup. It never uses a broad
  `pkill`; process identity is checked against the binary and configuration path.

### State and secrets

Runtime files live in `/run/wifi-hotspot` (mode `0755`). Configuration and
profiles live in `/etc/wifi-hotspot` (mode `0700`), and profile files are `0600`.
The runtime state intentionally contains no Wi-Fi password.

### CLI

`wifi-hotspot` exposes `start`, `stop`, `restart`, `status`, `clients`, `doctor`,
`profiles`, `qr`, and `version`. `--json` makes the output machine-readable.
`wifi.sh` is a compatibility wrapper for the original script; the original is
preserved unchanged in `legacy/wifi.sh`.

## Networking details

### Topologies

- **Ethernet plus Wi-Fi AP:** the most compatible setup. The AP uses a dedicated
  wireless interface and NATs to the Ethernet uplink.
- **AP + STA on one radio:** the AP must use the same channel as the station
  connection. The engine reads the current channel with `iw` and refuses to force
  a different one.
- **Two wireless cards:** the AP card can use an independent channel and band.

### NetworkManager

Creating an AP interface before NetworkManager knows about it is racy: the
manager can mark it managed and change its type. The engine writes a temporary
drop-in before creating the interface:

```ini
[keyfile]
unmanaged-devices=interface-name:wifi-ap
```

It is placed in `/run/NetworkManager/conf.d/90-wifi-hotspot-wifi-ap.conf`, then
NetworkManager is reloaded. The file is removed on stop, and `/run` guarantees it
does not survive a reboot.

### Channel selection

Country and channel availability are read from `iw phy <phy> info`. 5 GHz
operation requires a real regulatory domain; DFS channels are not selected
automatically. When AP and STA share a radio, the station channel wins.

### DNS

By default dnsmasq acts as the DNS server for clients and forwards to the
configured upstream servers. An upstream mode exists for environments where
another resolver owns port 53 (for example an LXC dnsmasq). The daemon is always
invoked as:

```bash
dnsmasq --conf-file=/run/wifi-hotspot/dnsmasq.conf --keep-in-foreground --log-facility=-
```

The `--conf-file=<path>` form is required by dnsmasq 2.90+; the separated form is
rejected as a command-line error.

## Security model

- The direct CLI uses the caller's `sudo`/`pkexec` privileges.
- The planned daemon listens on a Unix socket, authenticates the peer UID, and
  authorizes privileged actions through polkit.
- No telemetry and no outbound connections other than the configured DNS
  servers.
- Profiles are root-owned, mode `0600`; runtime state omits passwords.
- Logs are mode `0600` and contain no passwords.

## Packaging

- **Debian/Ubuntu/Kali:** `packaging/debian` with `debhelper` and `dh-python`.
- **Fedora/openSUSE:** `packaging/rpm/wifi-hotspot.spec`.
- **Arch:** `packaging/arch/PKGBUILD`.
- **Development:** `pip install .` or `pipx install .`.

System integration assets live in `data/`: desktop entry, AppStream metadata,
icons, polkit policy, and systemd unit.

## Roadmap

1. Core CLI and real-hardware validation (done for AP+STA on Kali).
2. Root daemon with a Unix socket and polkit.
3. GTK4/Libadwaita GUI and tray integration.
4. Native packaging and distribution testing.
5. Translations and hardware compatibility database.
