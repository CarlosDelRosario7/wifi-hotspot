"""Dependency checks and distro-specific install hints."""

from __future__ import annotations

from collections.abc import Iterable

from ..errors import DependencyError
from ..util import command_exists, human_join
from .detect import DistroInfo, detect_distro

CORE_COMMANDS = ("iw", "ip", "hostapd", "dnsmasq")

PACKAGE_NAMES: dict[str, dict[str, str]] = {
    "debian": {
        "iw": "iw",
        "ip": "iproute2",
        "hostapd": "hostapd",
        "dnsmasq": "dnsmasq-base",
        "iptables": "iptables",
        "nft": "nftables",
        "nmcli": "network-manager",
        "rfkill": "rfkill",
        "qrencode": "qrencode",
        "python3-gi": "python3-gi",
        "gtk4": "gir1.2-gtk-4.0",
        "libadwaita": "gir1.2-adw-1",
        "polkit": "polkitd",
    },
    "fedora": {
        "iw": "iw",
        "ip": "iproute",
        "hostapd": "hostapd",
        "dnsmasq": "dnsmasq",
        "iptables": "iptables-nft",
        "nft": "nftables",
        "nmcli": "NetworkManager",
        "rfkill": "rfkill",
        "qrencode": "qrencode",
        "python3-gi": "python3-gobject",
        "gtk4": "gtk4",
        "libadwaita": "libadwaita",
        "polkit": "polkit",
    },
    "arch": {
        "iw": "iw",
        "ip": "iproute2",
        "hostapd": "hostapd",
        "dnsmasq": "dnsmasq",
        "iptables": "iptables-nft",
        "nft": "nftables",
        "nmcli": "networkmanager",
        "rfkill": "rfkill",
        "qrencode": "qrencode",
        "python3-gi": "python-gobject",
        "gtk4": "gtk4",
        "libadwaita": "libadwaita",
        "polkit": "polkit",
    },
    "suse": {
        "iw": "iw",
        "ip": "iproute2",
        "hostapd": "hostapd",
        "dnsmasq": "dnsmasq",
        "iptables": "iptables",
        "nft": "nftables",
        "nmcli": "NetworkManager",
        "rfkill": "rfkill",
        "qrencode": "qrencode",
        "python3-gi": "python3-gobject",
        "gtk4": "gtk4",
        "libadwaita": "libadwaita",
        "polkit": "polkit",
    },
}

INSTALL_COMMANDS = {
    "debian": "sudo apt install",
    "fedora": "sudo dnf install",
    "arch": "sudo pacman -S",
    "suse": "sudo zypper install",
}


def missing_commands(commands: Iterable[str] = CORE_COMMANDS) -> list[str]:
    return [command for command in commands if not command_exists(command)]


def package_for(family: str, command: str) -> str:
    return PACKAGE_NAMES.get(family, {}).get(command, command)


def install_hint(distro: DistroInfo, commands: Iterable[str]) -> str:
    packages: list[str] = []
    family = distro.family
    for command in commands:
        if command == "firewall":
            # The caller decides which backend is available; iptables is the
            # widest fallback on Debian and Fedora.
            command = "iptables"
        package = package_for(family, command)
        if package not in packages:
            packages.append(package)
    if not packages:
        return ""
    prefix = INSTALL_COMMANDS.get(family)
    if not prefix:
        return "Instala los comandos: " + ", ".join(packages)
    if family == "arch":
        return f"{prefix} {' '.join(packages)} --needed"
    return f"{prefix} {' '.join(packages)}"


def check_dependencies(commands: Iterable[str] = CORE_COMMANDS) -> None:
    missing = missing_commands(commands)
    if not missing:
        return
    distro = detect_distro()
    hint = install_hint(distro, missing)
    message = f"Faltan dependencias: {human_join(missing)}."
    if hint:
        message += f"\n\nInstálalas con:\n  {hint}"
    raise DependencyError(message)
