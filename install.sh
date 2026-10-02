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

# Jarvis core uses the desktop Secret Service for credentials, a separate
# virtualenv for Google OAuth/API libraries, and desktop notifications.
if [ "$(uname -s)" = "Linux" ] && command -v apt-get >/dev/null 2>&1; then
  core_pkgs=()
  command -v secret-tool >/dev/null 2>&1 || core_pkgs+=(libsecret-tools)
  command -v notify-send >/dev/null 2>&1 || core_pkgs+=(libnotify-bin)
  if command -v dpkg >/dev/null 2>&1 && ! dpkg -s python3-venv >/dev/null 2>&1; then
    core_pkgs+=(python3-venv)
  fi
  python3 - <<'PY' >/dev/null 2>&1 || core_pkgs+=(python3-tk)
import tkinter
PY
  if [ "${#core_pkgs[@]}" -gt 0 ]; then
    say "Installing secure personal-agent prerequisites: ${core_pkgs[*]}"
    sudo apt-get update
    sudo apt-get install -y "${core_pkgs[@]}"
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

say "Installing Codex adapters into Backtalk..."
cp "$ROOT/adapters/backtalk/brain.py" "$HOME_DIR/backtalk/backtalk/brain.py"
cp "$ROOT/adapters/backtalk/ptt.py" "$HOME_DIR/backtalk/backtalk/ptt.py"
cp "$ROOT/adapters/backtalk/mouth.py" "$HOME_DIR/backtalk/backtalk/mouth.py"
python3 "$ROOT/tools/patch_backtalk.py" "$HOME_DIR/backtalk"
python3 "$ROOT/tools/patch_visualizer.py" "$HOME_DIR/ai-visualizer"

# Claude Agent SDK is no longer used. Add OpenAI's published Codex Python
# SDK, which keeps one app-server process alive and streams reply deltas.
python3 - "$HOME_DIR/backtalk/pyproject.toml" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
lines = [
    line for line in p.read_text().splitlines()
    if "claude-agent-sdk" not in line
]
if not any("openai-codex" in line for line in lines):
    for i, line in enumerate(lines):
        if line.strip() == "dependencies = [":
            lines.insert(i + 1, '    "openai-codex>=0.153.4",')
            break
if not any("kokoro-onnx" in line for line in lines):
    for i, line in enumerate(lines):
        if line.strip() == "dependencies = [":
            lines.insert(i + 1, '    "kokoro-onnx>=0.6.1",')
            break
if not any("piper-tts" in line for line in lines):
    for i, line in enumerate(lines):
        if line.strip() == "dependencies = [":
            lines.insert(i + 1, '    "piper-tts",')
            break
p.write_text("\n".join(lines) + "\n")
PY

if [ ! -e "$HOME_DIR/AGENTS.md" ]; then
  say "Creating Codex agent instructions."
  cp "$ROOT/AGENTS.md" "$HOME_DIR/AGENTS.md"
else
  say "Existing AGENTS.md found; preserving custom instructions."
fi
python3 "$ROOT/tools/update_agent_personality.py" "$ROOT/AGENTS.md" "$HOME_DIR/AGENTS.md"

python3 "$ROOT/tools/setup_memory.py" "$HOME_DIR"
python3 "$ROOT/setup.py"



say "Preparing low-latency Piper voice..."
python3 - "$HOME_DIR/backtalk/models" <<'PY'
from pathlib import Path
from urllib.request import urlretrieve
import sys

dest = Path(sys.argv[1])
dest.mkdir(parents=True, exist_ok=True)

assets = {
    "en_GB-alan-medium.onnx":
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx?download=true",
    "en_GB-alan-medium.onnx.json":
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json?download=true",
}

for name, url in assets.items():
    path = dest / name
    if path.exists() and path.stat().st_size > 1024:
        print(f"[codex-jarvis] {name} already present.")
        continue
    tmp = path.with_suffix(path.suffix + ".part")
    print(f"[codex-jarvis] downloading {name} (one-time)...")
    urlretrieve(url, tmp)
    tmp.replace(path)
PY

say "Preparing low-latency Kokoro ONNX fp32 model..."
python3 - "$HOME_DIR/backtalk/models" <<'PY'
from pathlib import Path
from urllib.request import urlretrieve
import sys

dest = Path(sys.argv[1])
dest.mkdir(parents=True, exist_ok=True)

assets = {
    "kokoro-v1.0.v1.1.onnx":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx",
    "voices-v1.0.v1.1.bin":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin",
}

for name, url in assets.items():
    path = dest / name
    if path.exists() and path.stat().st_size > 1024:
        print(f"[codex-jarvis] {name} already present.")
        continue
    tmp = path.with_suffix(path.suffix + ".part")
    print(f"[codex-jarvis] downloading {name} (one-time)...")
    urlretrieve(url, tmp)
    tmp.replace(path)
PY

say "Running Backtalk's dependency installer."
(
  cd "$HOME_DIR/backtalk"
  chmod +x install.sh run.sh
  ./install.sh
)

