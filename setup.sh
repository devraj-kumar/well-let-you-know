#!/bin/bash
# One-time setup: builds the Swift capture helper and installs Python deps.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== interview-coach setup =="

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "error: requires Apple Silicon (mlx-whisper needs it)" >&2; exit 1
fi

MACOS_MAJOR=$(sw_vers -productVersion | cut -d. -f1)
MACOS_MINOR=$(sw_vers -productVersion | cut -d. -f2)
if (( MACOS_MAJOR < 14 )) || { (( MACOS_MAJOR == 14 )) && (( MACOS_MINOR < 4 )); }; then
  echo "error: requires macOS 14.4+ (Core Audio process taps)" >&2; exit 1
fi

if ! xcode-select -p >/dev/null 2>&1; then
  echo "error: Xcode Command Line Tools missing — run: xcode-select --install" >&2; exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv missing — install with: brew install uv" >&2; exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "error: ffmpeg missing (needed by mlx-whisper) — install with: brew install ffmpeg" >&2; exit 1
fi

echo "-- building capture helper (swift build) ..."
swift build -c release --package-path "$DIR/capture"

echo "-- syncing Python environment (uv) ..."
uv sync --project "$DIR"

chmod +x "$DIR/coach"
echo
echo "Done. Record your next interview with:"
echo "  ./coach record"
echo
echo "First run will prompt for Microphone and System Audio Recording"
echo "permissions for your terminal — grant both."
