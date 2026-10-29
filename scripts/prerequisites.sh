#!/usr/bin/env bash
# Sourced by the launchers. No installation or persistent environment changes on load.
prerequisite_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

add_portable_tools_path() {
  local folder
  for folder in "$prerequisite_root/.runtime/tools/git/bin" "$prerequisite_root/.runtime/tools/uv" "$prerequisite_root/.runtime/tools/node/bin"; do
    if [[ -d "$folder" ]]; then export PATH="$folder:$PATH"; fi
  done
}

prerequisite_hints() {
  local distro='' like=''
  if [[ -r /etc/os-release ]]; then
    distro=$(awk -F= '$1 == "ID" {gsub(/"/, "", $2); print $2}' /etc/os-release)
    like=$(awk -F= '$1 == "ID_LIKE" {gsub(/"/, "", $2); print $2}' /etc/os-release)
  fi
  case " $distro $like " in
    *debian*|*ubuntu*) printf '%s\n' 'Git: sudo apt update && sudo apt install git' ;;
    *fedora*|*rhel*|*centos*) printf '%s\n' 'Git: sudo dnf install git' ;;
    *arch*|*manjaro*) printf '%s\n' 'Git: sudo pacman -S git' ;;
    *) printf '%s\n' 'Git: sudo apt install git / sudo dnf install git / sudo pacman -S git (choose your distro).' ;;
  esac
  printf '%s\n' \
    'Node.js >=24 with npm: use your distro if it provides 24+, NodeSource (https://github.com/nodesource/distributions), or nvm (https://github.com/nvm-sh/nvm).' \
    'uv: https://docs.astral.sh/uv/getting-started/installation/' \
    'Node.js 24: https://nodejs.org/en/download' \
    'Git: https://git-scm.com/downloads/linux' \
    'Rerun with --install-prerequisites to consent to portable uv/Node installation.'
}

# Read the simple, checked-in manifest without depending on Python, Node or jq
# before they are installed. Tests validate these fields against a JSON parser.
prerequisite_field() {
  local tool=$1 field=$2
  awk -v tool="$tool" -v field="$field" '
    /^  "linux":/ { linux_section=1 }
    linux_section && $0 ~ "^    \"" tool "\":" { asset=1; next }
    asset && /^    }/ { exit }
    asset && $0 ~ "^      \"" field "\":" {
      sub(/^[^:]+: */, ""); sub(/,$/, ""); gsub(/^"|"$/, ""); print; exit
    }
  ' "$prerequisite_root/scripts/prerequisites.json"
}

install_portable_prerequisite() (
  set -euo pipefail
  local tool=$1 url bytes sha prefix archive staging='' target
  trap 'code=$?; if [[ -n "$staging" ]]; then rm -rf -- "$staging"; fi; if (( code )); then prerequisite_hints; fi' EXIT
  [[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || { printf '%s\n' 'Portable prerequisites require Linux x86-64.' >&2; exit 1; }
  for dependency in sha256sum tar mktemp wc; do
    command -v "$dependency" >/dev/null || { printf 'Missing archive utility: %s\n' "$dependency" >&2; exit 1; }
  done
  url=$(prerequisite_field "$tool" url)
  bytes=$(prerequisite_field "$tool" bytes)
  sha=$(prerequisite_field "$tool" sha256)
  prefix=$(prerequisite_field "$tool" strip_prefix)
  [[ "$url" == https://* && "$sha" =~ ^[0-9a-f]{64}$ && "$bytes" =~ ^[0-9]+$ && -n "$prefix" ]] || exit 1
  mkdir -p -- "$prerequisite_root/.runtime/tools/downloads"
  archive="$prerequisite_root/.runtime/tools/downloads/${url##*/}"
  printf 'Downloading %s: %s\n' "$tool" "$url"
  if command -v curl >/dev/null; then
    curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' --output "$archive" "$url"
  elif command -v wget >/dev/null; then
    wget --https-only -O "$archive" "$url"
  else
    printf '%s\n' 'Install curl or wget with your distro package manager, then retry.' >&2
    exit 1
  fi
  if [[ $(wc -c < "$archive") -ne "$bytes" ]] || ! printf '%s  %s\n' "$sha" "$archive" | sha256sum --check --status; then
    rm -f -- "$archive"
    printf 'SHA-256 or size mismatch for %s; deleted archive. No files extracted.\n' "$tool" >&2
    exit 1
  fi
  staging=$(mktemp -d "$prerequisite_root/.runtime/tools/.extract-$tool-XXXXXX")
  tar -xf "$archive" -C "$staging" --strip-components=1
  if [[ "$tool" == node ]]; then
    [[ -x "$staging/bin/node" && -x "$staging/bin/npm" ]]
  else
    [[ -x "$staging/uv" ]]
  fi
  target="$prerequisite_root/.runtime/tools/$tool"
  [[ "$tool" == uv || "$tool" == node ]] || exit 1
  rm -rf -- "$target"
  mv -- "$staging" "$target"
  printf 'Verified and installed %s in %s\n' "$tool" "$target"
)

ensure_prerequisites() {
  local consent=$1 noninteractive=$2 tool answer major missing_git=0 failed=0
  local missing=()
  add_portable_tools_path
  command -v git >/dev/null || missing_git=1
  command -v uv >/dev/null || missing+=(uv)
  major=$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null) || major=0
  if [[ ! "$major" =~ ^[0-9]+$ ]] || (( major < 24 )) || ! command -v npm >/dev/null; then missing+=(node); fi
  if (( missing_git == 0 && ${#missing[@]} == 0 )); then return 0; fi
  if (( missing_git )); then printf '%s\n' 'Missing prerequisite: git'; fi
  if (( ${#missing[@]} )); then printf 'Missing prerequisites: %s\n' "${missing[*]}"; fi
  prerequisite_hints
  # Git requires the user's distro package manager; never invoke sudo ourselves.
  if (( missing_git )); then return 1; fi
  if (( ! consent )); then
    if (( noninteractive )) || [[ ! -t 0 ]]; then return 1; fi
    printf '%s\n' "Method: verified portable archives in .runtime/tools. Installing accepts these packages' licence terms."
    while true; do
      read -r -p "Install the missing prerequisites (${missing[*]}) now? [Y/n] " answer || return 1
      case "$answer" in
        ''|[yY]|[yY][eE][sS]) break ;;
        [nN]|[nN][oO]) return 1 ;;
      esac
    done
  else
    printf '%s\n' "Installing verified portable archives in .runtime/tools; --install-prerequisites accepts these packages' licence terms."
  fi
  for tool in "${missing[@]}"; do
    # Invoke as a simple command: Bash must retain errexit inside the installer.
    install_portable_prerequisite "$tool"
    add_portable_tools_path
  done
  command -v uv >/dev/null || failed=1
  command -v npm >/dev/null || failed=1
  major=$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null) || major=0
  if [[ ! "$major" =~ ^[0-9]+$ ]] || (( major < 24 )); then failed=1; fi
  if (( failed )); then prerequisite_hints; return 1; fi
  printf '%s\n' 'For npm in new terminals, optionally add export PATH="<repo>/.runtime/tools/node/bin:<repo>/.runtime/tools/uv:$PATH" to your shell profile; no persistent PATH change was made.'
}
