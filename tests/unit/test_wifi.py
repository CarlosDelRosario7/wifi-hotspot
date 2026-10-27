"""Tests for iw output parsing and channel helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

import pytest

from wifi_hotspot.errors import CommandError, StateError
from wifi_hotspot.platform import wifi

IW_DEV_OUTPUT = """phy#0
\tInterface wlan0
\t\tifindex 3
\t\twiphy 0
\t\tchannel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz
\t\taddr aa:bb:cc:dd:ee:ff
\t\tssid MyNetwork
\t\ttype managed
phy#1
\tInterface wlan1
\t\tifindex 4
\t\twiphy 1
\t\taddr 11:22:33:44:55:66
\t\ttype AP
"""

PHY_INFO = """Wiphy phy0
\tBand 1:
\t\tFrequencies:
\t\t\t* 2412 MHz [1] (20.0 dBm)
\t\t\t* 2417 MHz [2] (20.0 dBm) (no IR)
\t\t\t* 2422 MHz [3] (20.0 dBm) (disabled)
\tBand 2:
\t\tFrequencies:
\t\t\t* 5180 MHz [36] (23.0 dBm)
\t\t\t* 5200 MHz [40] (23.0 dBm) (radar detection)
\tvalid interface combinations:
\t\t * #{ managed } <= 1, #{ AP, P2P-client } <= 1, total <= 2, #channels <= 1
\t\t * #{ managed } <= 2, total <= 2, #channels <= 2
"""

STATION_DUMP = """Station aa:bb:cc:dd:ee:ff (on wlan-ap)
\tinactive time:\t1234 ms
\trx bytes:\t1000
\ttx bytes:\t2000
\tsignal:  \t-45 [-50, -55] dBm
\tconnected time:\t3600 seconds

