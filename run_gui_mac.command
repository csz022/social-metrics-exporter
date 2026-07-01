#!/bin/zsh
set -e

cd "$(dirname "$0")"

ensure_uv() {
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  if command -v uv >/dev/null 2>&1; then
    return
  fi

  echo "uv is required to run this app."
  echo "It can be installed automatically from https://astral.sh/uv/."
  echo
  read "?Install uv now? [Y/n] " answer
  case "$answer" in
    n|N|no|NO|No)
      echo "Install cancelled."
      read "?Press Enter to close..."
      exit 1
      ;;
  esac

  echo "[setup] Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

  if ! command -v uv >/dev/null 2>&1; then
    echo "uv was installed, but it is still not available on PATH."
    echo "Close this Terminal window, open the app again, and try once more."
    read "?Press Enter to close..."
    exit 1
  fi
}

ensure_uv

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
