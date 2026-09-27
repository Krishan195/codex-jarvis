"""Approval-gated Ubuntu command execution.

Jarvis gets broad user-level control without disabling Codex sandboxing or
storing a sudo password. Every shell command is shown verbatim in an approval
request before execution.
"""
from __future__ import annotations

import subprocess
from typing import Any


def run(command: str, timeout: int = 120) -> dict[str, Any]:
    if not command.strip():
        raise ValueError("Command is empty.")
    p = subprocess.run(
        ["bash", "-lc", command],
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    return {
        "returncode": p.returncode,
        "stdout": p.stdout[-12000:],
        "stderr": p.stderr[-12000:],
    }