Station 11:22:33:44:55:66 (on wlan-ap)
\tinactive time:\t10 ms
\trx bytes:\t500
\ttx bytes:\t600
\tsignal:  \t-70 [-75, -80] dBm
\tconnected time:\t120 seconds
"""


class FakeRun:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        args = [str(item) for item in command]
        self.calls.append(args)
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def test_list_interfaces_parses_iw_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "run", FakeRun(stdout=IW_DEV_OUTPUT))

    interfaces = wifi.list_interfaces()
    assert [interface.name for interface in interfaces] == ["wlan0", "wlan1"]
    first = interfaces[0]
    assert first.phy == "phy0"
    assert first.channel == 36
    assert first.frequency == 5180
    assert first.width == 80
    assert first.mac == "aa:bb:cc:dd:ee:ff"
    assert first.ssid == "MyNetwork"
    assert first.type == "managed"
    assert interfaces[1].type == "AP"


def test_list_interfaces_without_iw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: False)
    with pytest.raises(CommandError, match="'iw'"):
        wifi.list_interfaces()


def test_list_interfaces_handles_no_wireless_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(
        wifi,
        "run",
        FakeRun(returncode=1, stderr="nl80211 not found."),
    )
    assert wifi.list_interfaces() == []


def test_list_interfaces_raises_on_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "run", FakeRun(returncode=1, stderr="permission denied"))
    with pytest.raises(CommandError, match="No pude listar"):
        wifi.list_interfaces()


def test_get_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "run", FakeRun(stdout=IW_DEV_OUTPUT))
    assert wifi.get_interface("wlan1") is not None
    assert wifi.get_interface("missing") is None


def test_interface_exists_reads_sysfs() -> None:
    # /sys is hard-coded; exercise the real interfaces instead.
    assert wifi.interface_exists("lo") is True
    assert wifi.interface_exists("definitely-not-an-interface") is False


def test_get_phy_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    runner = FakeRun(stdout=PHY_INFO)
    monkeypatch.setattr(wifi, "run", runner)
    assert wifi.get_phy_info("phy0") == PHY_INFO
    assert runner.calls == [["iw", "phy", "phy0", "info"]]


def test_get_phy_info_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "run", FakeRun(returncode=1, stderr="not found"))
    with pytest.raises(CommandError, match="capacidades"):
        wifi.get_phy_info("phy9")


def test_combinations_parser() -> None:
    combinations = wifi._combinations_from_info(PHY_INFO)
    assert len(combinations) == 2
    assert "managed" in combinations[0]
    assert "AP" in combinations[0]


def test_get_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "get_phy_info", lambda _phy: PHY_INFO)
    caps = wifi.get_capabilities("phy0")
    assert caps.bands == ["2.4", "5"]
    assert caps.supports_ap is True
    assert caps.ap_sta_combination is True
    assert caps.supports_sae is False
    assert caps.supports_vht is False


def test_capabilities_without_combinations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "get_phy_info", lambda _phy: "Wiphy phy0\n\tBand 1:\n")
    caps = wifi.get_capabilities("phy0")
    assert caps.ap_sta_combination is None
    assert caps.supports_ap is False


def test_channel_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "get_phy_info", lambda _phy: PHY_INFO)
    assert wifi.channel_allowed("phy0", 1) == (True, "permitido")
    assert wifi.channel_allowed("phy0", 2)[0] is False
    assert wifi.channel_allowed("phy0", 3)[0] is False
    assert wifi.channel_allowed("phy0", 40)[0] is False
    assert wifi.channel_allowed("phy0", 40, allow_dfs=True) == (True, "permitido")
    assert wifi.channel_allowed("phy0", 99)[0] is False


def test_channel_allowed_without_frequency_information(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "get_phy_info", lambda _phy: "Wiphy phy0\n")
    assert wifi.channel_allowed("phy0", 36) == (True, "sin información de canales")


def test_available_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "get_phy_info", lambda _phy: PHY_INFO)
    assert wifi.available_channels("phy0") == [1, 36, 40]
    assert wifi.available_channels("phy0", "2.4") == [1]
    assert wifi.available_channels("phy0", "5") == [36, 40]


@pytest.mark.parametrize(
    ("channel", "frequency", "band"),
    [
        (1, 2412, "2.4"),
        (6, 2437, "2.4"),
        (14, 2484, "2.4"),
        (36, 5180, "5"),
        (149, 5745, "5"),
    ],
)
def test_channel_helpers(channel: int, frequency: int, band: str) -> None:
    assert wifi.channel_frequency(channel) == frequency
    assert wifi.channel_band(channel) == band


def test_invalid_channel_frequency() -> None:
    with pytest.raises(StateError):
        wifi.channel_frequency(0)


def test_default_channel_prefers_five_ghz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "channel_allowed", lambda _phy, _channel: (True, "permitido"))
    assert wifi.default_channel("phy0", "5", "ES") == 36


def test_default_channel_requires_country_for_five_ghz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(wifi, "channel_allowed", lambda _phy, _channel: (False, "bloqueado"))
    with pytest.raises(StateError, match="país"):
        wifi.default_channel("phy0", "5", "00")


def test_default_channel_returns_safe_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "channel_allowed", lambda _phy, _channel: (False, "bloqueado"))
    assert wifi.default_channel("phy0", "2.4") == 6
    assert wifi.default_channel("phy0", "auto") == 6


def test_parse_station_dump() -> None:
    stations = wifi.parse_station_dump(STATION_DUMP)
    assert len(stations) == 2
    first = stations[0]
    assert first["mac"] == "aa:bb:cc:dd:ee:ff"
    assert first["signal_dbm"] == -45
    assert first["rx_bytes"] == 1000
    assert first["tx_bytes"] == 2000
    assert first["connected_seconds"] == 3600
    assert first["inactive_ms"] == 1234


def test_parse_empty_station_dump() -> None:
    assert wifi.parse_station_dump("") == []


def test_get_stations_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "run", FakeRun(returncode=1))
    assert wifi.get_stations("wlan-ap") == []


def test_get_link(monkeypatch: pytest.MonkeyPatch) -> None:
    output = """Connected to aa:bb:cc:dd:ee:ff (on wlan0)
