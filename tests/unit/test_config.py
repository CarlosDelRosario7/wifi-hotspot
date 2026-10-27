"""Tests for profile storage and runtime paths."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from wifi_hotspot.config import (
    Paths,
    default_profile,
    delete_profile,
    list_profiles,
    load_profile,
    save_profile,
)
from wifi_hotspot.errors import ValidationError
from wifi_hotspot.models import HotspotConfig


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(run_dir=tmp_path / "run", etc_dir=tmp_path / "etc")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_default_profile_when_missing(paths: Paths) -> None:
    config = default_profile(paths)
    assert config.ssid == "LinuxAP"
    config.validate()


def test_save_and_load_profile_roundtrip(paths: Paths) -> None:
    config = HotspotConfig(
        profile_name="cafe",
        ssid="CafeNet",
        password="Password123",
        band="5",
        channel=36,
        country="ES",
        dns=["1.1.1.1"],
        max_clients=12,
    )
    saved = save_profile(paths, config)

    assert saved == paths.profiles_dir / "cafe.json"
    assert mode(saved) == 0o600
    assert mode(paths.profiles_dir) == 0o700
    assert mode(paths.etc_dir) == 0o700

    loaded = load_profile(paths, "cafe")
    assert loaded is not None
    assert loaded.ssid == "CafeNet"
    assert loaded.password == "Password123"
    assert loaded.band == "5"
    assert loaded.channel == 36
    assert loaded.country == "ES"
    assert loaded.max_clients == 12


def test_load_missing_profile_returns_none(paths: Paths) -> None:
    assert load_profile(paths, "missing") is None


def test_list_and_delete_profiles(paths: Paths) -> None:
    save_profile(paths, HotspotConfig(profile_name="one", ssid="One", password="Password123"))
    save_profile(paths, HotspotConfig(profile_name="two", ssid="Two", password="Password123"))

    assert list_profiles(paths) == ["one", "two"]
    assert delete_profile(paths, "one") is True
    assert delete_profile(paths, "one") is False
    assert list_profiles(paths) == ["two"]


def test_list_profiles_ignores_unrelated_files(paths: Paths) -> None:
    paths.ensure_secrets()
    (paths.profiles_dir / "notes.txt").write_text("hello", encoding="utf-8")
    (paths.profiles_dir / "spaces name.json").write_text("{}", encoding="utf-8")
    assert list_profiles(paths) == []


@pytest.mark.parametrize("name", ["../escape", "bad/name", "with space", ""])
def test_profile_names_are_sanitized(paths: Paths, name: str) -> None:
    with pytest.raises(ValidationError):
        load_profile(paths, name)
    with pytest.raises(ValidationError):
        delete_profile(paths, name)
    with pytest.raises(ValidationError):
        save_profile(paths, HotspotConfig(profile_name=name))


def test_corrupt_profile_raises_validation_error(paths: Paths) -> None:
    paths.ensure_secrets()
    (paths.profiles_dir / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationError, match="no pude leer|No pude leer"):
        load_profile(paths, "broken")


def test_non_object_profile_raises_validation_error(paths: Paths) -> None:
    paths.ensure_secrets()
    (paths.profiles_dir / "list.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValidationError, match="objeto JSON"):
        load_profile(paths, "list")


def test_saved_profile_contains_password_and_is_valid_json(paths: Paths) -> None:
    save_profile(paths, HotspotConfig(profile_name="json", ssid="Json", password="Password123"))
    data = json.loads((paths.profiles_dir / "json.json").read_text(encoding="utf-8"))
    assert data["password"] == "Password123"
    assert data["profile_name"] == "json"


def test_ensure_runtime_is_world_traversable(paths: Paths) -> None:
    paths.ensure_runtime()
    assert mode(paths.run_dir) == 0o755


def test_cleanup_runtime_files_removes_control_directory(paths: Paths) -> None:
    paths.ensure_runtime()
    paths.hostapd_ctrl.mkdir(parents=True)
    (paths.hostapd_ctrl / "wlan0").write_text("socket", encoding="utf-8")
    paths.dnsmasq_conf.write_text("config", encoding="utf-8")

    paths.cleanup_runtime_files()

    assert not paths.hostapd_ctrl.exists()
    assert not paths.dnsmasq_conf.exists()
