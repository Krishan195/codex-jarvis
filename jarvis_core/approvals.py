"""One-time approval broker for consequential external actions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import secrets as pysecrets
import tempfile
from typing import Any

STATE_DIR = Path.home() / ".local" / "share" / "codex-jarvis"
REQUEST_DIR = STATE_DIR / "approvals"
AUDIT_LOG = STATE_DIR / "audit.jsonl"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _ensure() -> None:
    REQUEST_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(STATE_DIR, 0o700)
        os.chmod(REQUEST_DIR, 0o700)
    except OSError:
        pass


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    _ensure()
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def audit(event: str, *, request_id: str | None = None,
          detail: str | None = None) -> None:
    _ensure()
    row = {"ts": _iso(), "event": event}
    if request_id:
        row["request_id"] = request_id
    if detail:
        row["detail"] = detail
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    try:
        os.chmod(AUDIT_LOG, 0o600)
    except OSError:
        pass


def propose(action: str, summary: str, payload: dict[str, Any],
            ttl_minutes: int = 15) -> dict[str, Any]:
    _ensure()
    rid = pysecrets.token_hex(4)
    req = {
        "id": rid,
        "action": action,
        "summary": summary,
        "payload": payload,
        "status": "pending",
        "created_at": _iso(),
        "expires_at": _iso(_now() + timedelta(minutes=ttl_minutes)),
    }
    _atomic_json(REQUEST_DIR / f"{rid}.json", req)
    audit("approval_requested", request_id=rid, detail=summary)
    return req


def load(request_id: str) -> dict[str, Any]:
    path = REQUEST_DIR / f"{request_id}.json"
    if not path.exists():
        raise KeyError(f"Unknown approval request: {request_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _expired(req: dict[str, Any]) -> bool:
    return _now() > datetime.fromisoformat(req["expires_at"])


def approve(request_id: str) -> dict[str, Any]:
    req = load(request_id)
    if req["status"] != "pending":
        raise RuntimeError(f"Request is {req['status']}, not pending.")
    if _expired(req):
        req["status"] = "expired"
        _atomic_json(REQUEST_DIR / f"{request_id}.json", req)
        audit("approval_expired", request_id=request_id)
        raise RuntimeError("Approval request expired.")
    req["status"] = "approved"
    req["approved_at"] = _iso()
    _atomic_json(REQUEST_DIR / f"{request_id}.json", req)
    audit("approval_granted", request_id=request_id, detail=req["summary"])
    return req


def reject(request_id: str) -> dict[str, Any]:
    req = load(request_id)
    if req["status"] not in {"pending", "approved"}:
        raise RuntimeError(f"Request is {req['status']}; cannot reject.")
    req["status"] = "rejected"
    req["rejected_at"] = _iso()
    _atomic_json(REQUEST_DIR / f"{request_id}.json", req)
    audit("approval_rejected", request_id=request_id, detail=req["summary"])
    return req


def claim(request_id: str) -> dict[str, Any]:
    """Consume an approval before the external side effect.

    This deliberately makes approvals one-shot. If execution fails, a fresh
    request is required rather than risking an accidental duplicate action.
    """
    req = load(request_id)
    if req["status"] != "approved":
        raise RuntimeError(f"Request is {req['status']}; explicit approval required.")
    if _expired(req):
        req["status"] = "expired"
        _atomic_json(REQUEST_DIR / f"{request_id}.json", req)
        audit("approval_expired", request_id=request_id)
        raise RuntimeError("Approval request expired.")
    req["status"] = "consumed"
    req["consumed_at"] = _iso()
    _atomic_json(REQUEST_DIR / f"{request_id}.json", req)
    audit("approval_consumed", request_id=request_id, detail=req["summary"])
    return req


def mark_result(request_id: str, success: bool, detail: str = "") -> None:
    req = load(request_id)
    req["execution_result"] = "success" if success else "failed"
    req["executed_at"] = _iso()
    if detail:
        req["execution_detail"] = detail[:500]
    _atomic_json(REQUEST_DIR / f"{request_id}.json", req)
    audit(
        "action_executed" if success else "action_failed",
        request_id=request_id,
        detail=detail[:500] if detail else None,
    )


def pending() -> list[dict[str, Any]]:
    _ensure()
    rows = []
    for p in sorted(REQUEST_DIR.glob("*.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            req = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if req.get("status") == "pending":
            if _expired(req):
                req["status"] = "expired"
                _atomic_json(p, req)
                continue
            rows.append(req)
    return rows
