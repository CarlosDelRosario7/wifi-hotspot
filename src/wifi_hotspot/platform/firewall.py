"""Firewall backends with dedicated, cleanly removable rules."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ..errors import CommandError, DependencyError
from ..util import command_exists, run

log = logging.getLogger(__name__)

IPTABLES_WAIT = "5"
NAT_CHAIN = "WIFI_HOTSPOT_NAT"
FWD_CHAIN = "WIFI_HOTSPOT_FWD"
NFT_TABLE = "wifi_hotspot"
COMMENT_NAT = "wifi-hotspot-nat"
COMMENT_FWD = "wifi-hotspot-fwd"


class Runner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True)
class FirewallContext:
    ap_interface: str
    uplink: str
    network: str


def _iptables_base(table: str | None = None) -> list[str]:
    command = ["iptables", "-w", IPTABLES_WAIT]
    if table:
        command.extend(["-t", table])
    return command


def iptables_optional_commands(context: FirewallContext) -> list[list[str]]:
    """Commands that are safe to run when rules or chains do not exist yet."""
    nat = _iptables_base("nat")
    filt = _iptables_base()
    return [
        [*nat, "-D", "POSTROUTING", "-m", "comment", "--comment", COMMENT_NAT, "-j", NAT_CHAIN],
        [*filt, "-D", "FORWARD", "-m", "comment", "--comment", COMMENT_FWD, "-j", FWD_CHAIN],
        [*nat, "-N", NAT_CHAIN],
        [*nat, "-F", NAT_CHAIN],
        [*filt, "-N", FWD_CHAIN],
        [*filt, "-F", FWD_CHAIN],
    ]


def iptables_mandatory_commands(context: FirewallContext) -> list[list[str]]:
    nat = _iptables_base("nat")
    filt = _iptables_base()
    jump_nat = [*nat, "-I", "POSTROUTING", "1", "-m", "comment", "--comment", COMMENT_NAT]
    jump_nat.extend(["-j", NAT_CHAIN])
    jump_fwd = [*filt, "-I", "FORWARD", "1", "-m", "comment", "--comment", COMMENT_FWD]
    jump_fwd.extend(["-j", FWD_CHAIN])
    return [
        [*nat, "-A", NAT_CHAIN, "-s", context.network, "-o", context.uplink, "-j", "MASQUERADE"],
        [
            *filt,
            "-A",
            FWD_CHAIN,
            "-i",
            context.ap_interface,
            "-o",
            context.uplink,
            "-j",
            "ACCEPT",
        ],
        [
            *filt,
            "-A",
            FWD_CHAIN,
            "-i",
            context.uplink,
            "-o",
            context.ap_interface,
            "-m",
            "conntrack",
            "--ctstate",
            "RELATED,ESTABLISHED",
            "-j",
            "ACCEPT",
        ],
        jump_nat,
        jump_fwd,
    ]


def iptables_setup_commands(context: FirewallContext) -> list[list[str]]:
    return [*iptables_optional_commands(context), *iptables_mandatory_commands(context)]


def iptables_teardown_commands(context: FirewallContext | None = None) -> list[list[str]]:
    nat = _iptables_base("nat")
    filt = _iptables_base()
    commands: list[list[str]] = [
        [*nat, "-D", "POSTROUTING", "-m", "comment", "--comment", COMMENT_NAT, "-j", NAT_CHAIN],
        [*filt, "-D", "FORWARD", "-m", "comment", "--comment", COMMENT_FWD, "-j", FWD_CHAIN],
        [*nat, "-F", NAT_CHAIN],
        [*nat, "-X", NAT_CHAIN],
        [*filt, "-F", FWD_CHAIN],
        [*filt, "-X", FWD_CHAIN],
    ]
    return commands


def nft_script(context: FirewallContext) -> str:
    return f"""table inet {NFT_TABLE} {{
    chain forward {{
        type filter hook forward priority 0; policy accept;
        iifname "{context.ap_interface}" oifname "{context.uplink}" accept
        iifname "{context.uplink}" oifname "{context.ap_interface}" ct state related,established accept
    }}
    chain postrouting {{
        type nat hook postrouting priority 100; policy accept;
        ip saddr {context.network} oifname "{context.uplink}" masquerade
    }}
}}
"""


def firewalld_rules(context: FirewallContext) -> list[list[str]]:
    return [
        [
            "ipv4",
            "nat",
            "POSTROUTING",
            "0",
            "-s",
            context.network,
            "-o",
            context.uplink,
            "-j",
            "MASQUERADE",
        ],
        [
            "ipv4",
            "filter",
            "FORWARD",
            "0",
            "-i",
            context.ap_interface,
            "-o",
            context.uplink,
            "-j",
            "ACCEPT",
        ],
        [
            "ipv4",
            "filter",
            "FORWARD",
            "0",
            "-i",
            context.uplink,
            "-o",
            context.ap_interface,
            "-m",
            "conntrack",
            "--ctstate",
            "RELATED,ESTABLISHED",
            "-j",
            "ACCEPT",
        ],
    ]


class FirewallBackend:
    name = "none"

    def __init__(self, runner: Runner = run) -> None:
        self._run = runner

    def setup(self, context: FirewallContext) -> None:
        raise NotImplementedError

    def teardown(self, context: FirewallContext | None = None) -> None:
        raise NotImplementedError

    def is_available(self) -> bool:
        return False


class IptablesBackend(FirewallBackend):
    name = "iptables"

    def is_available(self) -> bool:
        return command_exists("iptables")

    def setup(self, context: FirewallContext) -> None:
        for command in iptables_optional_commands(context):
            self._run(command, check=False)
        for command in iptables_mandatory_commands(context):
            self._run(command, check=True)
        log.info("Reglas iptables aplicadas (%s -> %s)", context.ap_interface, context.uplink)

    def teardown(self, context: FirewallContext | None = None) -> None:
        for command in iptables_teardown_commands(context):
            self._run(command, check=False)
        log.info("Reglas iptables eliminadas")


class NftBackend(FirewallBackend):
    name = "nft"

    def is_available(self) -> bool:
        return command_exists("nft")

    def setup(self, context: FirewallContext) -> None:
        self._run(["nft", "delete", "table", "inet", NFT_TABLE], check=False)
        result = self._run(["nft", "-f", "-"], check=False, input_text=nft_script(context))
        if result.returncode != 0:
            raise CommandError(
                "No pude aplicar las reglas nftables",
                ["nft", "-f", "-"],
                stderr=result.stderr,
            )
        log.info("Reglas nftables aplicadas (%s -> %s)", context.ap_interface, context.uplink)

    def teardown(self, context: FirewallContext | None = None) -> None:
        self._run(["nft", "delete", "table", "inet", NFT_TABLE], check=False)
        log.info("Reglas nftables eliminadas")


class FirewalldBackend(FirewallBackend):
    name = "firewalld"

    def is_available(self) -> bool:
        if not command_exists("firewall-cmd"):
            return False
        result = self._run(["firewall-cmd", "--state"], check=False)
        return result.returncode == 0 and result.stdout.strip().lower() == "running"

    def _change(self, action: str, rule: list[str], *, check: bool) -> None:
        self._run(["firewall-cmd", "--direct", action, *rule], check=check)

    def setup(self, context: FirewallContext) -> None:
        for rule in firewalld_rules(context):
            # Remove first to make repeated starts idempotent.
            self._change("--remove-rule", rule, check=False)
            self._change("--add-rule", rule, check=True)
        log.info("Reglas firewalld aplicadas (%s -> %s)", context.ap_interface, context.uplink)

    def teardown(self, context: FirewallContext | None = None) -> None:
        if context is None:
            return
        for rule in firewalld_rules(context):
            self._change("--remove-rule", rule, check=False)
        log.info("Reglas firewalld eliminadas")


BACKENDS: dict[str, type[FirewallBackend]] = {
    "iptables": IptablesBackend,
    "nft": NftBackend,
    "firewalld": FirewalldBackend,
}


def backend_by_name(
    name: str, runner: Runner = run, *, check_available: bool = True
) -> FirewallBackend:
    backend_class = BACKENDS.get(name)
    if backend_class is None:
        raise DependencyError(f"Backend de firewall desconocido: {name}")
    backend = backend_class(runner)
    if check_available and not backend.is_available():
        raise DependencyError(f"El backend de firewall {name!r} no está disponible.")
    return backend


def detect_backend(preference: str = "auto", runner: Runner = run) -> FirewallBackend:
    if preference != "auto":
        return backend_by_name(preference, runner)

    # firewalld owns forwarding policies, so it must be used when active.
    firewalld = FirewalldBackend(runner)
    if firewalld.is_available():
        return firewalld

    # iptables (including iptables-nft) integrates with Docker, ufw, and most
    # existing rule sets because it shares the standard FORWARD/POSTROUTING
    # chains. It is the safest default.
    iptables = IptablesBackend(runner)
    if iptables.is_available():
        return iptables

    nft = NftBackend(runner)
    if nft.is_available():
        return nft

    raise DependencyError(
        "No encontré un backend de firewall utilizable. "
        "Instala iptables/nftables o inicia firewalld."
    )


def backend_names() -> list[str]:
    return sorted(BACKENDS)
