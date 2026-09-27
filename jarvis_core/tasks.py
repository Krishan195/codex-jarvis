"""Safe local task scheduler for Jarvis.

The scheduler deliberately supports only allow-listed task types. It is not an
arbitrary shell cron replacement.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys
import time
from typing import Any

AGENT_HOME = Path.home() / "my-agent"
STATE_DIR = AGENT_HOME / ".jarvis-tasks"
DB_PATH = STATE_DIR / "tasks.sqlite3"

KINDS = {"notify", "briefing", "freelance-daily", "memory-refresh"}


def _now() -> datetime:
    return datetime.now().astimezone()


def _ensure() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(STATE_DIR, 0o700)
    except OSError:
        pass


def _db() -> sqlite3.Connection:
    _ensure()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            schedule_type TEXT NOT NULL,
            schedule_value TEXT NOT NULL,
            next_run TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_run TEXT NOT NULL DEFAULT '',
            last_result TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass
    return conn


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_now().tzinfo)
    return dt.astimezone()


def _next_daily(hhmm: str, base: datetime | None = None) -> datetime:
    base = base or _now()
    hour, minute = [int(x) for x in hhmm.split(":", 1)]
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Daily time must be HH:MM.")
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    return candidate


def add_once(title: str, kind: str, payload: dict[str, Any], when: str) -> int:
    if kind not in KINDS:
        raise ValueError(f"Unsupported task kind: {kind}")
    run = _parse_iso(when)
    if run <= _now():
        raise ValueError("Scheduled time must be in the future.")
    conn = _db()
    cur = conn.execute(
        """
        INSERT INTO tasks(title,kind,payload,schedule_type,schedule_value,
                          next_run,created_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        (title, kind, json.dumps(payload), "once", when, _iso(run), _iso(_now())),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_daily(title: str, kind: str, payload: dict[str, Any], hhmm: str) -> int:
    if kind not in KINDS:
        raise ValueError(f"Unsupported task kind: {kind}")
    run = _next_daily(hhmm)
    conn = _db()
    cur = conn.execute(
        """
        INSERT INTO tasks(title,kind,payload,schedule_type,schedule_value,
                          next_run,created_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        (title, kind, json.dumps(payload), "daily", hhmm, _iso(run), _iso(_now())),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_interval(
    title: str,
    kind: str,
    payload: dict[str, Any],
    minutes: int,
) -> int:
    if kind not in KINDS:
        raise ValueError(f"Unsupported task kind: {kind}")
    if minutes < 60:
        raise ValueError("Recurring interval must be at least 60 minutes.")
    run = _now() + timedelta(minutes=minutes)
    conn = _db()
    cur = conn.execute(
        """
        INSERT INTO tasks(title,kind,payload,schedule_type,schedule_value,
                          next_run,created_at)
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            title,
            kind,
            json.dumps(payload),
            "interval",
            str(minutes),
            _iso(run),
            _iso(_now()),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_tasks() -> list[dict[str, Any]]:
    rows = _db().execute(
        "SELECT * FROM tasks ORDER BY enabled DESC, next_run ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def set_enabled(task_id: int, enabled: bool) -> None:
    conn = _db()
    cur = conn.execute(
        "UPDATE tasks SET enabled=? WHERE id=?",
        (1 if enabled else 0, task_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise KeyError(f"Unknown task id: {task_id}")


def remove(task_id: int) -> None:
    conn = _db()
    cur = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    if cur.rowcount == 0:
        raise KeyError(f"Unknown task id: {task_id}")


def _notify(title: str, message: str) -> None:
    subprocess.run(
        ["notify-send", title[:120], message[:3500]],
        check=False,
        timeout=10,
    )


def _run_kind(kind: str, payload: dict[str, Any]) -> str:
    if kind == "notify":
        message = str(payload.get("message") or "").strip()
        if not message:
            raise ValueError("Reminder message is empty.")
        _notify("Jarvis reminder", message)
        return "notification delivered"

    if kind == "briefing":
        cmd = [str(Path.home() / ".local" / "bin" / "jarvis-core"), "briefing", "--notify"]
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=180)
        if p.returncode:
            raise RuntimeError((p.stderr or p.stdout)[-1000:])
        return "briefing completed"

    if kind == "freelance-daily":
        cmd = [
            str(Path.home() / ".local" / "bin" / "jarvis-freelance"),
            "daily",
            "--days",
            str(int(payload.get("days") or 3)),
            "--max-jobs",
            str(int(payload.get("max_jobs") or 5)),
            "--notify",
        ]
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=900)
        if p.returncode:
            raise RuntimeError((p.stderr or p.stdout)[-1000:])
        return "freelance daily run completed"

    if kind == "memory-refresh":
        cmd = [str(Path.home() / ".local" / "bin" / "jarvis-memory"), "refresh"]
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
        if p.returncode:
            raise RuntimeError((p.stderr or p.stdout)[-1000:])
        return "memory index refreshed"

    raise ValueError(f"Unsupported task kind: {kind}")


def _advance(row: sqlite3.Row, base: datetime) -> tuple[str, bool]:
    schedule_type = row["schedule_type"]
    value = row["schedule_value"]
    if schedule_type == "once":
        return row["next_run"], False
    if schedule_type == "daily":
        return _iso(_next_daily(value, base)), True
    if schedule_type == "interval":
        minutes = max(60, int(value))
        return _iso(base + timedelta(minutes=minutes)), True
    raise ValueError(f"Unknown schedule type: {schedule_type}")


def run_due() -> int:
    conn = _db()
    now = _now()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE enabled=1 ORDER BY next_run ASC"
    ).fetchall()
    ran = 0
    for row in rows:
        try:
            due = _parse_iso(row["next_run"])
        except Exception:
            continue
        if due > now:
            continue

        payload = json.loads(row["payload"] or "{}")
        result = ""
        try:
            result = _run_kind(row["kind"], payload)
        except Exception as exc:
            result = "failed: " + str(exc)[:500]

        next_run, keep_enabled = _advance(row, now)
        conn.execute(
            """
            UPDATE tasks
               SET last_run=?, last_result=?, next_run=?, enabled=?
             WHERE id=?
            """,
            (
                _iso(now),
                result,
                next_run,
                1 if keep_enabled else 0,
                row["id"],
            ),
        )
        conn.commit()
        ran += 1
    return ran


def daemon() -> None:
    _ensure()
    while True:
        try:
            run_due()
        except Exception:
            pass
        time.sleep(20)


def cmd_add_reminder(args) -> int:
    task_id = add_once(
        args.title or "Reminder",
        "notify",
        {"message": args.message},
        args.at,
    )
    print(f"Created reminder #{task_id}.")
    return 0


def cmd_add_daily(args) -> int:
    payload: dict[str, Any] = {}
    if args.kind == "notify":
        if not args.message:
            raise ValueError("--message is required for notify tasks.")
        payload["message"] = args.message
    elif args.kind == "freelance-daily":
        payload = {"days": args.days, "max_jobs": args.max_jobs}
    task_id = add_daily(args.title or args.kind, args.kind, payload, args.time)
    print(f"Created daily task #{task_id}.")
    return 0


def cmd_add_interval(args) -> int:
    payload: dict[str, Any] = {}
    if args.kind == "notify":
        if not args.message:
            raise ValueError("--message is required for notify tasks.")
        payload["message"] = args.message
    task_id = add_interval(
        args.title or args.kind,
        args.kind,
        payload,
        args.minutes,
    )
    print(f"Created recurring task #{task_id}.")
    return 0


def cmd_list(_args) -> int:
    rows = list_tasks()
    if not rows:
        print("No scheduled Jarvis tasks.")
        return 0
    for row in rows:
        state = "on" if row["enabled"] else "off"
        print(
            f"#{row['id']} [{state}] {row['title']} :: {row['kind']} "
            f"next={row['next_run']}"
        )
    return 0


def cmd_enable(args) -> int:
    set_enabled(args.id, True)
    print(f"Enabled task #{args.id}.")
    return 0


def cmd_disable(args) -> int:
    set_enabled(args.id, False)
    print(f"Disabled task #{args.id}.")
    return 0


def cmd_remove(args) -> int:
    remove(args.id)
    print(f"Removed task #{args.id}.")
    return 0


def cmd_run_due(_args) -> int:
    print(f"Ran {run_due()} due task(s).")
    return 0


def cmd_daemon(_args) -> int:
    daemon()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis-task")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add-reminder")
    s.add_argument("--at", required=True, help="ISO date/time; local timezone if omitted")
    s.add_argument("--message", required=True)
    s.add_argument("--title")
    s.set_defaults(func=cmd_add_reminder)

    s = sub.add_parser("add-daily")
    s.add_argument("--time", required=True, help="HH:MM local time")
    s.add_argument("--kind", choices=sorted(KINDS), required=True)
    s.add_argument("--message")
    s.add_argument("--title")
    s.add_argument("--days", type=int, default=3)
    s.add_argument("--max-jobs", type=int, default=5)
    s.set_defaults(func=cmd_add_daily)

    s = sub.add_parser("add-interval")
    s.add_argument("--minutes", type=int, required=True)
    s.add_argument("--kind", choices=sorted(KINDS), required=True)
    s.add_argument("--message")
    s.add_argument("--title")
    s.set_defaults(func=cmd_add_interval)

    s = sub.add_parser("list")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("enable")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_enable)

    s = sub.add_parser("disable")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_disable)

    s = sub.add_parser("remove")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("run-due")
    s.set_defaults(func=cmd_run_due)

    s = sub.add_parser("daemon", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_daemon)

    return p


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.func(args) or 0)
    except Exception as exc:
        print(f"Jarvis task error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
