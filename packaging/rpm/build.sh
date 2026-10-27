#!/bin/sh
# Build a noarch RPM from the current checkout.
set -eu

root=$(cd -- "$(dirname -- "$0")/../.." && pwd)
version=$("$root/packaging/common/version.sh")
top="$root/build/rpm"

rm -rf "$top"
install -d "$top/BUILD" "$top/BUILDROOT" "$top/RPMS" "$top/SOURCES" "$top/SPECS" "$top/SRPMS"

tar -C "$root" \
    --exclude=.git \
    --exclude=build \
    --exclude=dist \
    --exclude=.pytest_cache \
    --exclude=.ruff_cache \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.egg-info' \
    --transform "s,^\.,wifi-hotspot-${version}," \
    -czf "$top/SOURCES/wifi-hotspot-${version}.tar.gz" .

rpmbuild -bb "$root/packaging/rpm/wifi-hotspot.spec" \
    --define "_topdir $top" \
    --define "_sourcedir $top/SOURCES" \
    --define "_rpmdir $top/RPMS" \
    --define "_builddir $top/BUILD" \
    --define "_buildrootdir $top/BUILDROOT" \
    --define "_specdir $top/SPECS" \
    --define "_srcrpmdir $top/SRPMS"

mkdir -p "$root/dist"
cp "$top"/RPMS/noarch/wifi-hotspot-"$version"-*.rpm "$root/dist/"
printf '%s\n' "$root/dist/wifi-hotspot-${version}-1.noarch.rpm"
