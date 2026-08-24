#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Normally this script sits at the repo root, but finalize_install_layout seeds a
# copy in backend/ so reset.command can rebuild the root from it. run.py marks the
# backend, so resolve the root from whichever of the two layouts we were launched in.
if [[ -f "$SCRIPT_DIR/run.py" ]]; then
  ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  ROOT_DIR="$SCRIPT_DIR"
fi
VENV_DIR="$ROOT_DIR/.venv"
BACKEND_DIR="$ROOT_DIR/backend"
LOG_DIR="$BACKEND_DIR/logs"
LAUNCHER_LOG="$LOG_DIR/launcher.log"
ONBOARDING_FLAG="$BACKEND_DIR/data/onboarding_complete.flag"

mkdir -p "$LOG_DIR"

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] in {(3, 11), (3, 12), (3, 13)} else 1)' >/dev/null 2>&1; then
        printf '%s\n' "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

# Moves the file if the destination is free; otherwise just drops the root copy.
relocate_root_file() {
  local src="$ROOT_DIR/$1"
  local dest="$2"
  [[ -f "$src" ]] || return 0
  if [[ -e "$dest" ]]; then
    rm -f "$src"
  else
    mv "$src" "$dest"
  fi
}

# Tidy the repo root after a fresh install: keep only the launcher plus the
# backend/ and documentation/ folders. Canonical installer copies live in backend/.
finalize_install_layout() {
  local doc_dir="$ROOT_DIR/documentation"
  local devcfg_dir="$doc_dir/dev-config"
  local f
  mkdir -p "$doc_dir" "$devcfg_dir"

  # Docs -> documentation/ . The root CLAUDE.md is the context-mode routing copy and
  # documentation/ already ships its own, so park it under a distinct name.
  relocate_root_file "DESIGN.md" "$doc_dir/DESIGN.md"
  relocate_root_file "CLAUDE.md" "$doc_dir/CLAUDE.context-mode.md"
  # .gitattributes deliberately stays in the root -- see the note above the
  # dev-config move list. Losing it makes every .bat check out as LF.
  relocate_root_file ".gitignore" "$devcfg_dir/.gitignore"

  # Seed the canonical installer copies in backend/ BEFORE dropping the root ones.
  # reset.command and reset_win.bat rebuild the clean-install root from exactly
  # these copies, so skipping this step means a reset can never restore the
  # installers -- they would be gone for good.
  for f in MAC_Install.command WIN_Install.bat; do
    if [[ -f "$ROOT_DIR/$f" && ! -f "$BACKEND_DIR/$f" ]]; then
      cp "$ROOT_DIR/$f" "$BACKEND_DIR/$f"
      chmod +x "$BACKEND_DIR/$f" 2>/dev/null || true
    fi
  done

  # Drop each installer from the root -- only once its backend/ copy exists. Unlike
  # cmd.exe, which reads a batch file lazily and would kill this process mid-run,
  # bash keeps the open script fd valid after an unlink, so deleting the very file
  # we are executing is safe here and needs no deferral to the last line.
  for f in MAC_Install.command WIN_Install.bat; do
    if [[ -f "$BACKEND_DIR/$f" ]]; then
      rm -f "$ROOT_DIR/$f"
    fi
  done
}

ensure_venv() {
  local pyexe
  if [[ -x "$VENV_DIR/bin/python3" && -f "$VENV_DIR/bin/activate" ]]; then
    return 0
  fi

  echo
  echo "  First launch: setting up PINE..."
  echo

  if ! pyexe="$(find_python)"; then
    echo "  ERROR: Python 3.11, 3.12, or 3.13 is required."
    echo "  Install one of those versions, then run MAC_Install.command again."
    echo
    exit 1
  fi

  echo "  Using $("$pyexe" -c 'import sys; print(sys.executable)')"
  # pip is a wall of text; send it to the log and show one line per step instead.
  "$pyexe" -m venv "$VENV_DIR"
  echo "  Upgrading pip..."
  "$VENV_DIR/bin/python3" -m pip install --upgrade pip --quiet >>"$LAUNCHER_LOG" 2>&1
  echo "  Installing dependencies (this takes a few minutes)..."
  if ! "$VENV_DIR/bin/pip" install -r "$BACKEND_DIR/requirements.txt" --quiet >>"$LAUNCHER_LOG" 2>&1; then
    echo "  Dependency install failed. Details: $LAUNCHER_LOG"
    exit 1
  fi

  # Move dev/GitHub files out of root for end-users. .gitattributes is NOT in this
  # list on purpose: it carries "*.bat text eol=crlf", and cmd.exe cannot find
  # labels in an LF batch file -- WIN_Install.bat then dies at "call :open_browser"
  # and the window closes with no browser.
  mkdir -p "$ROOT_DIR/documentation/dev-config"
  for f in AGENTS.md LICENSE .editorconfig .gitignore .pre-commit-config.yaml; do
    [[ -f "$ROOT_DIR/$f" ]] && mv "$ROOT_DIR/$f" "$ROOT_DIR/documentation/dev-config/$f" 2>/dev/null || true
  done

  echo
  echo "  First setup step complete."
  echo

  # Tidy the repo root now that the install finished.
  finalize_install_layout
}

ensure_venv

cd "$BACKEND_DIR"

PORT_FILE="$BACKEND_DIR/data/supervisor.port"
rm -f "$PORT_FILE"

echo "  Starting PINE..."
nohup "$VENV_DIR/bin/python3" supervisor.py >>"$LAUNCHER_LOG" 2>&1 &

# The supervisor shuts itself down once its startup grace passes with no browser
# lease, so this window has to open the browser -- the job WIN_Install.bat does
# on Windows. Wait for the backend to answer health before handing over the URL.
BACKEND_PORT="$(
  "$VENV_DIR/bin/python3" - "$PORT_FILE" <<'PY' || true
import json
import sys
import time
import urllib.error
import urllib.request

port_file = sys.argv[1]
deadline = time.time() + 120
while time.time() < deadline:
    try:
        with open(port_file, encoding='utf-8') as handle:
            port = json.load(handle)['backend_port']
    except (OSError, ValueError, KeyError):
        time.sleep(0.5)
        continue
    try:
        with urllib.request.urlopen(
            f'http://127.0.0.1:{port}/api/health', timeout=2
        ) as response:
            if response.status == 200:
                print(port)
                break
    except (urllib.error.URLError, OSError):
        pass
    time.sleep(0.5)
PY
)"

if [[ -z "$BACKEND_PORT" ]]; then
  echo "  PINE did not answer in time. Details: $LAUNCHER_LOG"
  echo "  Open http://127.0.0.1:5000/ manually once it comes up."
  exit 1
fi

echo "  Opening PINE at http://127.0.0.1:$BACKEND_PORT/"
echo
open "http://127.0.0.1:$BACKEND_PORT/"
