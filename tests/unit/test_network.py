"""Tests for network and NetworkManager helpers."""

from __future__ import annotations

import ipaddress
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from wifi_hotspot.errors import CommandError, StateError
from wifi_hotspot.platform import network


class FakeRun:
    def __init__(self, responses: dict[str, tuple[int, str, str]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[list[str]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        args = [str(part) for part in command]
        self.calls.append(args)
        key = " ".join(args)
        returncode, stdout, stderr = self.responses.get(key, (0, "", ""))
        result = subprocess.CompletedProcess(args, returncode, stdout, stderr)
        if check and returncode != 0:
            raise CommandError("fallo simulado", args, returncode, stderr=stderr)
        return result


def test_list_interfaces_from_ip_json(monkeypatch: pytest.MonkeyPatch) -> None:
    links = [
        {"ifname": "lo", "operstate": "UNKNOWN", "address": "00:00:00:00:00:00", "mtu": 65536},
        {"ifname": "eth0", "operstate": "UP", "address": "aa:bb:cc:dd:ee:ff", "mtu": 1500},
    ]
    addresses = [
        {
            "ifname": "eth0",
            "addr_info": [
                {"family": "inet", "local": "192.168.1.10", "prefixlen": 24},
                {"family": "inet6", "local": "fe80::1", "prefixlen": 64},
            ],
        }
    ]
    runner = FakeRun(
        {
            "ip -j link show": (0, json.dumps(links), ""),
            "ip -j addr show": (0, json.dumps(addresses), ""),
        }
    )
    monkeypatch.setattr(network, "run", runner)
    monkeypatch.setattr(network, "get_carrier", lambda _name: True)

    interfaces = network.list_interfaces()
    assert [interface.name for interface in interfaces] == ["eth0", "lo"]
    eth0 = interfaces[0]
    assert eth0.state == "UP"
    assert eth0.mac == "aa:bb:cc:dd:ee:ff"
    assert eth0.mtu == 1500
    assert eth0.ipv4 == ["192.168.1.10/24"]
    assert eth0.carrier is True


def test_list_interfaces_tolerates_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network, "run", FakeRun({"ip -j link show": (0, "bad", "")}))
    assert network.list_interfaces() == []


def test_get_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network,
        "list_interfaces",
        lambda: [network.InterfaceInfo(name="eth0"), network.InterfaceInfo(name="wlan0")],
    )
    assert network.get_interface("wlan0") is not None
    assert network.get_interface("missing") is None


def test_detect_uplink_uses_route_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network,
        "run",
        FakeRun({"ip -j route get 1.1.1.1": (0, json.dumps([{"dev": "wlan0"}]), "")}),
    )
    assert network.detect_uplink() == "wlan0"


def test_detect_uplink_falls_back_to_text(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeRun(
        {
            "ip -j route get 1.1.1.1": (1, "", "unsupported"),
            "ip route get 1.1.1.1": (0, "1.1.1.1 via 192.168.1.1 dev eth0 src 192.168.1.10\n", ""),
        }
    )
    monkeypatch.setattr(network, "run", runner)
    assert network.detect_uplink() == "eth0"


def test_detect_uplink_without_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network,
        "run",
        FakeRun(
            {
                "ip -j route get 1.1.1.1": (1, "", ""),
                "ip route get 1.1.1.1": (1, "", ""),
            }
        ),
    )
    with pytest.raises(StateError, match="salida a Internet"):
        network.detect_uplink()


def test_get_default_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network,
        "run",
        FakeRun(
            {"ip route show default": (0, "default via 192.168.1.1 dev wlan0 metric 600\n", "")}
        ),
    )
    route = network.get_default_route()
    assert route == {"via": "192.168.1.1", "dev": "wlan0", "metric": "600"}


def test_get_ipv4_networks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network,
        "get_ipv4_addresses",
        lambda _name: ["192.168.1.10/24", "10.0.0.2/8", "invalid"],
    )
    assert network.get_ipv4_networks("eth0") == [
        ipaddress.ip_network("192.168.1.0/24"),
        ipaddress.ip_network("10.0.0.0/8"),
    ]


def test_subnet_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network,
        "get_ipv4_networks",
        lambda _name: [ipaddress.ip_network("10.42.0.0/24")],
    )
    assert network.subnet_overlaps_interface("eth0", ipaddress.ip_network("10.42.0.0/24"))
    assert not network.subnet_overlaps_interface("eth0", ipaddress.ip_network("10.43.0.0/24"))


