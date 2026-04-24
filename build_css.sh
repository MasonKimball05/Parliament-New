#!/bin/bash
# Rebuild static/css/tailwind.css from templates.
# Run this whenever you add new Tailwind classes to any template or JS file.
#
# Requires the Tailwind standalone CLI (no npm needed):
#   macOS ARM:  curl -sL https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-macos-arm64 -o tailwindcss && chmod +x tailwindcss
#   macOS x64:  curl -sL https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-macos-x64 -o tailwindcss && chmod +x tailwindcss
#   Linux x64:  curl -sL https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-linux-x64 -o tailwindcss && chmod +x tailwindcss

set -e

TAILWIND_BIN="${1:-./tailwindcss}"

if [ ! -f "$TAILWIND_BIN" ]; then
    echo "Error: Tailwind CLI binary not found at '$TAILWIND_BIN'"
    echo "Download it (no npm required):"
    echo "  curl -sL https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-macos-arm64 -o tailwindcss && chmod +x tailwindcss"
    exit 1
fi

echo "Building Tailwind CSS..."
"$TAILWIND_BIN" -i static/css/tailwind-input.css -o static/css/tailwind.css --minify
echo "Done: static/css/tailwind.css"

# Update the asset integrity manifest so check_env --update-hashes reflects
# the new tailwind.css hash.  Requires the venv to be active or the python
# binary to be on PATH.
PYTHON_BIN="${2:-$(command -v python3 || command -v python)}"
if [ -f ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
fi
if [ -n "$PYTHON_BIN" ] && [ -f "$PYTHON_BIN" ]; then
    echo "Updating asset integrity manifest..."
    "$PYTHON_BIN" manage.py check_env --update-hashes
else
    echo "Warning: Python not found — run 'python manage.py check_env --update-hashes' manually to update integrity hashes."
fi
