"""Privileged daemon exposing the hotspot engine over a Unix socket.

The daemon is intentionally small: it validates requests, checks polkit for
privileged methods, and delegates all networking to :class:`HotspotEngine`.
The GUI and an unprivileged CLI can therefore manage the hotspot without
running as root themselves.

Protocol: one JSON object per line.

    {"id": 1, "method": "status", "params": {}}
    {"id": 1, "ok": true, "result": {...}}
    {"id": 1, "ok": false, "error": "...", "code": "permission"}

The socket lives in ``/run/wifi-hotspot/daemon.sock`` and is recreated on each
start. Peer credentials are read with ``SO_PEERCRED``; uid 0 is always allowed,
other users go through ``pkcheck`` with the action defined in
``data/polkit/io.github.carlosdelrosario7.WifiHotspot.policy``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import socketserver
import struct
import sys
import threading
from pathlib import Path
from typing import Any

from . import APP_NAME, __version__
from .config import (
    Paths,
    default_profile,
    delete_profile,
    list_profiles,
    load_profile,
    resolve_config,
    save_profile,
)
from .engine import HotspotEngine
from .errors import (
    CommandError,
    DependencyError,
    HotspotError,
    PrivilegeError,
    StateError,
    ValidationError,
)
from .models import HotspotConfig
from .util import command_exists, remove_file, require_root, run, write_pid

log = logging.getLogger(__name__)

ACTION_ID = "io.github.carlosdelrosario7.WifiHotspot.manage"
MAX_REQUEST = 64 * 1024
PRIVILEGED_METHODS = frozenset(
    {
        "start",
        "stop",
        "restart",
        "disconnect",
        "profiles.get",
        "profiles.save",
        "profiles.delete",
        "shutdown",
    }
)


class PermissionDenied(Exception):
    """The peer is not authorized for a privileged method."""


class RpcError(Exception):
    """A request could not be processed."""

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


def peer_credentials(sock: socket.socket) -> tuple[int, int, int]:
    """Return (pid, uid, gid) for the process on the other end of a socket."""
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", raw)
    return pid, uid, gid


def pkcheck_authorize(pid: int) -> bool:
    """Ask polkit whether the peer may manage the hotspot."""
    if not command_exists("pkcheck"):
        return False
    result = run(
        [
            "pkcheck",
            "--action-id",
            ACTION_ID,
            "--process",
            str(pid),
            "--allow-user-interaction",
        ],
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        log.warning("polkit denegó la acción %s para el PID %s", ACTION_ID, pid)
        return False
    return True


def authorize(pid: int, uid: int, method: str) -> bool:
    if method not in PRIVILEGED_METHODS:
        return True
    if uid == 0:
        return True
    return pkcheck_authorize(pid)


def _error_code(exc: Exception) -> str:
    if isinstance(exc, PermissionDenied):
        return "permission"
    if isinstance(exc, ValidationError):
        return "validation"
    if isinstance(exc, PrivilegeError):
        return "privilege"
    if isinstance(exc, DependencyError):
        return "dependency"
    if isinstance(exc, StateError):
        return "state"
    if isinstance(exc, CommandError):
        return "command"
    if isinstance(exc, HotspotError):
        return "hotspot"
    return "internal"


class DaemonService:
    """RPC methods. Networking state is read from ``paths`` on every call."""

    def __init__(self, paths: Paths | None = None) -> None:
        self.paths = paths or Paths()

    # -- helpers ---------------------------------------------------------

    def _engine(self, config: HotspotConfig | None = None) -> HotspotEngine:
        return HotspotEngine(config or default_profile(self.paths), self.paths)

    def _config(self, params: dict[str, Any]) -> HotspotConfig:
        profile = str(params.get("profile") or "default")
        overrides = params.get("overrides") or {}
        if not isinstance(overrides, dict):
            raise RpcError("El campo 'overrides' debe ser un objeto.", "validation")
        return resolve_config(
            self.paths,
            profile,
            overrides,
            generate_password=bool(params.get("generate_password", False)),
        )

    # -- dispatch --------------------------------------------------------

    def dispatch(self, method: str, params: dict[str, Any], *, pid: int, uid: int) -> Any:
        if not authorize(pid, uid, method):
            raise PermissionDenied("No tienes permiso para realizar esta acción.")

        handler = getattr(self, f"_rpc_{method.replace('.', '_')}", None)
        if handler is None:
            raise RpcError(f"Método desconocido: {method}", "unknown_method")
        return handler(params)

    # -- unprivileged ----------------------------------------------------

    def _rpc_ping(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {"pong": True, "version": __version__}

    def _rpc_status(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self._engine().status()

    def _rpc_clients(self, _params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._engine().clients()

    def _rpc_doctor(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self._engine().doctor()

    def _rpc_profiles_list(self, _params: dict[str, Any]) -> list[str]:
        return list_profiles(self.paths)

    # -- privileged ------------------------------------------------------

    def _rpc_start(self, params: dict[str, Any]) -> dict[str, Any]:
        config = self._config(params)
        state = self._engine(config).start()
        return {
            "state": state.public_dict(),
            "config": config.to_dict(include_password=True),
        }

    def _rpc_stop(self, _params: dict[str, Any]) -> dict[str, bool]:
        return {"stopped": self._engine().stop()}

    def _rpc_restart(self, params: dict[str, Any]) -> dict[str, Any]:
        config = self._config(params)
        state = self._engine(config).restart()
        return {
            "state": state.public_dict(),
            "config": config.to_dict(include_password=True),
        }

    def _rpc_disconnect(self, params: dict[str, Any]) -> dict[str, str]:
        mac = str(params.get("mac") or "").strip()
        if not mac:
            raise RpcError("Falta la dirección MAC del cliente.", "validation")
        self._engine().disconnect_client(mac)
        return {"disconnected": mac.lower()}

    def _rpc_profiles_get(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("profile") or "default")
        config = load_profile(self.paths, name)
        if config is None:
            if name != "default":
                raise RpcError(f"No existe el perfil {name!r}.", "not_found")
            config = default_profile(self.paths)
        return config.to_dict(include_password=True)

    def _rpc_profiles_summary(self, params: dict[str, Any]) -> dict[str, Any]:
        """Profile data without the password, safe for populating a form."""
        name = str(params.get("profile") or "default")
        config = load_profile(self.paths, name)
        stored = config is not None
        if config is None:
            if name != "default":
                raise RpcError(f"No existe el perfil {name!r}.", "not_found")
            config = default_profile(self.paths)
        summary = config.to_dict(include_password=False)
        summary["stored"] = stored
        return summary

    def _rpc_profiles_save(self, params: dict[str, Any]) -> dict[str, Any]:
        raw = params.get("config")
        requested = params.get("profile")
        if isinstance(raw, dict):
            config = HotspotConfig.from_dict(raw)
            if requested:
                config.profile_name = str(requested)
            elif not config.profile_name:
                config.profile_name = "default"
        else:
            name = str(requested or "default")
            overrides = params.get("overrides") or {}
            if not isinstance(overrides, dict):
                raise RpcError("El campo 'overrides' debe ser un objeto.", "validation")
            config = resolve_config(self.paths, name, overrides)
        config.validate()
        path = save_profile(self.paths, config)
        return {"saved": config.profile_name, "path": str(path)}

    def _rpc_profiles_delete(self, params: dict[str, Any]) -> dict[str, bool]:
        name = str(params.get("profile") or "")
        if not name:
            raise RpcError("Falta el nombre del perfil.", "validation")
        return {"deleted": delete_profile(self.paths, name)}

    def _rpc_shutdown(self, _params: dict[str, Any]) -> dict[str, bool]:
        return {"shutdown": True}


class RequestHandler(socketserver.StreamRequestHandler):
    """Read newline-delimited JSON requests from one local peer."""

    server: UnixSocketServer

    def handle(self) -> None:
        try:
            pid, uid, _gid = peer_credentials(self.request)
        except OSError:
            return
        self.request.settimeout(120)

        while True:
            try:
                line = self.rfile.readline(MAX_REQUEST + 1)
            except (OSError, TimeoutError):
                return
            if not line:
                return
            if len(line) > MAX_REQUEST:
                self._write({"id": None, "ok": False, "error": "Petición demasiado grande."})
                return
            if not line.strip():
                continue

            method = ""
            request: dict[str, Any] = {}
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise RpcError("La petición debe ser un objeto JSON.", "bad_request")
                method = request.get("method", "")
                if not isinstance(method, str) or not method:
                    raise RpcError("Falta el campo 'method'.", "bad_request")
                params = request.get("params") or {}
                if not isinstance(params, dict):
                    raise RpcError("El campo 'params' debe ser un objeto.", "bad_request")
                result = self.server.service.dispatch(method, params, pid=pid, uid=uid)
                response: dict[str, Any] = {
                    "id": request.get("id"),
                    "ok": True,
                    "result": result,
                }
            except PermissionDenied as exc:
                response = {
                    "id": request.get("id"),
                    "ok": False,
                    "error": str(exc),
                    "code": _error_code(exc),
                }
            except RpcError as exc:
                response = {
                    "id": request.get("id"),
                    "ok": False,
                    "error": str(exc),
                    "code": exc.code,
                }
            except HotspotError as exc:
                response = {
                    "id": request.get("id"),
                    "ok": False,
                    "error": str(exc),
                    "code": _error_code(exc),
                }
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("Error inesperado atendiendo %s", method)
                response = {
                    "id": request.get("id"),
                    "ok": False,
                    "error": f"Error interno: {exc}",
                    "code": "internal",
                }

            self._write(response)
            if method == "shutdown" and response.get("ok"):
                self.server.stop_event.set()
                return

    def _write(self, payload: dict[str, Any]) -> None:
        try:
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.wfile.write(data + b"\n")
            self.wfile.flush()
        except OSError:
            return


class UnixSocketServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        path: Path,
        service: DaemonService,
        stop_event: threading.Event,
    ) -> None:
        self.service = service
        self.stop_event = stop_event
        super().__init__(str(path), RequestHandler)
        os.chmod(path, 0o666)

    def server_bind(self) -> None:
        # UnixStreamServer.server_bind uses the path passed to the constructor.
        super().server_bind()


def prepare_socket(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        probe.connect(str(path))
    except OSError:
        remove_file(path)
    else:
        raise StateError("Ya hay un daemon de WiFi Hotspot escuchando en el socket.")
    finally:
        probe.close()


def serve(paths: Paths, service: DaemonService | None = None) -> int:
    paths.ensure_runtime()
    prepare_socket(paths.daemon_socket)
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    try:
        server = UnixSocketServer(paths.daemon_socket, service or DaemonService(paths), stop_event)
    except OSError as exc:
        log.error("No pude crear el socket %s: %s", paths.daemon_socket, exc)
        return 1

    write_pid(paths.daemon_pid, os.getpid())
    log.info("%s %s escuchando en %s", APP_NAME, __version__, paths.daemon_socket)
    try:
        server.timeout = 0.5
        while not stop_event.is_set():
            server.handle_request()
    finally:
        server.server_close()
        remove_file(paths.daemon_socket)
        remove_file(paths.daemon_pid)
        log.info("Daemon detenido.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wifi-hotspotd",
        description="Daemon privilegiado de WiFi Hotspot.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Mostrar logs de depuración")
    parser.add_argument(
        "--run-dir",
        default="/run/wifi-hotspot",
        help="Directorio de estado y socket (por defecto /run/wifi-hotspot)",
    )
    parser.add_argument(
        "--etc-dir",
        default="/etc/wifi-hotspot",
        help="Directorio de perfiles (por defecto /etc/wifi-hotspot)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
        force=True,
    )
    try:
        require_root()
    except PrivilegeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    paths = Paths(run_dir=Path(args.run_dir), etc_dir=Path(args.etc_dir))
    try:
        return serve(paths)
    except (HotspotError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
