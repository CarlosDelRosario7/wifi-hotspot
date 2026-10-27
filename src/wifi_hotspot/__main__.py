"""Allow `python3 -m wifi_hotspot`."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
