#!/usr/bin/env bash
set -euo pipefail
menu_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# Always use this repository, independent of the caller's working directory.
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$menu_root/wistron_build_menu.py" "$@"
