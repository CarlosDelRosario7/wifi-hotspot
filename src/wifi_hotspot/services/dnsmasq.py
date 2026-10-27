"""Dnsmasq configuration, lifecycle, and lease parsing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import HotspotConfig
from ..util import atomic_write, remove_file
from . import process

DEFAULT_DNS = ("1.1.1.1", "8.8.8.8")


@dataclass(frozen=True)
class DnsmasqPaths:
    config: Path
    pid: Path
    log: Path
    leases: Path


def dns_servers(config: HotspotConfig) -> list[str]:
    if config.dns:
        return [server for server in config.dns if ":" not in server]
    return list(DEFAULT_DNS)


def build_config(
    config: HotspotConfig,
    *,
    ap_interface: str,
    lease_file: Path,
    dns_mode: str = "server",
) -> str:
    """Generate a dnsmasq configuration.

    dns_mode="server" uses dnsmasq as the clients' DNS resolver. If port 53 is
    occupied by another process, the engine can retry with dns_mode="upstream";
    in that mode dnsmasq only provides DHCP and clients receive the upstream
    resolvers directly.
    """
    servers = dns_servers(config)
    lines = [
        f"interface={ap_interface}",
        "bind-dynamic",
        f"dhcp-range={config.dhcp_start_address},{config.dhcp_end_address},"
        f"{config.netmask},{config.lease_time}",
        f"dhcp-option=3,{config.ap_ip}",
        "dhcp-authoritative",
        "domain-needed",
        "bogus-priv",
        "log-dhcp",
        f"dhcp-leasefile={lease_file}",
    ]
    if dns_mode == "server":
        lines.extend([f"dhcp-option=6,{config.ap_ip}", "no-resolv"])
        lines.extend(f"server={server}" for server in servers)
    elif dns_mode == "upstream":
        if servers:
            lines.append(f"dhcp-option=6,{','.join(servers)}")
        lines.append("port=0")
    else:
        raise ValueError(f"Modo DNS no soportado: {dns_mode}")
    return "\n".join(lines) + "\n"


def write_config(path: Path, content: str) -> None:
    atomic_write(path, content, mode=0o600)


def start(conf: Path, pid: Path, log: Path) -> int:
    return process.start_foreground(
        ["dnsmasq", f"--conf-file={conf}", "--keep-in-foreground", "--log-facility=-"],
        name="dnsmasq",
        pid_file=pid,
        log_file=log,
        startup_delay=1.0,
    )


def stop(pid: Path, conf: Path | None = None) -> bool:
    return process.stop_foreground(
        name="dnsmasq",
        binary="dnsmasq",
        pid_file=pid,
        config_path=conf,
    )


def parse_leases(path: Path) -> dict[str, dict[str, str]]:
    """Return leases keyed by lowercase MAC address."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {}
    leases: dict[str, dict[str, str]] = {}
    for line in content.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        expiry, mac, ip, hostname = parts[:4]
        client_id = parts[4] if len(parts) > 4 else ""
        leases[mac.lower()] = {
            "expiry": expiry,
            "mac": mac.lower(),
            "ip": ip,
            "hostname": "" if hostname == "*" else hostname,
            "client_id": client_id,
        }
    return leases


def cleanup(paths: DnsmasqPaths) -> None:
    stop(paths.pid, paths.config)
    for path in (paths.config, paths.pid, paths.log, paths.leases):
        remove_file(path)
