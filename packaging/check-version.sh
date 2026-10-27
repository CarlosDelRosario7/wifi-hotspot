#!/bin/sh
# Ensure that every package metadata file uses the same version.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
version=$("$root/packaging/common/version.sh")
failed=0

check() {
    file=$1
    pattern=$2
    if ! grep -q "$pattern" "$root/$file"; then
        printf 'ERROR: %s no contiene la versión %s\n' "$file" "$version" >&2
        failed=1
    fi
}

check "src/wifi_hotspot/__init__.py" "__version__ = \"$version\""
check "packaging/rpm/wifi-hotspot.spec" "%global pkgversion $version"
check "packaging/arch/PKGBUILD" "pkgver=$version"
check "CHANGELOG.md" "## \[$version\]"

if [ -n "${GITHUB_REF_NAME:-}" ]; then
    case "$GITHUB_REF_NAME" in
        "v$version") ;;
        *)
            printf 'ERROR: el tag %s no coincide con la versión %s\n' \
                "$GITHUB_REF_NAME" "$version" >&2
            failed=1
            ;;
    esac
fi

if [ "$failed" -ne 0 ]; then
    exit 1
fi
printf 'Versión consistente: %s\n' "$version"
