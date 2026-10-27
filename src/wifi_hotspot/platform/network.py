"""Network interface, route, sysctl, and NetworkManager helpers."""

from __future__ import annotations

import ipaddress
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import CommandError, StateError
from ..util import atomic_write, command_exists, remove_file, run

log = logging.getLogger(__name__)

NM_RUN_CONF_DIR = Path("/run/NetworkManager/conf.d")


@dataclass
class InterfaceInfo:
    name: str
    state: str = "UNKNOWN"
    mac: str = ""
    mtu: int | None = None
    ipv4: list[str] = field(default_factory=list)
    carrier: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "state": self.state,
            "mac": self.mac,
            "mtu": self.mtu,
            "ipv4": list(self.ipv4),
            "carrier": self.carrier,
        }


def _ip_json(args: list[str]) -> list[dict[str, object]] | dict[str, object] | None:
    result = run(["ip", "-j", *args], check=False)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def list_interfaces() -> list[InterfaceInfo]:
    links = _ip_json(["link", "show"])
    addrs = _ip_json(["addr", "show"])
    interfaces: dict[str, InterfaceInfo] = {}
    if isinstance(links, list):
        for item in links:
            if not isinstance(item, dict):
                continue
            name = str(item.get("ifname", ""))
            if not name:
                continue
            interfaces[name] = InterfaceInfo(
                name=name,
                state=str(item.get("operstate", "UNKNOWN")),
                mac=str(item.get("address", "")),
                mtu=int(item["mtu"]) if str(item.get("mtu", "")).isdigit() else None,
            )
    if isinstance(addrs, list):
        for item in addrs:
            if not isinstance(item, dict):
                continue
            name = str(item.get("ifname", ""))
            interface = interfaces.setdefault(name, InterfaceInfo(name=name))
            for address in item.get("addr_info", []) or []:
                if not isinstance(address, dict) or address.get("family") != "inet":
                    continue
                local = address.get("local")
                prefix = address.get("prefixlen")
                if local and prefix is not None:
                    interface.ipv4.append(f"{local}/{prefix}")
    for interface in interfaces.values():
        interface.carrier = get_carrier(interface.name)
    return sorted(interfaces.values(), key=lambda item: item.name)


def get_interface(name: str) -> InterfaceInfo | None:
    for interface in list_interfaces():
        if interface.name == name:
            return interface
    return None


def interface_exists(name: str) -> bool:
    from pathlib import Path

    return Path(f"/sys/class/net/{name}").exists()


def get_carrier(name: str) -> bool | None:
    from pathlib import Path

    path = Path(f"/sys/class/net/{name}/carrier")
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="ascii").strip() == "1"
    except OSError:
        return None


def detect_uplink() -> str:
    """Return the interface used to reach the internet.

    A VPN default route is intentionally accepted: sharing a VPN is a valid and
    useful setup. The caller can still override it from the configuration.
    """
    route = _ip_json(["route", "get", "1.1.1.1"])
    if isinstance(route, list) and route:
        route = route[0]
    if isinstance(route, dict):
        dev = route.get("dev")
        if dev:
            return str(dev)
    # Older iproute2 versions may not support -j for route get.
    result = run(["ip", "route", "get", "1.1.1.1"], check=False)
    if result.returncode == 0:
        parts = result.stdout.split()
        if "dev" in parts:
            index = parts.index("dev")
            if index + 1 < len(parts):
                return parts[index + 1]
    raise StateError(
        "No pude detectar la interfaz con salida a Internet. Conecta el uplink o indica --uplink."
    )


