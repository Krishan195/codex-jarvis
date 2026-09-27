#!/usr/bin/env python3
"""Import private user profile files into the local Jarvis Memory vault.

The profile contents are intentionally supplied at runtime and are never stored
in the public codex-jarvis repository.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import os

PROFILE_START = "<!-- JARVIS-PRIVATE-PROFILE-START -->"
PROFILE_END = "<!-- JARVIS-PRIVATE-PROFILE-END -->"
FREELANCE_START = "<!-- JARVIS-PRIVATE-FREELANCE-START -->"
FREELANCE_END = "<!-- JARVIS-PRIVATE-FREELANCE-END -->"


def managed_text(start: str, end: str, body: str) -> str:
    body = body.strip()
    return f"{start}\n{body}\n{end}\n"


def merge_managed(path: Path, start: str, end: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    block = managed_text(start, end, body)

    if path.exists():
        current = path.read_text(encoding="utf-8")
        if start in current and end in current:
            before = current[:current.index(start)]
            after = current[current.index(end) + len(end):]
            updated = before.rstrip() + "\n\n" + block + after.lstrip("\n")
        else:
            updated = current.rstrip() + "\n\n" + block
    else:
        updated = block

    path.write_text(updated.rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True)
    p.add_argument("--freelance", required=True)
    p.add_argument("--home", default=str(Path.home() / "my-agent"))
    args = p.parse_args()

    home = Path(args.home).expanduser().resolve()
    vault = home / "Memory"

    profile = Path(args.profile).expanduser().read_text(encoding="utf-8")
    freelance = Path(args.freelance).expanduser().read_text(encoding="utf-8")

    merge_managed(
        vault / "User Profile.md",
        PROFILE_START,
        PROFILE_END,
        profile,
    )
    merge_managed(
        vault / "02 - Projects" / "Freelance Profile.md",
        FREELANCE_START,
        FREELANCE_END,
        freelance,
    )

    print(f"[codex-jarvis] imported private profile into {vault}")
    print("[codex-jarvis] source files remain outside the public repository")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
