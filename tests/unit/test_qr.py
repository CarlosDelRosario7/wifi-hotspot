"""Tests for Wi-Fi QR payloads."""

from __future__ import annotations

from wifi_hotspot.models import HotspotConfig
from wifi_hotspot.qr import escape, wifi_qr_payload


def test_wpa_payload() -> None:
    config = HotspotConfig(ssid="CafeNet", password="Password123")
    assert wifi_qr_payload(config) == "WIFI:T:WPA;S:CafeNet;P:Password123;;"


def test_open_payload_has_no_password() -> None:
    config = HotspotConfig(ssid="CafeNet", password="", security="open")
    assert wifi_qr_payload(config) == "WIFI:T:nopass;S:CafeNet;;"


def test_hidden_payload() -> None:
    config = HotspotConfig(ssid="CafeNet", password="Password123", hidden=True)
    assert wifi_qr_payload(config) == "WIFI:T:WPA;S:CafeNet;P:Password123;H:true;;"


def test_special_characters_are_escaped() -> None:
    assert escape('a;b,c:d"e\\f') == 'a\\;b\\,c\\:d\\"e\\\\f'


def test_payload_escapes_ssid_and_password() -> None:
    config = HotspotConfig(ssid="My;Net", password="p:a,ss")
    payload = wifi_qr_payload(config)
    assert "S:My\\;Net;" in payload
    assert "P:p\\:a\\,ss;" in payload
    assert payload.count("\\;") == 1


def test_unicode_ssid_is_preserved() -> None:
    config = HotspotConfig(ssid="Café", password="Password123")
    assert "S:Café;" in wifi_qr_payload(config)
