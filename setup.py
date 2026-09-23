#!/usr/bin/env python3
"""Write/update the minimal Linux alpha configs without clobbering user choices."""

from __future__ import annotations

import json
from pathlib import Path

root = Path(__file__).resolve().parent
home = root.parent

backtalk = home / "backtalk"
visualizer = home / "ai-visualizer"

bt_cfg = backtalk / "backtalk.json"
if bt_cfg.exists():
    try:
        data = json.loads(bt_cfg.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Refusing to rewrite invalid {bt_cfg}: {exc}")
else:
    data = {}

defaults = {
    "agent_dir": str(home),
    "name": "Jarvis",
    "ptt_key": "home",
    "voice": "bm_lewis",
    "stt_model": "small.en",
    "mic_mode": "ptt",
    "permission_mode": "ask",
    "resume_last_session": True,
    "signals_dir": str(backtalk),
    "greeting": "Hello Krishan, what are we working on today?",
    # Voice is latency-sensitive: use the efficient model at low effort.
    # Change these any time if more reasoning depth is worth the delay.
    "codex_model": "gpt-5.6-luna",
    "codex_effort": "low",
}
changed = False
for key, value in defaults.items():
    if key not in data:
        data[key] = value
        changed = True

# The permanent default stays push-to-talk. --open-mic remains an explicit
# one-session override, never something setup silently enables.
if data.get("mic_mode") != "ptt":
    data["mic_mode"] = "ptt"
    changed = True

if changed or not bt_cfg.exists():
    bt_cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"[codex-jarvis] updated {bt_cfg}")
else:
    print(f"[codex-jarvis] keeping existing {bt_cfg}")

av_cfg = visualizer / "ai-visualizer.json"
if not av_cfg.exists():
    data = {
        "name": "JARVIS",
        "badge": "",
        "face": "board",
        "port": 8790,
        "bus_dir": str(backtalk),
        "thinking_sound": True
    }
    av_cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"[codex-jarvis] created {av_cfg}")
else:
    print(f"[codex-jarvis] keeping existing {av_cfg}")
