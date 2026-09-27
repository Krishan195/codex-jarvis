"""Trusted local control broker for Jarvis.

The Codex voice brain stays workspace-scoped. It can write requests into a
workspace spool, while this user service performs explicitly allow-listed local
actions outside the Codex sandbox.

Consequential actions use a two-step design:
1. Codex may PROPOSE an action through the broker.
2. Only an already-approved one-time approval ID may be EXECUTED.

The approval payload is stored outside the workspace so it cannot be modified
after approval. Approval itself is intentionally not exposed through the broker.
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

READ_ONLY_ACTIONS = {
    "browser.start",
    "browser.open",
    "browser.search",
    "browser.read",
    "browser.inspect",
    "browser.upwork_jobs",
    "browser.login_window",
    "show",
}

APPROVABLE_ACTIONS = {
    "gmail.send",
    "calendar.create",
    "browser.login",
    "browser.click",
    "system.exec",
}


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


def submit(
    action: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 45.0,
) -> Any:
    """Submit one request to the trusted broker and wait for its response."""
    _ensure()
    rid = secrets.token_hex(8)
    _write_json(
        REQUEST_DIR / f"{rid}.json",
        {
            "id": rid,
            "action": action,
            "payload": payload or {},
            "created_at": time.time(),
        },
    )

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
    raise BrokerError(
        f"Jarvis broker timed out waiting for {action}. "
        "Check jarvis-broker.service."
    )


def _execute_readonly(action: str, payload: dict[str, Any]) -> Any:
    from . import browser as browserctl
    from . import showcase

    if action == "browser.start":
        browserctl.ensure_started()
        return {"ready": True, "profile": str(browserctl.PROFILE_DIR)}
    if action == "browser.open":
        return browserctl.open_url(str(payload["url"]))
    if action == "browser.search":
        return browserctl.search(str(payload["query"]))
    if action == "browser.read":
        return browserctl.read_page(
            payload.get("url"),
            max_chars=min(int(payload.get("max_chars") or 12000), 20000),
        )
    if action == "browser.inspect":
        return browserctl.inspect_interactive(
            min(int(payload.get("max_items") or 80), 120)
        )
    if action == "browser.upwork_jobs":
        return browserctl.extract_upwork_jobs(
            min(int(payload.get("max_items") or 40), 80)
        )
    if action == "browser.login_window":
        return browserctl.login_window(str(payload["url"]))
    if action == "show":
        showcase.spawn(
            str(payload["title"]),
            str(payload["description"]),
            str(payload.get("image_url", "")),
            str(payload.get("source_url", "")),
        )
        return {"shown": True}
    raise BrokerError(f"Unsupported read-only broker action: {action}")


def _propose(payload: dict[str, Any]) -> dict[str, Any]:
    from . import approvals

    action = str(payload.get("action") or "")
    if action not in APPROVABLE_ACTIONS:
        raise BrokerError(f"Action is not approvable through Jarvis: {action}")
    summary = str(payload.get("summary") or "").strip()
    body = payload.get("payload")
    if not summary or not isinstance(body, dict):
        raise BrokerError("Approval proposal requires summary and payload.")
    return approvals.propose(action, summary[:1000], body)


def _execute_approved(approval_id: str) -> dict[str, Any]:
    """Claim and execute one already-approved action.

    Audit entries contain only a compact result. Command output is returned to
    the caller but is deliberately not persisted in the audit log.
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
            detail = f"Gmail send completed; message id {result.get('id', '(unknown)')}"
            out = {"detail": detail}

        elif action == "calendar.create":
            result = create_event(
                p["summary"],
                p["start"],
                p["end"],
                p.get("description", ""),
                p.get("location", ""),
            )
            detail = f"Calendar create completed; event id {result.get('id', '(unknown)')}"
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
            detail = "Browser credential login completed."
            out = {"detail": detail, "result": result}

        elif action == "browser.click":
            result = browserctl.click(p["selector"])
            detail = "Approved browser click completed."
            out = {"detail": detail, "result": result}

        elif action == "system.exec":
            result = system_access.run(p["command"])
            rc = int(result["returncode"])
            detail = f"Approved Ubuntu command completed with exit code {rc}."
            out = {"detail": detail, "result": result}
            if rc != 0:
                approvals.mark_result(req["id"], False, detail)
                raise RuntimeError(
                    detail
                    + "\nstdout:\n"
                    + result["stdout"]
                    + "\nstderr:\n"
                    + result["stderr"]
                )

        else:
            raise BrokerError(f"Unsupported approved action: {action}")

    except Exception as exc:
        # Never persist arbitrary exception text; it may contain command output
        # or sensitive page details.
        try:
            approvals.mark_result(req["id"], False, "Approved action failed.")
        except Exception:
            pass
        raise

    approvals.mark_result(req["id"], True, detail)
    return out


def _handle(req: dict[str, Any]) -> Any:
    action = str(req.get("action") or "")
    payload = req.get("payload") or {}
    if not isinstance(payload, dict):
        raise BrokerError("Broker payload must be an object.")

    if action in READ_ONLY_ACTIONS:
        return _execute_readonly(action, payload)

    if action == "approval.propose":
        return _propose(payload)

    if action == "approved.execute":
        approval_id = str(payload.get("approval_id") or "")
        if not approval_id:
            raise BrokerError("approval_id is required")
        return _execute_approved(approval_id)

    # Approval granting/rejection is deliberately absent. A sandboxed model
    # must never be able to approve its own proposed action.
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
                    "error": str(exc)[:4000],
                    "trace": traceback.format_exc(limit=4)[:6000],
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
