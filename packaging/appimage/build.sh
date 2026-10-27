#!/bin/sh
# Build a portable AppImage of the CLI and GTK4 front end.
#
# The AppImage intentionally does not bundle GTK, libadwaita, PyGObject, or the
# privileged daemon. Those are system integration points; install the package
# for your distribution to get the daemon, polkit policy, and systemd unit.
set -eu

root=$(cd -- "$(dirname -- "$0")/../.." && pwd)
version=$("$root/packaging/common/version.sh")
appdir="$root/build/appimage/AppDir"
output="$root/dist/WiFi_Hotspot-${version}-x86_64.AppImage"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/wifi-hotspot"
tool="$cache/appimagetool-x86_64.AppImage"
url="${APPIMAGETOOL_URL:-https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage}"

rm -rf "$appdir"
install -d \
    "$appdir/usr/bin" \
    "$appdir/usr/lib/wifi-hotspot" \
    "$appdir/usr/lib/systemd/system" \
    "$appdir/usr/share/applications" \
    "$appdir/usr/share/icons/hicolor/scalable/apps" \
    "$appdir/usr/share/metainfo" \
    "$appdir/usr/share/polkit-1/actions"

cp -a "$root/src/wifi_hotspot" "$appdir/usr/lib/wifi-hotspot/"
find "$appdir/usr/lib/wifi-hotspot" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$appdir/usr/lib/wifi-hotspot" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

install -m 0755 "$root/packaging/appimage/AppRun" "$appdir/AppRun"
install -m 0755 "$root/packaging/common/wifi-hotspot" "$appdir/usr/bin/"
install -m 0755 "$root/packaging/common/wifi-hotspotd" "$appdir/usr/bin/"
install -m 0755 "$root/packaging/common/wifi-hotspot-gui" "$appdir/usr/bin/"
install -m 0755 "$root/wifi.sh" "$appdir/usr/bin/wifi.sh"

install -m 0644 "$root/data/desktop/io.github.carlosdelrosario7.WifiHotspot.desktop" \
    "$appdir/io.github.carlosdelrosario7.WifiHotspot.desktop"
install -m 0644 "$root/data/desktop/io.github.carlosdelrosario7.WifiHotspot.desktop" \
    "$appdir/usr/share/applications/"
install -m 0644 "$root/data/icons/hicolor/scalable/apps/io.github.carlosdelrosario7.WifiHotspot.svg" \
    "$appdir/io.github.carlosdelrosario7.WifiHotspot.svg"
install -m 0644 "$root/data/icons/hicolor/scalable/apps/io.github.carlosdelrosario7.WifiHotspot.svg" \
    "$appdir/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$root/data/metainfo/io.github.carlosdelrosario7.WifiHotspot.metainfo.xml" \
    "$appdir/usr/share/metainfo/io.github.carlosdelrosario7.WifiHotspot.appdata.xml"
install -m 0644 "$root/data/polkit/io.github.carlosdelrosario7.WifiHotspot.policy" \
    "$appdir/usr/share/polkit-1/actions/"
install -m 0644 "$root/data/systemd/wifi-hotspotd.service" \
    "$appdir/usr/lib/systemd/system/"

if [ ! -x "$tool" ]; then
    install -d "$cache"
    printf 'Descargando appimagetool...\n' >&2
    curl -fL --retry 3 --connect-timeout 20 -o "$tool.part" "$url"
    chmod 0755 "$tool.part"
    mv "$tool.part" "$tool"
fi

mkdir -p "$root/dist"
export ARCH=x86_64
APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream "$appdir" "$output"
printf '%s\n' "$output"
