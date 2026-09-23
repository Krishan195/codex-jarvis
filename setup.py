#!/usr/bin/env python3
"""Write the minimal configs for the Linux alpha without overwriting user files."""

from __future__ import annotations

import json
from pathlib import Path

root = Path(__file__).resolve().parent
home = root.parent

backtalk = home / "backtalk"
visualizer = home / "ai-visualizer"

bt_cfg = backtalk / "backtalk.json"
if not bt_cfg.exists():
    data = {
        "agent_dir": str(home),
        "name": "Jarvis",
        "ptt_key": "home",
        "voice": "bm_lewis",
        "stt_model": "small.en",
        "mic_mode": "ptt",
        "permission_mode": "ask",
        "resume_last_session": True,
        "signals_dir": str(backtalk),
        "greeting": "Hello Krishan, what are we working on today?"
    }
    bt_cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"[codex-jarvis] created {bt_cfg}")
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
