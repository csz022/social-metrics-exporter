#!/bin/zsh
set -e

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed or not on PATH."
  echo "Install uv first:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo
  read "?Press Enter to close..."
  exit 1
fi

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp ".env.example" ".env"
fi

echo "Syncing Python dependencies..."
uv sync

echo "Installing or checking Playwright Chromium..."
uv run python -m playwright install chromium

PORT="${GUI_PORT:-5001}"
echo "Starting Social Metrics at http://127.0.0.1:${PORT}"
uv run python gui_app.py --port "${PORT}" --open-browser

echo
read "?Server stopped. Press Enter to close..."
