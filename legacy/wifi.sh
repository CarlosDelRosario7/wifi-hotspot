#!/usr/bin/env bash

# Generic Linux Wi-Fi Hotspot
# Debian / Ubuntu / Mint / Kali
#
# Requisitos:
#   iw
#   ip
#   hostapd
#   dnsmasq
#   iptables
#
# Uso:
#   sudo ./kali-hotspot.sh start
#   sudo ./kali-hotspot.sh start "MiHotspot" "MiClave123456"
#   sudo ./kali-hotspot.sh stop
#   sudo ./kali-hotspot.sh restart
#   sudo ./kali-hotspot.sh status

set -u

# ------------------------------------------------------------
# Configuración por defecto
# ------------------------------------------------------------

DEFAULT_SSID="LinuxAP"
DEFAULT_PASSWORD="12345678"

AP_IFACE="wlan0_ap"
AP_IP="10.42.0.1"
AP_NET="10.42.0.0/24"
DHCP_START="10.42.0.10"
DHCP_END="10.42.0.100"
DHCP_LEASE="12h"

HOSTAPD_CONF="/tmp/linux-hotspot-hostapd.conf"
DNSMASQ_CONF="/tmp/linux-hotspot-dnsmasq.conf"
PID_DIR="/run/linux-hotspot"

HOSTAPD_PID="$PID_DIR/hostapd.pid"
DNSMASQ_PID="$PID_DIR/dnsmasq.pid"

# ------------------------------------------------------------
# Colores
# ------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

