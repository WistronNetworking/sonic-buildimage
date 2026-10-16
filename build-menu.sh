#!/usr/bin/env bash
set -euo pipefail
menu_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$menu_root/wistron_build_menu.py" "$@"
