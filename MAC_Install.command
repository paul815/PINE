#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"
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

  # Move dev/GitHub files out of root for end-users
  mkdir -p "$ROOT_DIR/Documentation/dev-config"
  for f in AGENTS.md LICENSE .editorconfig .gitattributes .gitignore .pre-commit-config.yaml .python-version; do
    [[ -f "$ROOT_DIR/$f" ]] && mv "$ROOT_DIR/$f" "$ROOT_DIR/Documentation/dev-config/$f" 2>/dev/null || true
  done

  echo
  echo "  First setup step complete."
  echo
}

ensure_venv

cd "$BACKEND_DIR"
"$VENV_DIR/bin/python3" supervisor.py
