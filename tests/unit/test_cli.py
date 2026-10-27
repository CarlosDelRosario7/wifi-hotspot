"""Tests for the command-line interface."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from wifi_hotspot import cli
from wifi_hotspot.config import Paths, save_profile
from wifi_hotspot.errors import ValidationError
from wifi_hotspot.models import HotspotConfig


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(run_dir=tmp_path / "run", etc_dir=tmp_path / "etc")


def parse(argv: list[str]):  # noqa: ANN201 - argparse.Namespace is noisy to type
    return cli.build_parser().parse_args(argv)


def test_parser_requires_command() -> None:
    with pytest.raises(SystemExit):
        parse([])


def test_parser_start_positional() -> None:
    args = parse(["start", "CafeNet", "Password123"])
    assert args.command == "start"
    assert args.ssid_positional == "CafeNet"
    assert args.password_positional == "Password123"


def test_parser_global_options_before_command() -> None:
    args = parse(["--json", "--verbose", "status"])
    assert args.json is True
    assert args.verbose is True


def test_resolve_config_uses_positionals(paths: Paths) -> None:
    args = parse(["start", "CafeNet", "Password123"])
    config = cli._resolve_config(args, paths)
    assert config.ssid == "CafeNet"
    assert config.password == "Password123"
    assert config.profile_name == "default"


def test_resolve_config_inherits_profile(paths: Paths) -> None:
    save_profile(
        paths,
        HotspotConfig(
            profile_name="cafe",
            ssid="CafeNet",
            password="Password123",
            band="2.4",
            channel=6,
            country="ES",
            max_clients=10,
        ),
    )
    args = parse(["start", "--profile", "cafe", "--ssid", "NewName", "--channel", "11"])
    config = cli._resolve_config(args, paths)
    assert config.profile_name == "cafe"
    assert config.ssid == "NewName"
    assert config.channel == 11
    assert config.band == "2.4"
    assert config.country == "ES"
    assert config.max_clients == 10


def test_resolve_config_generates_password(paths: Paths) -> None:
    args = parse(["start", "CafeNet", "--generate-password"])
    config = cli._resolve_config(args, paths)
    assert len(config.password) >= 16
    assert config.password != "12345678"


def test_resolve_config_open_network_clears_password(paths: Paths) -> None:
    args = parse(["start", "CafeNet", "--security", "open"])
    config = cli._resolve_config(args, paths)
    assert config.security == "open"
    assert config.password == ""


def test_resolve_config_dns_list(paths: Paths) -> None:
    args = parse(["start", "CafeNet", "--dns", "9.9.9.9", "--dns", "1.1.1.1"])
    config = cli._resolve_config(args, paths)
    assert config.dns == ["9.9.9.9", "1.1.1.1"]


def test_resolve_config_rejects_invalid_values(paths: Paths) -> None:
    args = parse(["start", "x" * 33, "Password123"])
    with pytest.raises(ValidationError, match="SSID"):
        cli._resolve_config(args, paths)


def test_main_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["version"]) == 0
    assert "WiFi Hotspot" in capsys.readouterr().out


def test_main_status_text(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "DEFAULT_PATHS", paths)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "El hotspot no está activo" in out


def test_main_status_json(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "DEFAULT_PATHS", paths)
    assert cli.main(["--json", "status"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "stopped"


def test_main_profiles(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "DEFAULT_PATHS", paths)
    assert cli.main(["profiles"]) == 0
    assert "No hay perfiles" in capsys.readouterr().out

    save_profile(paths, HotspotConfig(profile_name="cafe", ssid="CafeNet", password="Password123"))
    assert cli.main(["profiles"]) == 0
    assert "cafe" in capsys.readouterr().out


def test_main_stop(
    paths: Paths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "DEFAULT_PATHS", paths)
    monkeypatch.setattr(cli.HotspotEngine, "stop", lambda _self: False)
    monkeypatch.setattr(cli.HotspotEngine, "start", lambda _self: pytest.fail("not start"))
    assert cli.main(["stop"]) == 0
    assert "No había" in capsys.readouterr().out


def test_main_stop_json(
    paths: Paths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "DEFAULT_PATHS", paths)
    monkeypatch.setattr(cli.HotspotEngine, "stop", lambda _self: True)
    assert cli.main(["--json", "stop"]) == 0
    assert json.loads(capsys.readouterr().out) == {"stopped": True}


def test_main_reports_validation_error(
    paths: Paths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "DEFAULT_PATHS", paths)
    assert cli.main(["start", "x" * 33, "Password123"]) == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err
    assert "SSID" in captured.err


def test_main_reports_json_error(
    paths: Paths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "DEFAULT_PATHS", paths)
    assert cli.main(["--json", "start", "x" * 33, "Password123"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert "SSID" in data["error"]


def test_print_status_without_hotspot(capsys: pytest.CaptureFixture[str]) -> None:
    cli._print_status({"status": "stopped"})
    assert "no está activo" in capsys.readouterr().out


def test_print_status_with_clients(capsys: pytest.CaptureFixture[str]) -> None:
    cli._print_status(
        {
            "status": "running",
            "ssid": "CafeNet",
            "security": "wpa2",
            "ap_interface": "wlan-ap",
            "ap_interface_present": True,
            "uplink": "eth0",
            "ap_ip": "10.42.0.1",
            "channel": 36,
            "band": "5",
            "firewall_backend": "iptables",
            "hostapd_alive": True,
            "dnsmasq_alive": True,
            "clients": [
                {
                    "hostname": "phone",
                    "ip": "10.42.0.20",
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "signal_dbm": -45,
                    "rx_bytes": 1024,
                    "tx_bytes": 2048,
                    "connected_seconds": 60,
                }
            ],
        }
    )
    out = capsys.readouterr().out
    assert "CafeNet" in out
    assert "Clientes (1)" in out
    assert "phone" in out


def test_print_clients_empty(capsys: pytest.CaptureFixture[str]) -> None:
    cli._print_clients([])
    assert "No hay clientes" in capsys.readouterr().out


def test_print_clients_table(capsys: pytest.CaptureFixture[str]) -> None:
    cli._print_clients(
        [
            {
                "hostname": "",
                "ip": "",
                "mac": "aa:bb:cc:dd:ee:ff",
                "signal_dbm": None,
                "rx_bytes": 0,
                "tx_bytes": 0,
                "connected_seconds": 0,
            }
        ]
    )
    out = capsys.readouterr().out
    assert "aa:bb:cc:dd:ee:ff" in out
    assert "HOSTNAME" in out


def test_print_qr_requires_tool(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "command_exists", lambda _name: False)
    with pytest.raises(ValidationError, match="qrencode"):
        cli._print_qr(HotspotConfig(ssid="CafeNet", password="Password123"))


def test_print_qr(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], *, input_text: str | None = None, **_kwargs: object):
        captured["command"] = command
        captured["payload"] = input_text
        return subprocess.CompletedProcess(command, 0, "QR\n", "")

    monkeypatch.setattr(cli, "command_exists", lambda _name: True)
    monkeypatch.setattr(cli, "run", fake_run)
    cli._print_qr(HotspotConfig(ssid="CafeNet", password="Password123"))
    assert capsys.readouterr().out == "QR\n"
    assert captured["command"] == ["qrencode", "-t", "ANSIUTF8", "-o", "-"]
    assert captured["payload"] == "WIFI:T:WPA;S:CafeNet;P:Password123;;"
