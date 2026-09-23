#!/usr/bin/env python3
"""Patch upstream Backtalk for the Codex Jarvis adapter.

Patches are intentionally small and idempotent:
1. Disable the Claude-SDK permission callback; Codex owns approvals/sandboxing.
2. Use one-sentence TTS chunks after the first sentence as well. Upstream
   Backtalk batches later sentences in pairs for prosody, but that creates an
   artificial mid-reply pause in a streaming voice conversation.
"""

from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_backtalk.py /path/to/backtalk")

root = Path(sys.argv[1]).resolve()
main = root / "backtalk" / "main.py"
text = main.read_text(encoding="utf-8")
changed = False

# Claude-specific permission hook.
old = "can_use_tool=make_permission_gate(mouth),"
new = "can_use_tool=None,  # Codex owns approvals/sandboxing"
if old in text:
    text = text.replace(old, new, 1)
    changed = True

# Low-latency voice mode. The first sentence already ships immediately.
# Change the rest from 2-sentence batching to immediate sentence chunks.
old_batch = "if len(batch) >= 2:"
new_batch = "if len(batch) >= 1:  # Codex Jarvis low-latency voice mode"
if old_batch in text:
    text = text.replace(old_batch, new_batch, 1)
    changed = True

old_doc = '''    """First sentence ships alone (fast start); the rest go in
    2-sentence breaths — fuller chunks get livelier prosody (single
    short sentences come out flat)."""'''
new_doc = '''    """Ship each completed sentence to TTS immediately.

    This trades a little prosody smoothness for much lower conversational
    latency and removes the artificial pause between streamed paragraphs.
    """'''
if old_doc in text:
    text = text.replace(old_doc, new_doc, 1)
    changed = True

if changed:
    main.write_text(text, encoding="utf-8")
    print("[codex-jarvis] Patched Backtalk for Codex approvals + low-latency TTS.")
else:
    if new in text and new_batch in text:
        print("[codex-jarvis] Backtalk Codex patches already applied.")
    else:
        raise SystemExit(
            "Backtalk changed upstream: expected patch points were not found. "
            "Refusing to guess."
        )
