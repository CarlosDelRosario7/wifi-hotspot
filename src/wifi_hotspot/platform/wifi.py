"""Thin, testable wrappers around `iw` and Wi-Fi capability discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..errors import CommandError, StateError
from ..util import command_exists, run

FREQ_RE = re.compile(r"\((\d+(?:\.\d+)?) MHz\)")
WIDTH_RE = re.compile(r"width:\s*(\d+)\s*MHz")
CHANNEL_RE = re.compile(r"^channel\s+(\d+)", re.MULTILINE)
COUNTRY_RE = re.compile(r"^country\s+([A-Z0-9]{2}):", re.MULTILINE)
STATION_RE = re.compile(r"^Station\s+([0-9a-fA-F:]{17})", re.MULTILINE)
SIGNAL_RE = re.compile(r"signal:\s*(-?\d+)")
RX_BYTES_RE = re.compile(r"rx bytes:\s*(\d+)")
TX_BYTES_RE = re.compile(r"tx bytes:\s*(\d+)")
CONNECTED_RE = re.compile(r"connected time:\s*(\d+)\s*seconds")
INACTIVE_RE = re.compile(r"inactive time:\s*(\d+)\s*ms")


@dataclass
class WifiInterface:
    name: str
    phy: str
    type: str = ""
    ssid: str = ""
    mac: str = ""
    channel: int | None = None
    frequency: int | None = None
    width: int | None = None


@dataclass
class PhyCapabilities:
    phy: str
    bands: list[str] = field(default_factory=list)
    supports_vht: bool = False
    supports_he: bool = False
    supports_sae: bool = False
    supports_ap: bool = True
    ap_sta_combination: bool | None = None
    combinations: list[str] = field(default_factory=list)


def _require_iw() -> None:
    if not command_exists("iw"):
        raise CommandError("El comando 'iw' no está instalado", ["iw"])


def list_interfaces() -> list[WifiInterface]:
    """Parse `iw dev` into a list of interfaces."""
    _require_iw()
    result = run(["iw", "dev"], check=False)
    if result.returncode != 0:
        # No wireless interfaces is a normal situation.
        if "No such file" in result.stderr or "not found" in result.stderr:
            return []
        raise CommandError(
            "No pude listar las interfaces Wi-Fi", ["iw", "dev"], stderr=result.stderr
        )

    interfaces: list[WifiInterface] = []
    current: WifiInterface | None = None
    phy = ""
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("phy#"):
            phy = f"phy{line[4:]}"
            continue
        if line.startswith("Interface "):
            current = WifiInterface(name=line.split(maxsplit=1)[1].strip(), phy=phy)
            interfaces.append(current)
            continue
        if current is None:
            continue
        if line.startswith("addr "):
            current.mac = line.split(maxsplit=1)[1].strip()
        elif line.startswith("ssid "):
            current.ssid = line[5:].strip()
        elif line.startswith("type "):
            current.type = line.split(maxsplit=1)[1].strip()
        elif line.startswith("channel "):
            match = CHANNEL_RE.match(line)
            if match:
                current.channel = int(match.group(1))
            freq = FREQ_RE.search(line)
            if freq:
                current.frequency = int(float(freq.group(1)))
            width = WIDTH_RE.search(line)
            if width:
                current.width = int(width.group(1))
    return interfaces


def get_interface(name: str) -> WifiInterface | None:
    for interface in list_interfaces():
        if interface.name == name:
            return interface
    return None


def interface_exists(name: str) -> bool:
    from pathlib import Path

    return Path(f"/sys/class/net/{name}").exists()


def get_phy_for_interface(name: str) -> str | None:
    interface = get_interface(name)
    if interface is not None:
        return interface.phy
    # Fallback: ask iw directly for non-standard interface names.
    result = run(["iw", "dev", name, "info"], check=False)
    if result.returncode != 0:
        return None
    match = re.search(r"wiphy\s+(\d+)", result.stdout)
    return f"phy{match.group(1)}" if match else None


def get_phy_info(phy: str) -> str:
    _require_iw()
    result = run(["iw", "phy", phy, "info"], check=False)
    if result.returncode != 0:
        raise CommandError(
            f"No pude leer las capacidades de {phy}",
            ["iw", "phy", phy, "info"],
            stderr=result.stderr,
        )
    return result.stdout


def _combinations_from_info(info: str) -> list[str]:
    lines = info.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("valid interface combinations:"):
            start = index + 1
            break
    if start is None:
        return []
    combinations: list[str] = []
    current: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            if current:
                combinations.append(" ".join(current))
                current = []
            break
        if stripped.startswith("*"):
            if current:
                combinations.append(" ".join(current))
            current = [stripped]
        elif current:
            current.append(stripped)
        else:
            break
    if current:
        combinations.append(" ".join(current))
    return combinations


def get_capabilities(phy: str) -> PhyCapabilities:
    info = get_phy_info(phy)
    caps = PhyCapabilities(phy=phy)
    caps.combinations = _combinations_from_info(info)
    if "Band 1:" in info:
        caps.bands.append("2.4")
    if "Band 2:" in info or "Band 4:" in info:
        caps.bands.append("5")
    caps.supports_vht = "VHT" in info
    caps.supports_he = bool(re.search(r"\bHE\b|802\.11ax", info))
    caps.supports_sae = "SAE" in info
    caps.supports_ap = "AP" in info
    if caps.combinations:
        caps.ap_sta_combination = any(
            "managed" in combo and re.search(r"\bAP\b", combo) for combo in caps.combinations
        )
        if not caps.ap_sta_combination and "AP" not in info:
            caps.supports_ap = False
    return caps


def ap_sta_support(phy: str) -> bool | None:
    """Return True/False if the driver reports combinations, None if unknown."""
    caps = get_capabilities(phy)
    return caps.ap_sta_combination


def get_station_channel(interface: str) -> tuple[int | None, int | None]:
    wifi = get_interface(interface)
    if wifi is None:
        return None, None
    return wifi.channel, wifi.frequency


def get_reg_domain() -> str:
    _require_iw()
    result = run(["iw", "reg", "get"], check=False)
    if result.returncode != 0:
        return "00"
    match = COUNTRY_RE.search(result.stdout)
    return match.group(1) if match else "00"


def set_reg_domain(country: str) -> None:
    _require_iw()
    result = run(["iw", "reg", "set", country.upper()], check=False)
    if result.returncode != 0:
        raise CommandError(
            f"No pude configurar el dominio regulatorio {country.upper()}",
            ["iw", "reg", "set", country.upper()],
            stderr=result.stderr,
        )
    actual = get_reg_domain()
    if actual not in {country.upper(), "00"}:
        raise StateError(f"El dominio regulatorio quedó en {actual}, no en {country.upper()}.")


def channel_frequency(channel: int) -> int:
    if channel == 14:
        return 2484
    if 1 <= channel <= 13:
        return 2407 + channel * 5
    if 32 <= channel <= 196:
        return 5000 + channel * 5
    if channel >= 1:
        return 5000 + channel * 5
    raise StateError(f"Canal inválido: {channel}")


def channel_band(channel: int) -> str:
    return "2.4" if channel <= 14 else "5"


def _frequency_lines(info: str) -> list[tuple[int, str]]:
    """Return (channel, original line) for every frequency entry."""
    entries: list[tuple[int, str]] = []
    for line in info.splitlines():
        stripped = line.strip()
        if not stripped.startswith("*"):
            continue
        match = re.search(r"\[(\d+)\]", stripped)
        if match:
            entries.append((int(match.group(1)), stripped))
    return entries


def channel_allowed(phy: str, channel: int, *, allow_dfs: bool = False) -> tuple[bool, str]:
    info = get_phy_info(phy)
    entries = _frequency_lines(info)
    if not entries:
        return True, "sin información de canales"
    for number, line in entries:
        if number != channel:
            continue
        lower = line.lower()
        if "disabled" in lower:
            return False, "canal deshabilitado por el driver"
        if "no ir" in lower:
            return False, "canal sin radiación iniciada (no IR); configura el país"
        if "passive scan" in lower and not allow_dfs:
            return False, "canal de solo escaneo pasivo"
        if "radar detection" in lower and not allow_dfs:
            return False, "canal DFS; usa otro canal o permite DFS"
        return True, "permitido"
    return False, f"el canal {channel} no está disponible en {phy}"


def default_channel(phy: str, band: str, country: str | None = None) -> int:
    """Pick a conservative default channel for a dedicated AP."""
    preferred = [36, 40, 44, 48] if band == "5" else [1, 6, 11]
    if band == "auto":
        preferred = [36, 40, 44, 48, 1, 6, 11]
    for channel in preferred:
        allowed, _ = channel_allowed(phy, channel)
        if allowed:
            return channel
    if band == "5" and country in {None, "00"}:
        raise StateError("5 GHz requiere un país regulatorio. Usa --country con tu código ISO.")
    return 6 if band != "5" else 36


def create_ap_interface(phy: str, name: str, base_interface: str | None = None) -> None:
    _require_iw()
    if interface_exists(name):
        raise StateError(f"La interfaz {name!r} ya existe.")
    result = run(
        ["iw", "phy", phy, "interface", "add", name, "type", "__ap"],
        check=False,
    )
    if result.returncode == 0:
        return
    if base_interface:
        result = run(
            ["iw", "dev", base_interface, "interface", "add", name, "type", "__ap"],
            check=False,
        )
        if result.returncode == 0:
            return
    raise CommandError(
        f"No pude crear la interfaz AP {name!r} en {phy}",
        ["iw", "phy", phy, "interface", "add", name, "type", "__ap"],
        stderr=result.stderr,
    )


def delete_interface(name: str) -> None:
    if not interface_exists(name):
        return
    result = run(["iw", "dev", name, "del"], check=False)
    if result.returncode != 0:
        # Fall back to ip link delete for drivers that expose a normal link.
        run(["ip", "link", "delete", name], check=False)


def get_link(interface: str) -> dict[str, str] | None:
    result = run(["iw", "dev", interface, "link"], check=False)
    if result.returncode != 0 or "Not connected" in result.stdout:
        return None
    data: dict[str, str] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("Connected to "):
            data["bssid"] = line.split()[2]
        elif line.startswith("SSID: "):
            data["ssid"] = line[6:]
        elif line.startswith("freq: "):
            data["frequency"] = line.split()[1]
        elif line.startswith("signal: "):
            data["signal"] = line.split()[1]
        elif line.startswith("tx bitrate: "):
            data["tx_bitrate"] = line.split("tx bitrate: ", 1)[1]
    return data or None


def parse_station_dump(text: str) -> list[dict[str, int | str]]:
    stations: list[dict[str, int | str]] = []
    blocks = re.split(r"(?=^Station\s+)", text, flags=re.MULTILINE)
    for block in blocks:
        match = STATION_RE.match(block.strip())
        if not match:
            continue
        station: dict[str, int | str] = {"mac": match.group(1).lower()}
        for key, regex in (
            ("signal_dbm", SIGNAL_RE),
            ("rx_bytes", RX_BYTES_RE),
            ("tx_bytes", TX_BYTES_RE),
            ("connected_seconds", CONNECTED_RE),
            ("inactive_ms", INACTIVE_RE),
        ):
            found = regex.search(block)
            if found:
                station[key] = int(found.group(1))
        stations.append(station)
    return stations


def get_stations(interface: str) -> list[dict[str, int | str]]:
    result = run(["iw", "dev", interface, "station", "dump"], check=False)
    if result.returncode != 0:
        return []
    return parse_station_dump(result.stdout)


def disconnect_station(interface: str, mac: str) -> None:
    """Disconnect a client from the AP.

    hostapd_cli is preferred for a clean deauthentication frame; iw is a
    fallback for drivers that support station deletion.
    """
    result = run(["iw", "dev", interface, "station", "del", mac], check=False)
    if result.returncode != 0:
        raise CommandError(
            f"No pude expulsar al cliente {mac}",
            ["iw", "dev", interface, "station", "del", mac],
            stderr=result.stderr,
        )


def available_channels(phy: str, band: str | None = None) -> list[int]:
    info = get_phy_info(phy)
    channels: list[int] = []
    for number, line in _frequency_lines(info):
        if band == "2.4" and number > 14:
            continue
        if band == "5" and number <= 14:
            continue
        lower = line.lower()
        if "disabled" in lower or "no ir" in lower:
            continue
        channels.append(number)
    return sorted(set(channels))
