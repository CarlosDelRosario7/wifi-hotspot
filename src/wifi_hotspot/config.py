"""Paths, profiles, and persistent configuration."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ValidationError
from .models import HotspotConfig
from .util import PROFILE_NAME_RE, ensure_dir, random_password, read_json, remove_file, write_json

OVERRIDE_FIELDS = (
    "ssid",
    "password",
    "band",
    "channel",
    "security",
    "country",
    "hidden",
    "max_clients",
    "client_isolation",
    "ipv6",
    "subnet",
    "dhcp_start",
    "dhcp_end",
    "lease_time",
    "uplink",
    "ap_interface",
    "firewall_backend",
)


@dataclass(frozen=True)
class Paths:
    """Filesystem locations used by the engine.

    Tests can replace these with a temporary directory. The directory containing
    the state file is world-traversable (0755) so that `status` works without
    root; everything containing secrets is private (0600/0700).
    """

    run_dir: Path = Path("/run/wifi-hotspot")
    etc_dir: Path = Path("/etc/wifi-hotspot")
    profiles_dir: Path = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.profiles_dir is None:
            object.__setattr__(self, "profiles_dir", self.etc_dir / "profiles")

    @property
    def state_file(self) -> Path:
        return self.run_dir / "state.json"

    @property
    def lock_file(self) -> Path:
        return self.run_dir / "lock"

    @property
    def hostapd_conf(self) -> Path:
        return self.run_dir / "hostapd.conf"

    @property
    def dnsmasq_conf(self) -> Path:
        return self.run_dir / "dnsmasq.conf"

    @property
    def hostapd_pid(self) -> Path:
        return self.run_dir / "hostapd.pid"

    @property
    def dnsmasq_pid(self) -> Path:
        return self.run_dir / "dnsmasq.pid"

    @property
    def hostapd_log(self) -> Path:
        return self.run_dir / "hostapd.log"

    @property
    def dnsmasq_log(self) -> Path:
        return self.run_dir / "dnsmasq.log"

    @property
    def dnsmasq_leases(self) -> Path:
        return self.run_dir / "dnsmasq.leases"

    @property
    def daemon_socket(self) -> Path:
        return self.run_dir / "daemon.sock"

    @property
    def daemon_pid(self) -> Path:
        return self.run_dir / "daemon.pid"

    @property
    def daemon_log(self) -> Path:
        return self.run_dir / "daemon.log"

    @property
    def hostapd_ctrl(self) -> Path:
        return self.run_dir / "hostapd"

    def ensure_runtime(self) -> None:
        ensure_dir(self.run_dir, 0o755)

    def ensure_secrets(self) -> None:
        ensure_dir(self.run_dir, 0o755)
        ensure_dir(self.etc_dir, 0o700)
        ensure_dir(self.profiles_dir, 0o700)

    def cleanup_runtime_files(self) -> None:
        for path in (
            self.hostapd_conf,
            self.dnsmasq_conf,
            self.hostapd_pid,
            self.dnsmasq_pid,
            self.hostapd_log,
            self.dnsmasq_log,
            self.dnsmasq_leases,
        ):
            remove_file(path)
        if self.hostapd_ctrl.exists():
            shutil.rmtree(self.hostapd_ctrl, ignore_errors=True)


def _profile_path(paths: Paths, name: str) -> Path:
    if not PROFILE_NAME_RE.match(name):
        raise ValidationError(f"Nombre de perfil inválido: {name!r}")
    return paths.profiles_dir / f"{name}.json"


def load_profile(paths: Paths, name: str = "default") -> HotspotConfig | None:
    path = _profile_path(paths, name)
    if not path.exists():
        return None
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"No pude leer el perfil {name!r}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"El perfil {name!r} no contiene un objeto JSON válido.")
    data.setdefault("profile_name", name)
    config = HotspotConfig.from_dict(data)
    config.validate()
    return config


def save_profile(paths: Paths, config: HotspotConfig) -> Path:
    config.validate()
    paths.ensure_secrets()
    path = _profile_path(paths, config.profile_name)
    write_json(path, config.to_dict(include_password=True), mode=0o600)
    return path


def delete_profile(paths: Paths, name: str) -> bool:
    path = _profile_path(paths, name)
    if not path.exists():
        return False
    path.unlink()
    return True


def list_profiles(paths: Paths) -> list[str]:
    if not paths.profiles_dir.exists():
        return []
    profiles: list[str] = []
    for path in sorted(paths.profiles_dir.glob("*.json")):
        if PROFILE_NAME_RE.match(path.stem):
            profiles.append(path.stem)
    return profiles


def default_profile(paths: Paths) -> HotspotConfig:
    loaded = load_profile(paths, "default")
    if loaded is not None:
        return loaded
    return HotspotConfig()


def resolve_config(
    paths: Paths,
    profile: str = "default",
    overrides: dict[str, object] | None = None,
    *,
    generate_password: bool = False,
) -> HotspotConfig:
    """Load a profile and apply non-None overrides from a client.

    This is shared by the direct CLI and the privileged daemon so both resolve
    arguments identically. Overrides are deliberately restricted to fields that
    are safe to change at start time.
    """
    stored = load_profile(paths, profile) if profile else None
    config = stored.copy() if stored is not None else HotspotConfig()
    config.profile_name = profile or "default"
    values = overrides or {}
    for attribute in OVERRIDE_FIELDS:
        value = values.get(attribute)
        if value is not None:
            setattr(config, attribute, value)
    if values.get("dns") is not None:
        dns = values.get("dns")
        if isinstance(dns, (list, tuple)):
            config.dns = [str(server) for server in dns]
        else:
            raise ValidationError("El campo DNS debe ser una lista de direcciones IP.")
    if generate_password:
        config.password = random_password()
    if config.security == "open":
        config.password = ""
    config.validate()
    return config
