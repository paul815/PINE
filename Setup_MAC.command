#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Normally this script sits at the repo root, but seed_backend_installers puts a
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

# Store the canonical installer copies in backend/ after a fresh install.
# reset.command and reset_win.bat rebuild the clean-install root from exactly
# these copies, so skipping this step means a reset can never restore the
# installers -- they would be gone for good.
#
# Nothing else happens to the root here. Its installers and dev files are
# finalize_root_layout_after_onboarding's business, and that runs when the user
# presses Launch PINE: until the install is known to have succeeded, this
# installer is the only way back into it.
seed_backend_installers() {
  local f
  for f in Setup_MAC.command Setup_WIN.bat; do
    if [[ -f "$ROOT_DIR/$f" && ! -f "$BACKEND_DIR/$f" ]]; then
      cp "$ROOT_DIR/$f" "$BACKEND_DIR/$f"
      chmod +x "$BACKEND_DIR/$f" 2>/dev/null || true
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
    echo "  Install one of those versions, then run Setup_MAC.command again."
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

  echo
  echo "  First setup step complete."
  echo

  # Store the installers where a reset can find them, and stop there. The root
  # is rearranged only when the user presses Launch PINE.
  seed_backend_installers
}

ensure_venv

cd "$BACKEND_DIR"

PORT_FILE="$BACKEND_DIR/data/supervisor.port"
rm -f "$PORT_FILE"

echo "  Starting PINE..."
nohup "$VENV_DIR/bin/python3" supervisor.py >>"$LAUNCHER_LOG" 2>&1 &

# The supervisor shuts itself down once its startup grace passes with no browser
# lease, so this window has to open the browser -- the job Setup_WIN.bat does
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
