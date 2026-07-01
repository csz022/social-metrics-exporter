from __future__ import annotations

import plistlib
import shutil
import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "Social Metrics Exporter.app"
DIST_DIR = ROOT / "dist"
APP_DIR = DIST_DIR / APP_NAME
CONTENTS_DIR = APP_DIR / "Contents"
MACOS_DIR = CONTENTS_DIR / "MacOS"
RESOURCES_DIR = CONTENTS_DIR / "Resources"
BUNDLED_APP_DIR = RESOURCES_DIR / "app"


def main() -> int:
    if APP_DIR.exists():
        shutil.rmtree(APP_DIR)

    MACOS_DIR.mkdir(parents=True)
    BUNDLED_APP_DIR.mkdir(parents=True)
    (BUNDLED_APP_DIR / "input").mkdir()

    copy_file(ROOT / "gui_app.py", BUNDLED_APP_DIR / "gui_app.py")
    copy_file(ROOT / "pyproject.toml", BUNDLED_APP_DIR / "pyproject.toml")
    copy_file(ROOT / "requirements.txt", BUNDLED_APP_DIR / "requirements.txt")
    copy_file(ROOT / "uv.lock", BUNDLED_APP_DIR / "uv.lock")
    copy_file(ROOT / ".env.example", BUNDLED_APP_DIR / ".env.example")
    copy_file(ROOT / "input" / "urls.example.txt", BUNDLED_APP_DIR / "input" / "urls.example.txt")
    copy_tree(ROOT / "src", BUNDLED_APP_DIR / "src")
    copy_file(ROOT / "assets" / "AppIcon.icns", RESOURCES_DIR / "AppIcon.icns")

    write_info_plist()
    write_launcher()
    write_launch_script()

    print(f"Built {APP_DIR}")
    return 0


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )


def write_info_plist() -> None:
    data = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": "Social Metrics Exporter",
        "CFBundleExecutable": "launcher",
        "CFBundleIconFile": "AppIcon",
        "CFBundleIdentifier": "local.social-metrics-exporter",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Social Metrics Exporter",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "12.0",
    }
    with (CONTENTS_DIR / "Info.plist").open("wb") as plist_file:
        plistlib.dump(data, plist_file)


def write_launcher() -> None:
    launcher = MACOS_DIR / "launcher"
    launcher.write_text(
        """#!/bin/zsh
set -e

RESOURCES_DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
RUNNER="$RESOURCES_DIR/launch_gui.command"

if [ ! -f "$RUNNER" ]; then
  osascript -e 'display dialog "The bundled launcher was not found inside this app." buttons {"OK"} default button "OK" with icon stop'
  exit 1
fi

chmod +x "$RUNNER"
open -a Terminal "$RUNNER"
""",
        encoding="utf-8",
    )
    make_executable(launcher)


def write_launch_script() -> None:
    script = RESOURCES_DIR / "launch_gui.command"
    script.write_text(
        """#!/bin/zsh
set -e

BUNDLED_APP_DIR="$(cd "$(dirname "$0")/app" && pwd)"
DATA_ROOT="$HOME/Library/Application Support/Social Metrics Exporter"
APP_DIR="$DATA_ROOT/app"

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

echo "[1/5] Checking uv package manager..."
ensure_uv

echo "[2/5] Preparing writable app workspace..."
mkdir -p "$DATA_ROOT"
rm -rf "$APP_DIR"
ditto "$BUNDLED_APP_DIR" "$APP_DIR"
cd "$APP_DIR"

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp ".env.example" ".env"
fi

echo "[3/5] Installing Python packages..."
uv sync

echo "[4/5] Installing browser runtime if needed..."
uv run python -m playwright install chromium

PORT="${GUI_PORT:-5001}"
echo "[5/5] Starting local dashboard..."
echo "Starting Social Metrics at http://127.0.0.1:${PORT}"
echo "Opening browser. Keep this Terminal window open while using the app."
uv run python gui_app.py --port "${PORT}" --open-browser

echo
read "?Server stopped. Press Enter to close..."
""",
        encoding="utf-8",
    )
    make_executable(script)


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


if __name__ == "__main__":
    raise SystemExit(main())
