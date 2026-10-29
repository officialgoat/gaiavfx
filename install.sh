#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
missing=0
for tool in git uv node npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    case "$tool" in
      git) hint='https://git-scm.com/downloads/linux' ;;
      uv) hint='https://docs.astral.sh/uv/getting-started/installation/' ;;
      node|npm) hint='Install Node.js >=24 (includes npm): https://nodejs.org/en/download' ;;
    esac
    printf 'Missing %s. %s\n' "$tool" "$hint"
    missing=1
  fi
done
if (( missing )); then exit 1; fi
if (( $(node -p 'Number(process.versions.node.split(".")[0])') < 24 )); then
  printf '%s\n' 'Node.js >=24 required: https://nodejs.org/en/download'
  exit 1
fi
export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
uv sync --project backend --python 3.12 --locked
npm ci
.venv/bin/python -m gaiavfx.setup media install
npm run build
exec .venv/bin/python scripts/install.py "$@"
