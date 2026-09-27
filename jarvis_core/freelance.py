"""Jarvis Freelance Agent: local opportunity pipeline and delivery workspace.

This module is deliberately a co-pilot, not an autonomous bidding bot.
It may ingest/read opportunities, score and organize them, save proposal drafts,
and create delivery workspaces. Platform submissions and commercial commitments
remain manual human actions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import sqlite3
import subprocess
import sys
from typing import Any

from . import browser as browserctl
from .google_workspace import GoogleNotConnected, search_messages

STATE_DIR = Path.home() / ".local" / "share" / "codex-jarvis"
DB_PATH = STATE_DIR / "freelance.sqlite3"
AGENT_HOME = Path.home() / "my-agent"
MEMORY_REPORT = AGENT_HOME / "Memory" / "05 - Resources" / "Jobs" / "Freelance Opportunities.md"
WORKSPACE_ROOT = AGENT_HOME / "Freelance"


STATUSES = {
    "new",
    "shortlisted",
    "reviewed",
    "applied-manual",
    "interview",
    "active",
    "delivered",
    "won",
    "lost",
    "skip",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE,
            source TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            budget TEXT NOT NULL DEFAULT '',
            skills TEXT NOT NULL DEFAULT '',
            client TEXT NOT NULL DEFAULT '',
            fit_score INTEGER,
            fit_reason TEXT NOT NULL DEFAULT '',
            proposal TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_freelance_status ON opportunities(status)"
    )
    conn.commit()
    return conn


def _clean(text: str | None) -> str:
    return " ".join((text or "").split()).strip()


def _budget_from_text(text: str) -> str:
    patterns = [
        r"\$[\d,]+(?:\.\d+)?\s*(?:-|to)\s*\$[\d,]+(?:\.\d+)?",
        r"\$[\d,]+(?:\.\d+)?\s*/?\s*(?:hr|hour)",
        r"(?:fixed[- ]price|budget)\s*[:\-]?\s*\$[\d,]+(?:\.\d+)?",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            return _clean(m.group(0))
    return ""


def upsert(
    *,
    external_id: str,
    source: str,
    source_url: str,
    title: str,
    description: str = "",
    budget: str = "",
    skills: str = "",
    client: str = "",
) -> tuple[int, bool]:
    conn = _db()
    existing = conn.execute(
        "SELECT id FROM opportunities WHERE external_id = ?", (external_id,)
    ).fetchone()
    now = _now()
    if existing:
        conn.execute(
            """
            UPDATE opportunities
               SET source_url=?, title=?, description=?, budget=?, skills=?,
                   client=?, updated_at=?
             WHERE id=?
            """,
            (
                source_url,
                title,
                description,
                budget,
                skills,
                client,
                now,
                existing["id"],
            ),
        )
        conn.commit()
        return int(existing["id"]), False

    cur = conn.execute(
        """
        INSERT INTO opportunities
            (external_id, source, source_url, title, description, budget,
             skills, client, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            external_id,
            source,
            source_url,
            title,
            description,
            budget,
            skills,
            client,
            now,
            now,
        ),
    )
    conn.commit()
    return int(cur.lastrowid), True


def scan_browser() -> dict[str, Any]:
    rows = browserctl.extract_upwork_jobs()
    added = 0
    ids: list[int] = []
    for row in rows:
        url = _clean(row.get("url"))
        title = _clean(row.get("title")) or "Untitled Upwork opportunity"
        text = _clean(row.get("text"))
        external = url or f"browser:{title}:{text[:120]}"
        oid, created = upsert(
            external_id=external,
            source="upwork-browser",
            source_url=url,
            title=title,
            description=text,
            budget=_budget_from_text(text),
        )
        ids.append(oid)
        added += int(created)
    write_memory_report()
    return {"found": len(rows), "added": added, "ids": ids}


def scan_email(days: int = 7, max_results: int = 50) -> dict[str, Any]:
    # Gmail's from: search accepts domains/strings. This keeps the unattended
    # workflow to Upwork's own mail alerts rather than scraping the site.
    query = f"from:upwork newer_than:{max(1, days)}d"
    rows = search_messages(query, max_results=max_results, include_body=True)
    added = 0
    ids: list[int] = []
    for row in rows:
        links = [u for u in row.get("links", []) if "upwork.com" in u.lower()]
        url = links[0] if links else ""
        subject = _clean(row.get("subject")) or "Upwork email alert"
        body = _clean(row.get("body") or row.get("snippet"))
        oid, created = upsert(
            external_id=f"gmail:{row['id']}",
            source="upwork-email",
            source_url=url,
            title=subject,
            description=body[:12000],
            budget=_budget_from_text(body),
        )
        ids.append(oid)
        added += int(created)
    write_memory_report()
    return {"found": len(rows), "added": added, "ids": ids}