def test_ip_link_and_address_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeRun()
    monkeypatch.setattr(network, "run", runner)
    network.ip_link_up("wlan0")
    network.ip_addr_flush("wlan0")
    network.ip_addr_add("wlan0", "10.42.0.1/24")
    assert runner.calls == [
        ["ip", "link", "set", "wlan0", "up"],
        ["ip", "addr", "flush", "dev", "wlan0"],
        ["ip", "addr", "add", "10.42.0.1/24", "dev", "wlan0"],
    ]


def test_sysctl_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {"net.ipv4.ip_forward": "0"}
    written: list[tuple[str, str]] = []

    monkeypatch.setattr(network, "sysctl_read", lambda key: values.get(key))
    monkeypatch.setattr(
        network,
        "sysctl_write",
        lambda key, value: (written.append((key, value)), values.__setitem__(key, value)),
    )
    assert network.enable_ip_forward() == "0"
    assert network.enable_ip_forward() == "1"
    assert written == [("net.ipv4.ip_forward", "1")]

    network.restore_ip_forward("0")
    assert written[-1] == ("net.ipv4.ip_forward", "0")


def test_sysctl_write_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network, "run", FakeRun({"sysctl -w net.ipv4.ip_forward=1": (1, "", "denied")})
    )
    with pytest.raises(CommandError, match="ip_forward"):
        network.sysctl_write("net.ipv4.ip_forward", "1")


def test_disable_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {"net.ipv6.conf.wlan0.disable_ipv6": "0"}
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(network, "sysctl_read", lambda key: values.get(key))
    monkeypatch.setattr(
        network,
        "sysctl_write",
        lambda key, value: (written.append((key, value)), values.__setitem__(key, value)),
    )
    assert network.disable_ipv6("wlan0") is True
    assert written == [("net.ipv6.conf.wlan0.disable_ipv6", "1")]

    assert network.disable_ipv6("missing") is False


def test_network_manager_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network, "command_exists", lambda _name: True)
    monkeypatch.setattr(
        network,
        "run",
        FakeRun({"nmcli -t -f RUNNING general": (0, "running\n", "")}),
    )
    assert network.NetworkManager.is_running() is True

    monkeypatch.setattr(network, "command_exists", lambda _name: False)
    assert network.NetworkManager.is_running() is False


def test_network_manager_prepare_and_clear_unmanaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(network, "NM_RUN_CONF_DIR", tmp_path / "nm")
    monkeypatch.setattr(network, "command_exists", lambda _name: True)
    monkeypatch.setattr(network.NetworkManager, "is_running", staticmethod(lambda: True))
    runner = FakeRun()
    monkeypatch.setattr(network, "run", runner)

    manager = network.NetworkManager()
    assert manager.available is True
    assert manager.prepare_unmanaged("wifi-ap") is True

    path = network.NetworkManager.unmanaged_file("wifi-ap")
    assert path == tmp_path / "nm" / "90-wifi-hotspot-wifi-ap.conf"
    assert "unmanaged-devices=interface-name:wifi-ap" in path.read_text(encoding="utf-8")
    assert runner.calls[-1] == ["nmcli", "general", "reload"]

    assert manager.clear_unmanaged("wifi-ap") is True
    assert not path.exists()
    assert manager.clear_unmanaged("wifi-ap") is False


def test_network_manager_prepare_skips_without_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(network, "NM_RUN_CONF_DIR", tmp_path / "nm")
    monkeypatch.setattr(network.NetworkManager, "is_running", staticmethod(lambda: False))
    manager = network.NetworkManager()
    assert manager.available is False
    assert manager.prepare_unmanaged("wifi-ap") is False
    assert not (tmp_path / "nm").exists()


def test_network_manager_prepare_reload_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(network, "NM_RUN_CONF_DIR", tmp_path / "nm")
    monkeypatch.setattr(network, "command_exists", lambda _name: True)
    monkeypatch.setattr(network.NetworkManager, "is_running", staticmethod(lambda: True))
    runner = FakeRun({"nmcli general reload": (1, "", "reload failed")})
    monkeypatch.setattr(network, "run", runner)

    manager = network.NetworkManager()
    with pytest.raises(StateError, match="recargar"):
        manager.prepare_unmanaged("wifi-ap")
    assert not network.NetworkManager.unmanaged_file("wifi-ap").exists()


def test_network_manager_state_and_managed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network,
        "run",
        FakeRun(
            {
                "nmcli -t -f DEVICE,STATE device status": (
                    0,
                    "wlan0:connected\nwifi-ap:unmanaged\n",
                    "",
                )
            }
        ),
    )
    manager = network.NetworkManager.__new__(network.NetworkManager)
    manager.available = True
    assert manager.get_state("wlan0") == "connected"
    assert manager.get_state("missing") is None
    assert manager.was_managed("wlan0") is True
    assert manager.was_managed("wifi-ap") is False
    assert manager.was_managed("missing") is None
