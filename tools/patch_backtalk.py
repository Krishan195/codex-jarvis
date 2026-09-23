#!/usr/bin/env python3
"""Patch the upstream Backtalk checkout for the Codex adapter.

Backtalk's main loop constructs a Claude-SDK-specific spoken permission
callback. The Codex alpha does not use that callback: Codex itself owns
sandbox/approval handling. Replace only the constructor call, leaving the
upstream function intact so the patch stays small and easy to audit.
"""

from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: patch_backtalk.py /path/to/backtalk")

root = Path(sys.argv[1]).resolve()
main = root / "backtalk" / "main.py"

text = main.read_text(encoding="utf-8")
old = "can_use_tool=make_permission_gate(mouth),"
new = "can_use_tool=None,  # Codex owns approvals/sandboxing"

if new in text:
    print("[codex-jarvis] Backtalk Codex permission patch already applied.")
elif old in text:
    main.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("[codex-jarvis] Patched Backtalk to use Codex-native approvals.")
else:
    raise SystemExit(
        "Backtalk changed upstream: could not find the permission-gate "
        "constructor call. Refusing to guess."
    )