\tSSID: MyNetwork
\tfreq: 5180
\tsignal: -44 dBm
\ttx bitrate: 866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
"""
    monkeypatch.setattr(wifi, "run", FakeRun(stdout=output))
    link = wifi.get_link("wlan0")
    assert link == {
        "bssid": "aa:bb:cc:dd:ee:ff",
        "ssid": "MyNetwork",
        "frequency": "5180",
        "signal": "-44",
        "tx_bitrate": "866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2",
    }


def test_get_link_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "run", FakeRun(stdout="Not connected."))
    assert wifi.get_link("wlan0") is None


def test_get_station_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "run", FakeRun(stdout=IW_DEV_OUTPUT))
    assert wifi.get_station_channel("wlan0") == (36, 5180)
    assert wifi.get_station_channel("missing") == (None, None)


def test_reg_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "run", FakeRun(stdout="global\ncountry ES: DFS-ETSI\n"))
    assert wifi.get_reg_domain() == "ES"

    monkeypatch.setattr(wifi, "run", FakeRun(stdout="global\n"))
    assert wifi.get_reg_domain() == "00"


def test_set_reg_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    calls: list[list[str]] = []

    def runner(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append([str(part) for part in command])
        stdout = "" if len(calls) == 1 else "country ES: DFS-ETSI\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(wifi, "run", runner)
    wifi.set_reg_domain("es")
    assert calls == [["iw", "reg", "set", "ES"], ["iw", "reg", "get"]]


def test_set_reg_domain_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "run", FakeRun(returncode=1, stderr="failure"))
    with pytest.raises(CommandError, match="dominio"):
        wifi.set_reg_domain("ES")


def test_create_ap_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "interface_exists", lambda _name: False)
    runner = FakeRun()
    monkeypatch.setattr(wifi, "run", runner)

    wifi.create_ap_interface("phy0", "wlan-ap", base_interface="wlan0")
    assert runner.calls == [["iw", "phy", "phy0", "interface", "add", "wlan-ap", "type", "__ap"]]


def test_create_ap_interface_uses_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "interface_exists", lambda _name: False)
    calls: list[list[str]] = []

    def runner(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        args = [str(part) for part in command]
        calls.append(args)
        code = 1 if len(calls) == 1 else 0
        return subprocess.CompletedProcess(args, code, "", "error")

    monkeypatch.setattr(wifi, "run", runner)
    wifi.create_ap_interface("phy0", "wlan-ap", base_interface="wlan0")
    assert calls[1] == [
        "iw",
        "dev",
        "wlan0",
        "interface",
        "add",
        "wlan-ap",
        "type",
        "__ap",
    ]


def test_create_ap_interface_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "interface_exists", lambda _name: False)
    monkeypatch.setattr(wifi, "run", FakeRun(returncode=1, stderr="busy"))
    with pytest.raises(CommandError, match="interfaz AP"):
        wifi.create_ap_interface("phy0", "wlan-ap")


def test_create_ap_interface_rejects_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "command_exists", lambda _name: True)
    monkeypatch.setattr(wifi, "interface_exists", lambda _name: True)
    with pytest.raises(StateError, match="ya existe"):
        wifi.create_ap_interface("phy0", "wlan-ap")


def test_delete_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "interface_exists", lambda _name: False)
    runner = FakeRun()
    monkeypatch.setattr(wifi, "run", runner)
    wifi.delete_interface("wlan-ap")
    assert runner.calls == []

    monkeypatch.setattr(wifi, "interface_exists", lambda _name: True)
    wifi.delete_interface("wlan-ap")
    assert runner.calls[-1] == ["iw", "dev", "wlan-ap", "del"]


def test_delete_interface_falls_back_to_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "interface_exists", lambda _name: True)
    runner = FakeRun(returncode=1)
    monkeypatch.setattr(wifi, "run", runner)
    wifi.delete_interface("wlan-ap")
    assert runner.calls == [
        ["iw", "dev", "wlan-ap", "del"],
        ["ip", "link", "delete", "wlan-ap"],
    ]


def test_disconnect_station(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeRun()
    monkeypatch.setattr(wifi, "run", runner)
    wifi.disconnect_station("wlan-ap", "aa:bb:cc:dd:ee:ff")
    assert runner.calls == [["iw", "dev", "wlan-ap", "station", "del", "aa:bb:cc:dd:ee:ff"]]


def test_disconnect_station_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "run", FakeRun(returncode=1, stderr="not found"))
    with pytest.raises(CommandError, match="expulsar"):
        wifi.disconnect_station("wlan-ap", "aa:bb:cc:dd:ee:ff")
