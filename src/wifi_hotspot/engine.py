"""High-level hotspot orchestration and state machine."""

from __future__ import annotations

import contextlib
import ipaddress
import logging
from typing import Any

from . import __version__
from .config import Paths, save_profile
from .errors import CommandError, HotspotError, StateError
from .models import HotspotConfig
from .platform import deps, firewall, network, wifi
from .services import dnsmasq, hostapd
from .state import RuntimeState, load_state, remove_state, save_state
from .util import (
    command_exists,
    exclusive_lock,
    format_bytes,
    format_duration,
    is_root,
    require_root,
    run,
    utc_now,
)

log = logging.getLogger(__name__)


class HotspotEngine:
    def __init__(self, config: HotspotConfig, paths: Paths | None = None) -> None:
        self.config = config
        self.paths = paths or Paths()

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def start(self) -> RuntimeState:
        require_root()
        self.config.validate()
        deps.check_dependencies()
        self.paths.ensure_runtime()
        with exclusive_lock(self.paths.lock_file):
            existing = load_state(self.paths.state_file)
            if existing is not None:
                if existing.alive:
                    raise StateError(
                        "El hotspot ya está activo. Usa 'restart' para aplicar cambios."
                    )
                log.warning("Limpiando un estado anterior incompleto...")
                self._stop_state(existing, best_effort=True)
            return self._start_locked()

    def stop(self) -> bool:
        require_root()
        self.paths.ensure_runtime()
        with exclusive_lock(self.paths.lock_file):
            state = load_state(self.paths.state_file)
            if state is None:
                return False
            self._stop_state(state, best_effort=False)
            return True

    def restart(self) -> RuntimeState:
        require_root()
        self.stop()
        return self.start()

    def status(self) -> dict[str, Any]:
        try:
            state = load_state(self.paths.state_file)
        except StateError as exc:
            return {
                "status": "error",
                "error": str(exc),
                "state_file": str(self.paths.state_file),
            }
        if state is None:
            return {
                "status": "stopped",
                "state_file": str(self.paths.state_file),
                "ap_interface": self.config.ap_interface,
            }
        data = state.public_dict()
        data["ap_interface_present"] = wifi.interface_exists(state.ap_interface)
        data["ipv4"] = network.get_ipv4_addresses(state.ap_interface)
        data["default_route"] = network.get_default_route()
        data["clients"] = self.clients()
        if state.status == "running" and not state.alive:
            data["status"] = "stale"
        return data

    def clients(self) -> list[dict[str, Any]]:
        state = self._load_state_optional()
        if state is None or not state.ap_interface:
            return []
        if not wifi.interface_exists(state.ap_interface):
            return []
        stations = wifi.get_stations(state.ap_interface)
        leases = dnsmasq.parse_leases(self.paths.dnsmasq_leases)
        clients: list[dict[str, Any]] = []
        for station in stations:
            mac = str(station.get("mac", "")).lower()
            lease = leases.get(mac, {})
            clients.append(
                {
                    "mac": mac,
                    "ip": lease.get("ip", ""),
                    "hostname": lease.get("hostname", ""),
                    "signal_dbm": station.get("signal_dbm"),
                    "rx_bytes": station.get("rx_bytes", 0),
                    "tx_bytes": station.get("tx_bytes", 0),
                    "connected_seconds": station.get("connected_seconds", 0),
                    "inactive_ms": station.get("inactive_ms", 0),
                }
            )
        clients.sort(key=lambda item: (item["ip"] == "", item["ip"], item["mac"]))
        return clients

    def disconnect_client(self, mac: str) -> None:
        require_root()
        state = self._load_state_optional()
        if state is None or not state.alive:
            raise StateError("El hotspot no está activo.")
        wifi.disconnect_station(state.ap_interface, mac.lower())

    def doctor(self) -> dict[str, Any]:
        distro = deps.detect_distro()
        missing = deps.missing_commands()
        data: dict[str, Any] = {
            "version": __version__,
            "distro": distro.as_dict(),
            "root": is_root(),
            "missing_dependencies": missing,
            "install_hint": deps.install_hint(distro, missing) if missing else "",
            "commands": {
                command: command_exists(command)
                for command in (
                    "iw",
                    "ip",
                    "hostapd",
                    "dnsmasq",
                    "nmcli",
                    "iptables",
                    "nft",
                    "firewall-cmd",
                    "rfkill",
                    "qrencode",
                )
            },
            "firewall_backends": {},
            "wifi": [],
            "reg_domain": None,
            "default_route": network.get_default_route(),
            "uplink": None,
            "uplink_link": None,
            "dns_port_53": [],
            "notes": [],
        }
        for name, backend_class in firewall.BACKENDS.items():
            try:
                data["firewall_backends"][name] = backend_class().is_available()
            except Exception as exc:  # pragma: no cover - defensive
                data["firewall_backends"][name] = f"error: {exc}"

        try:
            data["reg_domain"] = wifi.get_reg_domain()
        except HotspotError as exc:
            data["notes"].append(str(exc))

        try:
            data["uplink"] = self.config.uplink or network.detect_uplink()
        except HotspotError as exc:
            data["notes"].append(str(exc))

        if data["uplink"]:
            data["uplink_link"] = wifi.get_link(str(data["uplink"]))
            if data["uplink_link"] is None:
                carrier = network.get_carrier(str(data["uplink"]))
                if carrier is False:
                    data["notes"].append(
                        f"{data['uplink']} no tiene portadora (¿cable desconectado?)."
                    )

        try:
            interfaces = wifi.list_interfaces()
        except HotspotError as exc:
            interfaces = []
            data["notes"].append(str(exc))
        for interface in interfaces:
            entry: dict[str, Any] = {
                "name": interface.name,
                "phy": interface.phy,
                "type": interface.type,
                "ssid": interface.ssid,
                "channel": interface.channel,
                "frequency": interface.frequency,
                "width": interface.width,
            }
            try:
                caps = wifi.get_capabilities(interface.phy)
                entry["capabilities"] = {
                    "bands": caps.bands,
                    "ap_sta": caps.ap_sta_combination,
                    "vht": caps.supports_vht,
                    "he": caps.supports_he,
                    "sae": caps.supports_sae,
                    "ap": caps.supports_ap,
                }
                if caps.ap_sta_combination is False:
                    data["notes"].append(
                        f"{interface.phy} no soporta AP+STA simultáneos; "
                        "usa Ethernet o una segunda tarjeta Wi-Fi."
                    )
            except HotspotError as exc:
                entry["capabilities_error"] = str(exc)
            data["wifi"].append(entry)

        if data["reg_domain"] == "00":
            data["notes"].append(
                "Dominio regulatorio sin configurar (00): 5 GHz puede estar bloqueado."
            )

        try:
            result = run(["ss", "-lunp"], check=False)
            data["dns_port_53"] = [
                line.strip() for line in result.stdout.splitlines() if ":53 " in line
            ]
        except HotspotError:
            pass

        if self.config.subnet:
            try:
                hotspot_network = ipaddress.ip_network(self.config.subnet, strict=False)
                if data["uplink"] and network.subnet_overlaps_interface(
                    str(data["uplink"]), hotspot_network
                ):
                    data["notes"].append(
                        f"La subred {hotspot_network} se solapa con el uplink "
                        f"{data['uplink']}; cambia la subred del hotspot."
                    )
            except ValueError:
                data["notes"].append("La subred configurada no es válida.")

        data["state"] = self.status()
        return data

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _start_locked(self) -> RuntimeState:
        state: RuntimeState | None = None
        try:
            uplink = self._select_uplink()
            ap_phy, ap_base, same_phy = self._select_ap_radio(uplink)
            capabilities = wifi.get_capabilities(ap_phy)
            if not capabilities.supports_ap:
                raise StateError(f"{ap_phy} no reporta soporte para modo AP.")
            if same_phy:
                support = capabilities.ap_sta_combination
                if support is False:
                    raise StateError(
                        f"{ap_phy} no soporta AP+STA simultáneos. "
                        "Conecta el uplink por Ethernet o usa otra tarjeta Wi-Fi."
                    )
                if support is None:
                    log.warning(
                        "No pude verificar las combinaciones de %s; intentaré AP+STA.",
                        ap_phy,
                    )

            country = self._configure_country()
            channel, band = self._select_channel(
                uplink=uplink,
                ap_phy=ap_phy,
                capabilities=capabilities,
                same_phy=same_phy,
                country=country,
            )
            self._validate_security(capabilities, channel)
            self._check_subnet_overlap(uplink)
            self._ensure_ap_interface_available()

            state = RuntimeState(
                status="starting",
                ssid=self.config.ssid,
                security=self.config.security,
                ap_interface=self.config.ap_interface,
                uplink=uplink,
                phy=ap_phy,
                channel=channel,
                band=band,
                ap_ip=self.config.ap_ip,
                network=str(self.config.network),
            )
            save_state(self.paths.state_file, state)

            nm = network.NetworkManager()
            if nm.available:
                # The drop-in must exist before the interface is created, or
                # NetworkManager may grab it and change its type to managed.
                nm.prepare_unmanaged(self.config.ap_interface)
                state.nm_unmanaged_configured = True
                save_state(self.paths.state_file, state)

            wifi.create_ap_interface(ap_phy, self.config.ap_interface, base_interface=ap_base)
            state.created_ap_interface = True
            save_state(self.paths.state_file, state)

            network.ip_addr_flush(self.config.ap_interface)
            network.ip_addr_add(
                self.config.ap_interface, f"{self.config.ap_ip}/{self.config.prefix_length}"
            )
            network.ip_link_up(self.config.ap_interface)
            if not self.config.ipv6:
                network.disable_ipv6(self.config.ap_interface)

            hostapd_paths = hostapd.HostapdPaths(
                config=self.paths.hostapd_conf,
                pid=self.paths.hostapd_pid,
                log=self.paths.hostapd_log,
                control_dir=self.paths.hostapd_ctrl,
            )
            hostapd.prepare_control_dir(hostapd_paths.control_dir)
            hostapd.write_config(
                hostapd_paths.config,
                hostapd.build_config(
                    self.config,
                    ap_interface=self.config.ap_interface,
                    channel=channel,
                    capabilities=capabilities,
                    control_dir=hostapd_paths.control_dir,
                ),
            )

            dnsmasq_paths = dnsmasq.DnsmasqPaths(
                config=self.paths.dnsmasq_conf,
                pid=self.paths.dnsmasq_pid,
                log=self.paths.dnsmasq_log,
                leases=self.paths.dnsmasq_leases,
            )
            dnsmasq.write_config(
                dnsmasq_paths.config,
                dnsmasq.build_config(
                    self.config,
                    ap_interface=self.config.ap_interface,
                    lease_file=dnsmasq_paths.leases,
                    dns_mode="server",
                ),
            )

            state.ip_forward_previous = network.enable_ip_forward()
            save_state(self.paths.state_file, state)

            firewall_backend = firewall.detect_backend(self.config.firewall_backend)
            context = firewall.FirewallContext(
                ap_interface=self.config.ap_interface,
                uplink=uplink,
                network=str(self.config.network),
            )
            state.firewall_backend = firewall_backend.name
            save_state(self.paths.state_file, state)
            firewall_backend.setup(context)

            state.hostapd_pid = hostapd.start(
                hostapd_paths.config, hostapd_paths.pid, hostapd_paths.log
            )
            save_state(self.paths.state_file, state)

            try:
                state.dnsmasq_pid = dnsmasq.start(
                    dnsmasq_paths.config, dnsmasq_paths.pid, dnsmasq_paths.log
                )
            except CommandError as exc:
                if not self._dnsmasq_port_conflict(exc):
                    raise
                log.warning(
                    "El puerto 53 está ocupado; dnsmasq solo dará DHCP y los "
                    "clientes usarán DNS upstream."
                )
                dnsmasq.write_config(
                    dnsmasq_paths.config,
                    dnsmasq.build_config(
                        self.config,
                        ap_interface=self.config.ap_interface,
                        lease_file=dnsmasq_paths.leases,
                        dns_mode="upstream",
                    ),
                )
                state.dns_mode = "upstream"
                state.dnsmasq_pid = dnsmasq.start(
                    dnsmasq_paths.config, dnsmasq_paths.pid, dnsmasq_paths.log
                )
            save_state(self.paths.state_file, state)

            state.status = "running"
            state.started_at = utc_now()
            state.last_error = None
            save_state(self.paths.state_file, state)

            try:
                save_profile(self.paths, self.config)
            except HotspotError as exc:
                log.warning("No pude guardar el perfil para reinicios: %s", exc)

            return state
        except Exception as exc:
            log.debug("Fallo al iniciar el hotspot", exc_info=True)
            if state is not None:
                state.last_error = str(exc)
                with contextlib.suppress(OSError):
                    save_state(self.paths.state_file, state)
                self._stop_state(state, best_effort=True)
            if isinstance(exc, HotspotError):
                raise
            raise StateError(f"No pude iniciar el hotspot: {exc}") from exc

    def _select_uplink(self) -> str:
        uplink = self.config.uplink or network.detect_uplink()
        if not network.interface_exists(uplink):
            raise StateError(f"La interfaz de uplink {uplink!r} no existe.")
        if uplink == self.config.ap_interface:
            raise StateError("La interfaz AP y el uplink no pueden ser la misma.")
        if uplink == "lo":
            raise StateError("El uplink no puede ser la interfaz loopback.")
        carrier = network.get_carrier(uplink)
        if carrier is False:
            raise StateError(
                f"{uplink} no tiene conexión física (portadora). "
                "Conecta el cable o elige otra interfaz."
            )
        return uplink

    def _select_ap_radio(self, uplink: str) -> tuple[str, str | None, bool]:
        interfaces = wifi.list_interfaces()
        uplink_wifi = next((item for item in interfaces if item.name == uplink), None)
        if uplink_wifi is not None:
            link = wifi.get_link(uplink)
            if link is None:
                raise StateError(
                    f"{uplink} es Wi-Fi pero no está conectada a una red. "
                    "Conéctala antes de iniciar el hotspot."
                )
            return uplink_wifi.phy, uplink, True

        candidates = [
            item
            for item in interfaces
            if item.name != uplink and item.type not in {"P2P-device", "P2P-client"}
        ]
        if not candidates:
            raise StateError(
                "No encontré una interfaz Wi-Fi para crear el punto de acceso. "
                "Conecta una tarjeta Wi-Fi o usa una segunda interfaz."
            )
        candidate = candidates[0]
        return candidate.phy, candidate.name, False

    def _configure_country(self) -> str:
        current = wifi.get_reg_domain()
        if self.config.country and current != self.config.country:
            log.info("Configurando dominio regulatorio: %s", self.config.country)
            wifi.set_reg_domain(self.config.country)
            current = wifi.get_reg_domain()
        return current

    def _select_channel(
        self,
        *,
        uplink: str,
        ap_phy: str,
        capabilities: wifi.PhyCapabilities,
        same_phy: bool,
        country: str,
    ) -> tuple[int, str]:
        if same_phy:
            sta_channel, _frequency = wifi.get_station_channel(uplink)
            if sta_channel is None:
                raise StateError(
                    f"No pude detectar el canal de {uplink}. La conexión debe estar activa."
                )
            band = wifi.channel_band(sta_channel)
            if self.config.channel is not None and self.config.channel != sta_channel:
                raise StateError(
                    f"En AP+STA la AP debe usar el mismo canal que la conexión "
                    f"({sta_channel}); no puedes forzar el canal {self.config.channel}."
                )
            if self.config.band != "auto" and self.config.band != band:
                raise StateError(
                    f"La conexión usa {band} GHz; no puedes forzar {self.config.band} GHz "
                    "en la misma tarjeta."
                )
            allowed, reason = wifi.channel_allowed(ap_phy, sta_channel)
            if not allowed:
                raise StateError(f"El canal {sta_channel} no es utilizable para la AP: {reason}.")
            return sta_channel, band

        band = self.config.band
        if band == "auto":
            band = "5" if "5" in capabilities.bands and country not in {"00", ""} else "2.4"
        if band == "5":
            if "5" not in capabilities.bands:
                raise StateError(f"{ap_phy} no soporta 5 GHz.")
            if country in {"00", ""}:
                raise StateError(
                    "5 GHz requiere configurar el país regulatorio. "
                    "Usa --country con tu código ISO (por ejemplo ES o MX)."
                )

        if self.config.channel is not None:
            channel = self.config.channel
            actual_band = wifi.channel_band(channel)
            if band != "auto" and actual_band != band:
                raise StateError(f"El canal {channel} es de {actual_band} GHz, no de {band} GHz.")
            band = actual_band
        else:
            channel = wifi.default_channel(ap_phy, band, country)

        allowed, reason = wifi.channel_allowed(ap_phy, channel)
        if not allowed:
            available = wifi.available_channels(ap_phy, band)
            raise StateError(
                f"El canal {channel} no está disponible: {reason}. "
                f"Canales disponibles en {band} GHz: {available}"
            )
        return channel, band

    def _validate_security(self, capabilities: wifi.PhyCapabilities, channel: int) -> None:
        if self.config.security in {"wpa3", "mixed"} and not capabilities.supports_sae:
            raise StateError("La tarjeta no reporta soporte SAE; elige WPA2 o red abierta.")
        if self.config.security == "wpa3" and wifi.channel_band(channel) == "2.4":
            # WPA3 works on 2.4 GHz, so this is only a capability note, not an
            # error. Keep the branch for future policy decisions.
            log.debug("WPA3 solicitado en 2.4 GHz; se usará SAE si el driver lo permite.")

    def _check_subnet_overlap(self, uplink: str) -> None:
        if network.subnet_overlaps_interface(uplink, self.config.network):
            raise StateError(
                f"La subred del hotspot {self.config.network} se solapa con la red "
                f"del uplink {uplink}. Usa otra subred, por ejemplo 10.43.0.0/24."
            )

    def _ensure_ap_interface_available(self) -> None:
        if wifi.interface_exists(self.config.ap_interface):
            raise StateError(
                f"La interfaz {self.config.ap_interface!r} ya existe y no pertenece "
                "a un hotspot activo. Elimínala o elige otro nombre con --ap-interface."
            )

    @staticmethod
    def _dnsmasq_port_conflict(exc: CommandError) -> bool:
        text = f"{exc} {exc.stderr}".lower()
        markers = (
            "address already in use",
            "failed to create listening socket",
            "failed to bind",
            "port 53",
            "cannot bind",
        )
        return any(marker in text for marker in markers)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _stop_state(self, state: RuntimeState, *, best_effort: bool) -> None:
        errors: list[str] = []

        try:
            dnsmasq.stop(
                self.paths.dnsmasq_pid,
                self.paths.dnsmasq_conf if self.paths.dnsmasq_conf.exists() else None,
            )
        except Exception as exc:
            errors.append(f"dnsmasq: {exc}")

        try:
            hostapd.stop(
                self.paths.hostapd_pid,
                self.paths.hostapd_conf if self.paths.hostapd_conf.exists() else None,
            )
        except Exception as exc:
            errors.append(f"hostapd: {exc}")

        if state.firewall_backend:
            try:
                backend = firewall.backend_by_name(state.firewall_backend, check_available=False)
                context = firewall.FirewallContext(
                    ap_interface=state.ap_interface,
                    uplink=state.uplink,
                    network=state.network,
                )
                backend.teardown(context)
            except Exception as exc:
                errors.append(f"firewall {state.firewall_backend}: {exc}")

        if state.ip_forward_previous is not None:
            try:
                network.restore_ip_forward(state.ip_forward_previous)
            except Exception as exc:
                errors.append(f"ip_forward: {exc}")

        if state.created_ap_interface and state.ap_interface:
            try:
                network.ip_addr_flush(state.ap_interface)
                wifi.delete_interface(state.ap_interface)
            except Exception as exc:
                errors.append(f"interfaz {state.ap_interface}: {exc}")

        if state.nm_unmanaged_configured and state.ap_interface:
            try:
                network.NetworkManager().clear_unmanaged(state.ap_interface)
            except Exception as exc:
                errors.append(f"NetworkManager: {exc}")

        try:
            self.paths.cleanup_runtime_files()
        except Exception as exc:
            errors.append(f"archivos temporales: {exc}")

        remove_state(self.paths.state_file)

        if errors:
            message = "El hotspot se detuvo con advertencias:\n  " + "\n  ".join(errors)
            if best_effort:
                log.warning(message)
            else:
                raise HotspotError(message)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_state_optional(self) -> RuntimeState | None:
        try:
            return load_state(self.paths.state_file)
        except StateError:
            return None

    def summary(self, state: RuntimeState | None = None) -> dict[str, Any]:
        if state is None:
            state = self._load_state_optional()
        if state is None:
            return {"status": "stopped"}
        data = state.public_dict()
        data["clients"] = self.clients()
        data["client_count"] = len(data["clients"])
        return data

    @staticmethod
    def format_client(client: dict[str, Any]) -> str:
        signal = client.get("signal_dbm")
        signal_text = f"{signal} dBm" if signal is not None else "sin señal"
        ip = client.get("ip") or "sin IP"
        hostname = client.get("hostname") or client.get("mac", "")
        return (
            f"{hostname:<24} {ip:<15} {client.get('mac', ''):<17} {signal_text:>10} "
            f"RX {format_bytes(int(client.get('rx_bytes', 0))):>9} "
            f"TX {format_bytes(int(client.get('tx_bytes', 0))):>9} "
            f"{format_duration(int(client.get('connected_seconds', 0)))}"
        )
