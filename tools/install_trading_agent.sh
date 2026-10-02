#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="$(dirname "$ROOT")"
PY="$HOME_DIR/.jarvis-core-venv/bin/python"

if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/jarvis-trader" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT"
exec "$PY" -m jarvis_core.trading.cli "\$@"
EOF
chmod +x "$HOME/.local/bin/jarvis-trader"

python3 "$ROOT/tools/update_agent_personality.py"   "$ROOT/AGENTS.md" "$HOME_DIR/AGENTS.md"

echo "[codex-jarvis] deterministic PAPER trading engine installed."
echo "[codex-jarvis] Initialize explicitly: jarvis-trader init --capital AMOUNT"
echo "[codex-jarvis] Then review ~/.config/codex-jarvis/trading.json before enable-paper."
