#!/usr/bin/env bash
# Install manual review commands and voice instructions; no order/service start.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_HOME="$(dirname "$ROOT")"
bash "$ROOT/tools/install_trading_agent.sh"
VOICE_BRAIN="$AGENT_HOME/backtalk/backtalk/brain.py"
if [ -f "$VOICE_BRAIN" ]; then
  if ! cmp -s "$ROOT/adapters/backtalk/brain.py" "$VOICE_BRAIN"; then
    cp "$VOICE_BRAIN" "${VOICE_BRAIN}.before-manual-trading.$(date +%Y%m%d%H%M%S)"
    cp "$ROOT/adapters/backtalk/brain.py" "$VOICE_BRAIN"
  fi
  echo "Voice adapter installed. Restart Jarvis voice to load the new instructions."
else
  echo "Backtalk not found here; CLI commands and agent-home instructions installed."
fi
echo "Restart an existing Telegram loop to load the new code (one loop only)."
echo "Manual proposals remain opt-in. Configure explicit limits with manual-config."
echo "No limits changed, no service started, no trade submitted."
