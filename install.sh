#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
source scripts/prerequisites.sh
install_prerequisites=0
noninteractive=0
for argument in "$@"; do
  case "$argument" in
    --install-prerequisites) install_prerequisites=1 ;;
    --non-interactive) noninteractive=1 ;;
  esac
done
ensure_prerequisites "$install_prerequisites" "$noninteractive"
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
uv sync --project backend --python 3.12 --locked
npm ci
.venv/bin/python -m gaiavfx.setup media install
npm run build
exec .venv/bin/python scripts/install.py "$@"
