# WiFi Hotspot

Gestor moderno y open source de puntos de acceso Wi-Fi para Linux.

WiFi Hotspot convierte un equipo Linux en un punto de acceso Wi-Fi. Está pensado
para funcionar en distintas distribuciones, con una interfaz nativa GTK4 +
Libadwaita, un CLI potente y un pequeño servicio privilegiado para que la GUI no
tenga que ejecutarse como root.

> **Estado:** alfa. Primero se está construyendo el núcleo genérico del CLI; el
> daemon y la interfaz GTK4 llegan en los siguientes hitos. El `wifi.sh` original
> se conserva en `legacy/` y sigue funcionando mientras el nuevo CLI alcanza la
> paridad.

## ¿Por qué otra herramienta de hotspot?

Muchas herramientas existentes dependen de una sola distribución, de un gestor
de red concreto o de una única topología Wi-Fi. WiFi Hotspot quiere soportar:

- **Uplink por Ethernet + AP Wi-Fi** (la configuración más compatible).
- **AP + STA simultáneos en la misma tarjeta** (si el driver lo permite).
- **Dos tarjetas Wi-Fi** con canal independiente.
- NetworkManager, systemd-networkd o ningún gestor de red.
- Entornos con `firewalld`, `iptables`, `nftables` y `ufw`.
- 2.4 GHz y 5 GHz, WPA2, WPA3 y modo mixto.
- Debian 13+, Ubuntu 24.04+, Linux Mint 22+, Fedora 41+, Arch Linux,
  Kali Linux y openSUSE Tumbleweed.

## Funciones previstas

- Hotspot en un clic con una app GTK4 + Libadwaita.
- Gestión de SSID/contraseña y QR para compartir.
- Lista de clientes en vivo con señal, tráfico y expulsión.
- Gráfica de tráfico, logs, perfiles y autoarranque.
- País/dominio regulatorio, banda, canal y seguridad.
- CLI con salida `--json` y comando `doctor` para diagnóstico.
- Paquetes nativos: `.deb`, `.rpm` y `PKGBUILD`.
- Interfaz en español e inglés.

## Inicio rápido (desarrollo)

Requisitos en Debian/Ubuntu/Kali:

```bash
sudo apt install python3 hostapd dnsmasq-base iw iproute2 iptables nftables
```

Ejecutar el CLI desde el repositorio:

```bash
PYTHONPATH=src python3 -m wifi_hotspot status
PYTHONPATH=src python3 -m wifi_hotspot doctor
sudo PYTHONPATH=src python3 -m wifi_hotspot start "MiHotspot" "MiPassword123"
sudo PYTHONPATH=src python3 -m wifi_hotspot stop
```

El script original sigue disponible:

```bash
sudo ./legacy/wifi.sh start "MiHotspot" "MiPassword123"
sudo ./legacy/wifi.sh stop
```

Al instalar el paquete Python se expone el comando `wifi-hotspot`, y el wrapper
`wifi.sh` conserva los comandos antiguos:

```bash
python3 -m pip install --user .
wifi-hotspot doctor
sudo wifi-hotspot start "MiHotspot" "MiPassword123"
sudo ./wifi.sh stop
```

## Arquitectura

```
GUI (GTK4)  ──socket Unix──>  wifi-hotspotd (root)  ──>  hostapd / dnsmasq / firewall
CLI         ──socket Unix──>  (o modo directo con sudo)
```

El núcleo es Python 3.10+ y usa solo la biblioteca estándar. GTK4, Libadwaita y
PyGObject son paquetes del sistema que usa la GUI.

Consulta [docs/architecture.md](docs/architecture.md) para más detalles.

## Seguridad

- Los secretos se guardan con permisos `0600` en directorios propiedad de root.
- Las configuraciones no se escriben en `/tmp`.
- El daemon usa un socket Unix local y polkit; la GUI nunca corre como root.
- Sin telemetría ni llamadas de red salvo los servidores DNS configurados.

Para reportar una vulnerabilidad, lee [SECURITY.md](SECURITY.md).

## Contribuir

Las contribuciones son bienvenidas. Lee [CONTRIBUTING.md](CONTRIBUTING.md) antes
de abrir un pull request. Los reportes de hardware son especialmente útiles.

## Licencia

Copyright (C) 2026 Carlos Del Rosario y colaboradores.

Este proyecto es software libre: puedes redistribuirlo y/o modificarlo bajo los
términos de la GNU General Public License, versión 3 o posterior. Consulta
[LICENSE](LICENSE).
