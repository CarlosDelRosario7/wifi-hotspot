"""Tests for foreground process management."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from wifi_hotspot.errors import CommandError
from wifi_hotspot.services import process

# The interpreter is called "python" on some CI images and "python3" on most
# distributions, so derive the expected process name from the running binary.
PYTHON_BINARY = Path(sys.executable).name


def wait_until_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process.pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_process_matches_current_process() -> None:
    pid = os.getpid()
    assert process.process_matches(pid, PYTHON_BINARY) is True
    assert process.process_matches(pid, PYTHON_BINARY, Path("/nonexistent/config")) is False
    assert process.process_matches(pid, "not-our-binary") is False
    assert process.process_matches(999999999, PYTHON_BINARY) is False


@pytest.mark.parametrize("form", ["path", "conf-file", "short-config"])
def test_process_matches_config_argument_forms(tmp_path: Path, form: str) -> None:
    config = tmp_path / "service.conf"
    argument = {
        "path": str(config),
        "conf-file": f"--conf-file={config}",
        "short-config": f"-C{config}",
    }[form]
    other = tmp_path / "other.conf"

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", argument])
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if process.process_matches(child.pid, PYTHON_BINARY, config):
                break
            time.sleep(0.01)
        assert process.process_matches(child.pid, PYTHON_BINARY, config) is True
        assert process.process_matches(child.pid, PYTHON_BINARY, other) is False
        assert process.process_matches(child.pid, "not-our-binary", config) is False
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_tail_log(tmp_path: Path) -> None:
    path = tmp_path / "service.log"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert process.tail_log(path, 2) == "two\nthree"
    assert process.tail_log(tmp_path / "missing") == ""


def test_start_foreground_failure_removes_pid_and_reports_log(tmp_path: Path) -> None:
    pid_file = tmp_path / "service.pid"
    log_file = tmp_path / "service.log"
    with pytest.raises(CommandError, match="terminó durante el arranque"):
        process.start_foreground(
            ["/bin/sh", "-c", "echo boom >&2; exit 3"],
            name="test",
            pid_file=pid_file,
            log_file=log_file,
            startup_delay=0.2,
        )
    assert not pid_file.exists()
    assert "boom" in log_file.read_text(encoding="utf-8")


def test_start_and_stop_foreground(tmp_path: Path) -> None:
    pid_file = tmp_path / "service.pid"
    log_file = tmp_path / "service.log"
    pid = process.start_foreground(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        name="test",
        pid_file=pid_file,
        log_file=log_file,
        startup_delay=0.1,
    )
    assert pid > 0
    assert process.read_pid(pid_file) == pid
    assert process.pid_alive(pid) is True

    stopped = process.stop_foreground(
        name="test",
        binary=PYTHON_BINARY,
        pid_file=pid_file,
    )
    assert stopped is True
    assert not pid_file.exists()
    assert wait_until_dead(pid)


def test_stop_foreground_does_not_kill_foreign_pid(tmp_path: Path) -> None:
    pid_file = tmp_path / "service.pid"
    process.write_pid(pid_file, os.getpid())

    stopped = process.stop_foreground(
        name="test",
        binary="definitely-not-our-binary",
        pid_file=pid_file,
    )
    assert stopped is False
    assert not pid_file.exists()
    assert process.pid_alive(os.getpid()) is True


def test_stop_foreground_recovers_orphan_from_config_path(tmp_path: Path) -> None:
    config = tmp_path / "service.conf"
    config.write_text("option=1\n", encoding="utf-8")
    pid_file = tmp_path / "service.pid"
    log_file = tmp_path / "service.log"

    pid = process.start_foreground(
        [sys.executable, "-c", "import time; time.sleep(30)", str(config)],
        name="test",
        pid_file=pid_file,
        log_file=log_file,
        startup_delay=0.1,
    )
    pid_file.unlink()

    stopped = process.stop_foreground(
        name="test",
        binary=PYTHON_BINARY,
        pid_file=pid_file,
        config_path=config,
    )
    assert stopped is True
    assert wait_until_dead(pid)


def test_stop_foreground_recovers_orphan_from_conf_file_argument(tmp_path: Path) -> None:
    config = tmp_path / "service.conf"
    config.write_text("option=1\n", encoding="utf-8")
    pid_file = tmp_path / "service.pid"
    log_file = tmp_path / "service.log"

    pid = process.start_foreground(
        [sys.executable, "-c", "import time; time.sleep(30)", f"--conf-file={config}"],
        name="test",
        pid_file=pid_file,
        log_file=log_file,
        startup_delay=0.1,
    )
    pid_file.unlink()

    stopped = process.stop_foreground(
        name="test",
        binary=PYTHON_BINARY,
        pid_file=pid_file,
        config_path=config,
    )
    assert stopped is True
    assert wait_until_dead(pid)


def test_log_permissions(tmp_path: Path) -> None:
    log_file = tmp_path / "service.log"
    fd = process._open_log(log_file)
    os.close(fd)
    assert (log_file.stat().st_mode & 0o777) == 0o600
