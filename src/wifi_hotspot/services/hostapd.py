"""Hostapd configuration and lifecycle."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ..models import HotspotConfig
from ..platform.wifi import PhyCapabilities, channel_band, channel_frequency
from ..util import atomic_write, ensure_dir, remove_file
from . import process


@dataclass(frozen=True)
class HostapdPaths:
    config: Path
    pid: Path
    log: Path
    control_dir: Path


def hw_mode(channel: int) -> str:
    return "g" if channel_band(channel) == "2.4" else "a"


def build_config(
    config: HotspotConfig,
    *,
    ap_interface: str,
    channel: int,
    capabilities: PhyCapabilities,
    control_dir: Path,
) -> str:
    band = channel_band(channel)
    lines = [
        f"interface={ap_interface}",
        "driver=nl80211",
        f"ctrl_interface={control_dir}",
        "ctrl_interface_group=0",
        f"ssid={config.ssid}",
        "utf8_ssid=1",
    ]
    if config.country:
        lines.extend([f"country_code={config.country}", "ieee80211d=1"])
    lines.extend(
        [
            f"hw_mode={hw_mode(channel)}",
            f"channel={channel}",
            "ieee80211n=1",
            "wmm_enabled=1",
            "auth_algs=1",
        ]
    )
    if band == "5" and capabilities.supports_vht:
        lines.extend(["ieee80211ac=1", "vht_oper_chwidth=0"])
    if capabilities.supports_he:
        lines.append("ieee80211ax=1")
    if config.hidden:
        lines.append("ignore_broadcast_ssid=1")
    if config.max_clients > 0:
        lines.append(f"max_num_sta={config.max_clients}")
    if config.client_isolation:
        lines.append("ap_isolate=1")

    if config.security == "wpa2":
        lines.extend(
            [
                "wpa=2",
                "wpa_key_mgmt=WPA-PSK",
                "rsn_pairwise=CCMP",
                f"wpa_passphrase={config.password}",
            ]
        )
    elif config.security == "wpa3":
        lines.extend(
            [
                "wpa=2",
                "wpa_key_mgmt=SAE",
                "rsn_pairwise=CCMP",
                "ieee80211w=2",
                "sae_require_mfp=1",
                f"wpa_passphrase={config.password}",
            ]
        )
    elif config.security == "mixed":
        lines.extend(
            [
                "wpa=2",
                "wpa_key_mgmt=WPA-PSK SAE",
                "rsn_pairwise=CCMP",
                "ieee80211w=1",
                f"wpa_passphrase={config.password}",
            ]
        )
    elif config.security == "open":
        lines.append("wpa=0")
    else:
        raise ValueError(f"Modo de seguridad no soportado: {config.security}")

    return "\n".join(lines) + "\n"


def write_config(path: Path, content: str) -> None:
    atomic_write(path, content, mode=0o600)


def prepare_control_dir(path: Path) -> None:
    ensure_dir(path, 0o700)


def start(conf: Path, pid: Path, log: Path) -> int:
    return process.start_foreground(
        ["hostapd", str(conf)],
        name="hostapd",
        pid_file=pid,
        log_file=log,
        startup_delay=1.5,
    )


def stop(pid: Path, conf: Path | None = None) -> bool:
    return process.stop_foreground(
        name="hostapd",
        binary="hostapd",
        pid_file=pid,
        config_path=conf,
    )


def cleanup(paths: HostapdPaths) -> None:
    stop(paths.pid, paths.config)
    remove_file(paths.config)
    remove_file(paths.pid)
    remove_file(paths.log)
    if paths.control_dir.exists():
        shutil.rmtree(paths.control_dir, ignore_errors=True)


def frequency(channel: int) -> int:
    return channel_frequency(channel)
