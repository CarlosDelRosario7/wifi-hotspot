"""GTK4 + Libadwaita front end for WiFi Hotspot.

The GUI is intentionally a thin client: all privileged work goes through
``wifi-hotspotd`` over its local Unix socket, so the interface never runs as
root and never manipulates the network directly.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import APP_NAME, __version__  # noqa: E402
from ..config import Paths  # noqa: E402
from ..daemon_client import DaemonClient, DaemonError, DaemonUnavailable  # noqa: E402
from ..models import HotspotConfig  # noqa: E402
from ..qr import wifi_qr_payload  # noqa: E402
from ..util import format_bytes, random_password  # noqa: E402

APP_ID = "io.github.carlosdelrosario7.WifiHotspot"
SECURITY_VALUES = ("wpa2", "wpa3", "mixed", "open")
SECURITY_LABELS = ("WPA2", "WPA3", "WPA2/WPA3", "Abierta")
BAND_VALUES = ("auto", "2.4", "5")
BAND_LABELS = ("Automática", "2.4 GHz", "5 GHz")
FIREWALL_VALUES = ("auto", "iptables", "nft", "firewalld")
FIREWALL_LABELS = ("Automático", "iptables", "nftables", "firewalld")


def _label_value(labels: tuple[str, ...], values: tuple[str, ...], value: str) -> int:
    try:
        return values.index(value)
    except ValueError:
        return 0


class HotspotWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, paths: Paths | None = None) -> None:
        super().__init__(application=application)
        self.paths = paths or Paths()
        self.client = DaemonClient(self.paths)
        self._busy = False
        self._refreshing = False
        self._poll_id = 0
        self._client_rows: list[Adw.ActionRow] = []
        self._qr_path: str | None = None
        self._daemon_available = True
        self._status = "unknown"
        self._profile_loaded = False

        self.set_title(APP_NAME)
        self.set_default_size(600, 820)
        self.set_size_request(420, 560)

        self._build_actions()
        self._build_ui()
        self._poll_id = GLib.timeout_add_seconds(2, self._poll)
        self.connect("close-request", self._on_close)
        self._refresh_async()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_actions(self) -> None:
        actions = {
            "start": self._start,
            "stop": self._stop,
            "refresh": self._refresh_async,
            "save-profile": self._save_profile,
            "load-profile": self._load_profile,
            "show-qr": self._show_qr,
            "start-daemon": self._start_daemon,
            "about": self._about,
            "quit": self._quit,
        }
        for name, callback in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _param, cb=callback: cb())
            self.add_action(action)

    def _build_menu(self) -> Gio.Menu:
        profile_menu = Gio.Menu()
        profile_menu.append("Guardar perfil", "win.save-profile")
        profile_menu.append("Cargar perfil", "win.load-profile")
        profile_menu.append("Compartir con QR", "win.show-qr")
        tool_menu = Gio.Menu()
        tool_menu.append("Iniciar daemon", "win.start-daemon")
        tool_menu.append("Comprobar de nuevo", "win.refresh")
        app_menu = Gio.Menu()
        app_menu.append("Acerca de", "win.about")
        app_menu.append("Salir", "win.quit")

        menu = Gio.Menu()
        menu.append_section("Perfil", profile_menu)
        menu.append_section("Sistema", tool_menu)
        menu.append_section(None, app_menu)
        return menu

    def _build_ui(self) -> None:
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        title = Adw.WindowTitle(title=APP_NAME, subtitle="Punto de acceso Wi-Fi")
        header.set_title_widget(title)

        self.start_button = Gtk.Button(label="Iniciar")
        self.start_button.add_css_class("suggested-action")
        self.start_button.set_tooltip_text("Iniciar o detener el hotspot")
        self.start_button.connect("clicked", lambda _button: self._toggle())
        header.pack_start(self.start_button)

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(self._build_menu())
        menu_button.set_tooltip_text("Menú")
        header.pack_end(menu_button)

        toolbar.add_top_bar(header)

        self.toast_overlay = Adw.ToastOverlay()
        self.preferences = Adw.PreferencesPage()
        self.toast_overlay.set_child(self.preferences)
        toolbar.set_content(self.toast_overlay)
        self.set_content(toolbar)

        self._build_status_group()
        self._build_network_group()
        self._build_advanced_group()
        self._build_clients_group()

    def _build_status_group(self) -> None:
        group = Adw.PreferencesGroup(title="Estado")
        self.status_row = Adw.ActionRow(title="Estado", subtitle="Comprobando…")
        refresh = Gtk.Button()
        refresh.set_icon_name("view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_tooltip_text("Actualizar")
        refresh.connect("clicked", lambda _button: self._refresh_async())
        self.status_row.add_suffix(refresh)
        group.add(self.status_row)

        self.network_row = Adw.ActionRow(title="Red", subtitle="-")
        group.add(self.network_row)
        self.connection_row = Adw.ActionRow(title="Conexión", subtitle="-")
        group.add(self.connection_row)
        self.clients_row = Adw.ActionRow(title="Clientes", subtitle="0")
        group.add(self.clients_row)
        self.preferences.add(group)

    def _build_network_group(self) -> None:
        group = Adw.PreferencesGroup(
            title="Red Wi-Fi",
            description="Los cambios se aplican al iniciar o reiniciar el hotspot.",
        )
        self.profile_row = Adw.EntryRow(title="Perfil")
        self.profile_row.set_text("default")
        group.add(self.profile_row)

        self.ssid_row = Adw.EntryRow(title="Nombre de la red (SSID)")
        self.ssid_row.set_text("LinuxAP")
        group.add(self.ssid_row)

        self.password_row = Adw.PasswordEntryRow(title="Contraseña")
        self.password_row.set_tooltip_text(
            "Déjala vacía para conservar la contraseña guardada del perfil."
        )
        generate = Gtk.Button()
        generate.set_icon_name("view-refresh-symbolic")
        generate.add_css_class("flat")
        generate.set_tooltip_text("Generar una contraseña segura")
        generate.connect("clicked", self._generate_password)
        self.password_row.add_suffix(generate)
        group.add(self.password_row)

        self.security_row = Adw.ComboRow(title="Seguridad")
        self.security_row.set_model(Gtk.StringList.new(list(SECURITY_LABELS)))
        self.security_row.set_selected(0)
        group.add(self.security_row)

        self.band_row = Adw.ComboRow(title="Banda")
        self.band_row.set_model(Gtk.StringList.new(list(BAND_LABELS)))
        self.band_row.set_selected(0)
        group.add(self.band_row)

        self.country_row = Adw.EntryRow(title="País")
        self.country_row.set_tooltip_text("Código ISO de dos letras, por ejemplo ES o MX.")
        group.add(self.country_row)

        channel_adjustment = Gtk.Adjustment(
            value=0, lower=0, upper=196, step_increment=1, page_increment=10, page_size=0
        )
        self.channel_row = Adw.SpinRow.new(channel_adjustment, 1.0, 0)
        self.channel_row.set_title("Canal")
        self.channel_row.set_subtitle("0 = automático")
        group.add(self.channel_row)

        self.hidden_row = Adw.SwitchRow(title="Red oculta")
        group.add(self.hidden_row)
        self.preferences.add(group)

    def _build_advanced_group(self) -> None:
        group = Adw.PreferencesGroup(title="Avanzado")
        self.ap_row = Adw.EntryRow(title="Interfaz AP")
        self.ap_row.set_text("wifi-ap")
        group.add(self.ap_row)

        self.uplink_row = Adw.EntryRow(title="Interfaz de salida")
        self.uplink_row.set_tooltip_text("Vacío = detectar automáticamente la ruta por defecto.")
        group.add(self.uplink_row)

        self.subnet_row = Adw.EntryRow(title="Subred IPv4")
        self.subnet_row.set_text("10.42.0.0/24")
        group.add(self.subnet_row)

        self.dns_row = Adw.EntryRow(title="Servidores DNS")
        self.dns_row.set_tooltip_text("Separados por comas. Vacío = 1.1.1.1 y 8.8.8.8.")
        group.add(self.dns_row)

        self.firewall_row = Adw.ComboRow(title="Firewall")
        self.firewall_row.set_model(Gtk.StringList.new(list(FIREWALL_LABELS)))
        self.firewall_row.set_selected(0)
        group.add(self.firewall_row)

        max_adjustment = Gtk.Adjustment(
            value=0, lower=0, upper=200, step_increment=1, page_increment=10, page_size=0
        )
        self.max_clients_row = Adw.SpinRow.new(max_adjustment, 1.0, 0)
        self.max_clients_row.set_title("Máximo de clientes")
        self.max_clients_row.set_subtitle("0 = sin límite")
        group.add(self.max_clients_row)

        self.isolation_row = Adw.SwitchRow(title="Aislar clientes")
        group.add(self.isolation_row)
        self.ipv6_row = Adw.SwitchRow(title="Habilitar IPv6")
        group.add(self.ipv6_row)
        self.preferences.add(group)

    def _build_clients_group(self) -> None:
        self.clients_group = Adw.PreferencesGroup(title="Clientes conectados")
        self.empty_client_row = Adw.ActionRow(title="No hay clientes conectados")
        self.clients_group.add(self.empty_client_row)
        self.preferences.add(self.clients_group)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    def _generate_password(self, _button: Gtk.Button) -> None:
        self.password_row.set_text(random_password())

    def _collect_overrides(self) -> dict[str, Any]:
        overrides: dict[str, Any] = {
            "security": SECURITY_VALUES[self.security_row.get_selected()],
            "band": BAND_VALUES[self.band_row.get_selected()],
            "firewall_backend": FIREWALL_VALUES[self.firewall_row.get_selected()],
            "hidden": self.hidden_row.get_active(),
            "client_isolation": self.isolation_row.get_active(),
            "ipv6": self.ipv6_row.get_active(),
            "max_clients": int(self.max_clients_row.get_value()),
        }
        ssid = self.ssid_row.get_text().strip()
        if ssid:
            overrides["ssid"] = ssid
        password = self.password_row.get_text()
        if password:
            overrides["password"] = password
        country = self.country_row.get_text().strip().upper()
        if country:
            overrides["country"] = country
        channel = int(self.channel_row.get_value())
        if channel > 0:
            overrides["channel"] = channel
        ap_interface = self.ap_row.get_text().strip()
        if ap_interface:
            overrides["ap_interface"] = ap_interface
        uplink = self.uplink_row.get_text().strip()
        if uplink:
            overrides["uplink"] = uplink
        subnet = self.subnet_row.get_text().strip()
        if subnet:
            overrides["subnet"] = subnet
        dns = [item.strip() for item in self.dns_row.get_text().split(",") if item.strip()]
        if dns:
            overrides["dns"] = dns
        return overrides

    def _apply_profile_summary(self, data: dict[str, Any]) -> None:
        self.profile_row.set_text(str(data.get("profile_name") or data.get("profile") or "default"))
        self.ssid_row.set_text(str(data.get("ssid") or "LinuxAP"))
        self.password_row.set_text("")
        if (
            not data.get("stored", True)
            and data.get("security") != "open"
            and not self.password_row.get_text()
        ):
            self.password_row.set_text(random_password())
        self.security_row.set_selected(
            _label_value(SECURITY_LABELS, SECURITY_VALUES, str(data.get("security") or "wpa2"))
        )
        self.band_row.set_selected(
            _label_value(BAND_LABELS, BAND_VALUES, str(data.get("band") or "auto"))
        )
        self.country_row.set_text(str(data.get("country") or ""))
        self.channel_row.set_value(int(data.get("channel") or 0))
        self.hidden_row.set_active(bool(data.get("hidden")))
        self.ap_row.set_text(str(data.get("ap_interface") or "wifi-ap"))
        self.uplink_row.set_text(str(data.get("uplink") or ""))
        self.subnet_row.set_text(str(data.get("subnet") or "10.42.0.0/24"))
        dns = data.get("dns") or []
        self.dns_row.set_text(", ".join(str(item) for item in dns))
        self.firewall_row.set_selected(
            _label_value(
                FIREWALL_LABELS,
                FIREWALL_VALUES,
                str(data.get("firewall_backend") or "auto"),
            )
        )
        self.max_clients_row.set_value(int(data.get("max_clients") or 0))
        self.isolation_row.set_active(bool(data.get("client_isolation")))
        self.ipv6_row.set_active(bool(data.get("ipv6")))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.start_button.set_sensitive(not busy)

    def _run_async(
        self,
        work: Callable[[], Any],
        on_success: Callable[[Any], None],
        *,
        busy: bool = True,
        refresh: bool = False,
    ) -> None:
        if busy and self._busy:
            return
        if busy:
            self._set_busy(True)

        def worker() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - shown to the user
                GLib.idle_add(self._async_error, exc, busy, refresh)
            else:
                GLib.idle_add(self._async_success, on_success, result, busy, refresh)

        threading.Thread(target=worker, daemon=True).start()

    def _async_success(
        self, callback: Callable[[Any], None], result: Any, busy: bool, refresh: bool
    ) -> bool:
        if busy:
            self._set_busy(False)
        if refresh:
            self._refreshing = False
        try:
            callback(result)
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)
        return False

    def _async_error(self, exc: Exception, busy: bool, refresh: bool) -> bool:
        if busy:
            self._set_busy(False)
        if refresh:
            self._refreshing = False
        self._show_error(exc)
        return False

    def _show_error(self, exc: Exception) -> None:
        if isinstance(exc, DaemonUnavailable):
            self._daemon_available = False
            self.status_row.set_subtitle("Daemon no disponible")
            self.start_button.set_sensitive(False)
        self._toast(str(exc))

    # ------------------------------------------------------------------
    # Status and clients
    # ------------------------------------------------------------------

    def _poll(self) -> bool:
        if not self._busy and not self._refreshing:
            self._refresh_async()
        return True

    def _refresh_async(self) -> None:
        if self._refreshing or self._busy:
            return
        self._refreshing = True
        self._run_async(self.client.status, self._apply_status, busy=False, refresh=True)

    def _apply_status(self, data: dict[str, Any]) -> None:
        self._daemon_available = True
        status = str(data.get("status") or "unknown")
        self._status = status
        if status == "running":
            self.status_row.set_subtitle("Activo")
            self.start_button.set_label("Detener")
            self.start_button.remove_css_class("suggested-action")
            self.start_button.add_css_class("destructive-action")
            self.start_button.set_sensitive(True)
            self.network_row.set_subtitle(
                f"{data.get('ssid', '')} · {data.get('security', '').upper()}"
            )
            self.connection_row.set_subtitle(
                f"{data.get('ap_interface', '')} · {data.get('uplink', '')} · "
                f"canal {data.get('channel', '?')} ({data.get('band', '?')} GHz) · "
                f"{data.get('ap_ip', '')}"
            )
        elif status == "stale":
            self.status_row.set_subtitle("Estado obsoleto: algunos procesos no están activos")
            self.start_button.set_label("Iniciar")
            self.start_button.remove_css_class("destructive-action")
            self.start_button.add_css_class("suggested-action")
            self.start_button.set_sensitive(True)
            self.network_row.set_subtitle(str(data.get("ssid") or "-"))
            self.connection_row.set_subtitle("Pulsa Detener para limpiar el estado")
        elif status == "error":
            self.status_row.set_subtitle(f"Error: {data.get('error', 'desconocido')}")
            self.start_button.set_label("Iniciar")
            self.start_button.remove_css_class("destructive-action")
            self.start_button.add_css_class("suggested-action")
            self.start_button.set_sensitive(True)
        else:
            self.status_row.set_subtitle("Detenido")
            self.start_button.set_label("Iniciar")
            self.start_button.remove_css_class("destructive-action")
            self.start_button.add_css_class("suggested-action")
            self.start_button.set_sensitive(True)
            self.network_row.set_subtitle("-")
            self.connection_row.set_subtitle("-")

        clients = data.get("clients") or []
        self._update_clients(clients)
        if not self._profile_loaded:
            self._profile_loaded = True
            self._load_profile_async("default", notify=False)

    def _update_clients(self, clients: list[dict[str, Any]]) -> None:
        for row in self._client_rows:
            self.clients_group.remove(row)
        self._client_rows.clear()
        self.clients_row.set_subtitle(str(len(clients)))
        self.empty_client_row.set_visible(not clients)
        for client in clients:
            mac = str(client.get("mac") or "")
            hostname = str(client.get("hostname") or mac)
            signal_dbm = client.get("signal_dbm")
            signal = f"{signal_dbm} dBm" if signal_dbm is not None else "señal desconocida"
            rx = format_bytes(int(client.get("rx_bytes") or 0))
            tx = format_bytes(int(client.get("tx_bytes") or 0))
            subtitle = f"{client.get('ip') or 'sin IP'} · {mac} · {signal} · RX {rx} · TX {tx}"
            row = Adw.ActionRow(title=hostname, subtitle=subtitle)
            kick = Gtk.Button()
            kick.set_icon_name("window-close-symbolic")
            kick.add_css_class("flat")
            kick.set_tooltip_text("Expulsar cliente")
            kick.connect("clicked", lambda _button, address=mac: self._disconnect(address))
            row.add_suffix(kick)
            self.clients_group.add(row)
            self._client_rows.append(row)

    def _disconnect(self, mac: str) -> None:
        self._run_async(
            lambda: self.client.disconnect(mac),
            lambda _result: self._toast("Cliente expulsado"),
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _toggle(self) -> None:
        if self._status in {"running", "stale"}:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        profile = self.profile_row.get_text().strip() or "default"
        overrides = self._collect_overrides()
        self._toast("Iniciando hotspot…")
        self._run_async(
            lambda: self.client.start(profile, overrides),
            self._on_started,
        )

    def _on_started(self, result: dict[str, Any]) -> None:
        config = result.get("config") or {}
        self._toast(f"Hotspot activo: {config.get('ssid', '')}")
        self.password_row.set_text("")
        self._refresh_async()

    def _stop(self) -> None:
        self._toast("Deteniendo hotspot…")
        self._run_async(
            self.client.stop,
            lambda stopped: self._toast("Hotspot detenido" if stopped else "No había hotspot"),
        )

    def _save_profile(self) -> None:
        profile = self.profile_row.get_text().strip() or "default"
        overrides = self._collect_overrides()
        self._run_async(
            lambda: self.client.save_profile(name=profile, overrides=overrides),
            lambda result: self._toast(f"Perfil «{result.get('saved', profile)}» guardado"),
        )

    def _load_profile_async(self, profile: str, *, notify: bool = True) -> None:
        self._run_async(
            lambda: self.client.get_profile_summary(profile),
            lambda data: self._on_profile_loaded(data, profile, notify),
            busy=False,
        )

    def _on_profile_loaded(self, data: dict[str, Any], profile: str, notify: bool) -> None:
        self._apply_profile_summary(data)
        self.profile_row.set_text(profile)
        if notify:
            self._toast(f"Perfil «{profile}» cargado")

    def _load_profile(self) -> None:
        profile = self.profile_row.get_text().strip() or "default"
        self._load_profile_async(profile)

    def _start_daemon(self) -> None:
        def work() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["pkexec", "systemctl", "start", "wifi-hotspotd.service"],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )

        def done(result: subprocess.CompletedProcess[str]) -> None:
            if result.returncode == 0:
                self._toast("Daemon iniciado")
                self._refresh_async()
            else:
                detail = (result.stderr or result.stdout).strip()
                self._toast(f"No pude iniciar el daemon: {detail}")

        self._run_async(work, done)

    def _show_qr(self) -> None:
        profile = self.profile_row.get_text().strip() or "default"
        form_password = self.password_row.get_text()
        form_ssid = self.ssid_row.get_text().strip()
        security = SECURITY_VALUES[self.security_row.get_selected()]
        hidden = self.hidden_row.get_active()

        def work() -> tuple[str, str]:
            password = form_password
            ssid = form_ssid
            if not password and security != "open":
                try:
                    data = self.client.get_profile(profile)
                except (DaemonError, DaemonUnavailable):
                    data = {}
                password = password or str(data.get("password") or "")
                ssid = ssid or str(data.get("ssid") or "LinuxAP")
            config = HotspotConfig(
                ssid=ssid or "LinuxAP",
                password=password,
                security=security,
                hidden=hidden,
            )
            config.validate()
            return wifi_qr_payload(config), ssid

        self._run_async(work, lambda result: self._display_qr(*result))

    def _display_qr(self, payload: str, ssid: str) -> None:
        path = os.path.join(tempfile.gettempdir(), f"wifi-hotspot-qr-{os.getpid()}.png")
        try:
            result = subprocess.run(
                ["qrencode", "-t", "PNG", "-o", path],
                input=payload.encode("utf-8"),
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._toast(f"No pude generar el QR: {exc}")
            return
        if result.returncode != 0:
            self._toast("No pude generar el QR. ¿Está instalado qrencode?")
            return

        dialog = Adw.Dialog()
        dialog.set_title("Compartir red")
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        picture = Gtk.Picture.new_for_filename(path)
        picture.set_can_shrink(True)
        picture.set_size_request(280, 280)
        box.append(picture)
        label = Gtk.Label(label=ssid)
        label.add_css_class("title-2")
        box.append(label)
        hint = Gtk.Label(label="Escanea este código para conectarte")
        hint.add_css_class("dim-label")
        box.append(hint)
        toolbar.set_content(box)
        dialog.set_child(toolbar)
        dialog.set_content_width(340)
        dialog.set_content_height(400)

        def cleanup(_dialog: Adw.Dialog) -> None:
            with contextlib.suppress(OSError):
                os.unlink(path)

        dialog.connect("closed", cleanup)
        dialog.present(self)

    def _about(self) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=__version__,
            developer_name="Carlos Del Rosario",
            website="https://github.com/CarlosDelRosario7/wifi-hotspot",
            issue_url="https://github.com/CarlosDelRosario7/wifi-hotspot/issues",
            license_type=Gtk.License.GPL_3_0,
        )
        about.set_developers(["Carlos Del Rosario"])
        about.present(self)

    def _quit(self) -> None:
        application = self.get_application()
        if application is not None:
            application.quit()

    def _on_close(self, _window: Adw.ApplicationWindow) -> bool:
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0
        return False


class HotspotApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window: HotspotWindow | None = None

    def do_activate(self) -> None:
        if self.window is None:
            self.window = HotspotWindow(self)
        self.window.present()


def main(argv: list[str] | None = None) -> int:
    # GTK would interpret CLI arguments as files to open. The GUI takes no
    # positional arguments, so only pass the program name.
    program = (argv or sys.argv)[0] if (argv or sys.argv) else "wifi-hotspot-gui"
    app = HotspotApplication()
    return app.run([program])


if __name__ == "__main__":
    raise SystemExit(main())
