"""Tests for shared helpers."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from wifi_hotspot import util
from wifi_hotspot.errors import CommandError, PrivilegeError, StateError


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_run_success() -> None:
    result = util.run([sys.executable, "-c", "print('hello')"])
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_run_missing_command() -> None:
    with pytest.raises(CommandError, match="No se encontró"):
        util.run(["definitely-not-a-command"])


def test_run_failure_with_check() -> None:
    with pytest.raises(CommandError, match="falló"):
        util.run([sys.executable, "-c", "raise SystemExit(3)"])


def test_run_failure_without_check() -> None:
    result = util.run([sys.executable, "-c", "raise SystemExit(3)"], check=False)
    assert result.returncode == 3


def test_run_timeout() -> None:
    with pytest.raises(CommandError, match="tiempo límite"):
        util.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)


def test_run_with_input() -> None:
    result = util.run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read())"], input_text="abc"
    )
    assert result.stdout.strip() == "abc"


def test_command_exists() -> None:
    assert util.command_exists("sh") is True
    assert util.command_exists("definitely-not-a-command") is False


def test_is_root_and_require_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(util, "is_root", lambda: False)
    with pytest.raises(PrivilegeError, match="root"):
        util.require_root()

    monkeypatch.setattr(util, "is_root", lambda: True)
    util.require_root()


def test_ensure_dir(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir"
    util.ensure_dir(path, 0o700)
    assert path.is_dir()
    assert mode(path) == 0o700

    # Existing directories with broader permissions are tightened.
    path.chmod(0o777)
    util.ensure_dir(path, 0o700)
    assert mode(path) == 0o700


def test_atomic_write(tmp_path: Path) -> None:
    path = tmp_path / "secret.conf"
    util.atomic_write(path, "content\n", mode=0o600)
    assert path.read_text(encoding="utf-8") == "content\n"
    assert mode(path) == 0o600


def test_atomic_write_replaces_file(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    util.atomic_write(path, "one")
    util.atomic_write(path, "two")
    assert path.read_text(encoding="utf-8") == "two"


def test_write_and_read_json(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    util.write_json(path, {"b": 2, "a": [1, 2]})
    assert util.read_json(path) == {"a": [1, 2], "b": 2}
    assert mode(path) == 0o600


def test_read_text_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        util.read_text(tmp_path / "missing")


def test_pid_file(tmp_path: Path) -> None:
    path = tmp_path / "service.pid"
    util.write_pid(path, 1234)
    assert util.read_pid(path) == 1234
    assert mode(path) == 0o644

    path.write_text("not-a-pid\n", encoding="utf-8")
    assert util.read_pid(path) is None

    path.write_text("-5\n", encoding="utf-8")
    assert util.read_pid(path) is None

    assert util.read_pid(tmp_path / "missing") is None


def test_pid_alive() -> None:
    assert util.pid_alive(os.getpid()) is True
    assert util.pid_alive(None) is False
    assert util.pid_alive(0) is False
    assert util.pid_alive(999999999) is False


def test_remove_file(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_text("x", encoding="utf-8")
    util.remove_file(path)
    util.remove_file(path)
    assert not path.exists()


def test_random_password() -> None:
    password = util.random_password(24)
    assert len(password) == 24
    assert all(character.isalnum() for character in password)
    assert util.random_password(2).__len__() >= 8


def test_format_bytes() -> None:
    assert util.format_bytes(0) == "0 B"
    assert util.format_bytes(512) == "512 B"
    assert util.format_bytes(1024) == "1.0 KiB"
    assert util.format_bytes(1024 * 1024) == "1.0 MiB"
    assert util.format_bytes(1024**3) == "1.0 GiB"


def test_format_duration() -> None:
    assert util.format_duration(0) == "0s"
    assert util.format_duration(59) == "59s"
    assert util.format_duration(60) == "1m 0s"
    assert util.format_duration(3600) == "1h 0m"
    assert util.format_duration(86400 + 3600 + 60) == "1d 1h 1m"


def test_human_join() -> None:
    assert util.human_join([]) == ""
    assert util.human_join(["one"]) == "one"
    assert util.human_join(["one", "two"]) == "one y two"
    assert util.human_join(["one", "two", "three"]) == "one, two y three"


def test_mask_and_sanitize_secret() -> None:
    assert util.mask_secret("") == ""
    assert util.mask_secret("abcd") == "****"
    assert util.mask_secret("abcdefgh") == "ab****gh"
    assert util.sanitize_for_log("password=secret", ["secret"]) == "password=[REDACTED]"
    assert util.sanitize_for_log("nothing", ["secret"]) == "nothing"


def test_exclusive_lock(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with util.exclusive_lock(path):
        assert path.exists()
    with util.exclusive_lock(path):
        assert path.exists()


def test_exclusive_lock_timeout(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with (
        util.exclusive_lock(path),
        pytest.raises(StateError, match="en curso"),
        util.exclusive_lock(path, timeout=0.1),
    ):
        pass
