"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from . import APP_NAME, __version__
from .config import Paths, default_profile, load_profile
from .engine import HotspotEngine
from .errors import HotspotError, ValidationError
from .models import HotspotConfig
from .qr import wifi_qr_payload
from .util import command_exists, format_bytes, format_duration, random_password, run

log = logging.getLogger(__name__)

DEFAULT_PATHS = Paths()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wifi-hotspot",
        description="Crea y gestiona un punto de acceso Wi-Fi en Linux.",
        epilog='Ejemplo: sudo wifi-hotspot start "MiHotspot" "MiClave123"',
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Mostrar logs de depuración")
    parser.add_argument("--json", action="store_true", help="Salida JSON legible por máquinas")

    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--profile", default="default", help="Nombre del perfil")
    config_parent.add_argument("--ssid", dest="ssid_option", help="SSID de la red")
    config_parent.add_argument("--password", dest="password_option", help="Contraseña WPA")
    config_parent.add_argument(
        "--generate-password", action="store_true", help="Generar una contraseña segura"
    )
    config_parent.add_argument("--band", choices=["auto", "2.4", "5"], default=None)
    config_parent.add_argument("--channel", type=int, default=None)
    config_parent.add_argument(
        "--security", choices=["wpa2", "wpa3", "mixed", "open"], default=None
    )
    config_parent.add_argument("--country", default=None, help="Código ISO del país (ES, MX...)")
    config_parent.add_argument("--hidden", action="store_true", default=None)
    config_parent.add_argument("--max-clients", type=int, default=None)
    config_parent.add_argument("--client-isolation", action="store_true", default=None)
    config_parent.add_argument("--ipv6", action="store_true", default=None)
    config_parent.add_argument(
        "--subnet", default=None, help="Subred IPv4, por ejemplo 10.42.0.0/24"
    )
    config_parent.add_argument("--dhcp-start", default=None)
    config_parent.add_argument("--dhcp-end", default=None)
    config_parent.add_argument("--lease-time", default=None)
    config_parent.add_argument("--dns", action="append", default=None, metavar="IP")
    config_parent.add_argument("--uplink", default=None, help="Interfaz de salida")
    config_parent.add_argument("--ap-interface", default=None, help="Nombre de la interfaz AP")
    config_parent.add_argument(
        "--firewall-backend",
        choices=["auto", "iptables", "nft", "firewalld"],
        default=None,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", parents=[config_parent], help="Iniciar el hotspot")
    start.add_argument("ssid_positional", nargs="?", help=argparse.SUPPRESS)
    start.add_argument("password_positional", nargs="?", help=argparse.SUPPRESS)

    stop = subparsers.add_parser("stop", help="Detener el hotspot")
    stop.set_defaults(ssid_positional=None, password_positional=None)

    restart = subparsers.add_parser("restart", parents=[config_parent], help="Reiniciar el hotspot")
    restart.add_argument("ssid_positional", nargs="?", help=argparse.SUPPRESS)
    restart.add_argument("password_positional", nargs="?", help=argparse.SUPPRESS)

    subparsers.add_parser("status", help="Mostrar el estado del hotspot")
    subparsers.add_parser("clients", help="Listar los clientes conectados")
    subparsers.add_parser("doctor", help="Diagnosticar el sistema y el hardware")
    subparsers.add_parser("qr", help="Mostrar el QR del perfil guardado")
    subparsers.add_parser("profiles", help="Listar los perfiles guardados")
    subparsers.add_parser("version", help="Mostrar la versión")

    return parser


def _resolve_config(args: argparse.Namespace, paths: Paths = DEFAULT_PATHS) -> HotspotConfig:
    stored = load_profile(paths, args.profile) if args.profile else None
    config = stored.copy() if stored is not None else HotspotConfig()
    config.profile_name = args.profile or "default"

    ssid = args.ssid_option or args.ssid_positional
    if ssid:
        config.ssid = ssid
    password = args.password_option or args.password_positional
    if args.generate_password:
        config.password = random_password()
    elif password is not None:
        config.password = password

    for attribute in (
        "band",
        "channel",
        "security",
        "country",
        "hidden",
        "max_clients",
        "client_isolation",
        "ipv6",
        "subnet",
        "dhcp_start",
        "dhcp_end",
        "lease_time",
        "uplink",
        "ap_interface",
        "firewall_backend",
    ):
        value = getattr(args, attribute, None)
        if value is not None:
            setattr(config, attribute, value)
    if args.dns is not None:
        config.dns = list(args.dns)
    if config.security == "open":
        config.password = ""
    config.validate()
    return config


def _print_start(state: Any, config: HotspotConfig) -> None:
    print()
    print("==========================================")
    print("          HOTSPOT ACTIVO")
    print("==========================================")
    print()
    print(f"SSID:       {config.ssid}")
    print(f"Password:   {config.password}")
    print(f"Seguridad:  {config.security_label}")
    print(f"AP:         {state.ap_interface}")
    print(f"Uplink:     {state.uplink}")
    print(f"Gateway:    {state.ap_ip}")
    print(f"DHCP:       {config.dhcp_start_address} - {config.dhcp_end_address}")
    print(f"Canal:      {state.channel} ({state.band} GHz)")
    print(f"Firewall:   {state.firewall_backend}")
    if state.dns_mode == "upstream":
        print("DNS:        upstream (el puerto 53 estaba ocupado)")
    print()
    print("==========================================")


def _print_status(data: dict[str, Any]) -> None:
    status = data.get("status", "unknown")
    print()
    print(f"Estado:     {status}")
    if status == "stopped":
        print("El hotspot no está activo.")
        print()
        return
    if status == "error":
        print(f"Error: {data.get('error', 'desconocido')}")
        print()
        return
    print(f"SSID:       {data.get('ssid', '')}")
    print(f"Seguridad:  {data.get('security', '')}")
    print(
        f"AP:         {data.get('ap_interface', '')} {'presente' if data.get('ap_interface_present') else 'ausente'}"
    )
    print(f"Uplink:     {data.get('uplink', '')}")
    print(f"Gateway:    {data.get('ap_ip', '')}")
    print(f"Canal:      {data.get('channel', '')} ({data.get('band', '')} GHz)")
    print(f"Firewall:   {data.get('firewall_backend', '')}")
    print(f"hostapd:    {'RUNNING' if data.get('hostapd_alive') else 'STOPPED'}")
    print(f"dnsmasq:    {'RUNNING' if data.get('dnsmasq_alive') else 'STOPPED'}")
    if data.get("ipv4"):
        print(f"IPv4:       {', '.join(data['ipv4'])}")
    print()
    clients = data.get("clients") or []
    if clients:
        print(f"Clientes ({len(clients)}):")
        for client in clients:
            print("  " + HotspotEngine.format_client(client))
    else:
        print("Clientes:   0")
    print()


def _print_clients(clients: list[dict[str, Any]]) -> None:
    if not clients:
        print("No hay clientes conectados.")
        return
    print(f"{'HOSTNAME':<24} {'IP':<15} {'MAC':<17} {'SEÑAL':>10} {'RX':>10} {'TX':>10} TIEMPO")
    for client in clients:
        signal = client.get("signal_dbm")
        signal_text = f"{signal} dBm" if signal is not None else "-"
        print(
            f"{(client.get('hostname') or '-'):<24} "
            f"{(client.get('ip') or '-'):<15} "
            f"{client.get('mac', ''):<17} "
            f"{signal_text:>10} "
            f"{format_bytes(int(client.get('rx_bytes', 0))):>10} "
            f"{format_bytes(int(client.get('tx_bytes', 0))):>10} "
            f"{format_duration(int(client.get('connected_seconds', 0)))}"
        )


def _print_doctor(data: dict[str, Any]) -> None:
    distro = data["distro"]
    print()
    print(f"{APP_NAME} {data['version']}")
    print(f"Distro:      {distro['name']} ({distro['family']})")
    print(f"Kernel:      {distro['kernel']}")
    print(f"Root:        {'sí' if data['root'] else 'no'}")
    print(f"Estado:      {data['state'].get('status', 'unknown')}")
    print()
    print("Dependencias:")
    for command, present in data["commands"].items():
        print(f"  {command:<14} {'OK' if present else 'FALTA'}")
    if data.get("missing_dependencies"):
        print()
        print(f"Instala:     {data.get('install_hint', '')}")
    print()
    print("Firewall:")
    for name, available in data.get("firewall_backends", {}).items():
        print(f"  {name:<14} {'disponible' if available else 'no disponible'}")
    print()
    print(f"Regdomain:   {data.get('reg_domain') or 'desconocido'}")
    print(f"Uplink:      {data.get('uplink') or 'no detectado'}")
    route = data.get("default_route") or {}
    if route:
        print(f"Ruta:        via {route.get('via', '?')} dev {route.get('dev', '?')}")
    print()
    print("Wi-Fi:")
    if not data.get("wifi"):
        print("  No se detectaron interfaces Wi-Fi.")
    for interface in data.get("wifi", []):
        caps = interface.get("capabilities", {})
        bands = ", ".join(caps.get("bands", [])) or "?"
        ap_sta = caps.get("ap_sta")
        ap_sta_text = "sí" if ap_sta else ("no" if ap_sta is False else "desconocido")
        print(f"  {interface['name']} ({interface['phy']}): bandas {bands}, AP+STA {ap_sta_text}")
        if interface.get("capabilities_error"):
            print(f"    error: {interface['capabilities_error']}")
    if data.get("dns_port_53"):
        print()
        print("Puerto 53 UDP:")
        for line in data["dns_port_53"]:
            print(f"  {line}")
    if data.get("notes"):
        print()
        print("Notas:")
        for note in data["notes"]:
            print(f"  - {note}")
    print()


def _print_profiles(paths: Paths) -> None:
    from .config import list_profiles

    profiles = list_profiles(paths)
    if not profiles:
        print("No hay perfiles guardados.")
        return
    for profile in profiles:
        print(profile)


def _print_qr(config: HotspotConfig) -> None:
    payload = wifi_qr_payload(config)
    if not command_exists("qrencode"):
        raise ValidationError(
            "Se necesita 'qrencode' para mostrar el QR. Instálalo con el gestor de paquetes."
        )
    result = run(["qrencode", "-t", "ANSIUTF8", "-o", "-"], input_text=payload)
    print(result.stdout, end="")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
        force=True,
    )
    paths = DEFAULT_PATHS

    try:
        if args.command == "version":
            print(f"{APP_NAME} {__version__}")
            return 0

        if args.command == "start":
            config = _resolve_config(args, paths)
            engine = HotspotEngine(config, paths)
            state = engine.start()
            if args.json:
                payload = state.public_dict()
                payload["password"] = config.password
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                _print_start(state, config)
            return 0

        if args.command == "stop":
            engine = HotspotEngine(HotspotConfig(), paths)
            stopped = engine.stop()
            if args.json:
                print(json.dumps({"stopped": stopped}, indent=2))
            else:
                print("Hotspot detenido." if stopped else "No había un hotspot activo.")
            return 0

        if args.command == "restart":
            config = _resolve_config(args, paths)
            engine = HotspotEngine(config, paths)
            state = engine.restart()
            if args.json:
                print(json.dumps(state.public_dict(), indent=2, ensure_ascii=False))
            else:
                _print_start(state, config)
            return 0

        if args.command == "status":
            engine = HotspotEngine(default_profile(paths), paths)
            data = engine.status()
            if args.json:
                print(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                _print_status(data)
            return 0

        if args.command == "clients":
            engine = HotspotEngine(default_profile(paths), paths)
            clients = engine.clients()
            if args.json:
                print(json.dumps(clients, indent=2, ensure_ascii=False))
            else:
                _print_clients(clients)
            return 0

        if args.command == "doctor":
            engine = HotspotEngine(default_profile(paths), paths)
            data = engine.doctor()
            if args.json:
                print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
            else:
                _print_doctor(data)
            return 0

        if args.command == "profiles":
            _print_profiles(paths)
            return 0

        if args.command == "qr":
            config = load_profile(paths, args.profile or "default") or default_profile(paths)
            _print_qr(config)
            return 0

        parser.print_help()
        return 1
    except HotspotError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
