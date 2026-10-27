"""Tests for hostapd configuration generation."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from wifi_hotspot.models import HotspotConfig
from wifi_hotspot.platform.wifi import PhyCapabilities
from wifi_hotspot.services import hostapd


def capabilities(**changes: object) -> PhyCapabilities:
    values: dict[str, object] = {
        "phy": "phy0",
        "bands": ["2.4", "5"],
        "supports_vht": True,
        "supports_he": True,
        "supports_sae": True,
    }
    values.update(changes)
    return PhyCapabilities(**values)


def build(config: HotspotConfig, channel: int = 36, caps: PhyCapabilities | None = None) -> str:
    return hostapd.build_config(
        config,
        ap_interface="wlan-ap",
        channel=channel,
        capabilities=caps or capabilities(),
        control_dir=Path("/run/wifi-hotspot/hostapd"),
    )


def test_common_configuration() -> None:
    text = build(HotspotConfig(ssid="CafeNet", password="Password123", country="ES"))
    assert "interface=wlan-ap" in text
    assert "driver=nl80211" in text
    assert "ssid=CafeNet" in text
    assert "utf8_ssid=1" in text
    assert "country_code=ES" in text
    assert "ieee80211d=1" in text
    assert "hw_mode=a" in text
    assert "channel=36" in text
    assert "ieee80211n=1" in text
    assert "wmm_enabled=1" in text
    assert "auth_algs=1" in text
    assert "ctrl_interface_group=0" in text


def test_2_4ghz_uses_g_mode_and_no_vht() -> None:
    text = build(HotspotConfig(ssid="CafeNet", password="Password123"), channel=6)
    assert "hw_mode=g" in text
    assert "channel=6" in text
    assert "ieee80211ac=1" not in text
    assert "ieee80211ax=1" in text


def test_5ghz_enables_vht_when_supported() -> None:
    text = build(HotspotConfig(ssid="CafeNet", password="Password123"), channel=36)
    assert "hw_mode=a" in text
    assert "ieee80211ac=1" in text
    assert "vht_oper_chwidth=0" in text


def test_5ghz_without_vht_does_not_enable_ac() -> None:
    text = build(
        HotspotConfig(ssid="CafeNet", password="Password123"),
        channel=36,
        caps=capabilities(supports_vht=False),
    )
    assert "ieee80211ac=1" not in text


def test_wpa2_configuration() -> None:
    text = build(HotspotConfig(ssid="CafeNet", password="Password123", security="wpa2"))
    assert "wpa=2" in text
    assert "wpa_key_mgmt=WPA-PSK" in text
    assert "rsn_pairwise=CCMP" in text
    assert "wpa_passphrase=Password123" in text
    assert "SAE" not in text
    assert "ieee80211w" not in text


def test_wpa3_configuration() -> None:
    text = build(HotspotConfig(ssid="CafeNet", password="Password123", security="wpa3"))
    assert "wpa_key_mgmt=SAE" in text
    assert "ieee80211w=2" in text
    assert "sae_require_mfp=1" in text
    assert "wpa_passphrase=Password123" in text


def test_mixed_configuration() -> None:
    text = build(HotspotConfig(ssid="CafeNet", password="Password123", security="mixed"))
    assert "wpa_key_mgmt=WPA-PSK SAE" in text
    assert "ieee80211w=1" in text


def test_open_configuration() -> None:
    text = build(HotspotConfig(ssid="CafeNet", password="", security="open"))
    assert "wpa=0" in text
    assert "wpa_passphrase" not in text


def test_hidden_network_and_limits() -> None:
    text = build(
        HotspotConfig(
            ssid="CafeNet",
            password="Password123",
            hidden=True,
            max_clients=5,
            client_isolation=True,
        )
    )
    assert "ignore_broadcast_ssid=1" in text
    assert "max_num_sta=5" in text
    assert "ap_isolate=1" in text


def test_zero_max_clients_is_omitted() -> None:
    text = build(HotspotConfig(ssid="CafeNet", password="Password123", max_clients=0))
    assert "max_num_sta" not in text


def test_no_country_lines_when_country_is_none() -> None:
    text = build(HotspotConfig(ssid="CafeNet", password="Password123", country=None))
    assert "country_code" not in text
    assert "ieee80211d" not in text


def test_unsupported_security_raises() -> None:
    config = HotspotConfig(ssid="CafeNet", password="Password123")
    config.security = "wep"
    with pytest.raises(ValueError, match="seguridad"):
        build(config)


def test_write_config_uses_restrictive_mode(tmp_path: Path) -> None:
    path = tmp_path / "hostapd.conf"
    hostapd.write_config(path, "ssid=Test\n")
    assert path.read_text(encoding="utf-8") == "ssid=Test\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_prepare_control_dir(tmp_path: Path) -> None:
    path = tmp_path / "hostapd"
    hostapd.prepare_control_dir(path)
    assert path.is_dir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_frequency_helper() -> None:
    assert hostapd.frequency(1) == 2412
    assert hostapd.frequency(36) == 5180