say "Installing Jarvis core services in an isolated virtual environment..."
CORE_VENV="$HOME_DIR/.jarvis-core-venv"
if [ ! -x "$CORE_VENV/bin/python" ]; then
  python3 -m venv "$CORE_VENV"
fi
"$CORE_VENV/bin/python" -m pip install --quiet --upgrade pip
"$CORE_VENV/bin/python" -m pip install --quiet \
  "google-auth>=2.40" \
  "google-auth-oauthlib>=1.2" \
  "google-api-python-client>=2.170" \
  "playwright>=1.50" \
  "Pillow>=10"

mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/jarvis" <<EOF
#!/usr/bin/env bash
cd "$ROOT"
exec bash start.sh "\$@"
EOF
chmod +x "$HOME/.local/bin/jarvis"

cat > "$HOME/.local/bin/jarvis-core" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT"
exec "$HOME_DIR/.jarvis-core-venv/bin/python" -m jarvis_core.cli "\$@"
EOF
chmod +x "$HOME/.local/bin/jarvis-core"

cat > "$HOME/.local/bin/jarvis-freelance" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT"
exec "$HOME_DIR/.jarvis-core-venv/bin/python" -m jarvis_core.freelance "\$@"
EOF
chmod +x "$HOME/.local/bin/jarvis-freelance"

cat > "$HOME/.local/bin/jarvis-binance" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT"
exec "$HOME_DIR/.jarvis-core-venv/bin/python" -m jarvis_core.binance_expert "\$@"
EOF
chmod +x "$HOME/.local/bin/jarvis-binance"

cat > "$HOME/.local/bin/jarvis-trader" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT"
exec "$HOME_DIR/.jarvis-core-venv/bin/python" -m jarvis_core.trading.cli "\$@"
EOF
chmod +x "$HOME/.local/bin/jarvis-trader"

if [ "$(uname -s)" = "Linux" ]; then
  desktop_dir="$HOME/Desktop"
  mkdir -p "$desktop_dir"
  cat > "$desktop_dir/Jarvis.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Jarvis
Comment=Start Codex Jarvis
Exec=bash -lc 'cd "$ROOT" && exec bash start.sh'
Terminal=true
Categories=Utility;
EOF
  chmod +x "$desktop_dir/Jarvis.desktop"
  if command -v gio >/dev/null 2>&1; then
    gio set "$desktop_dir/Jarvis.desktop" metadata::trusted true >/dev/null 2>&1 || true
  fi

  if command -v systemctl >/dev/null 2>&1; then
    unit_dir="$HOME/.config/systemd/user"
    mkdir -p "$unit_dir"
    cat > "$unit_dir/jarvis-browser.service" <<EOF
[Unit]
Description=Jarvis persistent browser
After=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/jarvis-core browser-daemon
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF

    cat > "$unit_dir/jarvis-briefing.service" <<EOF
[Unit]
Description=Jarvis daily Gmail and Calendar briefing
After=network-online.target

[Service]
Type=oneshot
ExecStart=%h/.local/bin/jarvis-core briefing --notify
EOF

    cat > "$unit_dir/jarvis-briefing.timer" <<EOF
[Unit]
Description=Run Jarvis daily briefing each morning

[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true
Unit=jarvis-briefing.service

[Install]
WantedBy=timers.target
EOF
    cat > "$unit_dir/jarvis-freelance-scan.service" <<EOF
[Unit]
Description=Jarvis daily freelance opportunity scan
After=network-online.target

[Service]
Type=oneshot
ExecStart=%h/.local/bin/jarvis-freelance daily --days 3 --max-jobs 5 --notify
EOF

    cat > "$unit_dir/jarvis-freelance-scan.timer" <<EOF
[Unit]
Description=Run Jarvis freelance opportunity scan each morning

[Timer]
OnCalendar=*-*-* 08:30:00
Persistent=true
Unit=jarvis-freelance-scan.service

[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload || true
    systemctl --user enable jarvis-browser.service >/dev/null 2>&1 || true
    say "Jarvis browser service installed. Start with: jarvis-core browser-start"
    say "Daily briefing timer installed at 08:00; it will enable after Google OAuth."
    say "Daily freelance agent timer installed at 08:30; it will enable after Google OAuth."
  fi
fi

say "Personal MVP installation finished."
say "Memory vault: $HOME_DIR/Memory"
say "Start with: jarvis"
say "Core status: jarvis-core status"
say "Browser: jarvis-core browser-start"
say "Freelance agent: jarvis-freelance status"
say "Binance expert: jarvis-binance analyze BTCUSDT --market futures --interval 15m"
say "Trading engine: jarvis-trader status"
say "Website password vault: jarvis-core credential-set example.com --username USER"
say "Google setup: jarvis-core google-auth --client-json /path/to/client_secret.json"
say "Or double-click: $HOME/Desktop/Jarvis.desktop"
