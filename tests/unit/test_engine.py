"""Tests for the high-level engine without touching the network."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wifi_hotspot import engine as engine_module
from wifi_hotspot.config import Paths
from wifi_hotspot.engine import HotspotEngine
from wifi_hotspot.errors import CommandError, StateError
from wifi_hotspot.models import HotspotConfig
from wifi_hotspot.platform import network, wifi
from wifi_hotspot.state import RuntimeState, save_state


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(run_dir=tmp_path / "run", etc_dir=tmp_path / "etc")


def make_engine(paths: Paths, **changes: object) -> HotspotEngine:
    values: dict[str, object] = {"ssid": "CafeNet", "password": "Password123"}
    values.update(changes)
    config = HotspotConfig(**values)
    config.validate()
    return HotspotEngine(config, paths)


def caps(**changes: object) -> wifi.PhyCapabilities:
    values: dict[str, object] = {
        "phy": "phy0",
        "bands": ["2.4", "5"],
        "supports_vht": True,
        "supports_he": True,
        "supports_sae": True,
    }
    values.update(changes)
    return wifi.PhyCapabilities(**values)


def test_status_without_state(paths: Paths) -> None:
    data = make_engine(paths).status()
    assert data["status"] == "stopped"
    assert data["state_file"] == str(paths.state_file)


def test_summary_without_state(paths: Paths) -> None:
    assert make_engine(paths).summary() == {"status": "stopped"}


def test_status_marks_stale_state(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    paths.ensure_runtime()
    save_state(
        paths.state_file,
        RuntimeState(
            status="running",
            ssid="CafeNet",
            ap_interface="wlan-ap",
            uplink="eth0",
            ap_ip="10.42.0.1",
            network="10.42.0.0/24",
            hostapd_pid=None,
            dnsmasq_pid=None,
        ),
    )
    monkeypatch.setattr(wifi, "interface_exists", lambda _name: False)
    monkeypatch.setattr(engine_module.network, "get_default_route", lambda: {"dev": "eth0"})

    data = make_engine(paths).status()
    assert data["status"] == "stale"
    assert data["ap_interface_present"] is False
    assert data["clients"] == []


def test_clients_merge_stations_and_leases(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    paths.ensure_runtime()
    save_state(
        paths.state_file,
        RuntimeState(status="running", ap_interface="wlan-ap", hostapd_pid=1, dnsmasq_pid=2),
    )
    monkeypatch.setattr(wifi, "interface_exists", lambda _name: True)
    monkeypatch.setattr(
        wifi,
        "get_stations",
        lambda _name: [
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "signal_dbm": -45,
                "rx_bytes": 1000,
                "tx_bytes": 2000,
                "connected_seconds": 60,
                "inactive_ms": 10,
            },
            {
                "mac": "11:22:33:44:55:66",
                "signal_dbm": -70,
                "rx_bytes": 10,
                "tx_bytes": 20,
                "connected_seconds": 5,
                "inactive_ms": 20,
            },
        ],
    )
    monkeypatch.setattr(
        engine_module.dnsmasq,
        "parse_leases",
        lambda _path: {
            "aa:bb:cc:dd:ee:ff": {
                "ip": "10.42.0.20",
                "hostname": "phone",
                "mac": "aa:bb:cc:dd:ee:ff",
            }
        },
    )

    clients = make_engine(paths).clients()
    assert [client["mac"] for client in clients] == [
        "aa:bb:cc:dd:ee:ff",
        "11:22:33:44:55:66",
    ]
    assert clients[0]["ip"] == "10.42.0.20"
    assert clients[0]["hostname"] == "phone"
    assert clients[1]["ip"] == ""
    assert clients[1]["hostname"] == ""


def test_disconnect_client(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    paths.ensure_runtime()
    pid = os.getpid()
    save_state(
        paths.state_file,
        RuntimeState(
            status="running",
            ap_interface="wlan-ap",
            hostapd_pid=pid,
            dnsmasq_pid=pid,
        ),
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(engine_module, "require_root", lambda: None)
    monkeypatch.setattr(
        engine_module.wifi,
        "disconnect_station",
        lambda interface, mac: calls.append((interface, mac)),
    )

    make_engine(paths).disconnect_client("AA:BB:CC:DD:EE:FF")
    assert calls == [("wlan-ap", "aa:bb:cc:dd:ee:ff")]


def test_disconnect_without_hotspot(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "require_root", lambda: None)
    with pytest.raises(StateError, match="no está activo"):
        make_engine(paths).disconnect_client("aa:bb:cc:dd:ee:ff")


def test_dnsmasq_port_conflict_detection() -> None:
    conflict = CommandError(
        "dnsmasq failed",
        ["dnsmasq"],
        stderr="failed to create listening socket for port 53: Address already in use",
    )
    assert HotspotEngine._dnsmasq_port_conflict(conflict) is True

    other = CommandError("dnsmasq failed", ["dnsmasq"], stderr="unknown option")
    assert HotspotEngine._dnsmasq_port_conflict(other) is False


def test_select_uplink_from_config(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths, uplink="eth0")
    monkeypatch.setattr(network, "interface_exists", lambda _name: True)
    monkeypatch.setattr(network, "get_carrier", lambda _name: True)
    assert engine._select_uplink() == "eth0"


def test_select_uplink_detects_route(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(network, "detect_uplink", lambda: "wlan0")
    monkeypatch.setattr(network, "interface_exists", lambda _name: True)
    monkeypatch.setattr(network, "get_carrier", lambda _name: None)
    assert engine._select_uplink() == "wlan0"


def test_select_uplink_missing_interface(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths, uplink="eth9")
    monkeypatch.setattr(network, "interface_exists", lambda _name: False)
    with pytest.raises(StateError, match="no existe"):
        engine._select_uplink()


def test_select_uplink_without_carrier(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths, uplink="eth0")
    monkeypatch.setattr(network, "interface_exists", lambda _name: True)
    monkeypatch.setattr(network, "get_carrier", lambda _name: False)
    with pytest.raises(StateError, match="portadora"):
        engine._select_uplink()


def test_select_uplink_cannot_be_loopback(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths, uplink="lo")
    monkeypatch.setattr(network, "interface_exists", lambda _name: True)
    monkeypatch.setattr(network, "get_carrier", lambda _name: True)
    with pytest.raises(StateError, match="loopback"):
        engine._select_uplink()


def test_select_ap_radio_reuses_uplink_wifi(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(
        wifi,
        "list_interfaces",
        lambda: [wifi.WifiInterface(name="wlan0", phy="phy0", type="managed")],
    )
    monkeypatch.setattr(wifi, "get_link", lambda _name: {"ssid": "Upstream"})
    assert engine._select_ap_radio("wlan0") == ("phy0", "wlan0", True)


def test_select_ap_radio_requires_connected_uplink_wifi(
    monkeypatch: pytest.MonkeyPatch, paths: Paths
) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(
        wifi,
        "list_interfaces",
        lambda: [wifi.WifiInterface(name="wlan0", phy="phy0", type="managed")],
    )
    monkeypatch.setattr(wifi, "get_link", lambda _name: None)
    with pytest.raises(StateError, match="no está conectada"):
        engine._select_ap_radio("wlan0")


def test_select_ap_radio_uses_second_wifi(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(
        wifi,
        "list_interfaces",
        lambda: [wifi.WifiInterface(name="wlan0", phy="phy0", type="managed")],
    )
    assert engine._select_ap_radio("eth0") == ("phy0", "wlan0", False)


def test_select_ap_radio_without_wifi(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(wifi, "list_interfaces", lambda: [])
    with pytest.raises(StateError, match="No encontré"):
        engine._select_ap_radio("eth0")


def test_configure_country(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths, country="ES")
    domains = iter(["00", "ES"])
    calls: list[str] = []
    monkeypatch.setattr(wifi, "get_reg_domain", lambda: next(domains))
    monkeypatch.setattr(wifi, "set_reg_domain", lambda country: calls.append(country))

    assert engine._configure_country() == "ES"
    assert calls == ["ES"]


def test_configure_country_when_already_set(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths, country="ES")
    monkeypatch.setattr(wifi, "get_reg_domain", lambda: "ES")
    monkeypatch.setattr(
        wifi, "set_reg_domain", lambda _country: pytest.fail("should not be called")
    )
    assert engine._configure_country() == "ES"


def test_select_channel_dedicated_auto_five_ghz(
    monkeypatch: pytest.MonkeyPatch, paths: Paths
) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(wifi, "default_channel", lambda _phy, _band, _country: 36)
    monkeypatch.setattr(wifi, "channel_allowed", lambda _phy, _channel: (True, "permitido"))

    channel, band = engine._select_channel(
        uplink="eth0",
        ap_phy="phy0",
        capabilities=caps(),
        same_phy=False,
        country="ES",
    )
    assert (channel, band) == (36, "5")


def test_select_channel_dedicated_auto_without_country(
    monkeypatch: pytest.MonkeyPatch, paths: Paths
) -> None:
    engine = make_engine(paths)
    seen: list[str] = []

    def default_channel(_phy: str, band: str, _country: str) -> int:
        seen.append(band)
        return 6

    monkeypatch.setattr(wifi, "default_channel", default_channel)
    monkeypatch.setattr(wifi, "channel_allowed", lambda _phy, _channel: (True, "permitido"))

    channel, band = engine._select_channel(
        uplink="eth0",
        ap_phy="phy0",
        capabilities=caps(),
        same_phy=False,
        country="00",
    )
    assert (channel, band) == (6, "2.4")
    assert seen == ["2.4"]


def test_select_channel_five_ghz_requires_country(paths: Paths) -> None:
    engine = make_engine(paths, band="5")
    with pytest.raises(StateError, match="país"):
        engine._select_channel(
            uplink="eth0",
            ap_phy="phy0",
            capabilities=caps(),
            same_phy=False,
            country="00",
        )


def test_select_channel_five_ghz_requires_capability(paths: Paths) -> None:
    engine = make_engine(paths, band="5")
    with pytest.raises(StateError, match="no soporta 5 GHz"):
        engine._select_channel(
            uplink="eth0",
            ap_phy="phy0",
            capabilities=caps(bands=["2.4"]),
            same_phy=False,
            country="ES",
        )


def test_select_channel_band_mismatch(paths: Paths) -> None:
    # Bypass validate() intentionally: the engine must also defend against
    # inconsistent configurations loaded from an old profile.
    config = HotspotConfig(ssid="CafeNet", password="Password123", band="2.4", channel=36)
    engine = HotspotEngine(config, paths)
    with pytest.raises(StateError, match="no de 2.4 GHz"):
        engine._select_channel(
            uplink="eth0",
            ap_phy="phy0",
            capabilities=caps(),
            same_phy=False,
            country="ES",
        )


def test_select_channel_rejects_unavailable(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths, band="2.4", channel=3)
    monkeypatch.setattr(wifi, "channel_allowed", lambda _phy, _channel: (False, "deshabilitado"))
    monkeypatch.setattr(wifi, "available_channels", lambda _phy, _band: [1, 6, 11])

    with pytest.raises(StateError, match="Canales disponibles"):
        engine._select_channel(
            uplink="eth0",
            ap_phy="phy0",
            capabilities=caps(),
            same_phy=False,
            country="ES",
        )


def test_select_channel_same_phy_uses_station_channel(
    monkeypatch: pytest.MonkeyPatch, paths: Paths
) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(wifi, "get_station_channel", lambda _name: (6, 2437))
    monkeypatch.setattr(wifi, "channel_allowed", lambda _phy, _channel: (True, "permitido"))

    result = engine._select_channel(
        uplink="wlan0",
        ap_phy="phy0",
        capabilities=caps(),
        same_phy=True,
        country="ES",
    )
    assert result == (6, "2.4")


def test_select_channel_same_phy_rejects_forced_channel(
    monkeypatch: pytest.MonkeyPatch, paths: Paths
) -> None:
    engine = make_engine(paths, channel=36)
    monkeypatch.setattr(wifi, "get_station_channel", lambda _name: (6, 2437))
    with pytest.raises(StateError, match="mismo canal"):
        engine._select_channel(
            uplink="wlan0",
            ap_phy="phy0",
            capabilities=caps(),
            same_phy=True,
            country="ES",
        )


def test_select_channel_same_phy_rejects_band(
    monkeypatch: pytest.MonkeyPatch, paths: Paths
) -> None:
    engine = make_engine(paths, band="5")
    monkeypatch.setattr(wifi, "get_station_channel", lambda _name: (6, 2437))
    with pytest.raises(StateError, match="no puedes forzar"):
        engine._select_channel(
            uplink="wlan0",
            ap_phy="phy0",
            capabilities=caps(),
            same_phy=True,
            country="ES",
        )


def test_select_channel_same_phy_without_channel(
    monkeypatch: pytest.MonkeyPatch, paths: Paths
) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(wifi, "get_station_channel", lambda _name: (None, None))
    with pytest.raises(StateError, match="No pude detectar"):
        engine._select_channel(
            uplink="wlan0",
            ap_phy="phy0",
            capabilities=caps(),
            same_phy=True,
            country="ES",
        )


def test_validate_security_requires_sae(paths: Paths) -> None:
    engine = make_engine(paths, security="wpa3")
    with pytest.raises(StateError, match="SAE"):
        engine._validate_security(caps(supports_sae=False), 36)


def test_validate_security_accepts_wpa3(paths: Paths) -> None:
    make_engine(paths, security="wpa3")._validate_security(caps(), 36)


def test_subnet_overlap_is_rejected(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(network, "subnet_overlaps_interface", lambda _name, _net: True)
    with pytest.raises(StateError, match="solapa"):
        engine._check_subnet_overlap("eth0")


def test_existing_ap_interface_is_rejected(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(wifi, "interface_exists", lambda _name: True)
    with pytest.raises(StateError, match="ya existe"):
        engine._ensure_ap_interface_available()


def test_ap_interface_is_accepted(monkeypatch: pytest.MonkeyPatch, paths: Paths) -> None:
    engine = make_engine(paths)
    monkeypatch.setattr(wifi, "interface_exists", lambda _name: False)
    engine._ensure_ap_interface_available()


def test_format_client() -> None:
    text = HotspotEngine.format_client(
        {
            "hostname": "phone",
            "ip": "10.42.0.20",
            "mac": "aa:bb:cc:dd:ee:ff",
            "signal_dbm": -45,
            "rx_bytes": 1024,
            "tx_bytes": 2048,
            "connected_seconds": 60,
        }
    )
    assert "phone" in text
    assert "10.42.0.20" in text
    assert "-45 dBm" in text
    assert "RX" in text
    assert "1m 0s" in text


def test_start_prepares_networkmanager_before_interface(
    paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(paths)
    events: list[tuple[str, str]] = []

    class FakeNetworkManager:
        available = True

        def prepare_unmanaged(self, interface: str) -> bool:
            events.append(("nm", interface))
            return True

        def clear_unmanaged(self, interface: str) -> bool:
            events.append(("nm-clear", interface))
            return True

    class FakeBackend:
        name = "iptables"

        def setup(self, context: object) -> None:
            events.append(("firewall", "setup"))

        def teardown(self, context: object) -> None:
            events.append(("firewall", "teardown"))

    monkeypatch.setattr(engine, "_select_uplink", lambda: "eth0")
    monkeypatch.setattr(engine, "_select_ap_radio", lambda _uplink: ("phy0", "wlan0", False))
    monkeypatch.setattr(engine, "_configure_country", lambda: "ES")
    monkeypatch.setattr(engine, "_select_channel", lambda **_kwargs: (36, "5"))
    monkeypatch.setattr(engine, "_validate_security", lambda *_args: None)
    monkeypatch.setattr(engine, "_check_subnet_overlap", lambda *_args: None)
    monkeypatch.setattr(engine, "_ensure_ap_interface_available", lambda: None)
    monkeypatch.setattr(engine_module.wifi, "get_capabilities", lambda _phy: caps())
    monkeypatch.setattr(
        engine_module.wifi,
        "create_ap_interface",
        lambda phy, name, base_interface=None: events.append(("create", name)),
    )
    monkeypatch.setattr(engine_module.network, "NetworkManager", FakeNetworkManager)
    monkeypatch.setattr(
        engine_module.network, "ip_addr_flush", lambda _name: events.append(("flush", _name))
    )
    monkeypatch.setattr(
        engine_module.network,
        "ip_addr_add",
        lambda name, address: events.append(("addr", f"{name} {address}")),
    )
    monkeypatch.setattr(
        engine_module.network, "ip_link_up", lambda name: events.append(("up", name))
    )
    monkeypatch.setattr(engine_module.network, "disable_ipv6", lambda _name: True)
    monkeypatch.setattr(engine_module.network, "enable_ip_forward", lambda: "1")
    monkeypatch.setattr(engine_module.firewall, "detect_backend", lambda _preference: FakeBackend())

    hostapd_started: dict[str, int] = {}

    def start_hostapd(conf: Path, pid: Path, log: Path) -> int:
        hostapd_started["pid"] = 111
        return 111

    def start_dnsmasq(conf: Path, pid: Path, log: Path) -> int:
        return 222

    monkeypatch.setattr(engine_module.hostapd, "start", start_hostapd)
    monkeypatch.setattr(engine_module.dnsmasq, "start", start_dnsmasq)

    state = engine._start_locked()

    assert state.status == "running"
    assert state.hostapd_pid == 111
    assert state.dnsmasq_pid == 222
    assert state.firewall_backend == "iptables"
    assert events.index(("nm", "wifi-ap")) < events.index(("create", "wifi-ap"))
    assert events.index(("create", "wifi-ap")) < events.index(("up", "wifi-ap"))
    assert hostapd_started == {"pid": 111}
    assert paths.state_file.exists()
