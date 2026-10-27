"""Wi-Fi QR payload generation."""

from __future__ import annotations

from .models import HotspotConfig


def escape(value: str) -> str:
    """Escape the characters defined by the Wi-Fi QR format."""
    value = value.replace("\\", "\\\\")
    for character in (";", ",", ":", '"'):
        value = value.replace(character, "\\" + character)
    return value


def wifi_qr_payload(config: HotspotConfig) -> str:
    security = "nopass" if config.is_open else "WPA"
    parts = [
        f"WIFI:T:{security};",
        f"S:{escape(config.ssid)};",
    ]
    if not config.is_open:
        parts.append(f"P:{escape(config.password)};")
    if config.hidden:
        parts.append("H:true;")
    parts.append(";")
    return "".join(parts)
