#!/bin/sh
# Print the project version from pyproject.toml so every package and tag agree.
set -eu

root=$(cd -- "$(dirname -- "$0")/../.." && pwd)
python3 - "$root/pyproject.toml" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
if not match:
    raise SystemExit("version not found in pyproject.toml")
print(match.group(1))
PY
