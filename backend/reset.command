#!/usr/bin/env bash
# PINE reset — clears project data but keeps downloaded models
# Run from repo root: backend/reset.command  OR  chmod +x backend/reset.command && ./backend/reset.command

set -e
cd "$(dirname "$0")"
shopt -s nullglob

ROOT_PRESERVE=(
  ".editorconfig"
  ".gitattributes"
  ".gitignore"
  ".git"
  ".pre-commit-config.yaml"
  ".python-version"
  "AGENTS.md"
  "Documentation"
  "LICENSE"
  "MAC_Install.command"
  "WIN_Install.bat"
  "backend"
  "models"
)

BACKEND_PRESERVE=(
  "app"
  "ml_worker"
  "design-audit.js"
  "package-lock.json"
  "package.json"
  "pytest.ini"
  "requirements-lock.txt"
  "requirements.txt"
  "reset.command"
  "reset_win.bat"
  "run.py"
  "scripts"
  "supervisor.py"
  "templates"
  "tests"
  "tools"
  "MAC_Install.command"
  "WIN_Install.bat"
)

should_keep() {
    local name="$1"
    shift
    local item
    for item in "$@"; do
        if [ "$name" = "$item" ]; then
            return 0
        fi
    done
    return 1
}

remove_unpreserved_children() {
    local base_dir="$1"
    shift
    local path
    local name
    for path in "$base_dir"/* "$base_dir"/.[!.]* "$base_dir"/..?*; do
        [ -e "$path" ] || continue
        name="$(basename "$path")"
        if should_keep "$name" "$@"; then
            continue
        fi
        echo "  Removing ${path#../}..."
        rm -rf "$path"
    done
}

echo ""
echo "  ============================================================"
echo "  WARNING: This will PERMANENTLY remove ALL project data."
echo "  All projects, recordings, transcripts, and annotations"
echo "  will be deleted. This cannot be undone."
echo "  ============================================================"
echo ""
echo "  Close the app before running."
echo ""
read -r -p "  Type Yes and press Enter to proceed: " CONFIRM
if [ "$CONFIRM" != "Yes" ]; then
    echo "  Reset cancelled."
    exit 0
fi
echo ""

rm -f ../backend/data/onboarding_complete.flag
rm -f ../"Launch Pine.bat" ../"Launch Pine.command" ../"Launch Pine.vbs" ../"Launch_WIN.bat" ../"Launch_MAC.command"

# Restore dev files moved during install
for f in AGENTS.md LICENSE .editorconfig .gitattributes .gitignore .pre-commit-config.yaml .python-version; do
  [[ -f "../Documentation/dev-config/$f" && ! -f "../$f" ]] && mv "../Documentation/dev-config/$f" "../$f" 2>/dev/null || true
done
rmdir ../Documentation/dev-config 2>/dev/null || true

if [ ! -f ../WIN_Install.bat ] && [ -f ./WIN_Install.bat ]; then
    cp ./WIN_Install.bat ../WIN_Install.bat
    chmod +x ../WIN_Install.bat 2>/dev/null || true
fi
if [ ! -f ../MAC_Install.command ] && [ -f ./MAC_Install.command ]; then
    cp ./MAC_Install.command ../MAC_Install.command
    chmod +x ../MAC_Install.command 2>/dev/null || true
fi
rm -f ./WIN_Install.bat ./MAC_Install.command

remove_unpreserved_children .. "${ROOT_PRESERVE[@]}"
remove_unpreserved_children . "${BACKEND_PRESERVE[@]}"

# Purge Python bytecode and test caches so reset returns a pristine source tree.
# cwd is backend/ (all Python lives here); models/, .git/ and .venv/ are left alone.
find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
find . -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.pyc.*' \) -delete 2>/dev/null || true

shopt -u nullglob

echo ""
echo "  Reset complete. Models folder preserved."
echo ""
