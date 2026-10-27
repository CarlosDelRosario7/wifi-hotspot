"""Small, dependency-free helpers used across the project."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import secrets
import string
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import CommandError, PrivilegeError, StateError

PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
INTERFACE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")


def run(
    command: Sequence[str],
    *,
    check: bool = True,
    timeout: float = 30.0,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with a deterministic locale and optional error handling."""
    command = [str(part) for part in command]
    full_env = os.environ.copy()
    full_env.setdefault("LC_ALL", "C")
    full_env.setdefault("LANG", "C")
    if env:
        full_env.update(env)
    try:
        result = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=full_env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandError(
            f"No se encontró el comando '{command[0]}'",
            command,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(
            f"El comando '{command[0]}' excedió el tiempo límite",
            command,
        ) from exc

    if check and result.returncode != 0:
        raise CommandError(
            f"El comando '{command[0]}' falló (código {result.returncode})",
            command,
            result.returncode,
            result.stdout,
            result.stderr,
        )
    return result


def command_exists(command: str) -> bool:
    """Return True when a command is available in PATH."""
    from shutil import which

    return which(command) is not None


def is_root() -> bool:
    return os.geteuid() == 0


def require_root() -> None:
    if not is_root():
        raise PrivilegeError("Esta operación requiere root. Usa sudo o la GUI.")


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    """Create a directory and make sure its permissions are not too broad."""
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    try:
        current = path.stat().st_mode & 0o777
        if current != mode:
            path.chmod(mode)
    except OSError:
        # If we cannot chmod, the caller will get a clear error when using it.
        pass
    return path


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    """Atomically write a text file with restrictive permissions."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        with contextlib.suppress(OSError):
            os.chmod(path, mode)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def write_json(path: Path, data: Any, mode: int = 0o600) -> None:
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n", mode=mode)


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path: Path) -> str:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return handle.read()


def write_pid(path: Path, pid: int) -> None:
    atomic_write(path, f"{pid}\n", mode=0o644)


def read_pid(path: Path) -> int | None:
    try:
        text = read_text(path).strip()
    except FileNotFoundError:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def pid_alive(pid: int | None) -> bool:
    """Return True when a PID exists and is not a zombie."""
    if not pid or pid <= 0:
        return False
    proc = Path(f"/proc/{pid}")
    if not proc.exists():
        return False
    try:
        stat = (proc / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # The process name may contain spaces and parentheses; state follows the last ')'.
    try:
        state = stat.rsplit(")", 1)[1].split()[0]
    except (IndexError, ValueError):
        return False
    return state != "Z"


def find_pids_by_cmdline(fragment: str) -> list[int]:
    """Find processes whose argv contains an exact fragment.

    This is intentionally narrow: callers pass a unique configuration path or
    binary+path pair, never a broad pattern.
    """
    matches: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        args = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if any(fragment == arg for arg in args):
            matches.append(pid)
    return matches


def terminate_pid(pid: int, timeout: float = 5.0) -> bool:
    """Terminate a process gracefully, then forcefully after a timeout."""
    if not pid_alive(pid):
        return True
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        return True
    except PermissionError as exc:
        raise PrivilegeError(f"No hay permiso para terminar el proceso {pid}") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)

    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        return True
    except PermissionError as exc:
        raise PrivilegeError(f"No hay permiso para terminar el proceso {pid}") from exc

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def remove_file(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


@contextlib.contextmanager
def exclusive_lock(path: Path, timeout: float = 10.0) -> Iterator[None]:
    """Serialize engine operations with an advisory file lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise StateError(
                        "Otra operación del hotspot está en curso. Inténtalo de nuevo."
                    ) from None
                time.sleep(0.1)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def random_password(length: int = 16) -> str:
    """Generate a WPA-compatible password without ambiguous characters."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(max(8, length)))


def format_bytes(value: int | float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024.0 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024.0
    return f"{amount:.1f} TiB"


def format_duration(seconds: int | float) -> str:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def human_join(items: Sequence[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " y " + items[-1]


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def sanitize_for_log(text: str, secrets_to_hide: Sequence[str] = ()) -> str:
    for secret in secrets_to_hide:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def password_alphabet() -> str:
    return string.ascii_letters + string.digits
