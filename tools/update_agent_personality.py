#!/usr/bin/env python3
"""Sync Codex Jarvis managed instruction blocks into agent-home AGENTS.md.

Only marked blocks are managed. Everything else in the user's AGENTS.md is
preserved.
"""
from __future__ import annotations

import sys
from pathlib import Path

BLOCKS = [
    (
        "<!-- CODEX-JARVIS-PERSONALITY-START -->",
        "<!-- CODEX-JARVIS-PERSONALITY-END -->",
        "Jarvis personality",
    ),
    (
        "<!-- CODEX-JARVIS-CORE-START -->",
        "<!-- CODEX-JARVIS-CORE-END -->",
        "Jarvis core services",
    ),
    (
        "<!-- CODEX-JARVIS-FREELANCE-START -->",
        "<!-- CODEX-JARVIS-FREELANCE-END -->",
        "Jarvis freelance agent",
    ),
    (
        "<!-- CODEX-JARVIS-BINANCE-START -->",
        "<!-- CODEX-JARVIS-BINANCE-END -->",
        "Jarvis Binance specialist",
    ),
]

if len(sys.argv) != 3:
    raise SystemExit("usage: update_agent_personality.py TEMPLATE_AGENTS HOME_AGENTS")

template = Path(sys.argv[1])
target = Path(sys.argv[2])
src = template.read_text(encoding="utf-8")

for start, end, _ in BLOCKS:
    if start not in src or end not in src:
        raise SystemExit(f"managed block markers missing: {start}")

if not target.exists():
    target.write_text(src, encoding="utf-8")
    print(f"[codex-jarvis] created {target}")
    raise SystemExit(0)

dst = target.read_text(encoding="utf-8")
new = dst

for start, end, title in BLOCKS:
    managed = src[src.index(start): src.index(end) + len(end)]
    if start in new and end in new:
        before = new[:new.index(start)]
        after = new[new.index(end) + len(end):]
        new = before + managed + after
    else:
        sep = "" if new.endswith("\n\n") else ("\n" if new.endswith("\n") else "\n\n")
        new = new + sep + managed + "\n"
        print(f"[codex-jarvis] added managed {title} block")

if new != dst:
    target.write_text(new, encoding="utf-8")
    print("[codex-jarvis] updated managed Jarvis instructions in AGENTS.md")
else:
    print("[codex-jarvis] managed Jarvis instructions already current")
