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
SELF_NAME="$(basename "$0")"
PYTHON_DOWNLOAD_URL="https://www.python.org/downloads/release/python-3130/"

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

html_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

# The first thing a new Mac user sees of PINE is a double-clicked .command
# window, and a stalled Terminal is a dead end in it -- no link to click, no
# page to read, and the text is gone the moment the window closes. When Python
# is what is missing, hand the instructions to the browser instead: the same
# handover the finished install makes with the app. The Terminal lines below
# stay as the fallback for when the page cannot be written or opened.
open_python_help_page() {
  local candidate found_version found_note page_dir page installer_path

  found_version=""
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      found_version="$("$candidate" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
      if [[ -n "$found_version" ]]; then
        break
      fi
    fi
  done

  found_note=""
  if [[ -n "$found_version" ]]; then
    found_note="<p class=\"found\">The Python already on this Mac is $(html_escape "$found_version"), which PINE cannot use.</p>"
  fi

  installer_path="$(html_escape "$SCRIPT_DIR/$SELF_NAME")"

  # mktemp keeps the page out of the repo; logs are the fallback because that
  # directory is the one place this script has already made sure it can write.
  page_dir="$(mktemp -d -t pine-setup 2>/dev/null || true)"
  if [[ ! -d "$page_dir" ]]; then
    page_dir="$LOG_DIR"
  fi
  page="$page_dir/install-python.html"

  cat >"$page" <<HTML || return 1
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PINE needs Python</title>
<style>
:root {
  --bg: hsl(220, 20%, 97%);
  --fg: hsl(220, 25%, 10%);
  --muted: hsl(220, 15%, 94%);
  --muted-fg: hsl(220, 10%, 46%);
  --border: hsl(220, 15%, 88%);
  --accent: hsl(152, 60%, 42%);
  --accent-on-dim: hsl(152, 60%, 27%);
  --accent-dim: hsla(152, 60%, 42%, 0.10);
  --accent-bd: hsla(152, 60%, 42%, 0.25);
  --radius: 6px;
  --radius-lg: 10px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: hsl(220, 20%, 7%);
    --fg: hsl(220, 15%, 93%);
    --muted: hsl(220, 14%, 14%);
    --muted-fg: hsl(220, 10%, 54%);
    --border: hsl(220, 16%, 17%);
    --accent: hsl(152, 55%, 45%);
    --accent-on-dim: hsl(152, 55%, 45%);
    --accent-dim: hsla(152, 55%, 45%, 0.12);
    --accent-bd: hsla(152, 55%, 45%, 0.28);
  }
}
html { font-size: 13px; }
body {
  margin: 0;
  padding: 3.5rem 1.5rem;
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
  font-size: 1rem;
  line-height: 1.65;
  -webkit-font-smoothing: antialiased;
}
main { max-width: 40rem; margin: 0 auto; }
.brand {
  margin: 0 0 0.75rem;
  font-size: 0.769rem;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--accent-on-dim);
}
h1 { margin: 0 0 0.5rem; font-size: 1.538rem; line-height: 1.3; font-weight: 600; }
.lead { margin: 0; color: var(--muted-fg); }
.found {
  margin: 1.25rem 0 0;
  padding: 0.65rem 0.9rem;
  border: 1px solid var(--accent-bd);
  border-radius: var(--radius);
  background: var(--accent-dim);
  color: var(--accent-on-dim);
  font-size: 0.923rem;
}
ol { counter-reset: step; list-style: none; margin: 2rem 0 0.5rem; padding: 0; }
li { counter-increment: step; position: relative; padding: 0 0 1.5rem 2.75rem; }
li::before {
  content: counter(step);
  position: absolute;
  left: 0;
  top: 0.1rem;
  width: 1.85rem;
  height: 1.85rem;
  border: 1px solid var(--accent-bd);
  border-radius: 999px;
  background: var(--accent-dim);
  color: var(--accent-on-dim);
  font-size: 0.923rem;
  font-weight: 600;
  line-height: 1.85rem;
  text-align: center;
}
h2 { margin: 0 0 0.15rem; font-size: 1rem; font-weight: 600; }
li p { margin: 0 0 0.5rem; color: var(--muted-fg); }
.btn {
  display: inline-block;
  margin-top: 0.35rem;
  padding: 0.5rem 1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--fg);
  text-decoration: none;
}
.btn:hover { border-color: var(--accent); color: var(--accent-on-dim); }
.mono {
  display: block;
  margin: 0.35rem 0 0;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--muted);
  color: var(--fg);
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 0.923rem;
  overflow-wrap: anywhere;
}
aside {
  padding: 1.1rem 1.25rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--muted);
}
aside p { margin: 0; color: var(--muted-fg); }
.foot {
  margin: 2rem 0 0;
  padding-top: 1.25rem;
  border-top: 1px solid var(--border);
  color: var(--muted-fg);
  font-size: 0.846rem;
}
code { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 0.923rem; }
</style>
</head>
<body>
<main>
  <p class="brand">PINE setup</p>
  <h1>Python has to be installed first</h1>
  <p class="lead">PINE runs on Python, and there is no version on this Mac that it can use. Installing one takes a couple of minutes, and it only has to be done once.</p>
  $found_note
  <ol>
    <li>
      <h2>Download Python 3.13</h2>
      <p>On the page that opens, scroll down to <strong>Files</strong> and pick <strong>macOS 64-bit universal2 installer</strong>.</p>
      <a class="btn" href="$PYTHON_DOWNLOAD_URL">Open the Python download page</a>
    </li>
    <li>
      <h2>Run the downloaded installer</h2>
      <p>Open the <code>.pkg</code> file from your Downloads folder and click through it. Nothing needs changing along the way.</p>
    </li>
    <li>
      <h2>Start PINE again</h2>
      <p>Double-click <strong>$SELF_NAME</strong> once more. This page and the Terminal window behind it can be closed.</p>
      <span class="mono">$installer_path</span>
    </li>
  </ol>
  <aside>
    <h2>Already have Homebrew?</h2>
    <p>Then one command replaces the first two steps:</p>
    <span class="mono">brew install python@3.13</span>
  </aside>
  <p class="foot">PINE needs Python 3.11, 3.12, or 3.13 &mdash; the speech models it runs on (WhisperX, ctranslate2, PyTorch) publish builds for those versions only. Newer releases of Python will work once those projects catch up.</p>
</main>
</body>
</html>
HTML

  open "$page" >/dev/null 2>&1 || return 1
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
    if open_python_help_page; then
      echo "  How to install it just opened in your browser."
    else
      echo "  Install one of those versions from https://www.python.org/downloads/"
      echo "  then run $SELF_NAME again."
    fi
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