def get(opportunity_id: int) -> sqlite3.Row:
    conn = _db()
    row = conn.execute(
        "SELECT * FROM opportunities WHERE id=?", (opportunity_id,)
    ).fetchone()
    if not row:
        raise KeyError(f"Unknown opportunity id: {opportunity_id}")
    return row


def list_rows(status: str | None = None, limit: int = 30) -> list[sqlite3.Row]:
    conn = _db()
    if status:
        return conn.execute(
            """
            SELECT * FROM opportunities
             WHERE status=?
             ORDER BY updated_at DESC
             LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    return conn.execute(
        """
        SELECT * FROM opportunities
         ORDER BY
           CASE status
             WHEN 'shortlisted' THEN 0
             WHEN 'new' THEN 1
             WHEN 'reviewed' THEN 2
             WHEN 'interview' THEN 3
             WHEN 'active' THEN 4
             ELSE 5
           END,
           COALESCE(fit_score, -1) DESC,
           updated_at DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()


def save_analysis(opportunity_id: int, score: int, reason: str) -> None:
    if score < 0 or score > 100:
        raise ValueError("fit score must be between 0 and 100")
    conn = _db()
    conn.execute(
        """
        UPDATE opportunities
           SET fit_score=?, fit_reason=?, updated_at=?
         WHERE id=?
        """,
        (score, reason.strip(), _now(), opportunity_id),
    )
    if conn.total_changes == 0:
        raise KeyError(f"Unknown opportunity id: {opportunity_id}")
    conn.commit()
    write_memory_report()


def save_proposal(opportunity_id: int, proposal: str) -> None:
    if not proposal.strip():
        raise ValueError("proposal draft is empty")
    conn = _db()
    conn.execute(
        """
        UPDATE opportunities
           SET proposal=?, updated_at=?
         WHERE id=?
        """,
        (proposal.strip(), _now(), opportunity_id),
    )
    if conn.total_changes == 0:
        raise KeyError(f"Unknown opportunity id: {opportunity_id}")
    conn.commit()


def set_status(opportunity_id: int, status: str) -> None:
    if status not in STATUSES:
        raise ValueError("invalid status: " + status)
    conn = _db()
    conn.execute(
        "UPDATE opportunities SET status=?, updated_at=? WHERE id=?",
        (status, _now(), opportunity_id),
    )
    if conn.total_changes == 0:
        raise KeyError(f"Unknown opportunity id: {opportunity_id}")
    conn.commit()
    write_memory_report()


def _slug(text: str) -> str:
    out = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return out[:60] or "project"


def create_workspace(opportunity_id: int) -> Path:
    row = get(opportunity_id)
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    path = WORKSPACE_ROOT / f"{opportunity_id:04d}-{_slug(row['title'])}"
    path.mkdir(parents=True, exist_ok=True)
    brief = path / "JOB.md"
    if not brief.exists():
        brief.write_text(
            f"""# {row['title']}

## Source

- Opportunity ID: {row['id']}
- Source: {row['source']}
- URL: {row['source_url']}
- Budget: {row['budget']}
- Fit score: {row['fit_score'] if row['fit_score'] is not None else 'not assessed'}

## Job description

{row['description']}

## Fit reasoning

{row['fit_reason']}

## Proposal draft

{row['proposal']}

## Delivery checklist

- [ ] Confirm exact scope with client.
- [ ] Record acceptance criteria.
- [ ] Create implementation plan.
- [ ] Build in small reviewable steps.
- [ ] Test the result.
- [ ] Remove secrets/private data from deliverables.
- [ ] Prepare concise handover notes.
- [ ] Boss reviews final output before delivery.
""",
            encoding="utf-8",
        )
    set_status(opportunity_id, "active")
    return path


def write_memory_report(limit: int = 25) -> None:
    rows = list_rows(limit=limit)
    MEMORY_REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "status: active",
        "project: freelance-agent",
        "type: dashboard",
        "---",
        "# Freelance Opportunities",
        "",
        "Generated from Jarvis's local opportunity database.",
        "Platform submission remains a manual user action.",
        "",
    ]
    if not rows:
        lines.append("No opportunities captured yet.")
    for row in rows:
        score = (
            f"{row['fit_score']}/100"
            if row["fit_score"] is not None
            else "not assessed"
        )
        lines += [
            f"## #{row['id']} - {row['title']}",
            f"- Status: {row['status']}",
            f"- Source: {row['source']}",
            f"- Fit: {score}",
            f"- Budget: {row['budget'] or 'not captured'}",
        ]
        if row["source_url"]:
            lines.append(f"- URL: {row['source_url']}")
        if row["fit_reason"]:
            lines.append(f"- Why: {row['fit_reason']}")
        lines.append("")
    MEMORY_REPORT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def digest_text(limit: int = 10) -> str:
    rows = list_rows(limit=limit)
    if not rows:
        return "No freelance opportunities are currently captured."
    lines = [f"Jarvis freelance digest: {len(rows)} opportunities shown."]
    for row in rows:
        score = (
            str(row["fit_score"]) if row["fit_score"] is not None else "unscored"
        )
        budget = f", {row['budget']}" if row["budget"] else ""
        lines.append(
            f"#{row['id']} [{row['status']}] fit {score}: "
            f"{row['title']}{budget}"
        )
    return "\n".join(lines)


