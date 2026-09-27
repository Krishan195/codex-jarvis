"""Secret storage backed by the OS credential service.

No secret value is written to the Jarvis repo, Memory vault, config JSON, or
logs. Linux uses Secret Service through secret-tool/libsecret.
"""
from __future__ import annotations

import getpass
import shutil
import subprocess

SERVICE = "codex-jarvis"


class SecretStoreError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("secret-tool") is not None


def _need_store() -> None:
    if not available():
        raise SecretStoreError(
            "secret-tool is unavailable. Install libsecret-tools first."
        )


def get(name: str) -> str | None:
    _need_store()
    p = subprocess.run(
        ["secret-tool", "lookup", "service", SERVICE, "key", name],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if p.returncode != 0:
        return None
    value = p.stdout.rstrip("\n")
    return value or None


def set(name: str, value: str) -> None:
    _need_store()
    if not value:
        raise SecretStoreError("Refusing to store an empty secret.")
    p = subprocess.run(
        [
            "secret-tool", "store",
            "--label", f"Jarvis: {name}",
            "service", SERVICE,
            "key", name,
        ],
        input=value + "\n",
        text=True,
        capture_output=True,
        timeout=30,
    )
    if p.returncode != 0:
        raise SecretStoreError(p.stderr.strip() or "secret-tool store failed")


def set_interactive(name: str) -> None:
    value = getpass.getpass(f"Secret value for {name}: ")
    set(name, value)


def delete(name: str) -> bool:
    _need_store()
    p = subprocess.run(
        ["secret-tool", "clear", "service", SERVICE, "key", name],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return p.returncode == 0
