"""Tests for firewall rule generation and backend behavior."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

import pytest

from wifi_hotspot.errors import CommandError, DependencyError
from wifi_hotspot.platform import firewall
from wifi_hotspot.platform.firewall import FirewallContext


class FakeRunner:
    def __init__(self, *, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.calls: list[tuple[list[str], bool, str | None]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        args = [str(item) for item in command]
        self.calls.append((args, check, input_text))
        result = subprocess.CompletedProcess(args, self.returncode, self.stdout, "")
        if check and self.returncode != 0:
            raise CommandError("fallo simulado", args, self.returncode)
        return result


def context() -> FirewallContext:
    return FirewallContext(ap_interface="wlan-ap", uplink="eth0", network="10.42.0.0/24")


def test_iptables_setup_commands_cover_nat_and_forwarding() -> None:
    commands = firewall.iptables_setup_commands(context())
    text = [" ".join(command) for command in commands]

    assert any("POSTROUTING" in line and "WIFI_HOTSPOT_NAT" in line for line in text)
    assert any("FORWARD" in line and "WIFI_HOTSPOT_FWD" in line for line in text)
    assert any("MASQUERADE" in line for line in text)
    assert any("10.42.0.0/24" in line and "eth0" in line for line in text)
    assert any("-i wlan-ap -o eth0 -j ACCEPT" in line for line in text)
    assert any("RELATED,ESTABLISHED" in line for line in text)


def test_iptables_teardown_removes_jumps_chains_and_rules() -> None:
    commands = firewall.iptables_teardown_commands(context())
    text = [" ".join(command) for command in commands]

    assert sum(" -D POSTROUTING " in line for line in text) == 1
    assert sum(" -D FORWARD " in line for line in text) == 1
    assert any("-F WIFI_HOTSPOT_NAT" in line for line in text)
    assert any("-X WIFI_HOTSPOT_NAT" in line for line in text)
    assert any("-F WIFI_HOTSPOT_FWD" in line for line in text)
    assert any("-X WIFI_HOTSPOT_FWD" in line for line in text)


def test_iptables_setup_runs_optional_then_mandatory() -> None:
    runner = FakeRunner()
    firewall.IptablesBackend(runner).setup(context())

    assert len(runner.calls) == 11
    optional = firewall.iptables_optional_commands(context())
    mandatory = firewall.iptables_mandatory_commands(context())
    assert [call[0] for call in runner.calls] == [*optional, *mandatory]
    assert all(call[1] is False for call in runner.calls[: len(optional)])
    assert all(call[1] is True for call in runner.calls[len(optional) :])


def test_iptables_setup_ignores_optional_failures() -> None:
    runner = FakeRunner(returncode=1)
    with pytest.raises(CommandError):
        firewall.IptablesBackend(runner).setup(context())
    # The failure happened only when the mandatory commands started.
    assert len(runner.calls) == len(firewall.iptables_optional_commands(context())) + 1


def test_iptables_teardown_ignores_failures() -> None:
    runner = FakeRunner(returncode=1)
    firewall.IptablesBackend(runner).teardown(context())
    assert len(runner.calls) == len(firewall.iptables_teardown_commands(context()))


def test_nft_script() -> None:
    script = firewall.nft_script(context())
    assert "table inet wifi_hotspot" in script
    assert 'iifname "wlan-ap" oifname "eth0" accept' in script
    assert 'ip saddr 10.42.0.0/24 oifname "eth0" masquerade' in script
    assert "ct state related,established accept" in script


def test_nft_setup_and_teardown() -> None:
    runner = FakeRunner()
    backend = firewall.NftBackend(runner)
    backend.setup(context())
    assert runner.calls[0][0] == ["nft", "delete", "table", "inet", "wifi_hotspot"]
    assert runner.calls[1][0] == ["nft", "-f", "-"]
    assert runner.calls[1][2] == firewall.nft_script(context())

    backend.teardown(context())
    assert runner.calls[-1][0] == ["nft", "delete", "table", "inet", "wifi_hotspot"]


def test_nft_setup_failure_raises() -> None:
    runner = FakeRunner(returncode=1)
    with pytest.raises(CommandError, match="nftables"):
        firewall.NftBackend(runner).setup(context())


def test_firewalld_rules() -> None:
    rules = firewall.firewalld_rules(context())
    assert len(rules) == 3
    assert rules[0][:4] == ["ipv4", "nat", "POSTROUTING", "0"]
    assert "MASQUERADE" in rules[0]
    assert rules[1][:4] == ["ipv4", "filter", "FORWARD", "0"]
    assert "wlan-ap" in rules[1]
    assert "RELATED,ESTABLISHED" in rules[2]


def test_firewalld_setup_removes_before_adding() -> None:
    runner = FakeRunner(stdout="running\n")
    backend = firewall.FirewalldBackend(runner)
    backend.setup(context())

    actions = [call[0][2] for call in runner.calls]
    assert actions == [
        "--remove-rule",
        "--add-rule",
        "--remove-rule",
        "--add-rule",
        "--remove-rule",
        "--add-rule",
    ]


def test_firewalld_is_available_requires_running_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(firewall, "command_exists", lambda _name: True)
    runner = FakeRunner(stdout="running\n")
    backend = firewall.FirewalldBackend(runner)
    assert backend.is_available() is True

    stopped = FakeRunner(stdout="not running\n")
    backend = firewall.FirewalldBackend(stopped)
    assert backend.is_available() is False


def test_backend_by_name_unknown() -> None:
    with pytest.raises(DependencyError, match="desconocido"):
        firewall.backend_by_name("pf")


def test_backend_by_name_checks_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firewall, "command_exists", lambda _name: False)
    with pytest.raises(DependencyError, match="no está disponible"):
        firewall.backend_by_name("iptables")

    backend = firewall.backend_by_name("iptables", check_available=False)
    assert backend.name == "iptables"


def test_detect_backend_prefers_firewalld(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firewall, "command_exists", lambda _name: True)
    monkeypatch.setattr(firewall.FirewalldBackend, "is_available", lambda _self: True)
    assert firewall.detect_backend("auto").name == "firewalld"


def test_detect_backend_falls_back_to_iptables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firewall, "command_exists", lambda _name: True)
    monkeypatch.setattr(firewall.FirewalldBackend, "is_available", lambda _self: False)
    assert firewall.detect_backend("auto").name == "iptables"


def test_detect_backend_falls_back_to_nft(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firewall, "command_exists", lambda _name: True)
    monkeypatch.setattr(firewall.FirewalldBackend, "is_available", lambda _self: False)
    monkeypatch.setattr(firewall.IptablesBackend, "is_available", lambda _self: False)
    assert firewall.detect_backend("auto").name == "nft"


def test_detect_backend_without_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firewall, "command_exists", lambda _name: False)
    monkeypatch.setattr(firewall.FirewalldBackend, "is_available", lambda _self: False)
    monkeypatch.setattr(firewall.IptablesBackend, "is_available", lambda _self: False)
    monkeypatch.setattr(firewall.NftBackend, "is_available", lambda _self: False)
    with pytest.raises(DependencyError, match="encontré"):
        firewall.detect_backend("auto")


def test_backend_names() -> None:
    assert firewall.backend_names() == ["firewalld", "iptables", "nft"]
