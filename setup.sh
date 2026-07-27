#!/bin/bash
# well-let-you-know guided setup. Safe to run as many times as you like.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

step() { printf "\n\033[1m==> %s\033[0m\n" "$*"; }
ok()   { printf "   \033[32m✓\033[0m %s\n" "$*"; }
note() { printf "   \033[33m!\033[0m %s\n" "$*"; }
die()  { printf "\n   \033[31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

ask() {  # ask "question" -> returns 0 for yes. Defaults to yes; skips when non-interactive.
  if [ ! -t 0 ]; then return 1; fi
  read -r -p "   $1 [Y/n] " reply
  [[ ! "$reply" =~ ^[Nn] ]]
}

echo "well-let-you-know setup — this checks your Mac and installs what's missing."

step "Checking your Mac"
[[ "$(uname -m)" == "arm64" ]] || die "This tool needs an Apple Silicon Mac (M1 or newer)."
MACOS_MAJOR=$(sw_vers -productVersion | cut -d. -f1)
MACOS_MINOR=$(sw_vers -productVersion | cut -d. -f2)
if (( MACOS_MAJOR < 14 )) || { (( MACOS_MAJOR == 14 )) && (( MACOS_MINOR < 4 )); }; then
  die "This tool needs macOS 14.4 or newer (you have $(sw_vers -productVersion)). Update via System Settings → General → Software Update."
fi
ok "Apple Silicon, macOS $(sw_vers -productVersion)"

step "Checking Apple's developer tools"
if ! xcode-select -p >/dev/null 2>&1; then
  xcode-select --install >/dev/null 2>&1 || true
  note "A popup asking to install 'command line developer tools' should have appeared."
  note "Click Install, wait for it to finish (a few minutes), then run ./setup.sh again."
  exit 1
fi
ok "Xcode Command Line Tools installed"

step "Checking Homebrew (the Mac package manager)"
if ! command -v brew >/dev/null 2>&1 && [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
if ! command -v brew >/dev/null 2>&1; then
  note "Homebrew is not installed. It is needed to install two small tools."
  if ask "Install Homebrew now? (it may ask for your Mac login password)"; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv)"
  else
    die "Install Homebrew from https://brew.sh and run ./setup.sh again."
  fi
fi
ok "Homebrew ready"

step "Installing tools (uv + ffmpeg)"
for pkg in uv ffmpeg; do
  if command -v "$pkg" >/dev/null 2>&1; then
    ok "$pkg already installed"
  else
    note "installing $pkg …"
    brew install "$pkg"
    ok "$pkg installed"
  fi
done

step "Building the audio recorder"
swift build -c release --package-path "$DIR/capture" >/dev/null
chmod +x "$DIR/wlyk"
ok "recorder built"

step "Setting up Python"
uv sync --project "$DIR" >/dev/null 2>&1 || uv sync --project "$DIR"
ok "Python environment ready"

step "Checking Claude access (writes your report)"
if command -v claude >/dev/null 2>&1; then
  ok "Claude Code found"
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  ok "ANTHROPIC_API_KEY found"
else
  note "Claude is not set up yet. Easiest fix — install Claude Code:"
  note "    curl -fsSL https://claude.ai/install.sh | bash"
  note "then run 'claude' once and log in. (Or set ANTHROPIC_API_KEY instead.)"
  note "You can finish this later; recording works without it."
fi

step "Transcription model (~3 GB, one-time download)"
if ask "Download it now so your first interview isn't slow?"; then
  uv run --project "$DIR" python -c "
from huggingface_hub import snapshot_download
snapshot_download('mlx-community/whisper-large-v3-mlx')
print('   model downloaded')"
else
  note "Skipped — it will download automatically after your first recording."
fi

step "Microphone & system-audio permissions"
echo "   macOS will ask for two permissions the first time you record:"
echo "     1. Microphone            2. System Audio Recording"
if ask "Run a 6-second test recording now to set them up? (play any song/video first!)"; then
  TESTDIR="$DIR/sessions/permission-test"
  rm -rf "$TESTDIR"
  if "$DIR/capture/.build/release/capturecli" "$TESTDIR" --duration 6; then
    VOL=$(ffmpeg -i "$TESTDIR/system.wav" -af volumedetect -f null - 2>&1 \
          | sed -n 's/.*mean_volume: \(-*[0-9.]*\) dB/\1/p')
    if [ -n "$VOL" ] && awk "BEGIN{exit !($VOL > -70)}"; then
      ok "system audio captured (level ${VOL} dB) — you're fully set up!"
      rm -rf "$TESTDIR"
    else
      note "system.wav sounds silent. If music WAS playing, grant the permission:"
      note "System Settings → Privacy & Security → Screen & System Audio Recording"
      note "→ turn ON your terminal app, then run ./setup.sh again."
      open "x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture" 2>/dev/null || true
    fi
  else
    note "The recorder couldn't get permission. Opening System Settings —"
    note "turn ON your terminal app under 'Screen & System Audio Recording',"
    note "then run ./setup.sh again."
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture" 2>/dev/null || true
  fi
else
  note "Skipped — you'll get the permission popups on your first ./wlyk record."
fi

printf "\n\033[1mAll set. Before your next interview run:\033[0m\n"
echo "  ./wlyk record"
echo "and press Ctrl-C when the interview ends. Your report opens by itself."
