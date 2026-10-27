#!/bin/sh
# Build the Debian binary package (architecture: all).
set -eu

root=$(cd -- "$(dirname -- "$0")/../.." && pwd)
version=$("$root/packaging/common/version.sh")

build="$root/build/deb"
stage="$build/wifi-hotspot_${version}_all"
output="$root/dist/wifi-hotspot_${version}_all.deb"

rm -rf "$build"
install -d "$stage/DEBIAN" \
    "$stage/usr/bin" \
    "$stage/usr/lib/wifi-hotspot" \
    "$stage/usr/lib/systemd/system" \
    "$stage/usr/share/applications" \
    "$stage/usr/share/doc/wifi-hotspot" \
    "$stage/usr/share/icons/hicolor/scalable/apps" \
    "$stage/usr/share/metainfo" \
    "$stage/usr/share/polkit-1/actions"

cp -a "$root/src/wifi_hotspot" "$stage/usr/lib/wifi-hotspot/"
find "$stage/usr/lib/wifi-hotspot" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$stage/usr/lib/wifi-hotspot" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find "$stage/usr/lib/wifi-hotspot" -type d -exec chmod 0755 {} +
find "$stage/usr/lib/wifi-hotspot" -type f -exec chmod 0644 {} +

install -m 0755 "$root/packaging/common/wifi-hotspot" "$stage/usr/bin/"
install -m 0755 "$root/packaging/common/wifi-hotspotd" "$stage/usr/bin/"
install -m 0755 "$root/packaging/common/wifi-hotspot-gui" "$stage/usr/bin/"
install -m 0755 "$root/wifi.sh" "$stage/usr/bin/wifi.sh"

install -m 0644 "$root/data/desktop/io.github.carlosdelrosario7.WifiHotspot.desktop" \
    "$stage/usr/share/applications/"
install -m 0644 "$root/data/icons/hicolor/scalable/apps/io.github.carlosdelrosario7.WifiHotspot.svg" \
    "$stage/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$root/data/metainfo/io.github.carlosdelrosario7.WifiHotspot.metainfo.xml" \
    "$stage/usr/share/metainfo/"
install -m 0644 "$root/data/polkit/io.github.carlosdelrosario7.WifiHotspot.policy" \
    "$stage/usr/share/polkit-1/actions/"
install -m 0644 "$root/data/systemd/wifi-hotspotd.service" \
    "$stage/usr/lib/systemd/system/"

install -m 0644 "$root/packaging/deb/copyright" "$stage/usr/share/doc/wifi-hotspot/copyright"
install -m 0644 "$root/README.md" "$stage/usr/share/doc/wifi-hotspot/README.md"
install -m 0644 "$root/README.en.md" "$stage/usr/share/doc/wifi-hotspot/README.en.md"
install -m 0644 "$root/CHANGELOG.md" "$stage/usr/share/doc/wifi-hotspot/CHANGELOG.md"
gzip -9n -c "$root/CHANGELOG.md" > "$stage/usr/share/doc/wifi-hotspot/changelog.gz"

size=$(du -sk "$stage" | cut -f1)
sed -e "s/@VERSION@/$version/g" -e "s/@SIZE@/$size/g" \
    "$root/packaging/deb/control.in" > "$stage/DEBIAN/control"
install -m 0755 "$root/packaging/deb/postinst" "$stage/DEBIAN/postinst"
install -m 0755 "$root/packaging/deb/prerm" "$stage/DEBIAN/prerm"
install -m 0755 "$root/packaging/deb/postrm" "$stage/DEBIAN/postrm"

mkdir -p "$root/dist"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$root" log -1 --format=%ct 2>/dev/null || date +%s)}"
dpkg-deb --root-owner-group --build "$stage" "$output"
printf '%s\n' "$output"
