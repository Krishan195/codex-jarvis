#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="$(dirname "$ROOT")"
PY="$HOME_DIR/.jarvis-core-venv/bin/python"

if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/jarvis-binance" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT"
exec "$PY" -m jarvis_core.binance_expert "\$@"
EOF
chmod +x "$HOME/.local/bin/jarvis-binance"

python3 "$ROOT/tools/update_agent_personality.py"   "$ROOT/AGENTS.md" "$HOME_DIR/AGENTS.md"
python3 "$ROOT/tools/setup_memory.py" "$HOME_DIR"

echo "[codex-jarvis] Binance expert installed."
echo "[codex-jarvis] Try: jarvis-binance multi BTCUSDT --market futures --intervals 15m,1h,4h"
