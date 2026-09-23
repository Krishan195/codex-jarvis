#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="$(dirname "$ROOT")"

say() { printf '[codex-jarvis] %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1"; exit 1; }; }

need git
need python3
need codex

# Backtalk's Linux voice stack builds the native webrtcvad extension.
# That build needs a C compiler plus Python development headers (Python.h).
# Install them up front so the Python dependency step does not fail halfway.
if [ "$(uname -s)" = "Linux" ]; then
  if command -v apt-get >/dev/null 2>&1; then
    missing_build_deps=0
    command -v gcc >/dev/null 2>&1 || missing_build_deps=1
    python3 - <<'PY' >/dev/null 2>&1 || missing_build_deps=1
import sysconfig
from pathlib import Path
inc = sysconfig.get_paths().get("include")
raise SystemExit(0 if inc and (Path(inc) / "Python.h").exists() else 1)
PY
    if [ "$missing_build_deps" -ne 0 ]; then
      say "Installing Linux build prerequisites for the local voice stack..."
      sudo apt-get update
      sudo apt-get install -y build-essential python3-dev
    fi
  elif command -v dnf >/dev/null 2>&1; then
    if ! command -v gcc >/dev/null 2>&1; then
      say "Installing Linux build prerequisites for the local voice stack..."
      sudo dnf install -y gcc python3-devel
    fi
  fi
fi

say "Agent home: $HOME_DIR"
say "Codex: $(codex --version 2>/dev/null || true)"

clone_if_missing() {
  local name="$1"
  local url="$2"
  local dest="$HOME_DIR/$name"
  if [ -d "$dest/.git" ]; then
    say "$name already exists; leaving it in place."
  elif [ -e "$dest" ]; then
    echo "$dest exists but is not a git checkout. Refusing to overwrite it."
    exit 1
  else
    say "Cloning $name..."
    git clone "$url" "$dest"
  fi
}

clone_if_missing backtalk https://github.com/jaredrhod/backtalk.git
clone_if_missing ai-visualizer https://github.com/jaredrhod/ai-visualizer.git
clone_if_missing ai-memory-vault https://github.com/jaredrhod/ai-memory-vault.git

say "Installing Codex brain adapter into Backtalk..."
cp "$ROOT/adapters/backtalk/brain.py" "$HOME_DIR/backtalk/backtalk/brain.py"
python3 "$ROOT/tools/patch_backtalk.py" "$HOME_DIR/backtalk"

# Claude Agent SDK is no longer used by the Codex brain. Keep every other
# upstream dependency unchanged.
python3 - "$HOME_DIR/backtalk/pyproject.toml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
s = "\n".join(
    line for line in s.splitlines()
    if "claude-agent-sdk" not in line
) + "\n"
p.write_text(s)
PY

if [ ! -e "$HOME_DIR/AGENTS.md" ]; then
  say "Creating Codex agent instructions."
  cp "$ROOT/AGENTS.md" "$HOME_DIR/AGENTS.md"
else
  say "Existing AGENTS.md found; leaving it untouched."
fi

python3 "$ROOT/setup.py"

say "Running Backtalk's dependency installer."
(
  cd "$HOME_DIR/backtalk"
  chmod +x install.sh run.sh
  ./install.sh
)

say "Phase-1 installation finished."
say "Run: cd $ROOT && bash start.sh"
