#!/usr/bin/env python3
"""Install a user-level Demo approval loop; starting it is a separate action."""
from pathlib import Path
import argparse
import subprocess


def unit_text(root: Path, python: Path) -> str:
    # systemd has its own quoting/specifier rules, not shell quoting.
    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
    return f'''[Unit]
Description=Jarvis Binance Demo Telegram approval loop
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={str(root).replace('%', '%%')}
Environment={quote('PYTHONPATH=' + str(root))}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart={quote(python)} -m jarvis_core.trading.cli telegram-loop --scan-seconds 60
Restart=always
RestartSec=15
TimeoutStopSec=300
KillSignal=SIGINT
UMask=0077

[Install]
WantedBy=default.target
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write a preview only; do not install or reload systemd.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    python = root.parent / '.jarvis-core-venv/bin/python'
    if not args.output and not python.is_file():
        raise SystemExit('Install Jarvis Core beside this repository first.')
    target = args.output or Path.home() / '.config/systemd/user/jarvis-trading.service'
    target.parent.mkdir(parents=True, exist_ok=True)
    text = unit_text(root, python)
    # Preserve an existing customized unit for review rather than overwrite it.
    if target.exists() and target.read_text() != text:
        raise SystemExit(f'Existing unit differs; inspect {target} before replacing it.')
    target.write_text(text)
    if not args.output:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    print(f'Wrote {target}. No process was started; no limits were changed.')
    if not args.output:
        print('After reviewing: systemctl --user enable --now jarvis-trading.service')
        print('Diagnostics: jarvis-trader telegram-health; jarvis-trader telegram-scan')


if __name__ == '__main__':
    main()