def get_default_route() -> dict[str, str] | None:
    result = run(["ip", "route", "show", "default"], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    first = result.stdout.splitlines()[0].split()
    data: dict[str, str] = {}
    for key in ("via", "dev", "src", "metric"):
        if key in first:
            index = first.index(key)
            if index + 1 < len(first):
                data[key] = first[index + 1]
    return data or None


def get_ipv4_addresses(name: str) -> list[str]:
    interface = get_interface(name)
    return list(interface.ipv4) if interface else []


def get_ipv4_networks(name: str) -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []
    for address in get_ipv4_addresses(name):
        try:
            interface = ipaddress.ip_interface(address)
        except ValueError:
            continue
        if isinstance(interface, ipaddress.IPv4Interface):
            networks.append(interface.network)
    return networks


def subnet_overlaps_interface(name: str, network: ipaddress.IPv4Network) -> bool:
    return any(network.overlaps(existing) for existing in get_ipv4_networks(name))


def ip_link_up(name: str) -> None:
    run(["ip", "link", "set", name, "up"])


def ip_addr_flush(name: str) -> None:
    run(["ip", "addr", "flush", "dev", name], check=False)


def ip_addr_add(name: str, address: str) -> None:
    run(["ip", "addr", "add", address, "dev", name])


def sysctl_read(key: str) -> str | None:
    from pathlib import Path

    path = Path("/proc/sys") / key.replace(".", "/")
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return None


def sysctl_write(key: str, value: str) -> None:
    result = run(["sysctl", "-w", f"{key}={value}"], check=False)
    if result.returncode != 0:
        raise CommandError(
            f"No pude escribir {key}={value}",
            ["sysctl", "-w", f"{key}={value}"],
            stderr=result.stderr,
        )


def enable_ip_forward() -> str:
    previous = sysctl_read("net.ipv4.ip_forward") or "0"
    if previous != "1":
        sysctl_write("net.ipv4.ip_forward", "1")
    return previous


def restore_ip_forward(previous: str | None) -> None:
    if previous is not None:
        current = sysctl_read("net.ipv4.ip_forward")
        if current != previous:
            sysctl_write("net.ipv4.ip_forward", previous)


def disable_ipv6(name: str) -> bool:
    """Disable IPv6 on the AP interface. Returns True if it was changed."""
    key = f"net.ipv6.conf.{name}.disable_ipv6"
    previous = sysctl_read(key)
    if previous is None:
        return False
    if previous != "1":
        sysctl_write(key, "1")
    return True


class NetworkManager:
    """Best-effort NetworkManager integration."""

    def __init__(self) -> None:
        self.available = command_exists("nmcli") and self.is_running()

    @staticmethod
    def is_running() -> bool:
        if not command_exists("nmcli"):
            return False
        result = run(["nmcli", "-t", "-f", "RUNNING", "general"], check=False)
        return result.returncode == 0 and result.stdout.strip().lower() == "running"

    @staticmethod
    def unmanaged_file(interface: str) -> Path:
        return NM_RUN_CONF_DIR / f"90-wifi-hotspot-{interface}.conf"

    def prepare_unmanaged(self, interface: str) -> bool:
        """Tell NetworkManager to ignore the AP interface before it exists.

        Creating an AP interface and then asking NetworkManager to release it is
        racy: NetworkManager can switch a fresh interface to managed mode and
        change its type before hostapd takes it over. A drop-in configuration in
        /run is applied first and disappears on reboot.
        """
        if not self.available:
            return False
        path = self.unmanaged_file(interface)
        try:
            NM_RUN_CONF_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write(
                path,
                f"[keyfile]\nunmanaged-devices=interface-name:{interface}\n",
                mode=0o644,
            )
        except OSError as exc:
            raise StateError(
                f"No pude configurar NetworkManager para la interfaz {interface}: {exc}"
            ) from exc

        result = run(["nmcli", "general", "reload"], check=False)
        if result.returncode != 0:
            remove_file(path)
            raise StateError(
                "NetworkManager no pudo recargar su configuración: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        log.info("NetworkManager ignorará la interfaz %s", interface)
        return True

    def clear_unmanaged(self, interface: str) -> bool:
        path = self.unmanaged_file(interface)
        if not path.exists():
            return False
        remove_file(path)
        result = run(["nmcli", "general", "reload"], check=False)
        if result.returncode != 0:
            log.warning(
                "No pude recargar NetworkManager tras quitar el drop-in de %s: %s",
                interface,
                result.stderr.strip(),
            )
            return False
        return True

    def get_state(self, interface: str) -> str | None:
        result = run(["nmcli", "-t", "-f", "DEVICE,STATE", "device", "status"], check=False)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[0] == interface:
                return parts[1]
        return None

    def was_managed(self, interface: str) -> bool | None:
        state = self.get_state(interface)
        if state is None:
            return None
        return state not in {"unmanaged", "unavailable"}

    def set_managed(self, interface: str, managed: bool) -> bool:
        if not self.available:
            return False
        value = "yes" if managed else "no"
        result = run(["nmcli", "device", "set", interface, "managed", value], check=False)
        if result.returncode != 0:
            log.warning(
                "No pude marcar %s como managed=%s: %s",
                interface,
                value,
                result.stderr.strip(),
            )
            return False
        return True

    def restore(self, interface: str, original: bool | None) -> None:
        if original is True:
            self.set_managed(interface, True)
