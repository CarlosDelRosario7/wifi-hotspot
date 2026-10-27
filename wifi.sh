#!/usr/bin/env bash
#
# WiFi Hotspot - compatibility wrapper for the original wifi.sh interface.
#
# The original script lives in legacy/wifi.sh and keeps working on its own.
# This wrapper maps the old commands to the new `wifi-hotspot` CLI:
#
#   sudo ./wifi.sh start "MiHotspot" "MiClave123"
#   sudo ./wifi.sh stop
#   sudo ./wifi.sh restart "MiHotspot" "MiClave123"
#   ./wifi.sh status
#
# Any other argument is forwarded to the new CLI, so commands such as
# `clients`, `doctor`, `profiles` or `qr` are available too.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR

usage() {
    cat <<EOF
WiFi Hotspot

Uso:
  sudo $0 start ["SSID" "Contraseña"]
  sudo $0 stop
  sudo $0 restart ["SSID" "Contraseña"]
  $0 status
  $0 clients | doctor | profiles | qr | version

El script original sigue disponible en:
  sudo $SCRIPT_DIR/legacy/wifi.sh start "SSID" "Contraseña"
EOF
}

# Prefer the installed command. Fall back to the source tree so the wrapper
# works straight from a git checkout without installing anything.
find_cli() {
    if command -v wifi-hotspot >/dev/null 2>&1; then
        CLI=(wifi-hotspot)
    elif [[ -d "$SCRIPT_DIR/src/wifi_hotspot" ]]; then
        CLI=(python3 -m wifi_hotspot)
        export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
    else
        echo "Error: no encontré el comando 'wifi-hotspot' ni el código fuente." >&2
        echo "Instala el paquete o ejecuta este script desde el repositorio." >&2
        exit 1
    fi
}

require_root() {
    if [[ ${EUID} -ne 0 ]]; then
        echo "Error: esta acción necesita root. Ejecútala con sudo." >&2
        exit 1
    fi
}

main() {
    local command="${1:-}"
    if [[ -n "$command" ]]; then
        shift
    fi

    case "$command" in
        ""|-h|--help|help)
            usage
            exit 0
            ;;
        start|restart)
            require_root
            find_cli
            exec "${CLI[@]}" "$command" "$@"
            ;;
        stop)
            require_root
            find_cli
            exec "${CLI[@]}" stop "$@"
            ;;
        status)
            find_cli
            exec "${CLI[@]}" status "$@"
            ;;
        *)
            find_cli
            exec "${CLI[@]}" "$command" "$@"
            ;;
    esac
}

main "$@"
