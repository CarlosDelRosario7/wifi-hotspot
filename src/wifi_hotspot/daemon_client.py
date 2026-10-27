"""Small JSON-RPC client for the local WiFi Hotspot daemon."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from .config import Paths


class DaemonUnavailable(Exception):
    """The daemon socket does not exist or nobody is listening."""


class DaemonError(Exception):
    """The daemon answered with an error."""

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class DaemonClient:
    def __init__(self, paths: Paths | None = None, timeout: float = 120.0) -> None:
        self.paths = paths or Paths()
        self.timeout = timeout

    @property
    def socket_path(self) -> Path:
        return self.paths.daemon_socket

    def available(self) -> bool:
        return self.socket_path.exists()

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not self.socket_path.exists():
            raise DaemonUnavailable("El daemon de WiFi Hotspot no está disponible.")
        payload = json.dumps(
            {"id": 1, "method": method, "params": params or {}},
            ensure_ascii=False,
        ).encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.socket_path))
            sock.sendall(payload + b"\n")
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        except (ConnectionRefusedError, FileNotFoundError, TimeoutError) as exc:
            raise DaemonUnavailable(f"No pude conectar con el daemon: {exc}") from exc
        except OSError as exc:
            raise DaemonUnavailable(f"Error de comunicación con el daemon: {exc}") from exc
        finally:
            sock.close()

        raw = b"".join(chunks).split(b"\n", 1)[0].strip()
        if not raw:
            raise DaemonUnavailable("El daemon cerró la conexión sin responder.")
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DaemonUnavailable("El daemon devolvió una respuesta inválida.") from exc
        if not isinstance(response, dict):
            raise DaemonUnavailable("El daemon devolvió una respuesta inválida.")
        if not response.get("ok"):
            raise DaemonError(
                str(response.get("error") or "Error desconocido del daemon."),
                str(response.get("code") or "error"),
            )
        return response.get("result")

    def ping(self) -> dict[str, Any]:
        result = self.request("ping")
        return result if isinstance(result, dict) else {}

    def status(self) -> dict[str, Any]:
        result = self.request("status")
        return result if isinstance(result, dict) else {}

    def clients(self) -> list[dict[str, Any]]:
        result = self.request("clients")
        return result if isinstance(result, list) else []

    def doctor(self) -> dict[str, Any]:
        result = self.request("doctor")
        return result if isinstance(result, dict) else {}

    def start(
        self,
        profile: str = "default",
        overrides: dict[str, Any] | None = None,
        *,
        generate_password: bool = False,
    ) -> dict[str, Any]:
        result = self.request(
            "start",
            {
                "profile": profile,
                "overrides": overrides or {},
                "generate_password": generate_password,
            },
        )
        return result if isinstance(result, dict) else {}

    def stop(self) -> bool:
        result = self.request("stop")
        return bool(isinstance(result, dict) and result.get("stopped"))

    def restart(
        self,
        profile: str = "default",
        overrides: dict[str, Any] | None = None,
        *,
        generate_password: bool = False,
    ) -> dict[str, Any]:
        result = self.request(
            "restart",
            {
                "profile": profile,
                "overrides": overrides or {},
                "generate_password": generate_password,
            },
        )
        return result if isinstance(result, dict) else {}

    def disconnect(self, mac: str) -> None:
        self.request("disconnect", {"mac": mac})

    def list_profiles(self) -> list[str]:
        result = self.request("profiles.list")
        return result if isinstance(result, list) else []

    def get_profile(self, name: str = "default") -> dict[str, Any]:
        result = self.request("profiles.get", {"profile": name})
        return result if isinstance(result, dict) else {}

    def get_profile_summary(self, name: str = "default") -> dict[str, Any]:
        result = self.request("profiles.summary", {"profile": name})
        return result if isinstance(result, dict) else {}

    def save_profile(
        self,
        config: dict[str, Any] | None = None,
        name: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"config": config} if config is not None else {}
        if name is not None:
            params["profile"] = name
        if overrides is not None:
            params["overrides"] = overrides
        result = self.request("profiles.save", params)
        return result if isinstance(result, dict) else {}

    def delete_profile(self, name: str) -> bool:
        result = self.request("profiles.delete", {"profile": name})
        return bool(isinstance(result, dict) and result.get("deleted"))

    def shutdown(self) -> None:
        self.request("shutdown")
