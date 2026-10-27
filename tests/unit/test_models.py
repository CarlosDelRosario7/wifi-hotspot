"""Tests for configuration validation."""

from __future__ import annotations

import pytest

from wifi_hotspot.errors import ValidationError
from wifi_hotspot.models import HotspotConfig


def make_config(**changes: object) -> HotspotConfig:
    values: dict[str, object] = {
        "ssid": "TestNet",
        "password": "Password123",
    }
    values.update(changes)
    return HotspotConfig(**values)


def test_defaults_are_valid() -> None:
    config = HotspotConfig(ssid="TestNet", password="Password123")
    config.validate()

    assert config.ap_ip == "10.42.0.1"
    assert config.dhcp_start_address == "10.42.0.10"
    assert config.dhcp_end_address == "10.42.0.100"
    assert config.netmask == "255.255.255.0"
    assert config.prefix_length == 24
    assert config.is_open is False


def test_default_ssid_and_password_are_valid() -> None:
    HotspotConfig().validate()


def test_summary_never_includes_password_by_default() -> None:
    config = make_config()
    assert config.summary()["password"] is None
    assert config.summary(include_password=True)["password"] == "Password123"


def test_to_dict_can_omit_password() -> None:
    config = make_config()
    public = config.to_dict(include_password=False)
    assert "password" not in public
    assert config.to_dict()["password"] == "Password123"


def test_from_dict_ignores_unknown_keys() -> None:
    config = HotspotConfig.from_dict(
        {"ssid": "TestNet", "password": "Password123", "unknown": True}
    )
    assert config.ssid == "TestNet"
    config.validate()


def test_copy_changes_only_requested_fields() -> None:
    config = make_config()
    changed = config.copy(ssid="OtherNet")
    assert changed.ssid == "OtherNet"
    assert changed.password == config.password
    assert config.ssid == "TestNet"


@pytest.mark.parametrize("ssid", ["", "x" * 33, "line\nbreak", "tab\there", "\x7f"])
def test_invalid_ssid_is_rejected(ssid: str) -> None:
    config = make_config(ssid=ssid)
    with pytest.raises(ValidationError):
        config.validate()


def test_ssid_limit_is_counted_in_utf8_bytes() -> None:
    make_config(ssid="ñ" * 16).validate()
    with pytest.raises(ValidationError):
        make_config(ssid="ñ" * 17).validate()


@pytest.mark.parametrize("password", ["short", "x" * 64, "has space\t"])
def test_invalid_wpa_passwords_are_rejected(password: str) -> None:
    config = make_config(password=password)
    with pytest.raises(ValidationError):
        config.validate()


def test_minimum_and_maximum_wpa_passwords_are_accepted() -> None:
    make_config(password="12345678").validate()
    make_config(password="x" * 63).validate()


def test_hex_psk_is_accepted() -> None:
    make_config(password="a" * 64).validate()
    make_config(password="0123456789abcdef" * 4).validate()


def test_hex_lookalike_with_non_hex_characters_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(password="g" * 64).validate()


def test_non_ascii_password_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_config(password="contraseña123").validate()


def test_open_network_requires_empty_password() -> None:
    make_config(security="open", password="").validate()
    with pytest.raises(ValidationError):
        make_config(security="open").validate()


def test_invalid_security_mode_is_rejected() -> None:
    with pytest.raises(ValidationError, match="seguridad"):
        make_config(security="wep").validate()


@pytest.mark.parametrize("band", ["6", "5GHz", ""])
def test_invalid_band_is_rejected(band: str) -> None:
    with pytest.raises(ValidationError, match="Banda"):
        make_config(band=band).validate()


@pytest.mark.parametrize(
    ("band", "channel"),
    [("2.4", 36), ("5", 6), ("auto", 0), ("auto", 197)],
)
def test_invalid_channel_for_band_is_rejected(band: str, channel: int) -> None:
    with pytest.raises(ValidationError):
        make_config(band=band, channel=channel).validate()


@pytest.mark.parametrize(("band", "channel"), [("2.4", 1), ("5", 36), ("auto", 149)])
def test_valid_channel_for_band_is_accepted(band: str, channel: int) -> None:
    make_config(band=band, channel=channel).validate()


def test_country_code_is_normalized() -> None:
    config = make_config(country="es")
    config.validate()
    assert config.country == "ES"


