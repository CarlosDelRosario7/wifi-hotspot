"""Exceptions used by the WiFi Hotspot core."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


class HotspotError(Exception):
    """Base class for expected, user-facing errors."""

    exit_code = 1


class DependencyError(HotspotError):
    """A required command or system capability is missing."""


class ValidationError(HotspotError):
    """The user supplied an invalid value."""


class StateError(HotspotError):
    """The requested operation is not valid in the current state."""


class PrivilegeError(HotspotError):
    """The operation requires privileges the caller does not have."""


@dataclass
class CommandError(HotspotError):
    """An external command failed."""

    message: str
    command: Sequence[str]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""

    def __str__(self) -> str:
        detail = self.stderr.strip() or self.stdout.strip()
        if detail:
            return f"{self.message}: {detail}"
        return self.message

    def __post_init__(self) -> None:
        super().__init__(str(self))
