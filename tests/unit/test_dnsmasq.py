"""Tests for dnsmasq configuration and lease parsing."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from wifi_hotspot.models import HotspotConfig
from wifi_hotspot.services import dnsmasq


def config(**changes: object) -> HotspotConfig:
    values: dict[str, object] = {"ssid": "CafeNet", "password": "Password123"}
    values.update(changes)
    return HotspotConfig(**values)


def build(cfg: HotspotConfig, mode: str = "server") -> str:
    return dnsmasq.build_config(
        cfg,
        ap_interface="wlan-ap",
        lease_file=Path("/run/wifi-hotspot/dnsmasq.leases"),
        dns_mode=mode,
    )


def test_server_configuration() -> None:
    text = build(config())
    assert "interface=wlan-ap" in text
    assert "bind-dynamic" in text
    assert "dhcp-range=10.42.0.10,10.42.0.100,255.255.255.0,12h" in text
    assert "dhcp-option=3,10.42.0.1" in text
    assert "dhcp-option=6,10.42.0.1" in text
    assert "dhcp-authoritative" in text
    assert "no-resolv" in text
    assert "server=1.1.1.1" in text
    assert "server=8.8.8.8" in text
    assert "port=0" not in text
    assert "dhcp-leasefile=/run/wifi-hotspot/dnsmasq.leases" in text


def test_custom_dns_and_range() -> None:
    cfg = config(
        dns=["9.9.9.9", "149.112.112.112"],
        dhcp_start="10.42.0.50",
        dhcp_end="10.42.0.60",
        lease_time="1h",
    )
    text = build(cfg)
    assert "server=9.9.9.9" in text
    assert "server=149.112.112.112" in text
    assert "server=1.1.1.1" not in text
    assert "dhcp-range=10.42.0.50,10.42.0.60,255.255.255.0,1h" in text


def test_upstream_mode_disables_dns_and_uses_upstream_resolvers() -> None:
    text = build(config(dns=["9.9.9.9"]), mode="upstream")
    assert "port=0" in text
    assert "no-resolv" not in text
    assert "server=9.9.9.9" not in text
    assert "dhcp-option=6,9.9.9.9" in text
    assert "dhcp-option=3,10.42.0.1" in text


def test_upstream_mode_without_custom_dns_uses_default_resolvers() -> None:
    text = build(config(dns=[]), mode="upstream")
    assert "port=0" in text
    assert "dhcp-option=6,1.1.1.1,8.8.8.8" in text


def test_invalid_mode_raises() -> None:
    with pytest.raises(ValueError, match="DNS"):
        build(config(), mode="invalid")


def test_ipv6_dns_servers_are_filtered() -> None:
    assert dnsmasq.dns_servers(config(dns=["1.1.1.1", "2001:4860:4860::8888"])) == ["1.1.1.1"]


def test_parse_leases(tmp_path: Path) -> None:
    path = tmp_path / "dnsmasq.leases"
    path.write_text(
        "1700000000 aa:bb:cc:dd:ee:ff 10.42.0.10 phone *\n"
        "1700000001 11:22:33:44:55:66 10.42.0.11 laptop 01:11:22:33:44:55:66\n"
        "garbage line\n",
        encoding="utf-8",
    )
    leases = dnsmasq.parse_leases(path)
    assert set(leases) == {"aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"}
    assert leases["aa:bb:cc:dd:ee:ff"]["ip"] == "10.42.0.10"
    assert leases["aa:bb:cc:dd:ee:ff"]["hostname"] == "phone"
    assert leases["aa:bb:cc:dd:ee:ff"]["client_id"] == "*"
    assert leases["11:22:33:44:55:66"]["hostname"] == "laptop"
    assert leases["11:22:33:44:55:66"]["client_id"] == "01:11:22:33:44:55:66"


def test_parse_missing_leases_file(tmp_path: Path) -> None:
    assert dnsmasq.parse_leases(tmp_path / "missing") == {}


def test_write_config_permissions(tmp_path: Path) -> None:
    path = tmp_path / "dnsmasq.conf"
    dnsmasq.write_config(path, "port=0\n")
    assert path.read_text(encoding="utf-8") == "port=0\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_start_uses_equals_form_for_conf_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_start(command, *, name: str, pid_file: Path, log_file: Path, startup_delay: float):
        captured["command"] = command
        captured["name"] = name
        return 123

    monkeypatch.setattr(dnsmasq.process, "start_foreground", fake_start)
    conf = tmp_path / "dnsmasq.conf"
    pid = tmp_path / "dnsmasq.pid"
    log = tmp_path / "dnsmasq.log"
    assert dnsmasq.start(conf, pid, log) == 123
    assert captured["command"] == [
        "dnsmasq",
        f"--conf-file={conf}",
        "--keep-in-foreground",
        "--log-facility=-",
    ]
