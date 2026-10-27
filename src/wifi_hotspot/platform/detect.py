"""Distribution and system detection."""

from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..util import command_exists, read_text


@dataclass(frozen=True)
class DistroInfo:
    id: str
    id_like: tuple[str, ...]
    name: str
    version: str
    family: str
    package_manager: str
    init_system: str
    selinux: str
    apparmor: str
    kernel: str

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "id_like": " ".join(self.id_like),
            "name": self.name,
            "version": self.version,
            "family": self.family,
            "package_manager": self.package_manager,
            "init_system": self.init_system,
            "selinux": self.selinux,
            "apparmor": self.apparmor,
            "kernel": self.kernel,
        }


def _parse_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.exists():
        return {}
    data: dict[str, str] = {}
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        data[key] = value
    return data


def _family(os_id: str, id_like: tuple[str, ...]) -> str:
    values = {os_id, *id_like}
    if values & {"debian", "ubuntu", "kali", "linuxmint", "pop", "raspbian"}:
        return "debian"
    if values & {"fedora", "rhel", "centos", "nobara", "rocky", "almalinux"}:
        return "fedora"
    if values & {"arch", "manjaro", "endeavouros", "garuda"}:
        return "arch"
    if values & {"opensuse", "opensuse-tumbleweed", "opensuse-leap", "suse", "sles"}:
        return "suse"
    return "unknown"


def _package_manager(family: str) -> str:
    return {
        "debian": "apt",
        "fedora": "dnf",
        "arch": "pacman",
        "suse": "zypper",
    }.get(family, "unknown")


def _selinux_status() -> str:
    if not Path("/sys/fs/selinux").exists():
        return "disabled"
    try:
        return read_text(Path("/sys/fs/selinux/enforce")).strip()
    except OSError:
        return "unknown"


def _apparmor_status() -> str:
    path = Path("/sys/module/apparmor/parameters/enabled")
    if not path.exists():
        return "disabled"
    try:
        return "enabled" if read_text(path).strip().lower().startswith("y") else "disabled"
    except OSError:
        return "unknown"


def _init_system() -> str:
    if Path("/run/systemd/system").exists():
        return "systemd"
    if command_exists("rc-service"):
        return "openrc"
    if Path("/sbin/init").exists():
        try:
            init = Path("/sbin/init").resolve().name
        except OSError:
            init = ""
        if "systemd" in init:
            return "systemd"
        if "openrc" in init:
            return "openrc"
    return "unknown"


def detect_distro() -> DistroInfo:
    data = _parse_os_release()
    os_id = data.get("ID", "unknown").lower()
    id_like = tuple(
        part.strip().lower() for part in data.get("ID_LIKE", "").split() if part.strip()
    )
    family = _family(os_id, id_like)
    return DistroInfo(
        id=os_id,
        id_like=id_like,
        name=data.get("PRETTY_NAME") or data.get("NAME") or platform.system(),
        version=data.get("VERSION_ID", ""),
        family=family,
        package_manager=_package_manager(family),
        init_system=_init_system(),
        selinux=_selinux_status(),
        apparmor=_apparmor_status(),
        kernel=platform.release(),
    )


def command_path(name: str) -> str | None:
    return shutil.which(name)
