"""Configuration and runtime models."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from .errors import ValidationError
from .util import INTERFACE_NAME_RE, PROFILE_NAME_RE

SECURITY_MODES = {"wpa2", "wpa3", "mixed", "open"}
BANDS = {"auto", "2.4", "5"}
FIREWALL_BACKENDS = {"auto", "iptables", "nft", "firewalld"}
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
LEASE_RE = re.compile(r"^(infinite|\d+[smhd]?)$")
HEX_PSK_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass
class HotspotConfig:
    """User-facing hotspot configuration.

    The password is part of this object because hostapd needs it, but it must
    never be written to the status file or returned by the status API.
    """

    profile_name: str = "default"
    ssid: str = "LinuxAP"
    password: str = "12345678"
    security: str = "wpa2"
    band: str = "auto"
    channel: int | None = None
    country: str | None = None
    hidden: bool = False
    max_clients: int = 0
    client_isolation: bool = False
    ipv6: bool = False
    subnet: str = "10.42.0.0/24"
    dhcp_start: str | None = None
    dhcp_end: str | None = None
    lease_time: str = "12h"
    dns: list[str] = field(default_factory=list)
    uplink: str | None = None
    ap_interface: str = "wifi-ap"
    firewall_backend: str = "auto"

    def __post_init__(self) -> None:
        if self.country:
            self.country = self.country.upper()
        if self.ap_interface:
            self.ap_interface = self.ap_interface.strip()
        if self.uplink:
            self.uplink = self.uplink.strip()

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(self.subnet, strict=False)

    @property
    def ap_ip(self) -> str:
        return str(self.network.network_address + 1)

    @property
    def prefix_length(self) -> int:
        return self.network.prefixlen

    @property
    def netmask(self) -> str:
        return str(self.network.netmask)

    @property
    def first_usable(self) -> ipaddress.IPv4Address:
        return self.network.network_address + 1

    @property
    def last_usable(self) -> ipaddress.IPv4Address:
        return self.network.broadcast_address - 1

    @property
    def dhcp_start_address(self) -> str:
        if self.dhcp_start:
            return self.dhcp_start
        candidate = self.network.network_address + 10
        if candidate > self.last_usable:
            candidate = self.network.network_address + 2
        return str(candidate)

    @property
    def dhcp_end_address(self) -> str:
        if self.dhcp_end:
            return self.dhcp_end
        candidate = self.network.network_address + 100
        if candidate > self.last_usable:
            candidate = self.last_usable
        return str(candidate)

    @property
    def is_open(self) -> bool:
        return self.security == "open"

    @property
    def has_password(self) -> bool:
        return bool(self.password)

    @property
    def security_label(self) -> str:
        return {
            "wpa2": "WPA2",
            "wpa3": "WPA3",
            "mixed": "WPA2/WPA3",
            "open": "Abierta",
        }.get(self.security, self.security)

    @property
    def band_label(self) -> str:
        return {
            "auto": "Automática",
            "2.4": "2.4 GHz",
            "5": "5 GHz",
        }.get(self.band, self.band)

    def validate(self) -> None:
        """Validate every field and raise ValidationError on the first problem."""
        self._validate_identity()
        self._validate_network()
        self._validate_misc()

    def _validate_identity(self) -> None:
        if not isinstance(self.ssid, str):
            raise ValidationError("El SSID debe ser texto.")
        ssid_bytes = self.ssid.encode("utf-8", "strict")
        if not 1 <= len(ssid_bytes) <= 32:
            raise ValidationError("El SSID debe ocupar entre 1 y 32 bytes UTF-8.")
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in self.ssid):
            raise ValidationError("El SSID no puede contener caracteres de control.")

        if self.security not in SECURITY_MODES:
            raise ValidationError(
                f"Modo de seguridad inválido: {self.security!r}. "
                f"Usa uno de: {', '.join(sorted(SECURITY_MODES))}."
            )
        if not self.is_open:
            if HEX_PSK_RE.match(self.password):
                return
            if not 8 <= len(self.password) <= 63:
                raise ValidationError(
                    "La contraseña WPA debe tener entre 8 y 63 caracteres, "
                    "o ser una PSK hexadecimal de 64 caracteres."
                )
            if any(not 0x20 <= ord(char) <= 0x7E for char in self.password):
                raise ValidationError("La contraseña WPA debe usar caracteres ASCII imprimibles.")
        elif self.password:
            raise ValidationError("Una red abierta no debe tener contraseña.")

    def _validate_network(self) -> None:
        try:
            network = ipaddress.ip_network(self.subnet, strict=False)
        except ValueError as exc:
            raise ValidationError(f"Subred inválida: {self.subnet!r}") from exc
        if not isinstance(network, ipaddress.IPv4Network):
            raise ValidationError("La subred del hotspot debe ser IPv4.")
        if not 8 <= network.prefixlen <= 30:
            raise ValidationError("La subred debe tener una máscara entre /8 y /30.")
        self.subnet = str(network)

        ap_ip = network.network_address + 1
        try:
            start = ipaddress.ip_address(self.dhcp_start_address)
            end = ipaddress.ip_address(self.dhcp_end_address)
            dhcp_start = self.dhcp_start_address
            dhcp_end = self.dhcp_end_address
        except ValueError as exc:
            raise ValidationError("El rango DHCP no es una dirección IP válida.") from exc

        if start not in network or end not in network:
            raise ValidationError(
                f"El rango DHCP {dhcp_start}-{dhcp_end} no pertenece a {network}."
            )
        if start == network.network_address or end == network.broadcast_address:
            raise ValidationError("El rango DHCP no puede incluir la red ni el broadcast.")
        if start == ap_ip or end == ap_ip:
            raise ValidationError("El rango DHCP no puede incluir la IP del hotspot.")
        if start > end:
            raise ValidationError("El inicio del rango DHCP debe ser menor que el final.")
        if start == end and network.prefixlen < 30:
            # A single-client range is legal, but warn via validation only for
            # genuinely impossible ranges.
            pass

        if not LEASE_RE.match(self.lease_time):
            raise ValidationError(
                "Tiempo de concesión inválido. Ejemplos: 12h, 1d, 3600 o infinite."
            )

        cleaned_dns: list[str] = []
        for server in self.dns:
            server = server.strip()
            if not server:
                continue
            try:
                ipaddress.ip_address(server)
            except ValueError as exc:
                raise ValidationError(f"Servidor DNS inválido: {server!r}") from exc
            cleaned_dns.append(server)
        self.dns = cleaned_dns

    def _validate_misc(self) -> None:
        if self.band not in BANDS:
            raise ValidationError(f"Banda inválida: {self.band!r}. Usa auto, 2.4 o 5.")
        if self.channel is not None:
            if not isinstance(self.channel, int) or not 1 <= self.channel <= 196:
                raise ValidationError("El canal debe ser un entero entre 1 y 196.")
            if self.band == "2.4" and self.channel > 14:
                raise ValidationError("El canal elegido no pertenece a 2.4 GHz.")
            if self.band == "5" and self.channel <= 14:
                raise ValidationError("El canal elegido no pertenece a 5 GHz.")
        if self.country is not None and not COUNTRY_RE.match(self.country):
            raise ValidationError(
                "El país debe ser un código ISO de dos letras, por ejemplo ES o MX."
            )
        if not isinstance(self.max_clients, int) or not 0 <= self.max_clients <= 200:
            raise ValidationError("El máximo de clientes debe estar entre 0 y 200.")
        if not isinstance(self.ap_interface, str) or not INTERFACE_NAME_RE.match(self.ap_interface):
            raise ValidationError(
                "El nombre de la interfaz AP debe tener 1-15 caracteres "
                "válidos (letras, números, punto, guion o guion bajo)."
            )
        if self.ap_interface == "lo":
            raise ValidationError("La interfaz AP no puede ser 'lo'.")
        if self.uplink is not None and not INTERFACE_NAME_RE.match(self.uplink):
            raise ValidationError("El nombre de la interfaz de uplink no es válido.")
        if self.uplink == self.ap_interface:
            raise ValidationError("La interfaz AP y el uplink no pueden ser la misma.")
        if not isinstance(self.profile_name, str) or not PROFILE_NAME_RE.match(self.profile_name):
            raise ValidationError(
                "El nombre del perfil debe empezar por letra o número y usar "
                "solo letras, números, punto, guion o guion bajo."
            )
        if self.firewall_backend not in FIREWALL_BACKENDS:
            raise ValidationError(
                "Backend de firewall inválido. Usa auto, iptables, nft o firewalld."
            )

    def to_dict(self, *, include_password: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not include_password:
            data.pop("password", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HotspotConfig:
        allowed = {field_name for field_name in cls.__dataclass_fields__}
        filtered = {key: value for key, value in data.items() if key in allowed}
        return cls(**filtered)

    def copy(self, **changes: Any) -> HotspotConfig:
        return replace(self, **changes)

    def summary(self, *, include_password: bool = False) -> dict[str, Any]:
        return {
            "profile": self.profile_name,
            "ssid": self.ssid,
            "password": self.password if include_password else None,
            "security": self.security,
            "band": self.band,
            "channel": self.channel,
            "country": self.country,
            "hidden": self.hidden,
            "max_clients": self.max_clients,
            "client_isolation": self.client_isolation,
            "ipv6": self.ipv6,
            "subnet": self.subnet,
            "ap_ip": self.ap_ip,
            "dhcp_start": self.dhcp_start_address,
            "dhcp_end": self.dhcp_end_address,
            "lease_time": self.lease_time,
            "dns": list(self.dns),
            "uplink": self.uplink,
            "ap_interface": self.ap_interface,
            "firewall_backend": self.firewall_backend,
        }
