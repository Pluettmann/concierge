#!/usr/bin/env bash
# Starts Concierge on Linux (the first start sets up the Python environment).
set -e
cd "$(dirname "$(readlink -f "$0")")"
VENV=.venv-linux

notify() {
    command -v kdialog >/dev/null && kdialog --error "$1" || echo "$1" >&2
}

if [ ! -x "$VENV/bin/python" ]; then
    echo "First start: setting up the Python environment ..."
    if ! python3 -m venv "$VENV"; then
        notify "Python venv is missing. Please run in a terminal: sudo apt install python3-venv"
        exit 1
    fi
fi

if ! cmp -s requirements.txt "$VENV/requirements.installed"; then
    echo "Installing dependencies ..."
    "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
    "$VENV/bin/python" -m pip install -r requirements.txt
    cp requirements.txt "$VENV/requirements.installed"
fi

nohup "$VENV/bin/python" -m app.main >/dev/null 2>&1 &
disown
