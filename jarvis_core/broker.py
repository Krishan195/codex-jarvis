"""Trusted local control broker for Jarvis.

Codex remains in workspace-write sandbox. Read-only desktop requests are
written to a workspace spool; this user service executes them outside the
sandbox. Privileged/consequential actions are never accepted directly: the
broker only executes an already-approved one-time approval ID.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import secrets
import time
import traceback
from typing import Any

AGENT_HOME = Path.home() / "my-agent"
CONTROL_DIR = AGENT_HOME / ".jarvis-control"
REQUEST_DIR = CONTROL_DIR / "requests"
RESPONSE_DIR = CONTROL_DIR / "responses"
PROCESSING_DIR = CONTROL_DIR / "processing"


class BrokerError(RuntimeError):
    pass


def _ensure() -> None:
    for p in (CONTROL_DIR, REQUEST_DIR, RESPONSE_DIR, PROCESSING_DIR):
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def submit(action: str, payload: dict[str, Any] | None = None,
           timeout: float = 45.0) -> Any:
    """Submit a broker request and wait for a response."""
    _ensure()
    rid = secrets.token_hex(8)
    req = {
        "id": rid,
        "action": action,
        "payload": payload or {},
        "created_at": time.time(),
    }
    _write_json(REQUEST_DIR / f"{rid}.json", req)

    response = RESPONSE_DIR / f"{rid}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if response.exists():
            try:
                data = json.loads(response.read_text(encoding="utf-8"))
            finally:
                try:
                    response.unlink()
                except OSError:
                    pass
            if not data.get("ok"):
                raise BrokerError(data.get("error") or "Jarvis broker request failed")
            return data.get("result")
        time.sleep(0.08)
    raise BrokerError(f"Jarvis broker timed out waiting for {action}")


def _execute_readonly(action: str, payload: dict[str, Any]) -> Any:
    from . import browser as browserctl
    from . import showcase

    if action == "browser.start":
        browserctl.ensure_started()
        return {"ready": True, "profile": str(browserctl.PROFILE_DIR)}
    if action == "browser.open":
        return browserctl.open_url(payload["url"])
    if action == "browser.search":
        return browserctl.search(payload["query"])
    if action == "browser.read":
        return browserctl.read_page(
            payload.get("url"),
            max_chars=int(payload.get("max_chars") or 12000),
        )
    if action == "browser.inspect":
        return browserctl.inspect_interactive(
            int(payload.get("max_items") or 80)
        )
    if action == "browser.login_window":
        return browserctl.login_window(payload["url"])
    if action == "show":
        showcase.spawn(
            payload["title"],
            payload["description"],
            payload.get("image_url", ""),
            payload.get("source_url", ""),
        )
        return {"shown": True}
    raise BrokerError(f"Unsupported read-only broker action: {action}")


def _execute_approved(approval_id: str) -> dict[str, Any]:
    """Claim and execute one already-approved action.

    The request payload lives in the protected approval store, not in the
    workspace spool. This prevents a sandboxed process from changing the
    approved command/action after Boss approved it.
    """
    from . import approvals
    from . import browser as browserctl
    from . import credentials as credential_store
    from . import system_access
    from .google_workspace import create_event, send_email

    req = approvals.claim(approval_id)
    try:
        action = req["action"]
        p = req["payload"]

        if action == "gmail.send":
            result = send_email(p["to"], p["subject"], p["body"])
            detail = f"Gmail message id {result.get('id', '(unknown)')}"
            out = {"detail": detail}

        elif action == "calendar.create":
            result = create_event(
                p["summary"], p["start"], p["end"],
                p.get("description", ""), p.get("location", ""),
            )
            detail = f"Calendar event id {result.get('id', '(unknown)')}"
            out = {"detail": detail}

        elif action == "browser.login":
            username, password = credential_store.get_credential(p["site"])
            result = browserctl.login_with_credential(
                p["url"],
                username,
                password,
                p["username_selector"],
                p["password_selector"],
                p.get("submit_selector") or None,
            )
            detail = f"Browser login completed at {result.get('url', '')}"
            out = {"detail": detail, "result": result}

        elif action == "browser.click":
            result = browserctl.click(p["selector"])
            detail = f"Browser click completed at {result.get('url', '')}"
            out = {"detail": detail, "result": result}

        elif action == "system.exec":
            result = system_access.run(p["command"])
            detail = (
                f"Ubuntu command rc={result['returncode']}\n"
                f"stdout:\n{result['stdout']}\n"
                f"stderr:\n{result['stderr']}"
            )
            if result["returncode"] != 0:
                raise RuntimeError(detail)
            out = {"detail": detail, "result": result}

        else:
            raise BrokerError(f"Unsupported approved action: {action}")

    except Exception as exc:
        approvals.mark_result(req["id"], False, str(exc))
        raise

    approvals.mark_result(req["id"], True, out["detail"])
    return out


def _handle(req: dict[str, Any]) -> Any:
    action = str(req.get("action") or "")
    payload = req.get("payload") or {}

    if action.startswith("browser.") or action == "show":
        # Only the explicit read-only actions below are permitted without an
        # approval ID. browser.click/browser.login are intentionally absent.
        allowed = {
            "browser.start",
            "browser.open",
            "browser.search",
            "browser.read",
            "browser.inspect",
            "browser.login_window",
            "show",
        }
        if action not in allowed:
            raise BrokerError(f"Approval required for broker action: {action}")
        return _execute_readonly(action, payload)

    if action == "approved.execute":
        approval_id = str(payload.get("approval_id") or "")
        if not approval_id:
            raise BrokerError("approval_id is required")
        return _execute_approved(approval_id)

    raise BrokerError(f"Unsupported broker action: {action}")


def daemon() -> None:
    _ensure()
    while True:
        handled = False
        for path in sorted(REQUEST_DIR.glob("*.json")):
            handled = True
            processing = PROCESSING_DIR / path.name
            try:
                os.replace(path, processing)
            except FileNotFoundError:
                continue

            rid = processing.stem
            response = RESPONSE_DIR / f"{rid}.json"
            try:
                req = json.loads(processing.read_text(encoding="utf-8"))
                result = _handle(req)
                data = {"ok": True, "result": result}
            except Exception as exc:
                data = {
                    "ok": False,
                    "error": str(exc),
                    "trace": traceback.format_exc(limit=8),
                }
            _write_json(response, data)
            try:
                processing.unlink()
            except OSError:
                pass

        if not handled:
            time.sleep(0.08)


if __name__ == "__main__":
    daemon()
