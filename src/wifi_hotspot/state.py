"""Runtime state persisted in /run (never contains the Wi-Fi password)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .errors import StateError
from .util import atomic_write, pid_alive, remove_file

STATE_VERSION = 1


@dataclass
class RuntimeState:
    version: int = STATE_VERSION
    status: str = "starting"
    ssid: str = ""
    security: str = ""
    ap_interface: str = ""
    uplink: str = ""
    phy: str = ""
    channel: int = 0
    band: str = "2.4"
    ap_ip: str = ""
    network: str = ""
    firewall_backend: str = ""
    ip_forward_previous: str | None = None
    nm_managed_previous: bool | None = None
    nm_unmanaged_configured: bool = False
    created_ap_interface: bool = False
    hostapd_pid: int | None = None
    dnsmasq_pid: int | None = None
    dns_mode: str = "server"
    started_at: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeState:
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})

    @property
    def hostapd_alive(self) -> bool:
        return pid_alive(self.hostapd_pid)

    @property
    def dnsmasq_alive(self) -> bool:
        return pid_alive(self.dnsmasq_pid)

    @property
    def alive(self) -> bool:
        return self.status == "running" and self.hostapd_alive and self.dnsmasq_alive

    def public_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("last_error", None)
        data["hostapd_alive"] = self.hostapd_alive
        data["dnsmasq_alive"] = self.dnsmasq_alive
        data["alive"] = self.alive
        return data


def load_state(path: Path) -> RuntimeState | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"No pude leer el estado del hotspot: {exc}") from exc
    if not isinstance(data, dict):
        raise StateError("El archivo de estado del hotspot no es válido.")
    return RuntimeState.from_dict(data)


def save_state(path: Path, state: RuntimeState) -> None:
    atomic_write(path, json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", mode=0o644)


def remove_state(path: Path) -> None:
    remove_file(path)
