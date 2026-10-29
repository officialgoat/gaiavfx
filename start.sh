#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
case "${1:-}" in
  --with-comfy) .venv/bin/python scripts/start_comfy.py ;;
  '') ;;
  *) printf 'Usage: %s [--with-comfy]\n' "$0" >&2; exit 2 ;;
esac
exec npm run dev
