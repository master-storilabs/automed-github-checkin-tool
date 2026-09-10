#!/usr/bin/env bash
# Bootstrap the Automated GitHub Check-in Tool on macOS / Linux.
# Clones (or updates) the repo under your home folder and runs the installer.
#
#   bash bootstrap.sh

set -euo pipefail

repo="https://github.com/master-storilabs/automed-github-checkin-tool.git"
dir="$HOME/automed-github-checkin-tool"

if [ -d "$dir/.git" ]; then
    echo "Updating existing checkout at $dir"
    git -C "$dir" pull --ff-only
else
    git clone "$repo" "$dir"
fi

cd "$dir"
python3 install.py