ok() {
    echo -e "${GREEN}[ OK ]${NC} $*"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# ------------------------------------------------------------
# Root
# ------------------------------------------------------------

require_root() {
    if [[ "$EUID" -ne 0 ]]; then
        error "Ejecuta este script con sudo."
        exit 1
    fi
}

# ------------------------------------------------------------
# Dependencias
# ------------------------------------------------------------

check_dependencies() {
    local missing=()

    for cmd in iw ip iptables hostapd dnsmasq; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            missing+=("$cmd")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Faltan dependencias:"
        printf '  %s\n' "${missing[@]}"
        echo
        echo "Debian/Ubuntu/Mint/Kali:"
        echo "  sudo apt install iw iproute2 iptables hostapd dnsmasq"
        exit 1
    fi
}

# ------------------------------------------------------------
# Detectar interfaz que tiene Internet
# ------------------------------------------------------------

detect_uplink() {
    UPLINK_IFACE="$(ip route show default 2>/dev/null \
        | awk 'NR==1 {print $5}')"

    if [[ -z "${UPLINK_IFACE:-}" ]]; then
        error "No pude detectar la interfaz con la ruta por defecto."
        exit 1
    fi

    info "Interfaz de salida detectada: $UPLINK_IFACE"
}

# ------------------------------------------------------------
# Detectar PHY de la interfaz Wi-Fi
# ------------------------------------------------------------

detect_phy() {
    PHY="$(iw dev "$UPLINK_IFACE" info 2>/dev/null \
        | awk '/wiphy/ {print $2; exit}')"

    if [[ -z "${PHY:-}" ]]; then
        error "$UPLINK_IFACE no parece ser una interfaz Wi-Fi compatible."
        exit 1
    fi

    PHY_NAME="phy$PHY"

    info "PHY detectado: $PHY_NAME"
}

# ------------------------------------------------------------
# Detectar canal actual
# ------------------------------------------------------------

detect_channel() {
    CHANNEL="$(iw dev "$UPLINK_IFACE" info 2>/dev/null \
        | awk '/channel/ {print $2; exit}')"

    if [[ -z "${CHANNEL:-}" ]]; then
        error "No pude detectar el canal de $UPLINK_IFACE."
        error "La interfaz debe estar conectada a una Wi-Fi antes de iniciar."
        exit 1
    fi

    info "Canal de la conexión existente: $CHANNEL"
}

# ------------------------------------------------------------
# Crear interfaz AP virtual
# ------------------------------------------------------------

create_ap_interface() {

    if iw dev "$AP_IFACE" info >/dev/null 2>&1; then
        info "$AP_IFACE ya existe."
    else
        info "Creando interfaz AP virtual: $AP_IFACE"

        iw dev "$UPLINK_IFACE" interface add "$AP_IFACE" type managed \
            || {
                error "No pude crear $AP_IFACE."
                exit 1
            }
    fi

    # NetworkManager/systemd-networkd/etc. no deben gestionar esta
    # interfaz mientras hostapd la utiliza.
    if command -v nmcli >/dev/null 2>&1; then
        nmcli device set "$AP_IFACE" managed no >/dev/null 2>&1 || true
    fi

    ip link set "$AP_IFACE" up

    iw dev "$AP_IFACE" set type __ap

    ok "Interfaz AP preparada: $AP_IFACE"
}

# ------------------------------------------------------------
# Configurar IP
# ------------------------------------------------------------

configure_ip() {

    ip addr flush dev "$AP_IFACE"

    ip addr add "$AP_IP/24" dev "$AP_IFACE"

    ip link set "$AP_IFACE" up

    ok "$AP_IFACE -> $AP_IP/24"
}

# ------------------------------------------------------------
# Crear configuración hostapd
# ------------------------------------------------------------

create_hostapd_config() {

    local ssid="$1"
    local password="$2"

    cat > "$HOSTAPD_CONF" <<EOF
interface=$AP_IFACE
driver=nl80211

ssid=$ssid

hw_mode=g
channel=$CHANNEL

ieee80211n=1
wmm_enabled=1

auth_algs=1

wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase=$password
EOF

    ok "Configuración hostapd creada."
}

# ------------------------------------------------------------
# Crear configuración dnsmasq
# ------------------------------------------------------------

create_dnsmasq_config() {

    cat > "$DNSMASQ_CONF" <<EOF
interface=$AP_IFACE
bind-interfaces

dhcp-range=$DHCP_START,$DHCP_END,255.255.255.0,$DHCP_LEASE

dhcp-option=3,$AP_IP
dhcp-option=6,$AP_IP

server=1.1.1.1
server=8.8.8.8

pid-file=$DNSMASQ_PID
EOF

    ok "Configuración DHCP/DNS creada."
}

# ------------------------------------------------------------
# Activar forwarding
# ------------------------------------------------------------

enable_forwarding() {

    sysctl -w net.ipv4.ip_forward=1 >/dev/null

    ok "IPv4 forwarding activado."
}

# ------------------------------------------------------------
# NAT
# ------------------------------------------------------------

setup_iptables() {

    # Evitar duplicados
    iptables -t nat -D POSTROUTING -s "$AP_NET" -o "$UPLINK_IFACE" \
        -j MASQUERADE 2>/dev/null || true

    iptables -D FORWARD -i "$AP_IFACE" -o "$UPLINK_IFACE" \
        -j ACCEPT 2>/dev/null || true

    iptables -D FORWARD -i "$UPLINK_IFACE" -o "$AP_IFACE" \
        -m conntrack --ctstate RELATED,ESTABLISHED \
        -j ACCEPT 2>/dev/null || true

    # NAT
    iptables -t nat -A POSTROUTING \
        -s "$AP_NET" \
        -o "$UPLINK_IFACE" \
        -j MASQUERADE

    # AP -> Internet
    iptables -A FORWARD \
        -i "$AP_IFACE" \
        -o "$UPLINK_IFACE" \
        -j ACCEPT

    # Respuestas Internet -> AP
    iptables -A FORWARD \
        -i "$UPLINK_IFACE" \
        -o "$AP_IFACE" \
        -m conntrack \
        --ctstate RELATED,ESTABLISHED \
        -j ACCEPT

    ok "NAT y forwarding configurados."
}

# ------------------------------------------------------------
# Lanzar hostapd
# ------------------------------------------------------------

start_hostapd() {

    info "Iniciando hostapd..."

    hostapd -B \
        -P "$HOSTAPD_PID" \
        "$HOSTAPD_CONF"

    if [[ $? -ne 0 ]]; then
        error "hostapd no pudo iniciar."
        exit 1
    fi

    sleep 1

    if [[ -f "$HOSTAPD_PID" ]]; then
        ok "hostapd iniciado."
    else
        error "No se encontró el PID de hostapd."
        exit 1
    fi
}

# ------------------------------------------------------------
# Lanzar dnsmasq
# ------------------------------------------------------------

start_dnsmasq() {

    info "Iniciando DHCP/DNS..."

    dnsmasq \
        --conf-file="$DNSMASQ_CONF" \
        --pid-file="$DNSMASQ_PID"

    if [[ $? -ne 0 ]]; then
        error "dnsmasq no pudo iniciar."
        exit 1
    fi

    sleep 1

    if [[ -f "$DNSMASQ_PID" ]]; then
        ok "dnsmasq iniciado."
    else
        error "No se encontró el PID de dnsmasq."
        exit 1
    fi
}

# ------------------------------------------------------------
# START
# ------------------------------------------------------------

start_hotspot() {

    local ssid="${1:-$DEFAULT_SSID}"
    local password="${2:-$DEFAULT_PASSWORD}"

    if [[ ${#password} -lt 8 ]]; then
        error "La contraseña WPA debe tener al menos 8 caracteres."
        exit 1
    fi

    mkdir -p "$PID_DIR"

    detect_uplink

    if [[ "$UPLINK_IFACE" == "$AP_IFACE" ]]; then
        error "La interfaz de salida y la interfaz AP no pueden ser la misma."
        exit 1
    fi

    detect_phy
    detect_channel

    create_ap_interface
    configure_ip

    create_hostapd_config "$ssid" "$password"
    create_dnsmasq_config

    enable_forwarding
    setup_iptables

    start_hostapd
    start_dnsmasq

    echo
    echo "=========================================="
    echo "          HOTSPOT ACTIVO"
    echo "=========================================="
    echo
    echo "SSID:       $ssid"
    echo "Password:   $password"
    echo "AP:         $AP_IFACE"
    echo "Uplink:     $UPLINK_IFACE"
    echo "Gateway:    $AP_IP"
    echo "DHCP:       $DHCP_START - $DHCP_END"
    echo "Canal:      $CHANNEL"
    echo
    echo "=========================================="
}

# ------------------------------------------------------------
# STOP
# ------------------------------------------------------------

stop_hotspot() {

    info "Deteniendo hotspot..."

    if [[ -f "$DNSMASQ_PID" ]]; then
        kill "$(cat "$DNSMASQ_PID")" 2>/dev/null || true
        rm -f "$DNSMASQ_PID"
    else
        pkill -f "dnsmasq.*linux-hotspot-dnsmasq.conf" 2>/dev/null || true
    fi

    if [[ -f "$HOSTAPD_PID" ]]; then
        kill "$(cat "$HOSTAPD_PID")" 2>/dev/null || true
        rm -f "$HOSTAPD_PID"
    else
        pkill -f "hostapd.*linux-hotspot-hostapd.conf" 2>/dev/null || true
    fi

    # Eliminar reglas que creó el script.
    if [[ -n "${UPLINK_IFACE:-}" ]]; then

        iptables -t nat -D POSTROUTING \
            -s "$AP_NET" \
            -o "$UPLINK_IFACE" \
            -j MASQUERADE 2>/dev/null || true

        iptables -D FORWARD \
            -i "$AP_IFACE" \
            -o "$UPLINK_IFACE" \
            -j ACCEPT 2>/dev/null || true

        iptables -D FORWARD \
            -i "$UPLINK_IFACE" \
            -o "$AP_IFACE" \
            -m conntrack \
            --ctstate RELATED,ESTABLISHED \
            -j ACCEPT 2>/dev/null || true
    fi

    # Quitar IP
    ip addr flush dev "$AP_IFACE" 2>/dev/null || true

    # Eliminar interfaz virtual
    if iw dev "$AP_IFACE" info >/dev/null 2>&1; then
        iw dev "$AP_IFACE" del
    fi

    rm -f "$HOSTAPD_CONF"
    rm -f "$DNSMASQ_CONF"

    ok "Hotspot detenido."

    if command -v nmcli >/dev/null 2>&1; then
        nmcli device status
    fi
}

# ------------------------------------------------------------
# STATUS
# ------------------------------------------------------------

status_hotspot() {

    echo
    echo "===== Hotspot status ====="
    echo

    echo "Interfaces:"
    iw dev

    echo
    echo "Direcciones:"
    ip addr show "$AP_IFACE" 2>/dev/null || true

    echo
    echo "Rutas:"
    ip route

    echo
    echo "Procesos:"

    if [[ -f "$HOSTAPD_PID" ]]; then
        echo "hostapd: RUNNING (PID $(cat "$HOSTAPD_PID"))"
    else
        echo "hostapd: STOPPED"
    fi

    if [[ -f "$DNSMASQ_PID" ]]; then
        echo "dnsmasq: RUNNING (PID $(cat "$DNSMASQ_PID"))"
    else
        echo "dnsmasq: STOPPED"
    fi

    echo
    echo "Clientes Wi-Fi:"
    iw dev "$AP_IFACE" station dump 2>/dev/null || true
}

# ------------------------------------------------------------
# RESTART
# ------------------------------------------------------------

restart_hotspot() {

    stop_hotspot

    sleep 1

    start_hotspot "$@"
}

# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

require_root
check_dependencies

case "${1:-}" in

    start)
        start_hotspot "${2:-}" "${3:-}"
        ;;

    stop)
        detect_uplink
        stop_hotspot
        ;;

    restart)
        detect_uplink
        restart_hotspot "${2:-}" "${3:-}"
        ;;

    status)
        status_hotspot
        ;;

    *)
        echo
        echo "Uso:"
        echo
        echo "  sudo $0 start"
        echo "  sudo $0 start \"MiHotspot\" \"MiPassword123\""
        echo "  sudo $0 stop"
        echo "  sudo $0 restart \"NuevoSSID\" \"NuevaPassword123\""
        echo "  sudo $0 status"
        echo
        exit 1
        ;;
esac
