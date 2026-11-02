#!/usr/bin/env bash
# Starts Git Project Manager (Linux / macOS).
# First time only:  chmod +x start.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$SCRIPT_DIR/git_project_manager.py"

if [ ! -f "$APP" ]; then
    echo "Could not find git_project_manager.py next to start.sh ($SCRIPT_DIR)." >&2
    exit 1
fi

# Use the first Python 3 that has Tkinter (the GUI library) available.
for py in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys, tkinter; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
        cd "$SCRIPT_DIR" || exit 1
        exec "$py" "$APP" "$@"
    fi
done

echo "Could not find Python 3.8+ with Tkinter." >&2
if [ "$(uname)" = "Darwin" ]; then
    echo "Install it with:  brew install python-tk   (or the installer from python.org)" >&2
else
    echo "Install it with:  sudo apt install python3-tk   (Debian/Ubuntu)" >&2
    echo "             or:  sudo dnf install python3-tkinter   (Fedora)" >&2
fi
exit 1
