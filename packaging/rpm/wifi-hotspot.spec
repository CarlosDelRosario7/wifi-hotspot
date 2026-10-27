%global pkgversion 0.1.0
%global appdir /usr/lib/wifi-hotspot
%{!?_unitdir:%global _unitdir /usr/lib/systemd/system}

Name:           wifi-hotspot
Version:        %{pkgversion}
Release:        1%{?dist}
Summary:        Modern Linux Wi-Fi hotspot manager

License:        GPL-3.0-or-later
URL:            https://github.com/CarlosDelRosario7/wifi-hotspot
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.10
Requires:       hostapd
Requires:       dnsmasq
Requires:       /usr/sbin/iw
Requires:       /usr/sbin/ip
Requires:       qrencode
Requires:       /usr/bin/pkcheck
Recommends:     python3-gobject
Recommends:     gtk4
Recommends:     libadwaita
Recommends:     NetworkManager
Suggests:       firewalld

%description
WiFi Hotspot turns a Linux computer into a Wi-Fi access point. It supports
Ethernet uplinks, concurrent AP+STA operation on one radio, and two wireless
cards.

It provides a command-line interface, a small privileged daemon protected by
polkit and a Unix socket, and a GTK4/Libadwaita interface. SSID, password,
security, band, channel, country, DHCP, DNS, firewall, client list, traffic
counters, QR sharing and profiles are configurable.

%prep
%setup -q

%build
# Pure Python package: nothing to compile.

%install
rm -rf %{buildroot}
install -d %{buildroot}%{_bindir}
install -d %{buildroot}%{appdir}
install -d %{buildroot}%{_datadir}/applications
install -d %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
install -d %{buildroot}%{_datadir}/metainfo
install -d %{buildroot}%{_datadir}/polkit-1/actions
install -d %{buildroot}%{_unitdir}

cp -a src/wifi_hotspot %{buildroot}%{appdir}/wifi_hotspot
find %{buildroot}%{appdir}/wifi_hotspot -type d -name __pycache__ -prune -exec rm -rf {} +
find %{buildroot}%{appdir}/wifi_hotspot -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
chmod -R u+rwX,go+rX,go-w %{buildroot}%{appdir}/wifi_hotspot

install -m 0755 packaging/common/wifi-hotspot %{buildroot}%{_bindir}/wifi-hotspot
install -m 0755 packaging/common/wifi-hotspotd %{buildroot}%{_bindir}/wifi-hotspotd
install -m 0755 packaging/common/wifi-hotspot-gui %{buildroot}%{_bindir}/wifi-hotspot-gui
install -m 0755 wifi.sh %{buildroot}%{_bindir}/wifi.sh

install -m 0644 data/desktop/io.github.carlosdelrosario7.WifiHotspot.desktop \
    %{buildroot}%{_datadir}/applications/
install -m 0644 data/icons/hicolor/scalable/apps/io.github.carlosdelrosario7.WifiHotspot.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/
install -m 0644 data/metainfo/io.github.carlosdelrosario7.WifiHotspot.metainfo.xml \
    %{buildroot}%{_datadir}/metainfo/
install -m 0644 data/polkit/io.github.carlosdelrosario7.WifiHotspot.policy \
    %{buildroot}%{_datadir}/polkit-1/actions/
install -m 0644 data/systemd/wifi-hotspotd.service %{buildroot}%{_unitdir}/

%files
%license LICENSE
%doc README.md README.en.md CHANGELOG.md
%{_bindir}/wifi-hotspot
%{_bindir}/wifi-hotspotd
%{_bindir}/wifi-hotspot-gui
%{_bindir}/wifi.sh
%{appdir}/wifi_hotspot
%{_datadir}/applications/io.github.carlosdelrosario7.WifiHotspot.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.github.carlosdelrosario7.WifiHotspot.svg
%{_datadir}/metainfo/io.github.carlosdelrosario7.WifiHotspot.metainfo.xml
%{_datadir}/polkit-1/actions/io.github.carlosdelrosario7.WifiHotspot.policy
%{_unitdir}/wifi-hotspotd.service

%post
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || :
    systemctl enable wifi-hotspotd.service >/dev/null 2>&1 || :
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q %{_datadir}/applications >/dev/null 2>&1 || :
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q %{_datadir}/icons/hicolor >/dev/null 2>&1 || :
fi

%preun
if [ "$1" -eq 0 ] && [ -d /run/systemd/system ]; then
    systemctl stop wifi-hotspotd.service >/dev/null 2>&1 || :
    systemctl disable wifi-hotspotd.service >/dev/null 2>&1 || :
fi

%postun
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload >/dev/null 2>&1 || :
fi

%changelog
* Mon Oct 26 2026 Carlos Del Rosario <65183782+CarlosDelRosario7@users.noreply.github.com> - 0.1.0-1
- Initial release with CLI, root daemon, GTK4 interface, and system integration.