@pytest.mark.parametrize("country", ["ESP", "E1", "e", ""])
def test_invalid_country_is_rejected(country: str) -> None:
    with pytest.raises(ValidationError, match="país"):
        make_config(country=country).validate()


def test_subnet_is_normalized() -> None:
    config = make_config(subnet="192.168.50.7/24")
    config.validate()
    assert config.subnet == "192.168.50.0/24"
    assert config.ap_ip == "192.168.50.1"


@pytest.mark.parametrize("subnet", ["not-a-network", "2001:db8::/64", "10.0.0.0/31"])
def test_invalid_subnet_is_rejected(subnet: str) -> None:
    with pytest.raises(ValidationError):
        make_config(subnet=subnet).validate()


def test_dhcp_range_must_be_inside_subnet() -> None:
    with pytest.raises(ValidationError, match="no pertenece"):
        make_config(dhcp_start="10.43.0.10", dhcp_end="10.43.0.20").validate()

    with pytest.raises(ValidationError, match="no pertenece"):
        make_config(dhcp_start="10.42.0.200", dhcp_end="10.42.1.20").validate()


def test_dhcp_range_cannot_include_gateway_or_broadcast() -> None:
    with pytest.raises(ValidationError, match="IP del hotspot"):
        make_config(dhcp_start="10.42.0.1", dhcp_end="10.42.0.20").validate()

    with pytest.raises(ValidationError, match="broadcast"):
        make_config(dhcp_start="10.42.0.200", dhcp_end="10.42.0.255").validate()


def test_dhcp_range_order_is_checked() -> None:
    with pytest.raises(ValidationError, match="menor"):
        make_config(dhcp_start="10.42.0.100", dhcp_end="10.42.0.50").validate()


def test_invalid_dhcp_address_is_rejected() -> None:
    with pytest.raises(ValidationError, match="dirección IP"):
        make_config(dhcp_start="not-an-ip").validate()


@pytest.mark.parametrize("lease", ["12h", "1d", "3600", "infinite"])
def test_valid_lease_times_are_accepted(lease: str) -> None:
    make_config(lease_time=lease).validate()


@pytest.mark.parametrize("lease", ["hours", "-1h", "1y"])
def test_invalid_lease_times_are_rejected(lease: str) -> None:
    with pytest.raises(ValidationError, match="Tiempo de concesión"):
        make_config(lease_time=lease).validate()


def test_dns_servers_are_cleaned_and_validated() -> None:
    config = make_config(dns=["1.1.1.1", " ", "8.8.8.8"])
    config.validate()
    assert config.dns == ["1.1.1.1", "8.8.8.8"]

    with pytest.raises(ValidationError, match="DNS"):
        make_config(dns=["dns.google"]).validate()


@pytest.mark.parametrize("name", ["bad/name", "../escape", "", "a" * 65, "with space"])
def test_invalid_profile_name_is_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        make_config(profile_name=name).validate()


@pytest.mark.parametrize("name", ["default", "home.lan", "cafe-2", "net_1"])
def test_valid_profile_names_are_accepted(name: str) -> None:
    make_config(profile_name=name).validate()


@pytest.mark.parametrize("interface", ["", "a" * 16, "bad name", "lo"])
def test_invalid_ap_interface_is_rejected(interface: str) -> None:
    with pytest.raises(ValidationError):
        make_config(ap_interface=interface).validate()


def test_uplink_cannot_equal_ap_interface() -> None:
    with pytest.raises(ValidationError, match="no pueden ser la misma"):
        make_config(uplink="wifi-ap", ap_interface="wifi-ap").validate()


def test_invalid_uplink_is_rejected() -> None:
    with pytest.raises(ValidationError, match="uplink"):
        make_config(uplink="bad/name").validate()


def test_max_clients_bounds() -> None:
    make_config(max_clients=0).validate()
    make_config(max_clients=200).validate()
    with pytest.raises(ValidationError):
        make_config(max_clients=-1).validate()
    with pytest.raises(ValidationError):
        make_config(max_clients=201).validate()


def test_invalid_firewall_backend_is_rejected() -> None:
    with pytest.raises(ValidationError, match="firewall"):
        make_config(firewall_backend="pf").validate()


def test_labels() -> None:
    assert make_config().security_label == "WPA2"
    assert make_config(band="5").band_label == "5 GHz"
    assert make_config(security="open", password="").security_label == "Abierta"