def notify(text: str) -> None:
    subprocess.run(
        ["notify-send", "Jarvis freelance agent", text[:3500]],
        check=False,
        timeout=10,
    )


def _print_row(row: sqlite3.Row) -> None:
    data = dict(row)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_status(_args) -> int:
    conn = _db()
    count = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    shortlisted = conn.execute(
        "SELECT COUNT(*) FROM opportunities WHERE status='shortlisted'"
    ).fetchone()[0]
    active = conn.execute(
        "SELECT COUNT(*) FROM opportunities WHERE status='active'"
    ).fetchone()[0]
    print(f"Database: {DB_PATH}")
    print(f"Opportunities: {count}")
    print(f"Shortlisted: {shortlisted}")
    print(f"Active projects: {active}")
    print(f"Memory report: {MEMORY_REPORT}")
    return 0


def cmd_scan_browser(args) -> int:
    result = scan_browser()
    print(json.dumps(result, indent=2))
    if args.notify:
        notify(
            f"Browser scan found {result['found']} opportunities; "
            f"{result['added']} were new."
        )
    return 0


def cmd_scan_email(args) -> int:
    result = scan_email(args.days, args.max_results)
    print(json.dumps(result, indent=2))
    if args.notify:
        notify(
            f"Upwork email scan found {result['found']} alerts; "
            f"{result['added']} were new."
        )
    return 0


def cmd_list(args) -> int:
    rows = list_rows(args.status, args.limit)
    if not rows:
        print("No matching opportunities.")
        return 0
    for row in rows:
        score = row["fit_score"] if row["fit_score"] is not None else "-"
        print(
            f"#{row['id']}  {row['status']:<14} fit={score!s:<3} "
            f"{row['title'][:90]}"
        )
    return 0


def cmd_show(args) -> int:
    _print_row(get(args.id))
    return 0


def cmd_analyze(args) -> int:
    save_analysis(args.id, args.score, args.reason)
    print(f"Saved analysis for opportunity #{args.id}.")
    return 0


def cmd_proposal_save(args) -> int:
    text = (
        Path(args.file).expanduser().read_text(encoding="utf-8")
        if args.file else args.text
    )
    save_proposal(args.id, text)
    print(f"Saved proposal draft for opportunity #{args.id}.")
    print("Submission is intentionally manual.")
    return 0


def cmd_set_status(args) -> int:
    set_status(args.id, args.status)
    print(f"Opportunity #{args.id} -> {args.status}")
    return 0


def cmd_workspace(args) -> int:
    path = create_workspace(args.id)
    print(path)
    return 0


def cmd_digest(args) -> int:
    text = digest_text(args.limit)
    write_memory_report()
    print(text)
    if args.notify:
        notify(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jarvis-freelance",
        description="Jarvis freelance opportunity co-pilot",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("scan-browser")
    s.add_argument("--notify", action="store_true")
    s.set_defaults(func=cmd_scan_browser)

    s = sub.add_parser("scan-email")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--max-results", type=int, default=50)
    s.add_argument("--notify", action="store_true")
    s.set_defaults(func=cmd_scan_email)

    s = sub.add_parser("list")
    s.add_argument("--status", choices=sorted(STATUSES))
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("analyze")
    s.add_argument("id", type=int)
    s.add_argument("--score", type=int, required=True)
    s.add_argument("--reason", required=True)
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("proposal-save")
    s.add_argument("id", type=int)
    group = s.add_mutually_exclusive_group(required=True)
    group.add_argument("--text")
    group.add_argument("--file")
    s.set_defaults(func=cmd_proposal_save)

    s = sub.add_parser("status-set")
    s.add_argument("id", type=int)
    s.add_argument("status", choices=sorted(STATUSES))
    s.set_defaults(func=cmd_set_status)

    s = sub.add_parser("workspace")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_workspace)

    s = sub.add_parser("digest")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--notify", action="store_true")
    s.set_defaults(func=cmd_digest)

    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args) or 0)
    except GoogleNotConnected as exc:
        print(f"Google connection required: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Jarvis freelance error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
