"""Website credential records backed by Linux Secret Service.

Only non-secret metadata (site + username label) is indexed on disk. Passwords
remain inside Secret Service and are returned only to trusted local code.
"""
from __future__ import annotations

from pathlib import Path
import getpass
import json
import os
from urllib.parse import urlparse

from . import secrets

STATE_DIR = Path.home() / ".local" / "share" / "codex-jarvis"
INDEX = STATE_DIR / "credential-index.json"


def _site_key(site: str) -> str:
    raw = site.strip()
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower().strip(".")
    if not host:
        raise ValueError("A valid website/domain is required.")
    return host


def _load_index() -> dict[str, dict[str, str]]:
    if not INDEX.exists():
        return {}
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(data: dict[str, dict[str, str]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    INDEX.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    try:
        os.chmod(INDEX, 0o600)
    except OSError:
        pass


def set_credential(site: str, username: str) -> str:
    host = _site_key(site)
    password = getpass.getpass(f"Password for {username}@{host}: ")
    if not password:
        raise ValueError("Refusing to store an empty password.")
    secrets.set(f"website:{host}:password", password)
    data = _load_index()
    data[host] = {"username": username}
    _save_index(data)
    return host


def get_credential(site: str) -> tuple[str, str]:
    host = _site_key(site)
    data = _load_index()
    meta = data.get(host)
    if not meta:
        raise KeyError(f"No website credential metadata for {host}")
    password = secrets.get(f"website:{host}:password")
    if not password:
        raise KeyError(f"No stored password for {host}")
    return meta["username"], password


def delete_credential(site: str) -> bool:
    host = _site_key(site)
    secrets.delete(f"website:{host}:password")
    data = _load_index()
    existed = host in data
    data.pop(host, None)
    _save_index(data)
    return existed


def list_credentials() -> list[dict[str, str]]:
    data = _load_index()
    return [
        {"site": site, "username": meta.get("username", "")}
        for site, meta in sorted(data.items())
    ]
