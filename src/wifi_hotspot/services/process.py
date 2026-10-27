"""Foreground process management for hostapd and dnsmasq."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from ..errors import CommandError, StateError
from ..util import (
    find_pids_by_cmdline,
    pid_alive,
    read_pid,
    read_text,
    remove_file,
    terminate_pid,
    write_pid,
)

log = logging.getLogger(__name__)


def _cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _config_argument_matches(argument: str, config_path: Path) -> bool:
    """Match both `path` and `--conf-file=path` style arguments."""
    target = str(config_path)
    return argument == target or argument == f"--conf-file={target}" or argument == f"-C{target}"


def process_matches(pid: int, binary: str, config_path: Path | None = None) -> bool:
    args = _cmdline(pid)
    if not args:
        return False
    binary_name = Path(args[0]).name
    if binary_name != binary and Path(binary).name not in binary_name:
        return False
    if config_path is None:
        return True
    return any(_config_argument_matches(argument, config_path) for argument in args)


def _open_log(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    return fd


def tail_log(path: Path, lines: int = 25) -> str:
    try:
        content = read_text(path)
    except FileNotFoundError:
        return ""
    return "\n".join(content.splitlines()[-lines:])


def start_foreground(
    command: Sequence[str],
    *,
    name: str,
    pid_file: Path,
    log_file: Path,
    startup_delay: float = 1.2,
) -> int:
    """Start a foreground daemon in a new session and verify it stayed alive."""
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    fd = _open_log(log_file)
    log_handle = os.fdopen(fd, "ab", buffering=0)
    try:
        process = subprocess.Popen(
            [str(item) for item in command],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_handle.close()

    write_pid(pid_file, process.pid)
    time.sleep(startup_delay)
    if process.poll() is not None:
        details = tail_log(log_file)
        remove_file(pid_file)
        message = f"{name} terminó durante el arranque (código {process.returncode})."
        if details:
            message += f"\n{details}"
        raise CommandError(message, command, process.returncode, stderr=details)
    return process.pid


def stop_foreground(
    *,
    name: str,
    binary: str,
    pid_file: Path,
    config_path: Path | None = None,
    timeout: float = 5.0,
) -> bool:
    """Stop a process, using the PID file and a narrow cmdline fallback."""
    pid = read_pid(pid_file)
    stopped = False
    if pid and pid_alive(pid):
        if not process_matches(pid, binary, config_path):
            log.warning(
                "El PID %s de %s no corresponde a nuestro proceso; no lo termino.",
                pid,
                name,
            )
        else:
            terminated = terminate_pid(pid, timeout=timeout)
            stopped = stopped or terminated
            if not terminated:
                raise StateError(f"No pude detener {name} (PID {pid}).")
    remove_file(pid_file)

    if config_path is not None:
        # Recover from a lost PID file after a crash, without broad pkill.
        target = str(config_path)
        orphans: set[int] = set()
        for fragment in (target, f"--conf-file={target}", f"-C{target}"):
            orphans.update(find_pids_by_cmdline(fragment))
        for orphan in orphans:
            if process_matches(orphan, binary, config_path):
                log.info("Deteniendo %s huérfano (PID %s)", name, orphan)
                terminate_pid(orphan, timeout=timeout)
                stopped = True
    return stopped


def log_tail(path: Path, lines: int = 20) -> str:
    return tail_log(path, lines)
