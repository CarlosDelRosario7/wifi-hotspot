"""Tests for the runtime state file."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from wifi_hotspot import state as state_module
from wifi_hotspot.errors import StateError
from wifi_hotspot.state import RuntimeState, load_state, remove_state, save_state


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = RuntimeState(
        status="running",
        ssid="CafeNet",
        security="wpa2",
        ap_interface="wlan-ap",
        uplink="eth0",
        phy="phy0",
        channel=36,
        band="5",
        ap_ip="10.42.0.1",
        network="10.42.0.0/24",
        firewall_backend="iptables",
        hostapd_pid=123,
        dnsmasq_pid=456,
        started_at="2026-10-26T12:00:00+00:00",
    )
    save_state(path, original)
    loaded = load_state(path)

    assert loaded is not None
    assert loaded.to_dict() == original.to_dict()
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    # The state file must never contain the Wi-Fi password.
    raw = path.read_text(encoding="utf-8")
    assert "password" not in raw


def test_load_missing_state(tmp_path: Path) -> None:
    assert load_state(tmp_path / "missing.json") is None


def test_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(StateError, match="No pude leer"):
        load_state(path)


def test_non_object_raises(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(StateError, match="no es válido"):
        load_state(path)


def test_unknown_fields_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"status": "running", "ssid": "CafeNet", "future": 1}),
        encoding="utf-8",
    )
    state = load_state(path)
    assert state is not None
    assert state.ssid == "CafeNet"
    assert not hasattr(state, "future")


def test_remove_state(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, RuntimeState())
    assert path.exists()
    remove_state(path)
    assert not path.exists()
    remove_state(path)


def test_alive_requires_running_and_both_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(state_module, "pid_alive", lambda pid: pid in {123, 456})
    running = RuntimeState(status="running", hostapd_pid=123, dnsmasq_pid=456)
    assert running.alive is True

    stopped = RuntimeState(status="stopped", hostapd_pid=123, dnsmasq_pid=456)
    assert stopped.alive is False

    missing_dnsmasq = RuntimeState(status="running", hostapd_pid=123, dnsmasq_pid=None)
    assert missing_dnsmasq.alive is False


def test_public_dict_hides_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_module, "pid_alive", lambda _pid: True)
    state = RuntimeState(
        status="running",
        hostapd_pid=1,
        dnsmasq_pid=2,
        last_error="secret detail",
    )
    data = state.public_dict()
    assert "last_error" not in data
    assert data["alive"] is True
    assert data["hostapd_alive"] is True
    assert data["dnsmasq_alive"] is True


def test_public_dict_marks_dead_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_module, "pid_alive", lambda _pid: False)
    state = RuntimeState(status="running", hostapd_pid=1, dnsmasq_pid=2)
    data = state.public_dict()
    assert data["alive"] is False
    assert data["hostapd_alive"] is False
    assert data["dnsmasq_alive"] is False
