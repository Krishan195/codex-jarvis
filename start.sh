#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="$(dirname "$ROOT")"

if [ ! -d "$HOME_DIR/backtalk" ]; then
  echo "Backtalk is not installed yet. Run ./install.sh first."
  exit 1
fi

# Visualizer is optional during early smoke tests.
if [ -x "$HOME_DIR/ai-visualizer/run.sh" ]; then
  (cd "$HOME_DIR/ai-visualizer" && ./run.sh >/tmp/codex-jarvis-visualizer.log 2>&1 &)
  sleep 1
fi

cd "$HOME_DIR/backtalk"
exec ./run.sh "$@"
