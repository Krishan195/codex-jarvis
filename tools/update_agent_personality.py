#!/usr/bin/env python3
"""Sync the managed Jarvis personality block into the agent-home AGENTS.md.

Only the marked personality section is managed. Everything else in the user's
AGENTS.md is preserved.
"""

from __future__ import annotations

import sys
from pathlib import Path

START = "<!-- CODEX-JARVIS-PERSONALITY-START -->"
END = "<!-- CODEX-JARVIS-PERSONALITY-END -->"

if len(sys.argv) != 3:
    raise SystemExit("usage: update_agent_personality.py TEMPLATE_AGENTS HOME_AGENTS")

template = Path(sys.argv[1])
target = Path(sys.argv[2])

src = template.read_text(encoding="utf-8")
if START not in src or END not in src:
    raise SystemExit("managed personality markers missing from template")

managed = src[src.index(START): src.index(END) + len(END)]

if not target.exists():
    target.write_text(src, encoding="utf-8")
    print(f"[codex-jarvis] created {target}")
    raise SystemExit(0)

dst = target.read_text(encoding="utf-8")
if START in dst and END in dst:
    before = dst[:dst.index(START)]
    after = dst[dst.index(END) + len(END):]
    new = before + managed + after
else:
    sep = "" if dst.endswith("\n\n") else ("\n" if dst.endswith("\n") else "\n\n")
    new = dst + sep + "## Jarvis personality\n\n" + managed + "\n"

if new != dst:
    target.write_text(new, encoding="utf-8")
    print("[codex-jarvis] updated managed Jarvis personality in AGENTS.md")
else:
    print("[codex-jarvis] Jarvis personality already current")
