#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# PINE dev reset — DEV ONLY, not part of the shipped flow.
#
# Clears runtime data only: the SQLite DB, the onboarding flag and the projects
# folder. It deliberately does NOT touch backend/.venv, models/, logs/ or the
# installed file layout, so the app comes back at the onboarding screen in
# seconds instead of reinstalling the multi-gigabyte ML stack that
# model_manager.py pulls during onboarding.
#
# Use this for the day-to-day loop. For a release-grade reset that also
# exercises the installer and the file layout, run backend/reset.command with
# no flags — that is the one that has to pass before shipping.
#
# Usage: backend/tools/dev_reset.command [-y] [--projects-dir <path>]
#   -y, --yes           Skip the confirmation prompt.
#   --projects-dir      Projects folder to clear. Defaults to <repo>/projects;
#                       pass this if you moved it in Settings, because the path
#                       lives in the database this script is about to delete.
# ---------------------------------------------------------------------------

set -e
cd "$(dirname "$0")"

BACKEND_DIR="$(cd .. && pwd)"
ROOT_DIR="$(cd ../.. && pwd)"
ASSUME_YES=0
PROJECTS_DIR="$ROOT_DIR/projects"

while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes)
            ASSUME_YES=1
            ;;
        --projects-dir)
            if [ -z "${2:-}" ]; then
                echo ""
                echo "  --projects-dir needs a path."
                exit 2
            fi
            PROJECTS_DIR="$2"
            shift
            ;;
        *)
            echo ""
            echo "  Unknown option: $1"
            echo "  Usage: backend/tools/dev_reset.command [-y] [--projects-dir <path>]"
            exit 2
            ;;
    esac
    shift
done

echo ""
echo "  ============================================================"
echo "  DEV RESET: removes ALL projects, recordings and settings."
echo "  Keeps backend/.venv, models/, logs/ and the file layout."
echo "  ============================================================"
echo ""
echo "  Data:     $BACKEND_DIR/data"
echo "  Projects: $PROJECTS_DIR"
echo ""
if [ "$ASSUME_YES" != "1" ]; then
    read -r -p "  Type Yes and press Enter to proceed: " CONFIRM
    if [ "$CONFIRM" != "Yes" ]; then
        echo "  Dev reset cancelled."
        exit 0
    fi
    echo ""
fi

# Stop ONLY PINE's own processes: the recorded supervisor PID first, then any
# process whose command line runs from THIS install directory. Never kill by
# name alone — that would also take out unrelated Python.
echo "  Stopping PINE background processes..."
PIDFILE="$BACKEND_DIR/data/supervisor.pid"
if [ -f "$PIDFILE" ]; then
    SUP_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$SUP_PID" ]; then
        kill "$SUP_PID" 2>/dev/null || true
        sleep 2
        kill -9 "$SUP_PID" 2>/dev/null || true
    fi
fi
pkill -f "$BACKEND_DIR" 2>/dev/null || true
sleep 1

FAIL=0

if [ -d "$BACKEND_DIR/data" ]; then
    echo "  Removing data..."
    rm -rf "$BACKEND_DIR/data"
    if [ -d "$BACKEND_DIR/data" ]; then
        echo "  [ERROR] Could not remove data - the app may still be running."
        FAIL=1
    fi
fi

if [ -d "$PROJECTS_DIR" ]; then
    echo "  Removing projects..."
    rm -rf "$PROJECTS_DIR"
    if [ -d "$PROJECTS_DIR" ]; then
        echo "  [ERROR] Could not remove projects - files may still be in use."
        FAIL=1
    fi
fi

echo ""
if [ "$FAIL" = "1" ]; then
    echo "  Dev reset INCOMPLETE. Close the app and any open file, then try again."
    exit 1
fi
echo "  Dev reset complete. Launch PINE to start at onboarding."
echo "  Models, venv and installed layout untouched."
echo ""
