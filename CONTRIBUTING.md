# Contributing to WiFi Hotspot

Thanks for taking the time to contribute.

## Ways to help

- Report bugs with the output of `wifi-hotspot doctor --json` (redact sensitive
  data first).
- Report hardware compatibility, including Wi-Fi chipset, driver, kernel, and
  whether AP+STA works.
- Improve documentation and translations.
- Test on distributions or desktop environments we do not own.
- Send pull requests with tests.

## Development setup

Requirements:

- Python 3.10 or newer.
- `hostapd`, `dnsmasq`, `iw`, `iproute2`, and a firewall tool for integration
  testing.
- GTK4, Libadwaita, and PyGObject for GUI work.

On Debian/Ubuntu/Kali:

```bash
sudo apt install python3 python3-venv python3-pip \
  hostapd dnsmasq-base iw iproute2 iptables nftables \
  python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 qrencode
```

Run from the source tree:

```bash
PYTHONPATH=src python3 -m wifi_hotspot --help
PYTHONPATH=src python3 -m pytest
ruff check src tests
ruff format --check src tests
```

Integration tests need root and Wi-Fi hardware or `mac80211_hwsim`:

```bash
sudo PYTHONPATH=src python3 -m pytest -m integration
```

## Code style

- Python 3.10+ syntax, type hints where they add clarity.
- `ruff` for linting and formatting.
- No external Python runtime dependencies in the core; use the standard library.
- Never use `shell=True`.
- Never log passwords or write secrets to world-readable paths.
- Add a test for behavior changes.

## Commit messages

Use short, imperative messages. Conventional Commits are welcome but not
mandatory:

```
feat: add 5 GHz channel selection
fix: restore NetworkManager managed state on stop
docs: document firewalld backend limitations
```

## Pull requests

- Keep changes focused.
- Include a test or explain why one is not possible.
- Update `CHANGELOG.md` for user-visible changes.
- Update documentation and translations when applicable.
- Verify that `make lint` and `make test` pass.

## Translations

The GUI uses gettext. Spanish and English are the initial languages. Do not
translate technical identifiers such as command names, interface names, or
firewall backends.

## License

By contributing, you agree that your contribution is licensed under
GPL-3.0-or-later, the same license as the project.
